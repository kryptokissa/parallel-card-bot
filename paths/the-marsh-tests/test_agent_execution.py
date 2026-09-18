"""Paths decide; the hunter's agent executes.

On Wayfinder a path holds no keys and is never handed a signer. The
agent has its own wallet and its own onchain_swap tool. So a live shot
is not something this pack performs — it is something it specifies,
exactly enough that the agent can run it without judgement of its own.

These pin the contract: the shot is fully specified, nothing here can
execute it, no position exists until a real transaction is named, and
the exits a rules-based hunt owes a position are produced even though
someone else does the selling.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from engine.config import MarshConfig
from engine.decision import (
    SOL_MINT,
    Decision,
    buy_decision,
    clear_pending,
    read_pending,
    write_pending,
)
from engine.events import EventLog
from engine.executor import QuoteOnlyExecutor
from engine.feed import FixtureFeed
from engine.hunt import HuntEngine
from engine.practice import load as load_marsh


class _Duck:
    token = "Mint111"
    symbol = "DUCK"
    chain = "solana"
    heat = 82.0
    biome = "the Pump Flats"
    price_usd = 0.0002


# -- the shot is specified, not taken ------------------------------------

def test_amount_always_carries_a_decimal_point():
    """onchain_swap rejects integer-looking amounts outright."""
    for size in (0.01, 0.1, 1.0, 10.0, 0.005):
        amount = buy_decision(_Duck(), size, 300).amount
        assert "." in amount, f"{size} rendered as {amount!r}"
        assert float(amount) == pytest.approx(size)


def test_the_buy_call_is_a_complete_onchain_swap():
    call = buy_decision(_Duck(), 0.01, 300).agent_call()
    assert call["tool"] == "onchain_swap"
    assert call["from_token"] == SOL_MINT
    assert call["to_token"] == "Mint111"
    assert call["amount"] == "0.01"
    assert call["slippage_bps"] == 300


def test_no_wallet_label_is_ever_assumed():
    """Labels are generated per install, so none may be hard-coded."""
    call = buy_decision(_Duck(), 0.01, 300).agent_call()
    assert call["wallet_label"].startswith("<"), \
        "the agent names its own wallet; this pack must not"


def test_a_sell_amount_is_a_visible_placeholder_not_an_empty_string():
    """An empty field reads like a bug and invites a guess."""
    from engine.decision import sell_decision

    class P:
        token, symbol, chain, position_id = "Mint111", "DUCK", "solana", "p1"

    call = sell_decision(P(), 0.5, "partial_retrieve", 300).agent_call()
    assert call["amount"].startswith("<") and "50%" in call["amount"]
    assert call["from_token"] == "Mint111" and call["to_token"] == SOL_MINT


def test_a_decision_goes_stale():
    """A stale quote turns a 3% shot into something else entirely."""
    d = buy_decision(_Duck(), 0.01, 300)
    assert not d.expired()
    d.created_at = "2020-01-01T00:00:00+00:00"
    assert d.expired()


# -- and cannot be executed here -----------------------------------------

def test_the_quote_only_executor_refuses_to_trade(tmp_path):
    feed = FixtureFeed(load_marsh("calm_day"))
    ex = QuoteOnlyExecutor(feed)
    for call in (ex.buy("Mint111", "solana", 0.01, 3.0),
                 ex.sell("Mint111", "solana", 1.0, 3.0)):
        fill = asyncio.run(call)
        assert not fill.ok
        assert "cannot execute" in fill.reason


def test_decide_only_opens_no_position(tmp_path):
    """A decision is a proposal. Only a real fill is a position."""
    log = EventLog(str(tmp_path / "e.jsonl"))
    feed = FixtureFeed(load_marsh("calm_day"))
    engine = HuntEngine(MarshConfig(), feed, QuoteOnlyExecutor(feed), log,
                        ghost=False)
    engine.kit_up(1.0)
    result = asyncio.run(engine.run_hunt(decide_only=True))

    assert not result.shot
    assert result.decision is not None
    assert not engine.positions, "no position may open before a fill"
    assert not [e for e in log.read() if e["type"] in ("shot", "ghost_shot")]
    assert [e for e in log.read() if e["type"] == "decision"]


def test_decide_only_still_honours_the_size_ceiling(tmp_path):
    log = EventLog(str(tmp_path / "e.jsonl"))
    feed = FixtureFeed(load_marsh("calm_day"))
    config = MarshConfig(hunt_size=0.02)
    engine = HuntEngine(config, feed, QuoteOnlyExecutor(feed), log, ghost=False)
    engine.kit_up(10.0)
    d = asyncio.run(engine.run_hunt(decide_only=True)).decision
    assert float(d.amount) <= config.hunt_size


# -- the exits still belong to the hunt ----------------------------------

def test_exits_are_decided_even_though_someone_else_sells(tmp_path):
    """A position opened through an agent still owes a stop."""
    log = EventLog(str(tmp_path / "e.jsonl"))
    feed = FixtureFeed(load_marsh("storm_bust"))
    engine = HuntEngine(MarshConfig(), feed, QuoteOnlyExecutor(feed), log,
                        ghost=False)
    engine.kit_up(1.0)
    d = asyncio.run(engine.run_hunt(decide_only=True)).decision

    # record a fill far above the fixture's next price -> stop fires
    from engine.hunt import Position
    from engine.events import utcnow
    engine.positions["p1"] = Position(
        position_id="p1", token=d.token, symbol=d.symbol, chain="solana",
        entry_price_usd=1_000_000.0, size_native=float(d.amount),
        opened_at=utcnow(), graduated=False, weather="Calm")

    exits = asyncio.run(engine.decide_exits())
    assert exits, "a position deep underwater must produce a stop"
    assert exits[0].reason == "stopped"
    assert exits[0].sell_fraction == 1.0
    assert exits[0].from_token == d.token and exits[0].to_token == SOL_MINT


# -- the pending handshake ------------------------------------------------

def test_pending_round_trips_and_clears(tmp_path):
    log_path = str(tmp_path / "e.jsonl")
    d = buy_decision(_Duck(), 0.01, 300, hunt_id="h1")
    write_pending(log_path, d)
    back = read_pending(log_path)
    assert back is not None and back.token == d.token and back.amount == d.amount
    clear_pending(log_path)
    assert read_pending(log_path) is None


def test_unreadable_pending_is_not_a_crash(tmp_path):
    log_path = str(tmp_path / "e.jsonl")
    from engine.decision import pending_path
    with open(pending_path(log_path), "w", encoding="utf-8") as fh:
        fh.write("{ not json")
    assert read_pending(log_path) is None
