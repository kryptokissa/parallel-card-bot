from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from game import marsh_engine  # noqa: E402

LOG_ENV = "MARSH_EVENT_LOG"
DEFAULT_LOG = os.path.join(_HERE, ".wayfinder_runs", "marsh_events.jsonl")


def _events() -> list[dict]:
    log_path = os.environ.get(LOG_ENV, DEFAULT_LOG)
    if not os.path.exists(log_path):
        return []
    return marsh_engine.load_events(log_path)


def wfpath_meta() -> dict:
    return {
        "name": "The Marsh 🦆",
        "kind": "strategy",
        "ui_mode": "auto",
        "tracking_mode": "hybrid",
    }


def wfpath_state() -> dict:
    events = _events()
    state = marsh_engine.replay(events)
    open_positions = state.expedition.get("open_positions", []) \
        if state.expedition.get("active") else []
    return {
        "status": "afield" if open_positions else "by the fire",
        "selection": {"open_positions": open_positions},
        "metrics": {
            "level": state.level,
            "xp": state.xp,
            "hunter_rating": state.hunter_rating,
            "discipline_streak": state.discipline_streak,
            "trophies": len(state.trophies),
        },
        "positions": open_positions,
        "game": state.to_dict(),
    }


def wfpath_decision() -> dict:
    events = _events()
    last_hunt: dict = {}
    for event in events:
        if event.get("type") in ("shot", "ghost_shot", "no_duck",
                                 "hunt_refused"):
            last_hunt = event
    if not last_hunt:
        return {
            "summary": "No hunts yet. The dog is by the fire, waiting on 'go now'.",
            "selected": {},
            "candidates": [],
        }
    if last_hunt.get("type") in ("shot", "ghost_shot"):
        summary = (f"Last hunt: took ${last_hunt.get('symbol')} in "
                   f"{last_hunt.get('biome')} — it passed every gate at the "
                   f"highest heat.")
        selected = {"token": last_hunt.get("token"),
                    "symbol": last_hunt.get("symbol")}
    else:
        failures = last_hunt.get("failures", {})
        detail = ", ".join(f"{c} {n}" for n, c in failures.items())
        detail = detail or "nothing worth wading in for"
        summary = (f"Last hunt: dog wouldn't fetch. "
                   f"{last_hunt.get('scouted', 0)} scouted: {detail}.")
        selected = {}
    candidates = [
        {"symbol": e.get("symbol"), "heat": e.get("heat"),
         "verdict": e.get("verdict"), "gate": e.get("gate_name")}
        for e in events
        if e.get("type") == "duck_scouted"
        and e.get("hunt_id") == last_hunt.get("hunt_id")
    ]
    return {"summary": summary, "selected": selected, "candidates": candidates}


# -- component entry point --------------------------------------------------
#
# The host runs this file: `wayfinder path exec --component main -- <args>`,
# which is what `scripts/wf_run.py` ultimately calls. Without a __main__
# block the component defines functions and exits silently, and the host
# correctly reports that no path code ran. Everything below routes the
# host's arguments into the same commands the CLI uses, so there is one
# implementation and two doors into it.

def _run_cli(argv: list[str]) -> None:
    scripts_dir = os.path.join(_HERE, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import marsh_run  # noqa: PLC0415

    saved = sys.argv
    try:
        sys.argv = ["marsh_run", *argv]
        marsh_run.main()
    finally:
        sys.argv = saved


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # No arguments: report the path's contract for the page.
        print(json.dumps(
            {"meta": wfpath_meta(), "state": wfpath_state(),
             "decision": wfpath_decision()},
            indent=2, default=str,
        ))
        return
    if argv[0] in ("go", "go-now", "ape"):
        argv[0] = "hunt"  # the trigger words the dog answers to
    _run_cli(argv)


if __name__ == "__main__":
    main()
