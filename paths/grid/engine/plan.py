"""Turning a frozen config plus observed state into orders for the agent.

The path never places an order. It emits `OrderIntent`s and the agent calls
`hyperliquid_place_limit_order` / `hyperliquid_cancel_order` (BUILDING_PATHS.md
§2.1). Each plan carries a TTL, because a plan computed against a mark from ten
minutes ago describes prices that no longer exist.

**`reduce_only` is not a blanket rule.** §4.4 says to set it on every exit, and
the reason is sound: a "close" that races a fill would otherwise open a
position the other way. But `_reject_unsafe_perp_order` in the SDK refuses
`reduce_only=true` outright with `reduce_only_no_position` when there is no
opposite position to reduce — and in a neutral grid, the sell rungs above the
mark are *opening* shorts, not exits, whenever the grid is flat or short.

So the rule the engine actually applies: `reduce_only` is set exactly when the
order reduces the position that is currently open. That keeps the protection on
every real exit and stops the venue rejecting every rung that is not one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from engine.config import GridConfig
from engine.levels import GridLevel, build_levels

Action = Literal["place", "cancel"]
Side = Literal["buy", "sell"]

# A plan describes prices. Past this age the mark it was computed against is
# not the market any more, and the agent must re-plan rather than execute.
PLAN_TTL_SECONDS = 90


@dataclass(frozen=True)
class OrderIntent:
    """One instruction for the agent. Never executed by the path."""

    action: Action
    side: Side | None = None
    price: float | None = None
    size: float | None = None
    reduce_only: bool = False
    cloid: str | None = None
    level_index: int | None = None
    reason: str = ""

    def as_tool_call(self, market: str, wallet_label: str) -> dict[str, object]:
        """The agent-facing shape. `wallet_label` stays a placeholder in stored
        plans — labels are generated per install (§2.2) and the agent knows its
        own."""
        if self.action == "cancel":
            return {
                "tool": "hyperliquid_cancel_order",
                "wallet_label": wallet_label,
                "asset_name": market,
                "cancel_cloid": self.cloid,
            }
        return {
            "tool": "hyperliquid_place_limit_order",
            "wallet_label": wallet_label,
            "asset_name": market,
            "is_buy": self.side == "buy",
            "price": self.price,
            "size": self.size,
            "reduce_only": self.reduce_only,
            "cloid": self.cloid,
        }


@dataclass
class GridPlan:
    """What the agent should do now, and why."""

    status: str
    summary: str
    intents: list[OrderIntent] = field(default_factory=list)
    ttl_seconds: int = PLAN_TTL_SECONDS
    breakout: str | None = None

    @property
    def is_empty(self) -> bool:
        return not self.intents


def _reduces(side: Side, position_size: float) -> bool:
    """True when an order on `side` shrinks the open position.

    `position_size` is signed: positive long, negative short. A sell reduces a
    long; a buy reduces a short. Flat reduces nothing, which is exactly the
    case the venue rejects.
    """
    if position_size > 0:
        return side == "sell"
    if position_size < 0:
        return side == "buy"
    return False


def plan_initial(
    config: GridConfig, mark_price: float, *, position_size: float = 0.0
) -> GridPlan:
    """Every rung, placed as a resting limit order."""
    levels = build_levels(config, mark_price)
    intents = [
        OrderIntent(
            action="place",
            side=level.side,
            price=level.price,
            size=level.size,
            reduce_only=_reduces(level.side, position_size),
            cloid=level.cloid_tag,
            level_index=level.index,
            reason=f"rung {level.index} at {level.price}",
        )
        for level in levels
    ]
    buys = sum(1 for level in levels if level.side == "buy")
    return GridPlan(
        status="opening",
        summary=(
            f"{len(levels)} rungs on {config.market}: {buys} buys below "
            f"{mark_price:,.2f}, {len(levels) - buys} sells above, "
            f"{config.spacing} spacing."
        ),
        intents=intents,
    )


def plan_refill(
    config: GridConfig,
    mark_price: float,
    *,
    resting_cloids: set[str],
    position_size: float = 0.0,
) -> GridPlan:
    """Replace rungs that filled or went missing, leave the rest alone.

    Idempotent by design: it compares the rungs the config implies against the
    cloids actually resting on the exchange, so running it twice places nothing
    the second time.
    """
    levels = build_levels(config, mark_price)
    intents: list[OrderIntent] = []
    for level in levels:
        if level.cloid_tag in resting_cloids:
            continue
        intents.append(
            OrderIntent(
                action="place",
                side=level.side,
                price=level.price,
                size=level.size,
                reduce_only=_reduces(level.side, position_size),
                cloid=level.cloid_tag,
                level_index=level.index,
                reason=f"rung {level.index} not resting — refill",
            )
        )
    if not intents:
        return GridPlan(status="working", summary="all rungs resting; nothing to do")
    return GridPlan(
        status="working",
        summary=f"refilling {len(intents)} of {len(levels)} rungs",
        intents=intents,
    )


def in_range(config: GridConfig, mark_price: float) -> bool:
    return config.lower <= mark_price <= config.upper


def plan_breakout(
    config: GridConfig,
    mark_price: float,
    *,
    resting_cloids: set[str],
    position_size: float = 0.0,
    recenters_used: int = 0,
) -> GridPlan:
    """Apply the breakout behaviour the user chose. Never invents one.

    All three modes cancel every resting rung first: whatever happens next, the
    old grid's orders describe a range price has already left.
    """
    if config.breakout is None:  # unreachable via validate(), kept explicit
        raise ValueError("breakout behaviour was never chosen; the grid must not run")

    side = "above" if mark_price > config.upper else "below"
    cancels = [
        OrderIntent(
            action="cancel",
            cloid=cloid,
            reason=f"price {side} the range — cancelling rung",
        )
        for cloid in sorted(resting_cloids)
    ]

    if config.breakout == "halt_hold":
        return GridPlan(
            status="halted",
            breakout="halt_hold",
            summary=(
                f"price {mark_price:,.2f} is {side} the range "
                f"({config.lower:,.2f}–{config.upper:,.2f}). Rungs cancelled, "
                f"position of {position_size:+g} held, trading stopped. "
                "This position is directional and unhedged until you act on it."
            ),
            intents=cancels,
        )

    if config.breakout == "halt_close":
        intents = list(cancels)
        if position_size != 0:
            close_side: Side = "sell" if position_size > 0 else "buy"
            intents.append(
                OrderIntent(
                    action="place",
                    side=close_side,
                    price=mark_price,
                    size=abs(position_size),
                    # A genuine exit: there is a position on the other side, so
                    # the venue accepts reduce_only and it protects against a
                    # racing fill flipping us.
                    reduce_only=True,
                    cloid="grid-flatten",
                    reason="breakout — flatten the position",
                )
            )
        return GridPlan(
            status="halted",
            breakout="halt_close",
            summary=(
                f"price {mark_price:,.2f} is {side} the range. Rungs cancelled and "
                + (
                    f"position of {position_size:+g} flattened."
                    if position_size
                    else "no position to flatten."
                )
            ),
            intents=intents,
        )

    # recenter
    if recenters_used >= config.max_recenters:
        return GridPlan(
            status="halted",
            breakout="recenter_exhausted",
            summary=(
                f"price {mark_price:,.2f} is {side} the range and the grid has "
                f"already re-centred {recenters_used} of {config.max_recenters} "
                "times. Holding and stopping rather than chasing further."
            ),
            intents=cancels,
        )
    return GridPlan(
        status="recentering",
        breakout="recenter",
        summary=(
            f"price {mark_price:,.2f} is {side} the range — re-centring "
            f"(#{recenters_used + 1} of {config.max_recenters}). The new range "
            "must clear every hard gate before rungs are placed."
        ),
        intents=cancels,
    )


def plan_daily_stop(
    config: GridConfig,
    mark_price: float,
    *,
    resting_cloids: set[str],
    position_size: float = 0.0,
    position_action: str = "halt_hold",
    detail: str = "",
) -> GridPlan:
    """Stop the grid because the day's loss limit was hit, not because price moved.

    Cancels every rung. The position is then handled by the action the user
    already chose for a stopped grid, rather than a second policy they were never
    asked about.
    """
    intents = [
        OrderIntent(
            action="cancel",
            cloid=cloid,
            reason="daily loss limit — cancelling rung",
        )
        for cloid in sorted(resting_cloids)
    ]
    if position_action == "halt_close" and position_size != 0:
        close_side: Side = "sell" if position_size > 0 else "buy"
        intents.append(
            OrderIntent(
                action="place",
                side=close_side,
                price=mark_price,
                size=abs(position_size),
                reduce_only=True,
                cloid="grid-flatten",
                reason="daily loss limit — flatten the position",
            )
        )
        tail = f"Position of {position_size:+g} flattened."
    elif position_size != 0:
        tail = (
            f"Position of {position_size:+g} held — it is directional and "
            "unhedged until you act on it."
        )
    else:
        tail = "No position open."
    return GridPlan(
        status="halted",
        breakout="daily_loss_limit",
        summary=f"{detail} Rungs cancelled. {tail}".strip(),
        intents=intents,
    )


def recentred_config(config: GridConfig, mark_price: float) -> GridConfig:
    """The config for a grid rebuilt around `mark_price`, span preserved.

    Returned unvalidated on purpose: the caller re-runs the hard gates against
    it, because a range that was safe at the old price may not be at the new
    one.
    """
    half_span = (config.upper - config.lower) / 2.0
    return config.with_updates(
        lower=max(mark_price - half_span, 1e-9),
        upper=mark_price + half_span,
    )


def plan(
    config: GridConfig,
    mark_price: float,
    *,
    resting_cloids: set[str] | None = None,
    position_size: float = 0.0,
    recenters_used: int = 0,
    started: bool = False,
) -> GridPlan:
    """Single entry point: what should happen right now."""
    resting = resting_cloids or set()
    if mark_price <= 0:
        return GridPlan(
            status="blocked",
            summary="no valid mark price — refusing to plan against a zero mark",
        )
    if not in_range(config, mark_price):
        return plan_breakout(
            config,
            mark_price,
            resting_cloids=resting,
            position_size=position_size,
            recenters_used=recenters_used,
        )
    if not started:
        return plan_initial(config, mark_price, position_size=position_size)
    return plan_refill(
        config, mark_price, resting_cloids=resting, position_size=position_size
    )
