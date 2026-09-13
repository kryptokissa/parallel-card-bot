"""Grid geometry, including the two failure modes that are silent if unchecked."""

from __future__ import annotations

import pytest

from engine.config import GridConfig
from engine.levels import (
    MIN_ORDER_USD_NOTIONAL,
    GridGeometryError,
    build_levels,
    raw_level_prices,
    snapped_level_prices,
    spacing_bps,
)


def test_geometric_spacing_has_equal_percentage_gaps(base_config):
    prices = raw_level_prices(base_config.with_updates(spacing="geometric"))
    ratios = [prices[i + 1] / prices[i] for i in range(len(prices) - 1)]
    assert all(abs(r - ratios[0]) < 1e-9 for r in ratios)


def test_arithmetic_spacing_has_equal_dollar_gaps(base_config):
    prices = raw_level_prices(base_config.with_updates(spacing="arithmetic"))
    gaps = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    assert all(abs(g - gaps[0]) < 1e-6 for g in gaps)


def test_endpoints_land_exactly_on_the_requested_range(base_config):
    # Repeated multiplication otherwise leaves the top level a hair under
    # `upper`, which then floors a whole tick below what the user asked for.
    for spacing in ("geometric", "arithmetic"):
        prices = snapped_level_prices(base_config.with_updates(spacing=spacing))
        assert prices[0] == base_config.lower
        assert prices[-1] == base_config.upper


def test_every_snapped_level_is_distinct(base_config):
    prices = snapped_level_prices(base_config)
    assert len(set(prices)) == len(prices)


def test_levels_collapsing_onto_one_tick_is_refused(base_config):
    # Near 100k with szDecimals=5 the effective tick is 10.0, so a $10 range
    # cannot hold 20 distinct levels. Without this check the orders would not
    # error, they would stack at one price and stop being a grid.
    tight = base_config.with_updates(lower=100000.0, upper=100010.0, levels=20)
    with pytest.raises(GridGeometryError, match="collapsed"):
        snapped_level_prices(tight)


def test_range_below_the_price_grid_is_refused():
    # szDecimals=4 leaves 2 price decimals, so a sub-cent range has no grid.
    doomed = GridConfig(
        market="TOKEN-USDC",
        sz_decimals=4,
        lower=0.001,
        upper=0.002,
        levels=4,
        capital_usd=5000.0,
        breakout="halt_hold",
    )
    with pytest.raises(GridGeometryError):
        snapped_level_prices(doomed)


def test_sides_split_around_the_mark(base_config):
    levels = build_levels(base_config, 100000.0)
    assert all(level.side == "buy" for level in levels if level.price < 100000.0)
    assert all(level.side == "sell" for level in levels if level.price >= 100000.0)


def test_every_order_clears_the_venue_minimum(base_config):
    for level in build_levels(base_config, 100000.0):
        assert level.notional_usd >= MIN_ORDER_USD_NOTIONAL


def test_capital_spread_too_thin_is_refused_with_a_usable_ceiling(base_config):
    thin = base_config.with_updates(levels=40, capital_usd=200.0, leverage=1.0)
    with pytest.raises(GridGeometryError, match="supports at most"):
        build_levels(thin, 100000.0)


def test_size_rounding_down_to_zero_is_refused():
    # One lot of a 5-decimal asset at a high price needs real capital behind it.
    starved = GridConfig(
        market="BTC-USDC",
        sz_decimals=2,
        lower=94000.0,
        upper=106000.0,
        levels=4,
        capital_usd=100.0,
        leverage=1.0,
        breakout="halt_hold",
    )
    with pytest.raises(GridGeometryError):
        build_levels(starved, 100000.0)


def test_a_zero_mark_is_never_a_usable_mark(base_config):
    # An entry price of 0.0 silently disabled every stop in The Marsh.
    with pytest.raises(GridGeometryError, match="mark_price"):
        build_levels(base_config, 0.0)
    with pytest.raises(GridGeometryError):
        build_levels(base_config, -1.0)


def test_cloid_tags_are_unique_and_stable(base_config):
    first = [level.cloid_tag for level in build_levels(base_config, 100000.0)]
    second = [level.cloid_tag for level in build_levels(base_config, 100000.0)]
    assert first == second
    assert len(set(first)) == len(first)


def test_spacing_is_measured_after_snapping(base_config):
    # Post-snap is what the venue trades; a nominally even grid can be uneven.
    gaps = spacing_bps(base_config)
    assert len(gaps) == base_config.levels - 1
    assert all(gap > 0 for gap in gaps)
