"""The daily loss limit — the one that used to be decoration."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from engine.plan import plan_daily_stop
from engine.risk import (
    assess,
    position_action_on_stop,
    requires_equity,
    roll_day,
    utc_day,
)


def test_no_limit_configured_needs_no_equity(base_config):
    assert not requires_equity(base_config)
    assert base_config.daily_loss_limit_usd is None


def test_a_configured_limit_makes_equity_mandatory(base_config):
    # Without an equity reading there is nothing to measure, and continuing
    # anyway is how the limit became decoration in the first place.
    assert requires_equity(base_config.with_updates(daily_loss_limit_usd=250.0))


def test_the_first_reading_of_a_day_marks_the_opening_equity():
    rolled = roll_day(stored_day="", stored_open_equity=0.0, equity=5000.0)
    assert rolled.open_equity == 5000.0
    assert rolled.day == utc_day()


def test_a_new_utc_day_re_marks_the_opening_equity():
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    rolled = roll_day(
        stored_day=yesterday, stored_open_equity=5000.0, equity=4200.0
    )
    assert rolled.rolled
    assert rolled.open_equity == 4200.0


def test_the_same_day_keeps_its_opening_mark():
    rolled = roll_day(
        stored_day=utc_day(), stored_open_equity=5000.0, equity=4200.0
    )
    assert not rolled.rolled
    assert rolled.open_equity == 5000.0


def test_loss_inside_the_limit_does_not_breach(base_config):
    config = base_config.with_updates(daily_loss_limit_usd=250.0)
    daily = assess(config, open_equity=5000.0, equity=4800.0)
    assert daily.loss == 200.0
    assert not daily.breached
    assert daily.headroom == 50.0


def test_loss_at_the_limit_breaches(base_config):
    config = base_config.with_updates(daily_loss_limit_usd=250.0)
    assert assess(config, open_equity=5000.0, equity=4750.0).breached


def test_a_profitable_day_can_never_breach(base_config):
    config = base_config.with_updates(daily_loss_limit_usd=250.0)
    daily = assess(config, open_equity=5000.0, equity=5400.0)
    assert daily.loss == -400.0
    assert not daily.breached
    # Headroom is how much further the day can fall before breaching, so a
    # profitable day carries the profit as extra room.
    assert daily.headroom == 650.0


def test_without_a_limit_nothing_breaches(base_config):
    daily = assess(base_config, open_equity=5000.0, equity=1.0)
    assert not daily.breached
    assert daily.headroom is None


def test_equity_measures_fees_and_funding_for_free(base_config):
    # The point of measuring equity rather than replaying fills: costs the path
    # never sees are already in the number.
    config = base_config.with_updates(daily_loss_limit_usd=100.0)
    assert assess(config, open_equity=5000.0, equity=4899.0).breached


def test_the_stop_reuses_the_position_choice_the_user_already_made(base_config):
    assert position_action_on_stop(base_config.with_updates(breakout="halt_hold")) == (
        "halt_hold"
    )
    assert position_action_on_stop(
        base_config.with_updates(breakout="halt_close")
    ) == "halt_close"


def test_recentring_after_a_loss_limit_degrades_to_holding(base_config):
    # Rebuilding the grid after hitting a loss limit is chasing the loss.
    assert position_action_on_stop(base_config.with_updates(breakout="recenter")) == (
        "halt_hold"
    )


def test_the_stop_cancels_every_rung(base_config):
    stop = plan_daily_stop(
        base_config,
        100000.0,
        resting_cloids={"grid-0-buy", "grid-5-sell"},
        position_size=0.0,
    )
    assert stop.status == "halted"
    assert stop.breakout == "daily_loss_limit"
    assert {i.cloid for i in stop.intents} == {"grid-0-buy", "grid-5-sell"}


def test_the_stop_flattens_only_when_that_was_the_choice(base_config):
    held = plan_daily_stop(
        base_config,
        100000.0,
        resting_cloids=set(),
        position_size=0.05,
        position_action="halt_hold",
    )
    assert not [i for i in held.intents if i.action == "place"]
    assert "unhedged" in held.summary

    closed = plan_daily_stop(
        base_config,
        100000.0,
        resting_cloids=set(),
        position_size=0.05,
        position_action="halt_close",
    )
    flatten = [i for i in closed.intents if i.action == "place"]
    assert len(flatten) == 1
    assert flatten[0].reduce_only and flatten[0].side == "sell"


def test_the_stop_reports_the_numbers_that_caused_it(base_config):
    config = base_config.with_updates(daily_loss_limit_usd=250.0)
    daily = assess(config, open_equity=5000.0, equity=4600.0)
    summary = daily.summary()
    assert "400" in summary and "250" in summary
