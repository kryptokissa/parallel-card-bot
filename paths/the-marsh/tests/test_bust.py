"""Law 5 — post-loss moments get calm, not hooks.

The bust path renders the ritual and nothing else: no re-kit nudge, no
next-hunt prompt, and zero XP hanging off the loss.
"""

from __future__ import annotations

from engine.narrator import narrate, narrate_log
from game.marsh_engine import replay
from helpers import run_day


def test_bust_fires_and_is_final(tmp_path):
    engine, events = run_day(
        tmp_path, "storm_bust.json", kit=0.25,
        plan=("hunt", "whistle", "hunt", "whistle"),
    )
    types = [e["type"] for e in events]
    assert "bust" in types
    # nothing after the bust but the bust itself
    assert types.index("bust") == len(types) - 1
    state = replay(events)
    assert state.lifetime["busts"] == 1
    assert not state.expedition.get("active")


def test_bust_narration_is_the_ritual_and_nothing_else():
    line = narrate({"type": "bust"})
    assert "the marsh took the kit" in line.lower()
    assert "by the fire" in line.lower()
    # calm, not hooks: the only onward door is the practice range
    for hook in ("kit up", "go now", "another hunt", "try again", "one more"):
        assert hook not in line.lower()


def test_bust_earns_zero_xp(tmp_path):
    _, events = run_day(
        tmp_path, "storm_bust.json", kit=0.25,
        plan=("hunt", "whistle", "hunt", "whistle"),
    )
    state = replay(events)
    bust_xp = [e for e in state.xp_events if e["event_type"] == "bust"]
    assert bust_xp == []
    # the two clean stops still earned their process XP — de-risking is
    # never penalized (Law 4), losing is never charged extra (Law 5)
    assert state.lifetime["clean_stops"] == 2


def test_bust_recap_has_equal_production_quality(tmp_path):
    _, events = run_day(
        tmp_path, "storm_bust.json", kit=0.25,
        plan=("hunt", "whistle", "hunt", "whistle"),
    )
    lines = narrate_log(events)
    assert any("the marsh took the kit" in line.lower() for line in lines)
