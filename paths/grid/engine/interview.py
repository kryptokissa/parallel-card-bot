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
from dataclasses import dataclass, field
from typing import Any, Literal

from engine.config import (
    BREAKOUTS,
    SPACINGS,
    GridConfig,
    ResolvedConfig,
    resolve,
)
from engine.gates import GateReport, evaluate
from engine.levels import MIN_ORDER_USD_NOTIONAL
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
            raise KeyError(f"{key!r} is not part of the interview")
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
) -> dict[str, tuple[Any, str]]:
    """A concrete grid the agent can put to the user, with reasoning per field.

    Searches rather than guesses: it proposes a range from observed volatility,
    then takes the largest level count that still clears fee coverage and the
    minimum notional, and widens or narrows until every hard gate passes. Every
    returned value carries the sentence the agent should say about it.

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
        required_bps = 2.0 * probe.fee_bps_per_side * probe.min_fee_coverage
        max_by_fees = int(
            math.log(upper / lower) / math.log(1 + required_bps / 1e4)
        ) + 1
        max_by_notional = int(
            (capital_usd * max_leverage) // MIN_ORDER_USD_NOTIONAL
        )
        ceiling = min(max_by_fees, max_by_notional, 20)

        for levels in range(ceiling, 1, -1):
            for leverage in (max_leverage, 1.0):
                candidate = GridConfig(
                    market=market,
                    sz_decimals=sz_decimals,
                    lower=lower,
                    upper=upper,
                    levels=levels,
                    spacing="geometric",
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
                        f"the most levels that still clear {required_bps:.1f} bps "
                        f"of round-trip fee coverage and HL's "
                        f"${MIN_ORDER_USD_NOTIONAL:.0f} minimum per order.",
                    ),
                    "spacing": (
                        "geometric",
                        "equal percentage gaps, so the bottom of the range is not "
                        "spaced more tightly than the top.",
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
