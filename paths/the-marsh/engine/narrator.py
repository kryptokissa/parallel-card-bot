"""The dog — every player-facing line comes from here.

Laconic, seen everything, incorruptible about the gates. The hunt log
IS the game, so this module renders engine events into the story. All
strings here are player-facing and pass the fiction lint
(tests/test_fiction_lint.py): the marsh, the dog, ducks — never the
machinery behind them.
"""

from __future__ import annotations

from typing import Any


def narrate(event: dict[str, Any]) -> str | None:
    etype = event.get("type")
    handler = _HANDLERS.get(etype)
    return handler(event) if handler else None


def narrate_log(events: list[dict[str, Any]]) -> list[str]:
    lines = []
    for event in events:
        line = narrate(event)
        if line:
            lines.append(line)
    return lines


def _weather(event: dict[str, Any]) -> str | None:
    state = event.get("state", "Calm")
    return {
        "Calm": "Flat water this morning. Quiet.",
        "Brisk": "Wind's up. Ducks moving quick today.",
        "Storm": "Storm over the marsh. Everything's flying and half of it's lying.",
    }.get(state)


def _hunt_started(event: dict[str, Any]) -> str:
    prefix = "Practice range. " if event.get("ghost") else ""
    return f"{prefix}Heading out. {event.get('weather', 'Calm')} weather."


def _duck_scouted(event: dict[str, Any]) -> str | None:
    if event.get("verdict") == "passed":
        return (f"${event.get('symbol')} — level {int(event.get('heat', 0))} duck. "
                f"Clean lines. Watching it.")
    gate = event.get("gate_name", "bad water")
    return f"${event.get('symbol')} — {gate}. Passed."


def _no_duck(event: dict[str, Any]) -> str:
    failures = event.get("failures", {})
    parts = [f"{count} {name}" for name, count in failures.items()]
    detail = ", ".join(parts) if parts else "nothing worth wading in for"
    return (f"Dog won't fetch. {event.get('scouted', 0)} scouted: {detail}. "
            f"A refusal is a good hunt.")


def _shot(event: dict[str, Any]) -> str:
    return (f"The shot: ${event.get('symbol')} in {event.get('biome')}, "
            f"level {int(event.get('heat', 0))} duck, "
            f"{event.get('size')} at the water line. Done before the echo.")


def _ghost_shot(event: dict[str, Any]) -> str:
    return (f"Practice shot: ${event.get('symbol')} in {event.get('biome')}, "
            f"level {int(event.get('heat', 0))} duck. No kit spent — "
            f"that one was air.")


def _retrieve_plan(event: dict[str, Any]) -> str:
    rules = event.get("rules", {})
    r1 = rules.get("retrieve_1", {})
    r2 = rules.get("retrieve_2", {})
    stop = rules.get("stop_loss", {})
    walk = rules.get("time_stop", {})
    return (f"The plan, plain: half at +{r1.get('gain_pct', 100):g}%, "
            f"everything at +{r2.get('gain_pct', 300):g}%, "
            f"bail at {stop.get('gain_pct', -50):g}%, and if it's floating "
            f"there in {walk.get('hours', 24):g} hours, I walk.")


def _partial_retrieve(event: dict[str, Any]) -> str:
    return (f"Whistle. Brought half of ${event.get('symbol')} back to the bank "
            f"at +{event.get('gain_pct'):g}%. The rest swims on the plan.")


def _retrieved(event: dict[str, Any]) -> str:
    return (f"Retrieved. ${event.get('symbol')} at +{event.get('gain_pct'):g}%. "
            f"Full fetch, by the book.")


def _stopped(event: dict[str, Any]) -> str:
    return (f"Winged. ${event.get('symbol')} stopped at "
            f"{event.get('gain_pct'):g}%. Clean stop, streak intact. "
            f"That's the marsh.")


def _walked(event: dict[str, Any]) -> str:
    return (f"Time's up on ${event.get('symbol')}. Sat at "
            f"{event.get('gain_pct'):g}% too long — I walked it. "
            f"Not every duck is worth the daylight.")


def _hunt_refused(event: dict[str, Any]) -> str:
    reason = event.get("reason", "")
    if reason == "satchel full":
        return "Not going out — still carrying a bird. One at a time."
    if reason == "day's done":
        return "Day's done. Three trips is three trips. Fire's warm."
    if reason == "empty satchel":
        return "Satchel's empty. Kit up when you're ready."
    return "Not going out. Marsh says no today."


def _kitted_up(event: dict[str, Any]) -> str:
    return (f"Kitted up: {event.get('amount'):g} in the satchel. "
            f"The marsh can take it — that's the arrangement. Say go when.")


def _expedition_started(event: dict[str, Any]) -> str:
    return (f"Expedition {event.get('id')}. Full satchel, "
            f"{event.get('starting_bankroll'):g} at first light.")


def _walked_out(event: dict[str, Any]) -> str:
    return (f"Walked out with {event.get('amount'):g} banked. "
            f"That's the whole game, hunter. The marsh remembers.")


def _make_camp(event: dict[str, Any]) -> str:
    return "Making camp. Kit stays staged. We go again when you say."


def _bust(event: dict[str, Any]) -> str:
    return ("The marsh took the kit. The gates held; the ducks didn't. "
            "Range is open if you want reps. I'll be by the fire.")


def _ghost_hunt(event: dict[str, Any]) -> str:
    return "That one was practice. Kit up when you're ready."


def _config_changed(event: dict[str, Any]) -> str | None:
    if event.get("field") == "hunt_size":
        return None  # tilt narration is the game layer's call, after the fold
    return None


_HANDLERS = {
    "weather": _weather,
    "hunt_started": _hunt_started,
    "duck_scouted": _duck_scouted,
    "no_duck": _no_duck,
    "shot": _shot,
    "ghost_shot": _ghost_shot,
    "retrieve_plan": _retrieve_plan,
    "partial_retrieve": _partial_retrieve,
    "retrieved": _retrieved,
    "stopped": _stopped,
    "walked": _walked,
    "hunt_refused": _hunt_refused,
    "kitted_up": _kitted_up,
    "expedition_started": _expedition_started,
    "walked_out": _walked_out,
    "make_camp": _make_camp,
    "bust": _bust,
    "ghost_hunt": _ghost_hunt,
    "config_changed": _config_changed,
}


def recap(events: list[dict[str, Any]], expedition_id: int) -> str:
    """One expedition, told at the fire. Wins and busts get equal care."""
    shots = 0
    weather_states: set[str] = set()
    lines: list[str] = []
    outcome = ""
    in_expedition = False
    for event in events:
        etype = event.get("type")
        if etype == "expedition_started" and event.get("id") == expedition_id:
            in_expedition = True
            continue
        if not in_expedition:
            continue
        if etype == "weather":
            weather_states.add(str(event.get("state")))
        elif etype in ("shot", "ghost_shot"):
            shots += 1
        elif etype == "partial_retrieve":
            lines.append(f"half of ${event.get('symbol')} banked at "
                         f"+{event.get('gain_pct'):g}%")
        elif etype == "retrieved":
            lines.append(f"${event.get('symbol')} retrieved at "
                         f"+{event.get('gain_pct'):g}%")
        elif etype == "stopped":
            lines.append(f"the stop took ${event.get('symbol')}")
        elif etype == "walked":
            lines.append(f"${event.get('symbol')} walked at the whistle")
        elif etype == "walked_out":
            outcome = f"Out with {event.get('amount'):g} banked."
            break
        elif etype == "bust":
            outcome = "The marsh took the kit."
            break
        elif etype == "make_camp":
            outcome = "Made camp."
            break
    weather_note = "/".join(sorted(weather_states)) or "Calm"
    story = "; ".join(lines) if lines else "no shots landed a story"
    tail = f" {outcome}" if outcome else " Still afield."
    return (f"Expedition {expedition_id}. {shots} shot(s) in {weather_note} "
            f"weather. {story}.{tail} The marsh remembers.")
