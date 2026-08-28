"""Shared scenario driver: run a scripted day through the engine."""

from __future__ import annotations

import asyncio
import os

from engine.config import MarshConfig
from engine.events import EventLog
from engine.executor import SimExecutor
from engine.feed import FixtureFeed
from engine.hunt import HuntEngine

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def run_day(tmp_path, fixture_name: str, *, kit: float = 0.3,
            plan: tuple[str, ...] = ("hunt", "whistle", "whistle"),
            config: MarshConfig | None = None, ghost: bool = False):
    """Execute a scripted sequence of "hunt" / "whistle" steps."""

    async def _run():
        log = EventLog(str(tmp_path / "events.jsonl"))
        feed = FixtureFeed(os.path.join(FIXTURES, fixture_name))
        engine = HuntEngine(config or MarshConfig(), feed, SimExecutor(feed),
                            log, ghost=ghost)
        engine.kit_up(kit)
        for step in plan:
            if step == "hunt":
                await engine.run_hunt()
            elif step == "whistle":
                await engine.check_positions()
        return engine, log.read()

    return asyncio.run(_run())
