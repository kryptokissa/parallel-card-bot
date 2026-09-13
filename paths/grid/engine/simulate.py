"""A deterministic grid simulator, for choosing spacing instead of guessing it.

The cost gate only proves a grid is not guaranteed to lose. It says nothing
about whether ten rungs across 12% beats six, which is the question that
actually decides how a grid performs, and BUILDING_PATHS.md §4.3 says to answer
it against real data rather than by reasoning.

So this walks a price series through the grid's own level arithmetic and
accounts what happened: cycles completed, fees, funding, realized and
unrealized PnL, worst drawdown, and whether price left the range.

**What this model is.** Each rung holds at most one lot. A rung buys when price
falls to it, having been above it first; that lot is sold when price reaches the
next rung up, completing a cycle worth the gap minus costs. That is the
paired-grid model, and it is what makes inventory bounded and the accounting
unambiguous.

The "having been above it first" is load-bearing rather than pedantic. Without
it, every rung above the opening mark counts as filled on the first bar, because
price is trivially below it — which invents a full directional position out of
nothing and inflates every number downstream.

Bars carry a high and a low when the series has them, so a rung fills when the
bar's range touches it rather than only when a close does. A close-only series
still works — each price becomes a bar of zero width — but it undercounts fills
badly, because a grid earns precisely from the wicks a close hides.

**What this model is not.** It assumes a resting limit order always fills when
price reaches its level: no queue position, no partial fills, no adverse
selection. Within a bar the order of the high and the low is unknowable, so a
lot bought in a bar is never sold in that same bar — this undercounts rather
than flatters. It uses one funding rate for the whole run. Treat the output as a
comparison between configurations over the same series, which is what it is good
for, and not as a forecast of what a grid will earn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from engine.config import GridConfig
from engine.gates import evaluate
from engine.levels import GridGeometryError, build_levels


@dataclass(frozen=True)
class Bar:
    """One observation. `high == low == close` for a close-only series."""

    close: float
    high: float
    low: float

    @classmethod
    def of(cls, value: Any) -> "Bar":
        """Accept a float, an (h, l, c) triple, or a dict of HL candle fields."""
        if isinstance(value, Bar):
            return value
        if isinstance(value, (int, float)):
            price = float(value)
            return cls(close=price, high=price, low=price)
        if isinstance(value, dict):
            close = float(value.get("c", value.get("close")))
            high = float(value.get("h", value.get("high", close)))
            low = float(value.get("l", value.get("low", close)))
            return cls(close=close, high=high, low=low)
        if isinstance(value, (tuple, list)) and len(value) == 3:
            high, low, close = (float(v) for v in value)
            return cls(close=close, high=high, low=low)
        raise ValueError(f"cannot read a bar from {value!r}")


def as_bars(series: Iterable[Any]) -> list[Bar]:
    return [Bar.of(item) for item in series]


@dataclass
class SimResult:
    """What the series did to this configuration."""

    bars: int = 0
    cycles: int = 0
    fees_paid_usd: float = 0.0
    funding_paid_usd: float = 0.0
    # Profit from completed cycles only. A breakout flatten is not a cycle, so
    # its PnL is kept in `exit_pnl_usd` — folding it in here would let a grid
    # that never cycled report a large "cycling edge".
    gross_pnl_usd: float = 0.0
    exit_pnl_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    inventory_units: float = 0.0
    inventory_cost_usd: float = 0.0
    ending_mark: float = 0.0
    unrealized_pnl_usd: float = 0.0
    equity_usd: float = 0.0
    peak_equity_usd: float = 0.0
    worst_drawdown_usd: float = 0.0
    breakout_bar: int | None = None
    breakout_action: str | None = None
    halted: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def net_pnl_usd(self) -> float:
        """Realized plus unrealized, after fees and funding."""
        return self.realized_pnl_usd + self.unrealized_pnl_usd

    @property
    def worst_drawdown_pct(self) -> float:
        if self.peak_equity_usd <= 0:
            return 0.0
        return self.worst_drawdown_usd / self.peak_equity_usd * 100.0

    @property
    def cost_usd(self) -> float:
        return self.fees_paid_usd + self.funding_paid_usd

    @property
    def cycle_edge_usd(self) -> float:
        """PnL from completed cycles, after every cost.

        This is the number that measures the grid. It excludes both the leftover
        inventory's mark-to-market and any profit from flattening at a breakout,
        because neither is the grid doing its job — in a trending window a sparse
        grid that bought once and held can otherwise top a sweep having cycled
        twice. All costs are charged against cycling, which is conservative: a
        grid with no completed cycles reports a negative edge, being the fees and
        funding it spent achieving nothing.
        """
        return self.gross_pnl_usd - self.cost_usd

    def summary(self) -> str:
        breakout = (
            f", breakout at bar {self.breakout_bar} ({self.breakout_action})"
            if self.breakout_bar is not None
            else ""
        )
        exited = (
            f", ${self.exit_pnl_usd:,.2f} realised flattening at the bound"
            if self.exit_pnl_usd
            else ""
        )
        return (
            f"{self.cycles} cycles over {self.bars} bars, net "
            f"${self.net_pnl_usd:,.2f} (${self.cycle_edge_usd:,.2f} from cycling, "
            f"${self.unrealized_pnl_usd:,.2f} on leftover inventory{exited}) after "
            f"${self.cost_usd:,.2f} of costs (${self.fees_paid_usd:,.2f} fees, "
            f"${self.funding_paid_usd:,.2f} funding), worst drawdown "
            f"{self.worst_drawdown_pct:.1f}%{breakout}"
        )


def simulate(
    config: GridConfig,
    prices: Sequence[Any],
    *,
    hours_per_bar: float = 1.0,
) -> SimResult:
    """Walk `prices` through `config` and account the result.

    `prices` may be floats, (high, low, close) triples, or HL candle dicts. The
    first bar's close is the starting mark, so it decides which rungs open as
    buys and which as sells — the same rule the live planner uses.
    """
    bars = as_bars(prices)
    if not bars:
        raise ValueError("a simulation needs at least one bar")
    start_price = bars[0].close
    if start_price <= 0:
        raise ValueError("the opening price must be positive")

    levels = build_levels(config, start_price)
    fee_rate = config.fee_bps_per_side / 1e4
    funding_rate = config.funding_rate_per_hour

    # One lot per rung: held[i] is the cost of the lot resting at level i.
    held: dict[int, float] = {}
    # A rung buys only when price falls *to* it, which means price must have been
    # above it first. Rungs below the opening mark start armed; a rung above it
    # arms once price trades through it. Without this every rung above the mark
    # would "buy" on the first bar, because price is trivially below it.
    armed: set[int] = {
        level.index for level in levels if level.price < start_price
    }
    result = SimResult(peak_equity_usd=config.capital_usd)
    result.equity_usd = config.capital_usd

    for index, bar in enumerate(bars):
        result.bars = index + 1
        if bar.close <= 0 or bar.high <= 0 or bar.low <= 0:
            result.notes.append(f"bar {index}: non-positive price skipped")
            continue
        result.ending_mark = bar.close

        # Funding is charged on inventory carried into this bar.
        if result.inventory_units and funding_rate:
            funding = (
                abs(result.inventory_units)
                * bar.close
                * abs(funding_rate)
                * hours_per_bar
            )
            result.funding_paid_usd += funding
            result.realized_pnl_usd -= funding

        if bar.low < config.lower or bar.high > config.upper:
            result.breakout_bar = index
            result.breakout_action = config.breakout
            result.halted = True
            if config.breakout == "halt_close" and result.inventory_units:
                # Flattened at the bound that was breached, not at the extreme.
                exit_price = (
                    config.lower if bar.low < config.lower else config.upper
                )
                proceeds = result.inventory_units * exit_price
                fee = abs(proceeds) * fee_rate
                result.fees_paid_usd += fee
                result.exit_pnl_usd += proceeds - result.inventory_cost_usd
                result.realized_pnl_usd += proceeds - result.inventory_cost_usd - fee
                result.inventory_units = 0.0
                result.inventory_cost_usd = 0.0
                result.ending_mark = exit_price
                held.clear()
            break

        # Sells resolve first, and only for lots held coming into this bar: the
        # order of a bar's high and low is unknowable, so a lot bought in this
        # bar is not sold in it.
        for level_index in sorted(held):
            if level_index + 1 >= len(levels):
                continue
            exit_level = levels[level_index + 1]
            if bar.high >= exit_level.price:
                lot_size = levels[level_index].size
                proceeds = lot_size * exit_level.price
                fee = proceeds * fee_rate
                cost = held.pop(level_index)
                result.fees_paid_usd += fee
                result.gross_pnl_usd += proceeds - cost
                result.realized_pnl_usd += proceeds - cost - fee
                result.inventory_units -= lot_size
                result.inventory_cost_usd -= cost
                result.cycles += 1

        for level in levels:
            if bar.high > level.price:
                armed.add(level.index)

        for level in levels:
            if level.index in held or level.index + 1 >= len(levels):
                continue
            if level.index not in armed:
                continue
            if bar.low <= level.price:
                cost = level.size * level.price
                fee = cost * fee_rate
                held[level.index] = cost
                result.fees_paid_usd += fee
                result.realized_pnl_usd -= fee
                result.inventory_units += level.size
                result.inventory_cost_usd += cost
                armed.discard(level.index)

        result.unrealized_pnl_usd = (
            result.inventory_units * bar.close - result.inventory_cost_usd
        )
        result.equity_usd = (
            config.capital_usd + result.realized_pnl_usd + result.unrealized_pnl_usd
        )
        result.peak_equity_usd = max(result.peak_equity_usd, result.equity_usd)
        result.worst_drawdown_usd = max(
            result.worst_drawdown_usd, result.peak_equity_usd - result.equity_usd
        )

    result.unrealized_pnl_usd = (
        result.inventory_units * result.ending_mark - result.inventory_cost_usd
    )
    result.equity_usd = (
        config.capital_usd + result.realized_pnl_usd + result.unrealized_pnl_usd
    )
    result.worst_drawdown_usd = max(
        result.worst_drawdown_usd, result.peak_equity_usd - result.equity_usd
    )
    return result


@dataclass
class SweepRow:
    levels: int
    spacing: str
    gates_passed: bool
    result: SimResult | None
    reason: str = ""

    @property
    def net_pnl_usd(self) -> float:
        return self.result.net_pnl_usd if self.result else float("-inf")

    @property
    def cycle_edge_usd(self) -> float:
        return self.result.cycle_edge_usd if self.result else float("-inf")


def sweep(
    config: GridConfig,
    prices: Sequence[Any],
    *,
    level_counts: Iterable[int] | None = None,
    spacings: Iterable[str] = ("geometric", "arithmetic"),
    hours_per_bar: float = 1.0,
) -> list[SweepRow]:
    """Simulate a grid of configurations, best net PnL first.

    Configurations that fail a hard gate are kept in the output with the reason,
    rather than dropped: "20 rungs would be better but does not clear the $10
    minimum" is the useful answer, not a silently shorter list.
    """
    bars = as_bars(prices)
    if not bars:
        raise ValueError("a sweep needs at least one bar")
    opening = bars[0].close
    counts = list(level_counts) if level_counts is not None else list(range(2, 21))
    rows: list[SweepRow] = []
    for spacing in spacings:
        for levels in counts:
            candidate = config.__class__(
                **{
                    **{
                        field: getattr(config, field)
                        for field in config.__dataclass_fields__
                    },
                    "levels": levels,
                    "spacing": spacing,
                }
            )
            try:
                candidate.validate()
            except ValueError as exc:
                rows.append(SweepRow(levels, spacing, False, None, str(exc)))
                continue
            report = evaluate(candidate, opening)
            if not report.passed:
                rows.append(SweepRow(levels, spacing, False, None, report.summary()))
                continue
            try:
                outcome = simulate(candidate, bars, hours_per_bar=hours_per_bar)
            except (GridGeometryError, ValueError) as exc:
                rows.append(SweepRow(levels, spacing, False, None, str(exc)))
                continue
            rows.append(SweepRow(levels, spacing, True, outcome))

    rows.sort(key=lambda row: (row.gates_passed, row.cycle_edge_usd), reverse=True)
    return rows


def best(
    rows: Sequence[SweepRow], *, by: str = "cycle_edge"
) -> SweepRow | None:
    """The best configuration that actually passed its gates.

    Ranked by cycling edge by default, not by net PnL. Net PnL includes whatever
    the leftover inventory is worth, so over a trending window it rewards a grid
    for having been accidentally long rather than for gridding well. Pass
    `by="net"` to rank on total money instead, knowing that is what it measures.
    """
    passed = [row for row in rows if row.gates_passed and row.result is not None]
    if not passed:
        return None
    key = (
        (lambda row: row.net_pnl_usd)
        if by == "net"
        else (lambda row: row.cycle_edge_usd)
    )
    return max(passed, key=key)
