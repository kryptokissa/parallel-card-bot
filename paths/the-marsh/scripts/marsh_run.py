#!/usr/bin/env python3
"""Run The Marsh from a terminal: hunt, whistle, recap, state.

Ghost mode needs nothing but a fixture. Live mode needs a configured
Wayfinder API key and a satchel wallet in config.json; without them it
refuses politely rather than guessing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.config import MarshConfig  # noqa: E402
from engine.events import EventLog  # noqa: E402
from engine.executor import SimExecutor  # noqa: E402
from engine.feed import FixtureFeed  # noqa: E402
from engine.hunt import HuntEngine  # noqa: E402
from engine.narrator import narrate, recap as tell_recap  # noqa: E402
from game import marsh_engine  # noqa: E402

DEFAULT_LOG = os.path.join(ROOT, ".wayfinder_runs", "marsh_events.jsonl")
GHOST_LOG = os.path.join(ROOT, ".wayfinder_runs", "ghost_events.jsonl")


def _log_path(ghost: bool) -> str:
    return os.environ.get("MARSH_EVENT_LOG", GHOST_LOG if ghost else DEFAULT_LOG)


def _print_events(events: list[dict]) -> None:
    for event in events:
        line = narrate(event)
        if line:
            print(f"  🐕 {line}")


async def _build_engine(args) -> HuntEngine:
    log = EventLog(_log_path(args.ghost))
    config = MarshConfig()
    if args.ghost or args.fixture:
        fixture = args.fixture or os.path.join(ROOT, "tests", "fixtures",
                                               "calm_day.json")
        feed = FixtureFeed(fixture,
                           cursor_file=_log_path(args.ghost) + ".cursor")
        executor = SimExecutor(feed)
        engine = HuntEngine(config, feed, executor, log, ghost=args.ghost)
        engine.restore_from_log()
        if args.size is not None:
            # A spoken size is a config change: logged, so raising it
            # right after a stop shows up as what it is (tilt).
            engine.update_config(hunt_size=args.size)
        if engine.bankroll_native <= 0:
            engine.kit_up(args.kit if args.kit else config.hunt_size * 3)
        return engine
    from engine.executor import LiveExecutor  # noqa: PLC0415
    from engine.feed import WayfinderFeed  # noqa: PLC0415

    satchel = os.environ.get("MARSH_SATCHEL_ADDRESS")
    if not satchel:
        print("No satchel configured (MARSH_SATCHEL_ADDRESS). "
              "Kit up first, or run --ghost for the practice range.")
        raise SystemExit(1)
    feed = WayfinderFeed()
    executor = LiveExecutor(satchel, signing_callback=None,
                            chain_ids={"solana": 792703809})
    engine = HuntEngine(config, feed, executor, log)
    engine.restore_from_log()
    if args.size is not None:
        engine.update_config(hunt_size=args.size)
    return engine


async def cmd_hunt(args) -> None:
    engine = await _build_engine(args)
    before = len(engine.log.read())
    result = await engine.run_hunt()
    _print_events(engine.log.read()[before:])
    if not result.shot and result.refusal_reason:
        pass  # the narration above already told it straight


async def cmd_whistle(args) -> None:
    engine = await _build_engine(args)
    before = len(engine.log.read())
    await engine.check_positions()
    _print_events(engine.log.read()[before:])


def cmd_recap(args) -> None:
    events = marsh_engine.load_events(_log_path(args.ghost))
    print(f"  🐕 {tell_recap(events, args.expedition)}")


def cmd_state(args) -> None:
    events = marsh_engine.load_events(_log_path(args.ghost))
    state = marsh_engine.replay(events)
    print(json.dumps(state.to_dict(), indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(prog="marsh_run")
    parser.add_argument("command",
                        choices=["hunt", "whistle", "recap", "state"])
    parser.add_argument("--ghost", action="store_true",
                        help="practice range: fixture feed, no funds")
    parser.add_argument("--fixture", default=None)
    parser.add_argument("--size", type=float, default=None,
                        help="shot size for this hunt (capped by config)")
    parser.add_argument("--kit", type=float, default=None,
                        help="ghost-mode satchel size")
    parser.add_argument("--expedition", type=int, default=1)
    args = parser.parse_args()

    if args.command == "hunt":
        asyncio.run(cmd_hunt(args))
    elif args.command == "whistle":
        asyncio.run(cmd_whistle(args))
    elif args.command == "recap":
        cmd_recap(args)
    elif args.command == "state":
        cmd_state(args)


if __name__ == "__main__":
    main()
