"""Hyperliquid price-grid arithmetic.

A grid level that is not on HL's tick grid is not a level — it is a rejected
order. `wayfinder_paths.mcp.tools.hyperliquid._validate_price` refuses any
price that `HyperliquidAdapter.get_valid_order_price` would move, so the
path has to land on the grid itself rather than hope the venue rounds.

`snap_price` mirrors that adapter method exactly:

- Any integer price is accepted as-is, with no significant-figure cap.
- Otherwise the price must carry <= 5 significant figures AND
  <= `price_decimals` decimal places, both applied as ROUND_DOWN.

`price_decimals` is `MAX_DECIMALS - szDecimals`, where MAX_DECIMALS is 6 for
perps and 8 for spot. szDecimals is a per-market property, so it has to be
supplied — the path cannot guess it, and guessing wrong silently shifts every
level in the grid.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

PERP_MAX_DECIMALS = 6
SPOT_MAX_DECIMALS = 8
MAX_SIG_FIGS = 5


def price_decimals(sz_decimals: int, *, spot: bool = False) -> int:
    """Decimal places HL allows for a market with this szDecimals."""
    if not isinstance(sz_decimals, int) or isinstance(sz_decimals, bool):
        raise TypeError("sz_decimals must be an int")
    if sz_decimals < 0:
        raise ValueError("sz_decimals cannot be negative")
    max_decimals = SPOT_MAX_DECIMALS if spot else PERP_MAX_DECIMALS
    decimals = max_decimals - sz_decimals
    if decimals < 0:
        raise ValueError(
            f"sz_decimals={sz_decimals} exceeds MAX_DECIMALS={max_decimals}; "
            "no valid price grid exists for that market"
        )
    return decimals


def snap_price(price: float, sz_decimals: int, *, spot: bool = False) -> float:
    """Floor `price` onto HL's valid tick grid. Mirrors the adapter exactly.

    Returns 0.0 for non-positive input, matching the adapter. Callers must
    treat a 0.0 result as "no valid price", never as a usable level — an entry
    price of 0.0 is the four-character bug BUILDING_PATHS.md §3 warns about.
    """
    if price <= 0:
        return 0.0
    if float(price).is_integer():
        return float(int(price))

    decimals = price_decimals(sz_decimals, spot=spot)
    decimal_step = Decimal(10) ** (-decimals)
    snapped = (Decimal(str(price)) / decimal_step).to_integral_value(
        rounding=ROUND_DOWN
    ) * decimal_step

    if snapped > 0:
        sig_step = Decimal(10) ** (snapped.adjusted() - (MAX_SIG_FIGS - 1))
        if sig_step > decimal_step:
            snapped = (snapped / sig_step).to_integral_value(
                rounding=ROUND_DOWN
            ) * sig_step

    return float(snapped)


def is_on_grid(price: float, sz_decimals: int, *, spot: bool = False) -> bool:
    """True when `price` survives `snap_price` unchanged."""
    if price <= 0:
        return False
    return snap_price(price, sz_decimals, spot=spot) == float(price)


def tick_size(reference_price: float, sz_decimals: int, *, spot: bool = False) -> float:
    """Smallest price increment available near `reference_price`.

    The significant-figure cap makes the effective tick price-dependent: at
    $0.05 it is the decimal step, at $50,000 it is 1.0 regardless of decimals.
    Level spacing below this is not representable, so the grid must check it.
    """
    if reference_price <= 0:
        raise ValueError("reference_price must be positive")
    decimals = price_decimals(sz_decimals, spot=spot)
    decimal_step = Decimal(10) ** (-decimals)
    magnitude = Decimal(str(float(reference_price))).adjusted()
    sig_step = Decimal(10) ** (magnitude - (MAX_SIG_FIGS - 1))
    return float(max(decimal_step, sig_step))


def lot_size(sz_decimals: int) -> float:
    """Smallest tradeable size increment for the market."""
    if not isinstance(sz_decimals, int) or isinstance(sz_decimals, bool):
        raise TypeError("sz_decimals must be an int")
    if sz_decimals < 0:
        raise ValueError("sz_decimals cannot be negative")
    return float(Decimal(10) ** (-sz_decimals))


def snap_size(size: float, sz_decimals: int) -> float:
    """Floor `size` onto the market's lot grid.

    HL rounds size down to szDecimals; a size below one lot rounds to zero and
    the order is rejected outright rather than shrunk.
    """
    if size <= 0:
        return 0.0
    step = Decimal(10) ** (-sz_decimals)
    return float(
        (Decimal(str(size)) / step).to_integral_value(rounding=ROUND_DOWN) * step
    )
