"""daily_hunt_limit rations risk, not attention.

A no-duck scout takes no position and risks nothing, so by default it
does not spend a trip; three empty marshes must not lock out the day.
Setting daily_limit_counts_refusals restores the older reading, where
every trip to the marsh costs the same. Both readings have to survive a
restart, which means restore_from_log must count the same events the
live path counts.
"""

from __future__ import annotations

import asyncio

from engine.config import MarshConfig
from engine.events import EventLog
from engine.executor import SimExecutor
from engine.feed import FixtureFeed
from engine.hunt import HuntEngine
from engine.practice import load as load_marsh


def _engine(tmp_path, marsh: str, config: MarshConfig, name="events.jsonl"):
    log = EventLog(str(tmp_path / name))
    feed = FixtureFeed(load_marsh(marsh))
    engine = HuntEngine(config, feed, SimExecutor(feed), log, ghost=True)
    engine.restore_from_log()
    if engine.bankroll_native <= 0:
        engine.kit_up(config.hunt_size * 10)
    return engine, log


def test_empty_marshes_do_not_spend_the_day(tmp_path):
    config = MarshConfig(daily_hunt_limit=3)
    engine, _ = _engine(tmp_path, "no_duck_day", config)
    for _ in range(4):
        result = asyncio.run(engine.run_hunt())
        assert result.refusal_reason != "day's done", \
            "a refusal must not consume the daily ration"
    assert engine.hunts_today == []


def test_shots_still_spend_the_day(tmp_path):
    config = MarshConfig(daily_hunt_limit=1, max_open_positions=5)
    engine, _ = _engine(tmp_path, "calm_day", config)
    first = asyncio.run(engine.run_hunt())
    assert first.shot
    assert len(engine.hunts_today) == 1
    second = asyncio.run(engine.run_hunt())
    assert not second.shot and second.refusal_reason == "day's done"


def test_opt_in_counts_every_trip(tmp_path):
    config = MarshConfig(daily_hunt_limit=2, daily_limit_counts_refusals=True)
    engine, _ = _engine(tmp_path, "no_duck_day", config)
    asyncio.run(engine.run_hunt())
    asyncio.run(engine.run_hunt())
    assert asyncio.run(engine.run_hunt()).refusal_reason == "day's done"


def test_count_survives_restart(tmp_path):
    """The rebuilt count must match the live one, in both modes."""
    for counts_refusals, expected in ((False, 0), (True, 3)):
        config = MarshConfig(daily_hunt_limit=9,
                             daily_limit_counts_refusals=counts_refusals)
        name = f"restart-{counts_refusals}.jsonl"
        engine, log = _engine(tmp_path, "no_duck_day", config, name)
        for _ in range(3):
            asyncio.run(engine.run_hunt())
        revived = HuntEngine(config, FixtureFeed(load_marsh("no_duck_day")),
                             SimExecutor(FixtureFeed(load_marsh("no_duck_day"))),
                             EventLog(str(tmp_path / name)), ghost=True)
        revived.restore_from_log()
        assert len(revived.hunts_today) == expected == len(engine.hunts_today)
