"""Grid geometry: where the levels sit, which side each one works, how big.

Spacing choice is the user's, with geometric as the default for volatile
assets — a fixed dollar gap is a far larger percentage move at the bottom of
the range than at the top (BUILDING_PATHS.md §4.3).

Two failure modes are specific to computing levels against a real venue, and
both are silent if unchecked:

- **Snap collision.** Levels are floored onto HL's tick grid, and the grid is
  coarse at high prices — near $50,000 with szDecimals=4 the effective tick is
  $1.00. Twenty levels across a $10 range all snap to the same few prices. The
  orders would not error; they would stack at one price and stop being a grid.
- **Lot underflow.** Size is floored to szDecimals. Split capital across
  enough levels and each order rounds to zero, or falls under HL's $10 minimum
  notional.

`build_levels` raises on both rather than returning a degenerate grid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.config import GridConfig
from engine.ids import rung_cloid, rung_tag
from engine.ticks import lot_size, snap_price, snap_size, tick_size

Side = Literal["buy", "sell"]

# HL rejects any order under this notional (MIN_ORDER_USD_NOTIONAL).
MIN_ORDER_USD_NOTIONAL = 10.0


class GridGeometryError(ValueError):
    """The requested grid cannot be represented on this market."""


@dataclass(frozen=True)
class GridLevel:
    """One rung. `side` is the order this rung rests as while price is away."""

    index: int
    price: float
    side: Side
    size: float
    notional_usd: float
    # Venue-valid client order id, derived from the market and the rung so it is
    # identical on every run — reconciliation matches resting orders by it.
    cloid: str = ""
    # Human-readable name for logs and reasons. Never sent to the venue.
    tag: str = ""


def raw_level_prices(config: GridConfig) -> list[float]:
    """Unsnapped level prices, low to high."""
    if config.levels < 2:
        raise GridGeometryError("a grid needs at least 2 levels")
    if config.upper <= config.lower:
        raise GridGeometryError("upper must sit above lower")

    n = config.levels
    if config.spacing == "geometric":
        ratio = (config.upper / config.lower) ** (1.0 / (n - 1))
        prices = [config.lower * (ratio**i) for i in range(n)]
    else:
        step = (config.upper - config.lower) / (n - 1)
        prices = [config.lower + step * i for i in range(n)]
    # Pin the endpoints. Repeated multiplication leaves the top level a hair
    # under `upper`, which then floors a whole tick below the range the user
    # actually asked for.
    prices[0] = config.lower
    prices[-1] = config.upper
    return prices


def snapped_level_prices(config: GridConfig) -> list[float]:
    """Level prices floored onto HL's tick grid, low to high.

    Raises if snapping collapses distinct levels onto the same price, or drives
    any level to zero.
    """
    raw = raw_level_prices(config)
    snapped = [snap_price(p, config.sz_decimals) for p in raw]

    if any(p <= 0 for p in snapped):
        raise GridGeometryError(
            f"a level snapped to 0 on {config.market} — the range "
            f"({config.lower}, {config.upper}) sits below this market's price "
            f"grid at szDecimals={config.sz_decimals}"
        )

    if len(set(snapped)) != len(snapped):
        tick = tick_size(config.lower, config.sz_decimals)
        collapsed = len(snapped) - len(set(snapped))
        raise GridGeometryError(
            f"{collapsed} of {config.levels} levels collapsed onto prices already "
            f"used by another level. The tick near {config.lower} is {tick}, so the "
            f"range ({config.lower}, {config.upper}) holds at most "
            f"{int((config.upper - config.lower) / tick) + 1} distinct levels. "
            "Widen the range or reduce the level count."
        )
    return snapped


def build_levels(config: GridConfig, mark_price: float) -> list[GridLevel]:
    """Full grid: prices, sides relative to `mark_price`, and sizes.

    Levels below the mark rest as buys, levels above rest as sells. The level
    nearest the mark on each side is the working edge of the grid.
    """
    if mark_price <= 0:
        raise GridGeometryError(
            "mark_price must be positive — a zero mark makes every level and "
            "every stop meaningless while looking plausible"
        )

    prices = snapped_level_prices(config)
    total_notional = config.capital_usd * config.leverage
    per_level_notional = total_notional / config.levels
    step = lot_size(config.sz_decimals)

    levels: list[GridLevel] = []
    for index, price in enumerate(prices):
        size = snap_size(per_level_notional / price, config.sz_decimals)
        if size <= 0:
            raise GridGeometryError(
                f"level {index} at {price} needs "
                f"{per_level_notional / price:.10f} units, which rounds down to 0 "
                f"at szDecimals={config.sz_decimals} (lot = {step}). "
                f"Raise capital, drop the level count, or pick a market with more "
                f"size precision."
            )
        notional = size * price
        if notional < MIN_ORDER_USD_NOTIONAL:
            max_levels = int(total_notional // MIN_ORDER_USD_NOTIONAL)
            raise GridGeometryError(
                f"level {index} is ${notional:.2f} after lot rounding; HL requires "
                f"${MIN_ORDER_USD_NOTIONAL:.2f}. ${config.capital_usd:,.2f} at "
                f"{config.leverage}x supports at most {max_levels} levels."
            )
        side: Side = "buy" if price < mark_price else "sell"
        levels.append(
            GridLevel(
                index=index,
                price=price,
                side=side,
                size=size,
                notional_usd=notional,
                cloid=rung_cloid(config.market, index, side),
                tag=rung_tag(index, side),
            )
        )
    return levels


def spacing_bps(config: GridConfig) -> list[float]:
    """Gap between adjacent snapped levels, in basis points of the lower level.

    Computed post-snap, because snapping is what the venue actually trades and
    it can make a nominally even grid uneven.
    """
    prices = snapped_level_prices(config)
    return [
        (prices[i + 1] - prices[i]) / prices[i] * 1e4 for i in range(len(prices) - 1)
    ]
