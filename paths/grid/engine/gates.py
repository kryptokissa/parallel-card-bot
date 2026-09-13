"""Hard gates. A grid that fails any of these does not start.

These apply identically whether the user chose the number or delegated the
choice to the agent. Delegation changes who proposes; it never changes what
passes. Nothing here is advisory — `evaluate` returning `passed=False` means
no orders are planned at all.

Three gates:

- **Mark in range.** Price must sit inside the range being built. A grid
  started outside its own range has no working side.
- **Geometry.** The levels must be representable on this market: distinct
  after tick snapping, each order above HL's $10 minimum, each size at least
  one lot.
- **Fee coverage.** The tightest post-snap gap must clear a round trip's fees
  by `min_fee_coverage`. Spacing below cost loses money on every successful
  cycle (§4.3), which is the failure mode that looks like it is working.
- **Liquidation buffer.** Fully loaded at the far edge of the range, equity
  must still exceed maintenance margin by `min_liquidation_buffer`. On a perp a
  breakout is not merely an unwanted position, it is a liquidation (§4.4).
- **Edge drawdown.** Fully loaded at the worst edge, unrealized loss must stay
  within `max_edge_drawdown` of capital. This is the gate that actually binds:
  HL maintenance margin is around 2%, so a grid can give back 80% of capital
  inside its own range while still clearing the liquidation buffer several
  times over. §4.4 names the liquidation buffer alone; on its own it is not
  enough.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.config import GridConfig
from engine.levels import GridGeometryError, build_levels, spacing_bps


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    detail: str


@dataclass
class GateReport:
    results: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        if self.passed:
            return "all gates pass"
        return "; ".join(f"{r.name}: {r.detail}" for r in self.failures)


def _geometry_gate(config: GridConfig, mark_price: float) -> GateResult:
    try:
        levels = build_levels(config, mark_price)
    except GridGeometryError as exc:
        return GateResult("geometry", False, str(exc))
    smallest = min(level.notional_usd for level in levels)
    return GateResult(
        "geometry",
        True,
        f"{len(levels)} distinct levels, smallest order ${smallest:.2f}",
    )


def _fee_gate(config: GridConfig) -> GateResult:
    try:
        gaps = spacing_bps(config)
    except GridGeometryError as exc:
        return GateResult("fee_coverage", False, f"spacing unavailable: {exc}")

    round_trip_bps = 2.0 * config.fee_bps_per_side
    required = round_trip_bps * config.min_fee_coverage
    tightest = min(gaps)
    if tightest < required:
        return GateResult(
            "fee_coverage",
            False,
            f"tightest gap is {tightest:.1f} bps; a round trip costs "
            f"{round_trip_bps:.1f} bps and the grid requires "
            f"{config.min_fee_coverage}x that ({required:.1f} bps). "
            f"Widen the range or cut the level count.",
        )
    return GateResult(
        "fee_coverage",
        True,
        f"tightest gap {tightest:.1f} bps clears {required:.1f} bps "
        f"({tightest / round_trip_bps:.1f}x round-trip cost)",
    )


def _edge_exposure(
    config: GridConfig, mark_price: float, *, downside: bool
) -> tuple[float, float, float]:
    """Position, equity and maintenance requirement at one edge of the range.

    Downside: every buy level has filled and price sits at `lower`.
    Upside: every sell level has filled and price sits at `upper`.
    """
    levels = build_levels(config, mark_price)
    edge = config.lower if downside else config.upper
    side = "buy" if downside else "sell"
    filled = [level for level in levels if level.side == side]

    units = sum(level.size for level in filled)
    basis = sum(level.size * level.price for level in filled)
    value_at_edge = units * edge

    # Long: paid `basis`, now worth `value_at_edge`. Short: received `basis`,
    # now owes `value_at_edge`.
    loss = (basis - value_at_edge) if downside else (value_at_edge - basis)
    equity = config.capital_usd - loss
    maintenance = value_at_edge * config.maintenance_margin_fraction
    return units, equity, maintenance


def _liquidation_gate(config: GridConfig, mark_price: float) -> GateResult:
    try:
        worst: tuple[str, float, float] | None = None
        for downside in (True, False):
            units, equity, maintenance = _edge_exposure(
                config, mark_price, downside=downside
            )
            if units <= 0:
                continue
            ratio = equity / maintenance if maintenance > 0 else float("inf")
            label = "lower" if downside else "upper"
            if worst is None or ratio < worst[1]:
                worst = (label, ratio, equity)
    except GridGeometryError as exc:
        return GateResult("liquidation_buffer", False, f"unavailable: {exc}")

    if worst is None:
        return GateResult(
            "liquidation_buffer",
            False,
            f"no level rests on either side of a {mark_price} mark — the mark "
            "sits outside the configured range",
        )

    label, ratio, equity = worst
    if equity <= 0:
        return GateResult(
            "liquidation_buffer",
            False,
            f"fully loaded at the {label} bound, equity is ${equity:,.2f} — this "
            f"grid liquidates inside its own range at {config.leverage}x. "
            "Reduce leverage, narrow the range, or add capital.",
        )
    if ratio < config.min_liquidation_buffer:
        return GateResult(
            "liquidation_buffer",
            False,
            f"fully loaded at the {label} bound, equity covers maintenance margin "
            f"{ratio:.2f}x; the grid requires {config.min_liquidation_buffer:.2f}x. "
            "Reduce leverage, narrow the range, or add capital.",
        )
    return GateResult(
        "liquidation_buffer",
        True,
        f"worst edge ({label}) leaves {ratio:.2f}x maintenance margin, "
        f"${equity:,.2f} equity",
    )


def _drawdown_gate(config: GridConfig, mark_price: float) -> GateResult:
    try:
        worst: tuple[str, float] | None = None
        for downside in (True, False):
            units, equity, _ = _edge_exposure(config, mark_price, downside=downside)
            if units <= 0:
                continue
            drawdown = (config.capital_usd - equity) / config.capital_usd
            label = "lower" if downside else "upper"
            if worst is None or drawdown > worst[1]:
                worst = (label, drawdown)
    except GridGeometryError as exc:
        return GateResult("edge_drawdown", False, f"unavailable: {exc}")

    if worst is None:
        return GateResult(
            "edge_drawdown",
            False,
            f"no level rests on either side of a {mark_price} mark",
        )

    label, drawdown = worst
    if drawdown > config.max_edge_drawdown:
        return GateResult(
            "edge_drawdown",
            False,
            f"fully loaded at the {label} bound, the grid is down "
            f"{drawdown * 100:.1f}% of capital; the limit is "
            f"{config.max_edge_drawdown * 100:.1f}%. Narrow the range, reduce "
            f"leverage, or accept a larger drawdown explicitly.",
        )
    return GateResult(
        "edge_drawdown",
        True,
        f"worst edge ({label}) is down {drawdown * 100:.1f}% of capital, within "
        f"the {config.max_edge_drawdown * 100:.1f}% limit",
    )


def _mark_in_range_gate(config: GridConfig, mark_price: float) -> GateResult:
    """The mark must sit inside the range the grid is being built around.

    A grid started with price already outside its range has no working side: it
    would place every rung on one side of the book and then immediately take the
    breakout branch. That is a configuration mistake, and it is cheaper to say so
    at start than to discover it on the first step.
    """
    if mark_price <= 0:
        return GateResult(
            "mark_in_range", False, "mark price must be positive"
        )
    if not config.lower <= mark_price <= config.upper:
        where = "above" if mark_price > config.upper else "below"
        return GateResult(
            "mark_in_range",
            False,
            f"the mark {mark_price:,.2f} is already {where} the range "
            f"({config.lower:,.2f}–{config.upper:,.2f}). Move the range around "
            "current price, or wait for price to come back into it.",
        )
    return GateResult(
        "mark_in_range",
        True,
        f"mark {mark_price:,.2f} sits inside {config.lower:,.2f}–{config.upper:,.2f}",
    )


def evaluate(config: GridConfig, mark_price: float) -> GateReport:
    """Run every hard gate. `passed=False` means no orders are planned."""
    return GateReport(
        results=[
            _mark_in_range_gate(config, mark_price),
            _geometry_gate(config, mark_price),
            _fee_gate(config),
            _liquidation_gate(config, mark_price),
            _drawdown_gate(config, mark_price),
        ]
    )
