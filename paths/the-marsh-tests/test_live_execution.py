"""The live path, against the shape the API really returns.

Every case here is pinned to brap_quote_sample.json, captured verbatim
from a live BRAP quote. The live executor was originally written
against guessed field names, and the failures were all silent:

- impact read `price_impact` on the wrong object, so it resolved to 0
  and the max_price_impact gate admitted any route, however deep
- price read `to_amount_usd`, which does not exist, so entry price came
  out 0 -- and check_positions skips a position whose entry price is
  not positive, which would have left a real position with no stop
  loss, no retrieve levels and no time stop, silently, forever
- the Solana sender was handed the calldata dict where it wants a
  decoded VersionedTransaction, under the wrong keyword name

None of that shows up in a ghost hunt, which is why it survived.
"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from engine.config import MarshConfig
from engine.events import EventLog
from engine.executor import (
    Fill,
    LiveExecutor,
    best_quote,
    clamp_fraction,
    quote_impact_pct_from,
    solana_submission_available,
    unit_price_usd_from,
)
from engine.feed import FixtureFeed
from engine.hunt import HuntEngine
from engine.practice import load as load_marsh

SAMPLE = json.load(open(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "brap_quote_sample.json"), encoding="utf-8"))
BEST = best_quote(SAMPLE)


# -- reading a real quote ------------------------------------------------

def test_impact_is_a_percentage_not_a_fraction():
    """priceImpact is already a percentage; priceImpactPct is the fraction.

    The names invite exactly the wrong reading, and getting it wrong is
    a hundredfold error in the gate that decides whether a shot is too
    deep to take.
    """
    assert quote_impact_pct_from(BEST) == pytest.approx(1.1065755, rel=1e-6)


def test_impact_falls_back_to_the_fraction_field():
    best = {"quote": {"priceImpactPct": "-0.025"}}
    assert quote_impact_pct_from(best) == pytest.approx(2.5)


def test_missing_impact_is_never_reported_as_zero():
    """No impact in the quote must not read as a shallow route."""
    assert quote_impact_pct_from({"quote": {}}) == float("inf")
    assert quote_impact_pct_from({}) == float("inf")


def test_price_is_per_token_and_reconciles_with_the_totals():
    """A unit price, cross-checked against amount and total USD."""
    price = unit_price_usd_from(BEST)
    assert price == pytest.approx(0.0002147186161, rel=1e-9)

    units = BEST["output_amount"] / (10 ** BEST["output_validation"]["decimals"])
    derived = BEST["output_amount_usd"] / units
    assert price == pytest.approx(derived, rel=1e-6)
    # and emphatically not the swap total, which is ~4700x larger
    assert price != pytest.approx(BEST["output_amount_usd"])


def test_price_derives_when_validation_omits_it():
    best = {"output_amount": 4697759284, "output_amount_usd": 1.0086963722314068,
            "output_validation": {"decimals": 6, "price_usd": 0}}
    assert unit_price_usd_from(best) == pytest.approx(0.000214718, rel=1e-5)


def test_unknown_price_is_zero_not_a_guess():
    assert unit_price_usd_from({}) == 0.0


# -- the gate that uses them --------------------------------------------

def test_a_deep_route_is_refused_by_the_impact_gate(tmp_path):
    """End to end: a 40% impact route must not become a shot."""

    class DeepExecutor:
        async def quote_impact_pct(self, token, chain, size):
            return quote_impact_pct_from({"quote": {"priceImpact": -40.0}})

        async def buy(self, *a, **k):
            raise AssertionError("must never buy through a 40% impact route")

        async def sell(self, *a, **k):
            raise AssertionError("must never sell here")

    log = EventLog(str(tmp_path / "events.jsonl"))
    feed = FixtureFeed(load_marsh("calm_day"))
    engine = HuntEngine(MarshConfig(), feed, DeepExecutor(), log, ghost=False)
    engine.kit_up(1.0)
    result = asyncio.run(engine.run_hunt())
    assert not result.shot
    assert result.refusal_reason == "price impact too deep"


def test_a_zero_entry_price_would_disable_every_exit(tmp_path):
    """Guards the reason the price bug mattered.

    check_positions skips a position whose entry price is not positive.
    If a fill ever reports 0 again, this is the damage: no stop, no
    retrieve, no time stop.
    """
    log = EventLog(str(tmp_path / "events.jsonl"))
    feed = FixtureFeed(load_marsh("calm_day"))

    class ZeroPriceExecutor:
        async def quote_impact_pct(self, token, chain, size):
            return 0.5

        async def buy(self, token, chain, size, slippage):
            return Fill(ok=True, tx="t", price_usd=0.0, price_impact_pct=0.5)

        async def sell(self, *a, **k):
            raise AssertionError("unreachable in this test")

    engine = HuntEngine(MarshConfig(), feed, ZeroPriceExecutor(), log, ghost=False)
    engine.kit_up(1.0)
    assert asyncio.run(engine.run_hunt()).shot
    events = asyncio.run(engine.check_positions())
    assert events == [], "a zero entry price silently disables the retrieve plan"


# -- custody and submission ---------------------------------------------

def test_no_signer_means_no_executor():
    with pytest.raises(ValueError, match="signing callback"):
        LiveExecutor("SatchelAddress", None, {"solana": 900})


def test_live_buy_refuses_before_quoting_when_submission_is_missing(monkeypatch):
    """0.11.0 ships no svm modules, so a live SOL buy cannot broadcast.

    It must say so before spending a quote, not after -- and certainly
    not after a hunter has funded a satchel expecting a trade.
    """
    import engine.executor as ex_mod

    monkeypatch.setattr(ex_mod, "solana_submission_available",
                        lambda: (False, "solana submission unavailable in "
                                        "this runtime (no svm modules)"))

    class ExplodingBrap:
        async def get_quote(self, **kwargs):
            raise AssertionError("must not quote when it cannot broadcast")

    ex = LiveExecutor.__new__(LiveExecutor)
    ex.brap = ExplodingBrap()
    ex.satchel = "SatchelAddress"
    ex.sign = lambda tx: b""
    ex.chain_ids = {"solana": 900}

    fill = asyncio.run(ex.buy("Mint", "solana", 0.1, 3.0))
    assert not fill.ok
    assert "solana submission unavailable" in fill.reason


def test_submission_probe_reports_the_published_runtime_honestly():
    """The probe must answer from the runtime, not from optimism."""
    available, reason = solana_submission_available()
    assert isinstance(available, bool)
    if not available:
        assert reason, "an unavailable runtime must say why"
    else:
        assert reason == ""


def test_a_flagged_route_is_refused():
    ex = LiveExecutor.__new__(LiveExecutor)
    ex.chain_ids = {"solana": 900}
    ex.sign = lambda tx: b""
    quote = {"best_quote": {"safety_warnings": ["honeypot"],
                            "calldata": {"serializedTransaction": "x"}}}
    fill = asyncio.run(ex._execute(quote, "solana"))
    assert not fill.ok and "flagged" in fill.reason


def test_sells_are_bounded_to_the_position():
    for asked, expected in ((1.5, 1.0), (-0.2, 0.0), (0.5, 0.5)):
        assert clamp_fraction(asked) == expected
