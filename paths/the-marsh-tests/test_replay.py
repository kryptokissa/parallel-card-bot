"""Law 3 — the log is the save file.

Fixture logs fold to deterministic snapshots; replay is idempotent and
order-insensitive on timestamps already in order.
"""

from __future__ import annotations

import copy
import json

from game.marsh_engine import replay
from helpers import run_day


def test_replay_deterministic_snapshot(tmp_path):
    _, events = run_day(tmp_path, "calm_day.json")
    state_a = replay(copy.deepcopy(events)).to_dict()
    state_b = replay(copy.deepcopy(events)).to_dict()
    assert json.dumps(state_a, sort_keys=True, default=str) == \
        json.dumps(state_b, sort_keys=True, default=str)
    assert state_a["lifetime"]["shots"] == 1
    assert state_a["lifetime"]["plan_completions"] == 1
    # +10 completed hunt, +15 retrieve_1 per plan, +25 full rule close
    assert state_a["hunter"]["xp"] == 50


def test_replay_does_not_mutate_scoring_inputs(tmp_path):
    _, events = run_day(tmp_path, "calm_day.json")
    first = replay(copy.deepcopy(events)).to_dict()
    twice_folded = replay(copy.deepcopy(events))
    again = replay(copy.deepcopy(events)).to_dict()
    assert json.dumps(first, sort_keys=True, default=str) == \
        json.dumps(again, sort_keys=True, default=str)
    assert twice_folded.xp == first["hunter"]["xp"]


def test_prefix_replay_is_consistent(tmp_path):
    """Folding a prefix never awards more XP than the full log."""
    _, events = run_day(tmp_path, "calm_day.json")
    full = replay(copy.deepcopy(events)).xp
    for k in range(len(events)):
        prefix_xp = replay(copy.deepcopy(events[:k])).xp
        assert prefix_xp <= full


def test_no_duck_day_scores_discipline(tmp_path):
    _, events = run_day(tmp_path, "no_duck_day.json", plan=("hunt",))
    state = replay(events)
    assert state.lifetime["no_ducks"] == 1
    assert state.xp == 10  # accepted refusal, no gate loosened after
    assert state.discipline_streak == 1


def test_ghost_events_never_touch_real_scoring(tmp_path):
    _, events = run_day(tmp_path, "calm_day.json", ghost=True)
    state = replay(events)
    assert state.lifetime["shots"] == 0
    assert state.lifetime["hunts"] == 0
    assert state.lifetime["ghost_hunts"] == 1
    assert state.xp == 3  # ghost hunt XP only
