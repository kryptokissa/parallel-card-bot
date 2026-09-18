"""The trenches feed is `active`, not `trending`.

`trending` lists established tokens — ages in months and years — so
every duck on it fails the age window. When it was preferred, scouting
saw no launches at all and the hunt could never fire, while reporting a
healthy-looking scouting log. These tests pin the order.
"""

from __future__ import annotations

import asyncio

from engine.feed import WayfinderFeed

TRENCH_ROW = {
    "address": "Mint1111111111111111111111111111111111111111",
    "symbol": "DUCK", "chain_code": "solana", "launchpad": "pumpfun",
    "liquidity_usd": 120_000.0, "buyers_h1": 900,
    "volume_h1_usd": 800_000.0, "price_change_h1_pct": 30.0,
    "price_change_m5_pct": 4.0, "pool_created_at": "2026-09-04T00:00:00Z",
}


def _feed_recording(calls: list[str], rows_for: dict[str, list[dict]]):
    feed = WayfinderFeed()

    async def _discover(chain: str, dimension: str, limit: int):
        calls.append(dimension)
        return rows_for.get(dimension, [])

    feed._discover = _discover  # type: ignore[method-assign]
    return feed


def test_scout_asks_active_first():
    calls: list[str] = []
    feed = _feed_recording(calls, {"active": [TRENCH_ROW],
                                   "trending": [dict(TRENCH_ROW, symbol="OLD")]})
    ducks = asyncio.run(feed.scout("solana", limit=25))
    assert calls == ["active"], "trending must not be scouted while active has rows"
    assert [d.symbol for d in ducks] == ["DUCK"]


def test_scout_falls_back_to_trending_when_active_is_dark():
    calls: list[str] = []
    feed = _feed_recording(calls, {"active": [], "trending": [TRENCH_ROW]})
    ducks = asyncio.run(feed.scout("solana", limit=25))
    assert calls == ["active", "trending"]
    assert len(ducks) == 1
