"""Order intents, the reduce_only rule, and the three breakout behaviours."""

from __future__ import annotations

import pytest

from engine.plan import (
    PLAN_TTL_SECONDS,
    in_range,
    plan,
    plan_breakout,
    plan_initial,
    plan_refill,
    recentred_config,
)


def test_the_path_never_executes_only_describes(base_config):
    result = plan_initial(base_config, 100000.0)
    for intent in result.intents:
        call = intent.as_tool_call(base_config.market, "<label>")
        assert call["tool"].startswith("hyperliquid_")


def test_wallet_label_stays_a_placeholder(base_config):
    # Labels are generated per install; a stored plan must not pin one.
    call = plan_initial(base_config, 100000.0).intents[0].as_tool_call(
        base_config.market, "<your grid wallet label>"
    )
    assert call["wallet_label"].startswith("<")


def test_a_plan_carries_a_ttl(base_config):
    # A plan computed against a stale mark describes prices that no longer exist.
    assert plan_initial(base_config, 100000.0).ttl_seconds == PLAN_TTL_SECONDS


def test_a_flat_grid_never_sets_reduce_only(base_config):
    # The venue rejects reduce_only with no opposite position
    # (`reduce_only_no_position`), so a neutral grid's opening sells must not
    # carry it — §4.4's blanket rule would get every sell rung rejected.
    result = plan_initial(base_config, 100000.0, position_size=0.0)
    assert not any(intent.reduce_only for intent in result.intents)


def test_reduce_only_is_set_exactly_on_orders_that_reduce(base_config):
    long_side = plan_initial(base_config, 100000.0, position_size=0.05)
    assert all(i.reduce_only for i in long_side.intents if i.side == "sell")
    assert not any(i.reduce_only for i in long_side.intents if i.side == "buy")

    short_side = plan_initial(base_config, 100000.0, position_size=-0.05)
    assert all(i.reduce_only for i in short_side.intents if i.side == "buy")
    assert not any(i.reduce_only for i in short_side.intents if i.side == "sell")


def test_every_rung_gets_its_own_client_order_id(base_config):
    result = plan_initial(base_config, 100000.0)
    cloids = [intent.cloid for intent in result.intents]
    assert len(set(cloids)) == len(cloids)
    assert all(cloid and cloid.startswith("grid-") for cloid in cloids)


def test_refill_replaces_only_what_is_missing(base_config):
    every = {f"grid-{i}-buy" for i in range(3)} | {f"grid-{i}-sell" for i in range(3, 6)}
    assert plan_refill(base_config, 100000.0, resting_cloids=every).is_empty

    partial = set(list(every)[:2])
    result = plan_refill(base_config, 100000.0, resting_cloids=partial)
    assert len(result.intents) == 4


def test_refill_is_idempotent(base_config):
    first = plan_refill(base_config, 100000.0, resting_cloids=set())
    placed = {intent.cloid for intent in first.intents}
    second = plan_refill(base_config, 100000.0, resting_cloids=placed)
    assert second.is_empty


def test_in_range_covers_the_bounds(base_config):
    assert in_range(base_config, 94000.0)
    assert in_range(base_config, 106000.0)
    assert not in_range(base_config, 93999.0)


def test_halt_hold_keeps_the_position_and_says_so(base_config):
    config = base_config.with_updates(breakout="halt_hold")
    result = plan_breakout(
        config, 108000.0, resting_cloids={"grid-0-buy"}, position_size=0.03
    )
    assert result.status == "halted"
    assert all(intent.action == "cancel" for intent in result.intents)
    assert "unhedged" in result.summary


def test_halt_close_flattens_with_reduce_only(base_config):
    config = base_config.with_updates(breakout="halt_close")
    result = plan_breakout(
        config, 108000.0, resting_cloids={"grid-0-buy"}, position_size=0.03
    )
    flatten = [i for i in result.intents if i.action == "place"]
    assert len(flatten) == 1
    # A genuine exit against an open position: reduce_only is both accepted by
    # the venue and necessary, so a racing fill cannot flip the position.
    assert flatten[0].reduce_only
    assert flatten[0].side == "sell"
    assert flatten[0].size == 0.03


def test_halt_close_with_no_position_places_nothing(base_config):
    config = base_config.with_updates(breakout="halt_close")
    result = plan_breakout(config, 108000.0, resting_cloids=set(), position_size=0.0)
    assert not [i for i in result.intents if i.action == "place"]


def test_halt_close_buys_back_a_short(base_config):
    config = base_config.with_updates(breakout="halt_close")
    result = plan_breakout(config, 90000.0, resting_cloids=set(), position_size=-0.02)
    flatten = [i for i in result.intents if i.action == "place"][0]
    assert flatten.side == "buy" and flatten.reduce_only


def test_recenter_stops_after_the_configured_number_of_attempts(base_config):
    config = base_config.with_updates(breakout="recenter", max_recenters=2)
    still_going = plan_breakout(
        config, 108000.0, resting_cloids=set(), recenters_used=1
    )
    assert still_going.status == "recentering"

    exhausted = plan_breakout(
        config, 108000.0, resting_cloids=set(), recenters_used=2
    )
    assert exhausted.status == "halted"
    assert exhausted.breakout == "recenter_exhausted"


def test_recentring_preserves_the_span(base_config):
    span = base_config.upper - base_config.lower
    moved = recentred_config(base_config, 108000.0)
    assert moved.upper - moved.lower == pytest.approx(span)
    assert moved.lower < 108000.0 < moved.upper


def test_all_breakout_modes_cancel_the_old_rungs(base_config):
    for mode in ("halt_hold", "halt_close", "recenter"):
        config = base_config.with_updates(breakout=mode)
        result = plan_breakout(
            config,
            108000.0,
            resting_cloids={"grid-0-buy", "grid-1-buy"},
            position_size=0.0,
        )
        cancels = {i.cloid for i in result.intents if i.action == "cancel"}
        assert cancels == {"grid-0-buy", "grid-1-buy"}


def test_plan_refuses_to_work_from_a_zero_mark(base_config):
    result = plan(base_config, 0.0)
    assert result.status == "blocked"
    assert result.is_empty


def test_plan_routes_out_of_range_price_to_the_breakout_branch(base_config):
    result = plan(base_config, 120000.0, started=True)
    assert result.breakout is not None


def test_plan_opens_before_it_refills(base_config):
    assert plan(base_config, 100000.0, started=False).status == "opening"
    assert plan(base_config, 100000.0, started=True).status in {"working"}
