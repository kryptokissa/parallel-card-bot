"""Exchange as the truth for state, the log as the truth for intent."""

from __future__ import annotations

from engine.levels import build_levels
from engine.reconcile import RestingOrder, reconcile


def _rungs(config):
    return {level.cloid_tag: level for level in build_levels(config, 100000.0)}


def test_a_fully_resting_grid_reconciles_clean(base_config):
    orders = [
        {"cloid": tag, "side": "B", "limitPx": level.price, "sz": level.size, "oid": i}
        for i, (tag, level) in enumerate(_rungs(base_config).items())
    ]
    result = reconcile(base_config, 100000.0, open_orders=orders)
    assert result.clean
    assert len(result.resting) == base_config.levels


def test_missing_rungs_are_reported(base_config):
    rungs = _rungs(base_config)
    tag, level = next(iter(rungs.items()))
    orders = [{"cloid": tag, "side": "B", "limitPx": level.price, "sz": level.size}]
    result = reconcile(base_config, 100000.0, open_orders=orders)
    assert len(result.missing) == base_config.levels - 1
    assert tag not in result.missing


def test_an_empty_book_reports_every_rung_missing(base_config):
    result = reconcile(base_config, 100000.0, open_orders=[])
    assert len(result.missing) == base_config.levels
    assert not result.clean


def test_a_rung_resting_at_the_wrong_price_is_flagged(base_config):
    rungs = _rungs(base_config)
    tag, level = next(iter(rungs.items()))
    orders = [{"cloid": tag, "side": "B", "limitPx": level.price + 500.0, "sz": level.size}]
    result = reconcile(base_config, 100000.0, open_orders=orders)
    assert result.mispriced
    cloid, actual, expected = result.mispriced[0]
    assert cloid == tag and expected == level.price and actual != expected


def test_orders_from_a_previous_range_are_marked_for_cancellation(base_config):
    orders = [{"cloid": "grid-99-buy", "side": "B", "limitPx": 80000.0, "sz": 0.01}]
    result = reconcile(base_config, 100000.0, open_orders=orders)
    assert [o.cloid for o in result.unexpected] == ["grid-99-buy"]


def test_orders_that_are_not_this_grids_are_left_alone(base_config):
    # They may be the user's own, or another path's. Cancelling them is not this
    # grid's business.
    orders = [
        {"cloid": None, "side": "A", "limitPx": 120000.0, "sz": 0.5, "oid": 7},
        {"cloid": "other-strategy-1", "side": "B", "limitPx": 50000.0, "sz": 0.1},
    ]
    result = reconcile(base_config, 100000.0, open_orders=orders)
    assert len(result.untagged) == 2
    assert not result.unexpected


def test_position_comes_from_the_exchange_not_the_log(base_config):
    result = reconcile(base_config, 100000.0, open_orders=[], position_size=-0.25)
    assert result.position_size == -0.25


def test_order_parsing_handles_the_shapes_the_venue_uses():
    assert RestingOrder.from_exchange({"side": "B"}).side == "buy"
    assert RestingOrder.from_exchange({"side": "A"}).side == "sell"
    assert RestingOrder.from_exchange({"dir": "Open Long"}).side == "buy"
    assert RestingOrder.from_exchange({"side": "sell"}).side == "sell"


def test_a_missing_price_is_zero_not_a_plausible_default():
    # A quietly defaulted price is how a real position ends up with no stop.
    parsed = RestingOrder.from_exchange({"cloid": "grid-0-buy", "side": "B"})
    assert parsed.price == 0.0


def test_order_ids_survive_either_field_name():
    assert RestingOrder.from_exchange({"oid": 42}).order_id == "42"
    assert RestingOrder.from_exchange({"order_id": 7}).order_id == "7"
    assert RestingOrder.from_exchange({}).order_id is None


def test_summary_names_what_diverged(base_config):
    orders = [{"cloid": "grid-99-buy", "side": "B", "limitPx": 80000.0, "sz": 0.01}]
    summary = reconcile(base_config, 100000.0, open_orders=orders).summary()
    assert "missing" in summary and "stale" in summary
