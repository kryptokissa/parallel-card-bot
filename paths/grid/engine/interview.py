"""The interview: how a grid gets its parameters before it has any orders.

Nothing about a grid has a safe default. Level count is bounded by capital and
by the venue's tick grid; range is a view about where price will oscillate; and
breakout behaviour has no neutral option at all — not choosing means holding a
directional position forever, unhedged (BUILDING_PATHS.md §4.3).

So the path refuses to infer. It publishes a question set, the agent asks it,
and the answers become a frozen config. The interview runs strictly before the
grid exists: it is not a channel into a running grid, and no answer collected
here can change a grid already working. Config changes happen between runs.

**Delegation.** Any question may be handed to the agent with "you decide".
`propose` then searches for a concrete grid that passes every hard gate and
returns it with the reasoning attached. Delegation changes who proposes a
number; it never changes which numbers are allowed. A delegated answer is
recorded as `delegated`, and any delegated answer makes confirmation
mandatory: the user sees the resolved config, including what the agent chose
and why, and confirms once before a single order exists.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Sequence

from engine.config import (
    BREAKOUTS,
    SPACINGS,
    GridConfig,
    ResolvedConfig,
    resolve,
)
from engine.gates import GateReport, evaluate
from engine.levels import MIN_ORDER_USD_NOTIONAL
from engine.simulate import as_bars
from engine.simulate import best as best_row
from engine.simulate import sweep
from engine.ticks import snap_price

Source = Literal["user", "delegated", "default"]


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    kind: str
    options: tuple[str, ...] = ()
    note: str = ""
    # A question with no default cannot be skipped, only answered or delegated.
    has_default: bool = False


QUESTIONS: tuple[Question, ...] = (
    Question(
        "market",
        "Which Hyperliquid perp should the grid trade?",
        "market",
        note="Canonical asset name, e.g. BTC-USDC or ETH-USDC.",
    ),
    Question(
        "lower",
        "Bottom of the range?",
        "price",
        note="The grid buys down to here and stops.",
    ),
    Question(
        "upper",
        "Top of the range?",
        "price",
        note="The grid sells up to here and stops.",
    ),
    Question(
        "levels",
        "How many levels across the range?",
        "int",
        note=(
            "Bounded from both sides: each order must clear HL's "
            f"${MIN_ORDER_USD_NOTIONAL:.0f} minimum, and adjacent levels must stay "
            "far enough apart to clear round-trip fees and to land on distinct "
            "ticks."
        ),
    ),
    Question(
        "spacing",
        "Equal percentage gaps, or equal dollar gaps?",
        "choice",
        options=SPACINGS,
        note=(
            "Geometric (equal percentage) is the usual choice for volatile "
            "assets — a fixed dollar gap is a much larger percentage move at the "
            "bottom of the range than at the top."
        ),
        has_default=True,
    ),
    Question(
        "capital_usd",
        "How much USDC should the grid work with?",
        "usd",
        note="Margin behind the whole grid, not per level.",
    ),
    Question(
        "leverage",
        "Leverage?",
        "float",
        note="Clamped to the path's ceiling regardless of what is asked for.",
        has_default=True,
    ),
    Question(
        "breakout",
        "When price leaves the range, what should the grid do?",
        "choice",
        options=BREAKOUTS,
        note=(
            "halt_hold keeps the position and stops trading. halt_close flattens "
            "and stops. recenter rebuilds the grid around the new price, up to "
            "max_recenters times. There is no neutral option: leaving this "
            "unanswered would mean holding a directional position forever, "
            "unhedged — so the grid will not start until it is answered."
        ),
    ),
)

QUESTIONS_BY_KEY: dict[str, Question] = {q.key: q for q in QUESTIONS}


@dataclass(frozen=True)
class Answer:
    key: str
    value: Any
    source: Source
    rationale: str = ""


@dataclass
class Interview:
    """Collected answers plus where each one came from."""

    answers: dict[str, Answer] = field(default_factory=dict)
    confirmed: bool = False

    def record(
        self, key: str, value: Any, source: Source = "user", rationale: str = ""
    ) -> None:
        if key not in QUESTIONS_BY_KEY and key not in _EXTRA_KEYS:
            accepted = ", ".join(sorted(set(QUESTIONS_BY_KEY) | _EXTRA_KEYS))
            raise KeyError(
                f"{key!r} is not part of the interview. Accepted keys: {accepted}"
            )
        self.answers[key] = Answer(key, value, source, rationale)

    def record_many(self, values: dict[str, Any], source: Source = "user") -> None:
        for key, value in values.items():
            rationale = ""
            if isinstance(value, tuple) and len(value) == 2:
                value, rationale = value
            self.record(key, value, source, rationale)

    def unanswered(self) -> list[Question]:
        return [q for q in QUESTIONS if q.key not in self.answers and not q.has_default]

    @property
    def delegated_keys(self) -> list[str]:
        return sorted(k for k, a in self.answers.items() if a.source == "delegated")

    @property
    def requires_confirmation(self) -> bool:
        """Any delegated answer must be confirmed before the grid starts."""
        return bool(self.delegated_keys)

    def provenance(self) -> dict[str, str]:
        return {k: a.source for k, a in sorted(self.answers.items())}

    def to_config(self, **overrides: Any) -> GridConfig:
        """Build a config from the answers. Does not validate or clamp."""
        values: dict[str, Any] = {
            k: a.value for k, a in self.answers.items() if k in _CONFIG_KEYS
        }
        values.update(overrides)
        return GridConfig(**values)


# Keys accepted by `record` that are config fields but not interview questions
# (sz_decimals is a market property the agent looks up, not a user opinion).
_EXTRA_KEYS = {
    "sz_decimals",
    "max_recenters",
    "fee_bps_per_side",
    # Observed market conditions the agent reads from the venue rather than
    # asking the user about: `MarketHandler.funding(symbol)` gives the live rate,
    # and passing it in is the whole reason the cost gate takes a rate at all.
    "funding_rate_per_hour",
    "expected_hold_hours",
    "maintenance_margin_fraction",
    "min_liquidation_buffer",
    "min_fee_coverage",
    "max_edge_drawdown",
    "daily_loss_limit_usd",
}
_CONFIG_KEYS = {q.key for q in QUESTIONS} | _EXTRA_KEYS


@dataclass
class StartDecision:
    """Whether this interview may start a grid, and why not if it may not."""

    ok: bool
    reason: str
    resolved: ResolvedConfig | None = None
    gates: GateReport | None = None

    def blockers(self) -> list[str]:
        out: list[str] = []
        if not self.ok:
            out.append(self.reason)
        if self.gates is not None:
            out.extend(f"{r.name}: {r.detail}" for r in self.gates.failures)
        return out


def ready_to_start(interview: Interview, mark_price: float) -> StartDecision:
    """The single gate between an interview and a live grid.

    Order matters: completeness, then confirmation, then clamping, then the
    hard gates. Each step's message has to be actionable on its own, because
    the agent relays it verbatim.
    """
    missing = interview.unanswered()
    if missing:
        return StartDecision(
            False,
            "unanswered questions: " + ", ".join(q.key for q in missing),
        )

    if interview.requires_confirmation and not interview.confirmed:
        return StartDecision(
            False,
            "the agent chose "
            + ", ".join(interview.delegated_keys)
            + " — show the resolved config and get one explicit confirmation "
            "before placing any order",
        )

    config = interview.to_config()
    resolved = resolve(config)
    try:
        resolved.config.validate()
    except ValueError as exc:
        return StartDecision(False, str(exc), resolved=resolved)

    gates = evaluate(resolved.config, mark_price)
    if not gates.passed:
        return StartDecision(
            False, "hard gates failed", resolved=resolved, gates=gates
        )
    return StartDecision(True, "ready", resolved=resolved, gates=gates)


def propose(
    *,
    market: str,
    sz_decimals: int,
    mark_price: float,
    capital_usd: float,
    volatility_pct: float | None = None,
    max_leverage: float = 1.0,
    prices: Sequence[Any] | None = None,
) -> dict[str, tuple[Any, str]]:
    """A concrete grid the agent can put to the user, with reasoning per field.

    Searches rather than guesses: it proposes a range from observed volatility,
    then takes the largest level count that still clears cost coverage and the
    minimum notional, and widens or narrows until every hard gate passes. Every
    returned value carries the sentence the agent should say about it.

    Pass `prices` — a recent series for this market — and the level count and
    spacing are chosen by simulating candidates over it instead, which is what
    §4.3 asks for. Without it the proposal is merely legal: it clears every gate,
    but nothing has checked whether a different rung count would have done
    better.

    Raises ValueError when no grid at this capital clears the gates — better a
    clear refusal than a proposal that fails at start.
    """
    if mark_price <= 0:
        raise ValueError("mark_price must be positive")
    if capital_usd <= 0:
        raise ValueError("capital_usd must be positive")

    vol = volatility_pct if volatility_pct and volatility_pct > 0 else 4.0
    # Half-range: wide enough to actually oscillate, not so wide that being
    # fully loaded at the edge blows the drawdown gate.
    for half_span_pct in (vol * 2.0, vol * 1.5, vol, vol * 0.75, vol * 0.5):
        # Snap the bounds onto the venue's tick grid up front, so the numbers
        # the user is shown are the numbers the exchange would accept.
        lower = snap_price(mark_price * (1 - half_span_pct / 100.0), sz_decimals)
        upper = snap_price(mark_price * (1 + half_span_pct / 100.0), sz_decimals)
        if lower <= 0 or upper <= lower:
            continue

        # Fee coverage caps the level count: adjacent geometric gaps must clear
        # `min_fee_coverage` round trips.
        probe = GridConfig(
            market=market,
            sz_decimals=sz_decimals,
            lower=lower,
            upper=upper,
            levels=2,
            capital_usd=capital_usd,
            leverage=max_leverage,
            breakout="halt_close",
        )
        required_bps = probe.required_spacing_bps
        max_by_fees = int(
            math.log(upper / lower) / math.log(1 + required_bps / 1e4)
        ) + 1
        max_by_notional = int(
            (capital_usd * max_leverage) // MIN_ORDER_USD_NOTIONAL
        )
        ceiling = min(max_by_fees, max_by_notional, 20)

        tuned: tuple[int, str, str] | None = None
        tuning_failed = ""
        if prices is not None and ceiling >= 2:
            # Tune the *shape* — level count and spacing — not the absolute
            # prices. A historical series opens wherever it opened, which is
            # rarely near today's mark, so simulating a range centred on today
            # would fail `mark_in_range` on every candidate and silently learn
            # nothing. Scale the same relative span around the series' own
            # opening price instead.
            opening = as_bars(prices)[0].close
            sim_lower = snap_price(
                opening * (1 - half_span_pct / 100.0), sz_decimals
            )
            sim_upper = snap_price(
                opening * (1 + half_span_pct / 100.0), sz_decimals
            )
            if sim_lower <= 0 or sim_upper <= sim_lower:
                tuning_failed = (
                    "the price series could not be scaled to a usable range"
                )
                rows = []
            else:
                probe_grid = replace(
                    probe,
                    lower=sim_lower,
                    upper=sim_upper,
                    levels=min(ceiling, 20),
                    leverage=max_leverage,
                )
                rows = sweep(
                    probe_grid, prices, level_counts=range(2, min(ceiling, 20) + 1)
                )
            winner = best_row(rows) if rows else None
            if winner is None and not tuning_failed:
                tuning_failed = (
                    "no simulated configuration cleared the gates over this series"
                )
            if winner is not None and winner.result is not None:
                caveat = ""
                if winner.result.cycles < 3:
                    caveat = (
                        f" Treat this as weak evidence: only "
                        f"{winner.result.cycles} cycle(s) completed, so the window "
                        "was trending rather than ranging and the ranking rests on "
                        "very few trades."
                    )
                elif winner.result.breakout_bar is not None:
                    caveat = (
                        f" Note the grid broke out at bar "
                        f"{winner.result.breakout_bar} of {winner.result.bars}, so "
                        "the simulation stopped there."
                    )
                tuned = (
                    winner.levels,
                    winner.spacing,
                    f"best of {len([r for r in rows if r.gates_passed])} "
                    f"configurations simulated over {winner.result.bars} bars of "
                    f"this market: ${winner.result.cycle_edge_usd:,.2f} of cycling "
                    f"edge across {winner.result.cycles} cycles, worst drawdown "
                    f"{winner.result.worst_drawdown_pct:.1f}%." + caveat,
                )

        level_order = (
            [tuned[0]] + [n for n in range(ceiling, 1, -1) if n != tuned[0]]
            if tuned
            else list(range(ceiling, 1, -1))
        )
        for levels in level_order:
            for leverage in (max_leverage, 1.0):
                candidate = GridConfig(
                    market=market,
                    sz_decimals=sz_decimals,
                    lower=lower,
                    upper=upper,
                    levels=levels,
                    spacing=(
                        tuned[1] if tuned and levels == tuned[0] else "geometric"
                    ),
                    capital_usd=capital_usd,
                    leverage=leverage,
                    breakout="halt_close",
                )
                try:
                    candidate.validate()
                except ValueError:
                    continue
                if not evaluate(candidate, mark_price).passed:
                    continue
                return {
                    "market": (market, f"as asked: {market}."),
                    "lower": (
                        candidate.lower,
                        f"{half_span_pct:.1f}% below the {mark_price:,.2f} mark, "
                        f"sized off {vol:.1f}% observed volatility.",
                    ),
                    "upper": (
                        candidate.upper,
                        f"{half_span_pct:.1f}% above the mark, symmetric with the "
                        "bottom so neither side is favoured.",
                    ),
                    "levels": (
                        levels,
                        tuned[2]
                        if tuned and levels == tuned[0]
                        else (
                            f"the most levels that still clear {required_bps:.1f} "
                            f"bps of round-trip cost — "
                            f"{candidate.fee_cost_bps:.1f} bps fees plus "
                            f"{candidate.funding_cost_bps:.1f} bps funding — and "
                            f"HL's ${MIN_ORDER_USD_NOTIONAL:.0f} minimum per "
                            "order. "
                            + (
                                f"A price series was supplied but did not tune "
                                f"this: {tuning_failed}."
                                if tuning_failed
                                else "Nothing simulated whether a different "
                                "count would do better; pass a price series to "
                                "tune it."
                            )
                        ),
                    ),
                    "spacing": (
                        candidate.spacing,
                        "chosen by simulation over this market's recent prices."
                        if tuned and levels == tuned[0]
                        else (
                            "equal percentage gaps, so the bottom of the range is "
                            "not spaced more tightly than the top."
                        ),
                    ),
                    "capital_usd": (capital_usd, "as asked."),
                    "leverage": (
                        leverage,
                        f"{leverage:g}x keeps the worst case at the range edge "
                        "inside the drawdown limit.",
                    ),
                    "breakout": (
                        "halt_close",
                        "flatten and stop. This is a choice, not a default: it "
                        "caps the loss at the edge instead of holding a "
                        "directional position indefinitely. Say halt_hold to keep "
                        "the position, or recenter to rebuild around the new "
                        "price.",
                    ),
                }

    raise ValueError(
        f"no grid on {market} at ${capital_usd:,.2f} clears the hard gates. "
        f"HL needs ${MIN_ORDER_USD_NOTIONAL:.0f} per order, so a useful grid "
        f"needs roughly ${MIN_ORDER_USD_NOTIONAL * 4:.0f} of margin at minimum, "
        "and more for a wide range."
    )
