"""The hard constraints are code, not prompt (§4).

hunt_size ceiling, max_open_positions, daily_hunt_limit, no averaging
down, the 7-day re-entry lockout, and mid-hunt config immutability.
"""

from __future__ import annotations

import dataclasses

import pytest

from engine.config import MarshConfig
from helpers import run_day


def test_shot_never_exceeds_hunt_size(tmp_path):
    _, events = run_day(tmp_path, "calm_day.json", kit=5.0)
    shots = [e for e in events if e["type"] in ("shot", "ghost_shot")]
    assert shots and all(e["size"] <= 0.1 for e in shots)


def test_max_open_positions_blocks_second_hunt(tmp_path):
    _, events = run_day(tmp_path, "calm_day.json",
                        plan=("hunt", "hunt"))
    refusals = [e for e in events if e["type"] == "hunt_refused"]
    assert any(e["reason"] == "satchel full" for e in refusals)
    assert sum(1 for e in events if e["type"] in ("shot", "ghost_shot")) == 1


def test_daily_hunt_limit_holds(tmp_path):
    _, events = run_day(
        tmp_path, "no_duck_day.json",
        plan=("hunt", "hunt", "hunt", "hunt", "hunt"),
    )
    hunts = [e for e in events if e["type"] == "hunt_started"]
    refusals = [e for e in events if e["type"] == "hunt_refused"
                and e["reason"] == "day's done"]
    assert len(hunts) == 3
    assert len(refusals) == 2


def test_stopped_token_locked_out(tmp_path):
    _, events = run_day(
        tmp_path, "storm_bust.json", kit=0.25,
        plan=("hunt", "whistle", "hunt"),
    )
    second_hunt_shots = [e for e in events if e["type"] in ("shot", "ghost_shot")]
    # first shot GALE, second must not be GALE (bad blood)
    assert second_hunt_shots[0]["symbol"] == "GALE"
    assert all(e["symbol"] != "GALE" for e in second_hunt_shots[1:])
    lockouts = [e for e in events if e["type"] == "duck_scouted"
                and e.get("gate") == "reentry_lockout"]
    assert lockouts


def test_config_is_immutable_mid_hunt():
    config = MarshConfig()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.hunt_size = 99.0  # type: ignore[misc]


def test_config_validation_rejects_nonsense():
    with pytest.raises(ValueError):
        MarshConfig(hunt_size=-1).validate()
    with pytest.raises(ValueError):
        MarshConfig(chain="ethereum").validate()
    with pytest.raises(ValueError):
        MarshConfig(stop_loss_pct=10.0).validate()


def test_never_average_down(tmp_path):
    """While a duck is held, the same token can't be bought again even
    if positions allow it (max_open_positions raised)."""
    _, events = run_day(
        tmp_path, "calm_day.json",
        config=MarshConfig(max_open_positions=2),
        plan=("hunt", "hunt"),
    )
    shots = [e for e in events if e["type"] in ("shot", "ghost_shot")]
    symbols = [e["symbol"] for e in shots]
    assert len(symbols) == len(set(symbols))
    held_refusals = [e for e in events if e["type"] == "duck_scouted"
                     and e.get("gate") == "already_holding"]
    assert held_refusals
