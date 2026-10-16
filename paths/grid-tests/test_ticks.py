"""The venue's price grid. A level off the grid is a rejected order."""

from __future__ import annotations

import pytest

from engine.ticks import (
    is_on_grid,
    lot_size,
    price_decimals,
    snap_price,
    snap_size,
    tick_size,
)


def test_price_decimals_is_max_minus_sz_decimals():
    assert price_decimals(0) == 6
    assert price_decimals(4) == 2
    assert price_decimals(4, spot=True) == 4


def test_price_decimals_rejects_impossible_market():
    with pytest.raises(ValueError):
        price_decimals(7)


def test_integer_prices_bypass_the_significant_figure_cap():
    # HL accepts any integer price, even at 6+ significant figures.
    assert snap_price(123456, 4) == 123456.0
    assert snap_price(1234567, 5) == 1234567.0


def test_five_significant_figures_floor():
    assert snap_price(1234.56789, 4) == 1234.5
    assert snap_price(0.0123456, 0) == 0.012345


def test_decimal_cap_dominates_on_low_precision_markets():
    # szDecimals=4 leaves only 2 price decimals, so a cheap asset collapses.
    assert snap_price(0.0123456, 4) == 0.01


def test_snapping_always_floors_never_rounds_up():
    for raw in (1234.99999, 0.019999, 99999.9):
        assert snap_price(raw, 4) <= raw


def test_non_positive_price_is_not_a_price():
    assert snap_price(0.0, 4) == 0.0
    assert snap_price(-5.0, 4) == 0.0
    assert not is_on_grid(0.0, 4)


def test_is_on_grid_agrees_with_snap():
    assert is_on_grid(1234.5, 4)
    assert not is_on_grid(1234.56, 4)


def test_tick_size_grows_with_price_magnitude():
    # The significant-figure cap makes the effective tick price-dependent.
    assert tick_size(50000, 4) == 1.0
    assert tick_size(0.05, 4) == 0.01
    assert tick_size(50000, 4) > tick_size(500, 4)


def test_lot_size_and_snap_size():
    assert lot_size(4) == 0.0001
    assert snap_size(0.123456, 4) == 0.1234
    assert snap_size(0.00001, 4) == 0.0
    assert snap_size(-1.0, 4) == 0.0
