"""The host runner must be able to find and drive this path.

For thirteen versions it could not. The SDK runner locates a strategy
by scanning the component module for a concrete Strategy subclass;
this pack had none, so the runner reported no live-shot mode and the
LiveExecutor -- correct, audited, five bugs fixed in it -- was
unreachable code that nothing could ever construct.

These tests exercise the runner's actual discovery rule against the
real base class, so the door cannot quietly close again.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys

import pytest

ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "the-marsh")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

Strategy = pytest.importorskip(
    "wayfinder_paths.core.strategies.Strategy").Strategy


def _component():
    import strategy
    return strategy


def test_the_runner_finds_exactly_one_strategy(): 
    """Mirrors find_strategy_class: concrete subclasses in this module."""
    module = _component()
    found = [
        obj for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Strategy)
        and obj is not Strategy
        and getattr(obj, "__module__", None) == module.__name__
    ]
    assert len(found) == 1, f"runner needs exactly one; found {found}"
    assert found[0].name == "the-marsh"


def test_every_abstract_method_is_implemented():
    """An abstract left over makes the class uninstantiable at runtime."""
    cls = _component().MarshStrategy
    missing = getattr(cls, "__abstractmethods__", frozenset())
    assert not missing, f"unimplemented: {sorted(missing)}"


def test_live_engine_refuses_without_a_signing_callback():
    """No trade authority means no executor, before any hunt begins."""
    cls = _component().MarshStrategy
    strat = cls({"strategy_wallet": {"address": "Satchel111"}},
                strategy_wallet_signing_callback=None)
    with pytest.raises(ValueError, match="signing callback"):
        strat._engine(live=True)


def test_live_engine_refuses_when_no_wallet_can_be_identified():
    cls = _component().MarshStrategy
    strat = cls({}, strategy_wallet_signing_callback=lambda tx: b"")
    with pytest.raises(ValueError, match="strategy_wallet"):
        strat._engine(live=True)


def _signer(address="SatchelWallet111", chain_type="solana"):
    """A callback shaped like the SDK's: it carries its own wallet."""
    async def sign(tx):
        return b""
    sign.wallet_address = address
    if chain_type is not None:
        sign.chain_type = chain_type
    return sign


def test_the_satchel_comes_from_the_signer_not_a_label(): 
    """Wallet labels are generated per install, so none may be assumed.

    get_strategy_config also fills strategy_wallet from the local
    config file only, so for anyone on Wayfinder-managed wallets that
    key is absent entirely. The signer knows which wallet it signs
    for; that is the answer.
    """
    cls = _component().MarshStrategy
    strat = cls({}, strategy_wallet_signing_callback=_signer(
        "ManagedSatchelAddr", "solana"))
    engine = strat._engine(live=True)
    assert engine.executor.satchel == "ManagedSatchelAddr"


def test_an_evm_leg_is_refused_rather_than_traded():
    """A ring shares one label across its EVM and Solana legs."""
    cls = _component().MarshStrategy
    strat = cls({}, strategy_wallet_signing_callback=_signer(
        "0xEvmLegAddress", "ethereum"))
    with pytest.raises(ValueError, match="hunts Solana"):
        strat._engine(live=True)


def test_an_explicit_config_address_still_works():
    cls = _component().MarshStrategy
    sign = _signer(address=None, chain_type=None)
    strat = cls({"strategy_wallet": {"address": "ConfiguredSatchel"}},
                strategy_wallet_signing_callback=sign)
    assert strat._engine(live=True).executor.satchel == "ConfiguredSatchel"


def test_the_main_wallet_never_reaches_the_executor():
    cls = _component().MarshStrategy
    strat = cls({"main_wallet": {"address": "MainWallet999"}},
                strategy_wallet_signing_callback=_signer("SatchelAddr"))
    engine = strat._engine(live=True)
    assert engine.executor.satchel == "SatchelAddr"
    assert "MainWallet999" not in repr(engine.executor.__dict__), \
        "the main wallet must never reach the executor"


def test_policies_ask_only_for_swap():
    """The satchel needs to swap. It never needs to transfer."""
    cls = _component().MarshStrategy
    assert asyncio.run(cls.policies()) == ["swap"]


def test_deposit_accepts_the_kwarg_the_runner_actually_sends():
    """run_strategy calls deposit(main_token_amount=..., gas_token_amount=...).

    Reading only "amount" made every deposit through the runner look
    like zero and get refused -- the first real command would have
    failed for a reason that had nothing to do with the hunter.
    """
    cls = _component().MarshStrategy
    strat = cls({"strategy_wallet": {"address": "Satchel111"}},
                strategy_wallet_signing_callback=lambda tx: b"")

    seen = {}

    class FakeEngine:
        bankroll_native = 0.0

        def kit_up(self, amount):
            seen["amount"] = amount

    strat._engine = lambda *, live: FakeEngine()

    ok, msg = asyncio.run(strat.deposit(main_token_amount=0.05,
                                        gas_token_amount=0.0))
    assert ok, msg
    assert seen["amount"] == 0.05

    ok, msg = asyncio.run(strat.deposit(main_token_amount=0.0))
    assert not ok and "Nothing to kit up" in msg
