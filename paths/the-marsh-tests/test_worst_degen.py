"""Law 1 stress test — the worst degen earns nothing.

A synthetic log full of enormous PnL, huge sizes, manual dumping, and
size-raising tilt must produce zero XP from the PnL alone: outcomes
may hang trophies on the wall, but progression only ever comes from
process, and this hunter has none.
"""

from __future__ import annotations

from game.marsh_engine import replay

BASE = "2026-01-0{d}T0{h}:00:00+00:00"


def _ts(d: int, h: int) -> str:
    return BASE.format(d=d, h=h)


def test_pnl_only_log_earns_zero_xp():
    events = [
        {"type": "kitted_up", "amount": 500.0, "ts": _ts(1, 0)},
        {"type": "expedition_started", "id": 1, "starting_bankroll": 500.0,
         "ts": _ts(1, 1)},
        # a lucky manual scalp: +900% but sold by hand, no plan followed
        {"type": "shot", "position_id": "p1", "token": "LUCK", "symbol": "LUCK",
         "heat": 99, "size": 250.0, "ts": _ts(1, 2)},
        {"type": "manual_sell", "position_id": "p1", "closes_position": True,
         "gain_pct": 900.0, "ts": _ts(1, 3)},
        # tilt: raises size right after a stop
        {"type": "shot", "position_id": "p2", "token": "TILT", "symbol": "TILT",
         "heat": 95, "size": 250.0, "ts": _ts(1, 4)},
        {"type": "manual_sell", "position_id": "p2", "closes_position": True,
         "gain_pct": -60.0, "ts": _ts(1, 5)},
        {"type": "config_changed", "field": "hunt_size", "old": 250.0,
         "new": 400.0, "ts": _ts(1, 6)},
    ]
    state = replay(events)
    # the two gated shots still earn their process XP (they went through
    # the gates) — but nothing from the +900%, the size, or the volume
    assert state.xp == 20
    xp_reasons = {e["reason"] for e in state.xp_events if e["xp"] > 0}
    assert xp_reasons == {"completed_hunt"}
    # and the behavior is remembered for what it was
    assert state.lifetime["flinches"] == 2


def test_manual_exits_never_earn_plan_xp():
    events = [
        {"type": "shot", "position_id": "p1", "token": "X", "symbol": "X",
         "heat": 90, "size": 1.0, "ts": _ts(1, 0)},
        {"type": "manual_sell", "position_id": "p1", "ts": _ts(1, 1)},
        {"type": "retrieved", "position_id": "p1", "gain_pct": 500.0,
         "ts": _ts(1, 2)},
    ]
    state = replay(events)
    reasons = {e["reason"] for e in state.xp_events if e["xp"] > 0}
    assert "full_rule_close" not in reasons  # manual action poisoned the plan
    # outcome trophy may still hang on the wall — outcomes are trophies
    assert any(t["id"] == "big_duck" for t in state.trophies)


def test_tilt_resets_discipline_streak():
    events = [
        {"type": "shot", "position_id": "p1", "token": "A", "symbol": "A",
         "heat": 90, "size": 1.0, "ts": _ts(1, 0)},
        {"type": "no_duck", "scouted": 5, "failures": {}, "ts": _ts(1, 1)},
        {"type": "stopped", "position_id": "p1", "gain_pct": -50.0,
         "ts": _ts(1, 2)},
        {"type": "config_changed", "field": "hunt_size", "old": 0.1,
         "new": 0.3, "ts": _ts(1, 3)},
    ]
    state = replay(events)
    assert state.lifetime["tilt_flags"] == 1
    assert state.discipline_streak == 0
    assert state.best_discipline_streak == 2


def test_gate_loosening_voids_no_duck_xp():
    events = [
        {"type": "no_duck", "scouted": 8, "failures": {"thin water": 8},
         "ts": _ts(1, 0)},
        {"type": "config_changed", "field": "min_liquidity", "old": 25000,
         "new": 5000, "ts": _ts(1, 5)},
    ]
    state = replay(events)
    assert state.xp == 0  # loosened a gate within 24h: refusal unrewarded
