"""A slow marsh is not a crashed hunt.

Safety facts are read over the network. When that read times out we
have learned nothing about the duck, and nothing is not a pass: the
duck is refused and the hunt carries on down the shortlist. Losing one
lookup must never lose the trip.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from engine.config import MarshConfig
from engine.events import EventLog
from engine.executor import SimExecutor
from engine.feed import FixtureFeed, WayfinderFeed
from engine.hunt import HuntEngine
from engine.practice import load as load_marsh


class TimingOutFeed(FixtureFeed):
    """A fixture marsh whose safety checks never come back."""

    safety_is_free = False

    async def safety_check(self, duck):
        raise httpx.ReadTimeout("safety check timed out")


def test_timeout_refuses_the_duck_not_the_hunt(tmp_path):
    log = EventLog(str(tmp_path / "events.jsonl"))
    feed = TimingOutFeed(load_marsh("calm_day"))
    engine = HuntEngine(MarshConfig(), feed, SimExecutor(feed), log, ghost=True)
    engine.kit_up(1.0)

    result = asyncio.run(engine.run_hunt())   # must not raise

    assert not result.shot
    refused = [e for e in log.read() if e.get("type") == "duck_scouted"
               and e.get("gate") == "safety_unchecked"]
    assert refused, "an unverifiable duck must be refused on its own gate"
    assert refused[0]["gate_name"] == "couldn't get a good look"
    assert [e for e in log.read() if e.get("type") == "no_duck"]


def test_daily_limit_untouched_by_a_timed_out_trip(tmp_path):
    """The trip fired no shot, so it must not spend the ration."""
    log = EventLog(str(tmp_path / "events.jsonl"))
    feed = TimingOutFeed(load_marsh("calm_day"))
    engine = HuntEngine(MarshConfig(), feed, SimExecutor(feed), log, ghost=True)
    engine.kit_up(1.0)
    asyncio.run(engine.run_hunt())
    assert engine.hunts_today == []


def test_send_retries_once_then_gives_up(monkeypatch):
    """A 5xx is retried once; a second failure is raised, not swallowed."""
    calls: list[str] = []

    class FakeResponse:
        status_code = 500
        request = httpx.Request("GET", "https://example.invalid")

        def raise_for_status(self):
            raise httpx.HTTPStatusError("500", request=self.request,
                                        response=self)  # type: ignore[arg-type]

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def request(self, method, url, **kwargs):
            calls.append(method)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(WayfinderFeed()._send("GET", "https://example.invalid"))
    assert len(calls) == 2, "one retry, then surface the failure"
