"""Fiction lint — §9.6. One game, enforced.

Scans every player-facing string — the dog's narration over an
exhaustive event set, the Lodge signal lines, and the applet copy —
for machinery words. The §10 risk disclosure is the one sanctioned
break in fiction and is exempted verbatim.
"""

from __future__ import annotations

import os
import re

from engine.narrator import narrate, recap
from engine.signals import format_signal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BANNED = ("path", "strategy", "bot", "asset", "trade execution")

DISCLOSURE_MARKERS = (
    "extremely high-risk, newly launched, low-liquidity tokens",
)

EVENT_SAMPLES = [
    {"type": "weather", "state": s} for s in ("Calm", "Brisk", "Storm")
] + [
    {"type": "hunt_started", "hunt_id": "h", "chain": "solana",
     "weather": "Calm", "ghost": False},
    {"type": "hunt_started", "hunt_id": "h", "chain": "solana",
     "weather": "Storm", "ghost": True},
    {"type": "duck_scouted", "symbol": "DUCK", "heat": 82, "verdict": "passed"},
    {"type": "duck_scouted", "symbol": "POND", "heat": 71, "verdict": "refused",
     "gate": "top10", "gate_name": "whale pond"},
    {"type": "duck_scouted", "symbol": "OLD", "heat": 75, "verdict": "refused",
     "gate": "reentry_lockout", "gate_name": "bad blood"},
    {"type": "no_duck", "scouted": 14,
     "failures": {"thin water": 9, "decoy": 3, "baited trap": 2}},
    {"type": "shot", "symbol": "DUCK", "biome": "the Pump Flats", "heat": 82,
     "size": 0.1, "tx": "abc"},
    {"type": "ghost_shot", "symbol": "DUCK", "biome": "Bonk Hollow", "heat": 82},
    {"type": "retrieve_plan", "rules": {
        "retrieve_1": {"gain_pct": 100, "sell_fraction": 0.5},
        "retrieve_2": {"gain_pct": 300}, "stop_loss": {"gain_pct": -50},
        "time_stop": {"hours": 24, "low_pct": -20, "high_pct": 50}}},
    {"type": "partial_retrieve", "symbol": "DUCK", "gain_pct": 110.0},
    {"type": "retrieved", "symbol": "DUCK", "gain_pct": 310.0},
    {"type": "stopped", "symbol": "GALE", "gain_pct": -55.0},
    {"type": "walked", "symbol": "SLOW", "gain_pct": 12.0},
    {"type": "hunt_refused", "reason": "satchel full"},
    {"type": "hunt_refused", "reason": "day's done"},
    {"type": "hunt_refused", "reason": "empty satchel"},
    {"type": "hunt_refused", "reason": "other"},
    {"type": "kitted_up", "amount": 0.3},
    {"type": "expedition_started", "id": 1, "starting_bankroll": 0.3},
    {"type": "walked_out", "amount": 0.29},
    {"type": "make_camp"},
    {"type": "bust"},
    {"type": "ghost_hunt"},
    {"type": "trophy", "name": "Storm Hunter", "season": 1},
]


def _assert_clean(text: str, source: str) -> None:
    lowered = text.lower()
    if any(marker in lowered for marker in DISCLOSURE_MARKERS):
        return  # §10 disclosure: the one sanctioned break in fiction
    for word in BANNED:
        pattern = r"\b" + re.escape(word) + r"s?\b"
        assert not re.search(pattern, lowered), (
            f"fiction break in {source}: {word!r} in {text!r}"
        )


def test_narration_stays_in_fiction():
    for event in EVENT_SAMPLES:
        line = narrate(event)
        if line:
            _assert_clean(line, f"narrator:{event['type']}")


def test_signals_stay_in_fiction():
    for event in EVENT_SAMPLES:
        formatted = format_signal(event)
        if formatted:
            _assert_clean(formatted[1], f"signal:{event['type']}")


def test_recap_stays_in_fiction():
    events = [
        {"type": "expedition_started", "id": 7, "ts": "2026-01-01T00:00:00+00:00"},
        {"type": "weather", "state": "Brisk", "ts": "2026-01-01T00:00:01+00:00"},
        {"type": "shot", "symbol": "DUCK", "ts": "2026-01-01T00:00:02+00:00"},
        {"type": "partial_retrieve", "symbol": "DUCK", "gain_pct": 118.0,
         "ts": "2026-01-01T01:00:00+00:00"},
        {"type": "stopped", "symbol": "GALE", "gain_pct": -50.0,
         "ts": "2026-01-01T02:00:00+00:00"},
        {"type": "walked", "symbol": "SLOW", "gain_pct": 10.0,
         "ts": "2026-01-01T03:00:00+00:00"},
        {"type": "walked_out", "amount": 0.29,
         "ts": "2026-01-02T00:00:00+00:00"},
    ]
    _assert_clean(recap(events, 7), "recap")


def test_applet_copy_stays_in_fiction():
    dist = os.path.join(ROOT, "applet", "dist")
    for base, _, files in os.walk(dist):
        for fname in files:
            if not fname.endswith((".html", ".js")):
                continue
            with open(os.path.join(base, fname), encoding="utf-8") as fh:
                raw = fh.read()
            # strip tags/code punctuation down to visible-ish text lines
            for line in raw.splitlines():
                text = re.sub(r"<[^>]+>", " ", line)
                if "//lint:machinery-ok" in line:
                    continue  # code identifiers, not player-facing copy
                _assert_clean(text, f"applet:{fname}")
