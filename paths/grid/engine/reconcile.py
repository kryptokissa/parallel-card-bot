"""Reconciling intent against the exchange.

Orders fill, cancel and get rejected without this process observing any of it,
and restarts happen mid-grid. So the rule from BUILDING_PATHS.md §4.4: the
exchange is the source of truth for positions and open orders, the local log is
the source of truth for intent. Never the other way round — trusting the log
about state is how a grid ends up placing a rung it already has, or holding a
position it thinks it closed.

`reconcile` is deliberately dumb about causes. It reports the difference; the
planner decides. A rung that is missing because it filled and a rung that is
missing because the venue rejected it look identical here, and both want the
same next step: decide from the current state, not from a remembered one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from engine.config import GridConfig
from engine.levels import build_levels


@dataclass(frozen=True)
class RestingOrder:
    """One order the exchange says is live."""

    cloid: str | None
    side: str
    price: float
    size: float
    order_id: str | None = None

    @classmethod
    def from_exchange(cls, raw: dict[str, Any]) -> "RestingOrder":
        """Parse one open order as the venue reports it.

        Tolerant of the shapes HL and the SDK use, and of missing fields: an
        order with no usable price is reported with 0.0 so the caller treats it
        as unidentifiable rather than silently trusting a default.
        """
        side = str(raw.get("side") or raw.get("dir") or "").lower()
        if side in {"b", "buy", "open long", "long"}:
            side = "buy"
        elif side in {"a", "s", "sell", "open short", "short"}:
            side = "sell"
        return cls(
            cloid=raw.get("cloid") or raw.get("client_order_id") or None,
            side=side,
            price=float(raw.get("limit_price") or raw.get("limitPx") or 0.0),
            size=float(raw.get("size") or raw.get("sz") or 0.0),
            order_id=(
                str(raw["order_id"])
                if raw.get("order_id") is not None
                else (str(raw["oid"]) if raw.get("oid") is not None else None)
            ),
        )


@dataclass
class Reconciliation:
    """The difference between what the grid intends and what the venue holds."""

    resting: dict[str, RestingOrder] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    unexpected: list[RestingOrder] = field(default_factory=list)
    mispriced: list[tuple[str, float, float]] = field(default_factory=list)
    untagged: list[RestingOrder] = field(default_factory=list)
    position_size: float = 0.0

    @property
    def clean(self) -> bool:
        return not (self.missing or self.unexpected or self.mispriced)

    def summary(self) -> str:
        if self.clean and not self.untagged:
            return f"{len(self.resting)} rungs resting, exchange agrees"
        parts = [f"{len(self.resting)} rungs resting"]
        if self.missing:
            parts.append(f"{len(self.missing)} missing")
        if self.mispriced:
            parts.append(f"{len(self.mispriced)} at the wrong price")
        if self.unexpected:
            parts.append(f"{len(self.unexpected)} stale grid orders to cancel")
        if self.untagged:
            parts.append(
                f"{len(self.untagged)} untagged orders left alone (not this grid's)"
            )
        return ", ".join(parts)


def reconcile(
    config: GridConfig,
    mark_price: float,
    *,
    open_orders: Iterable[dict[str, Any]],
    position_size: float = 0.0,
) -> Reconciliation:
    """Compare the rungs this config implies against the venue's open orders.

    Orders carrying no `grid-` cloid are reported as `untagged` and never
    cancelled: they may belong to the user or another path, and cancelling
    someone else's order is not this grid's business.
    """
    intended = {level.cloid_tag: level for level in build_levels(config, mark_price)}

    parsed = [RestingOrder.from_exchange(raw) for raw in open_orders]
    result = Reconciliation(position_size=position_size)

    for order in parsed:
        if not order.cloid or not str(order.cloid).startswith("grid-"):
            result.untagged.append(order)
            continue
        cloid = str(order.cloid)
        if cloid in intended:
            result.resting[cloid] = order
            expected = intended[cloid].price
            if order.price > 0 and order.price != expected:
                result.mispriced.append((cloid, order.price, expected))
        else:
            # Tagged as this grid's but not a rung of the current config —
            # left over from a previous range, typically after a re-centre.
            result.unexpected.append(order)

    result.missing = sorted(set(intended) - set(result.resting))
    return result
