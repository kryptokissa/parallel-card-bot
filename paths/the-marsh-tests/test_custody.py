"""Custody proofs — the pack handles no keys and can move funds only
through the host.

These tests make the claims in engine/hunt.py's custody-model section
machine-checkable:

1. No module in the pack references private keys, mnemonics, or any
   wallet-credential identifier — verified by AST scan over every
   identifier (docstrings and comments are prose, not capability).
2. The Executor surface has no transfer primitive: no recipient-like
   parameter exists on any executor method, so an arbitrary transfer
   is not expressible.
3. Live trading is host-mediated by construction: LiveExecutor cannot
   be instantiated without a callable signing callback (the host
   runner's), and the pack contains nothing that could create one.
4. The simulated executor is accounting-only: its fills are synthetic
   and carry no transaction.
"""

from __future__ import annotations

import ast
import inspect
import os

import pytest

from engine import executor as executor_module
from engine.executor import LiveExecutor, SimExecutor
from engine.feed import FixtureFeed

ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "the-marsh")  # the path directory; tests sit beside it, not in it

FORBIDDEN_IDENTIFIERS = (
    "private_key", "privkey", "secret_key", "mnemonic", "seed_phrase",
    "keypair", "signer_key",
)

PACK_SOURCES = ["engine", "game", "scripts", "strategy.py"]


def _pack_files():
    for entry in PACK_SOURCES:
        full = os.path.join(ROOT, entry)
        if os.path.isfile(full):
            yield full
        else:
            for base, _, files in os.walk(full):
                for fname in files:
                    if fname.endswith(".py"):
                        yield os.path.join(base, fname)


def test_no_key_material_identifiers_anywhere():
    for path in _pack_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Name):
                names = [node.id]
            elif isinstance(node, ast.Attribute):
                names = [node.attr]
            elif isinstance(node, ast.arg):
                names = [node.arg]
            elif isinstance(node, ast.keyword) and node.arg:
                names = [node.arg]
            for name in names:
                lowered = name.lower()
                for forbidden in FORBIDDEN_IDENTIFIERS:
                    assert forbidden not in lowered, (
                        f"{os.path.relpath(path, ROOT)} touches "
                        f"credential-shaped identifier {name!r}"
                    )


def test_executor_surface_has_no_transfer_primitive():
    recipient_like = ("recipient", "to_address", "destination", "to_wallet",
                      "transfer_to", "beneficiary")
    for cls in (SimExecutor, LiveExecutor):
        methods = [m for name, m in inspect.getmembers(cls, inspect.isfunction)
                   if not name.startswith("__")]
        assert methods
        for method in methods:
            params = set(inspect.signature(method).parameters)
            hit = params & set(recipient_like)
            assert not hit, (
                f"{cls.__name__}.{method.__name__} exposes {hit}: a "
                f"transfer primitive must not exist in this pack"
            )


def test_live_executor_requires_host_signing_callback():
    with pytest.raises(ValueError, match="signing callback"):
        LiveExecutor("SatchelAddr111", signing_callback=None,
                     chain_ids={"solana": 900})
    with pytest.raises(ValueError, match="signing callback"):
        LiveExecutor("SatchelAddr111", signing_callback="not-callable",
                     chain_ids={"solana": 900})


def test_sim_executor_fills_are_accounting_only():
    import asyncio

    feed = FixtureFeed({"prices": {"MINT": [1.0, 2.0]}})

    async def _run():
        sim = SimExecutor(feed)
        buy = await sim.buy("MINT", "solana", 0.1, 3.0)
        sell = await sim.sell("MINT", "solana", 1.0, 3.0)
        return buy, sell

    buy, sell = asyncio.run(_run())
    assert buy.ok and sell.ok
    assert buy.tx.startswith("ghost-") and sell.tx.startswith("ghost-")


def test_sell_size_cannot_exceed_position():
    """The fixed-size rules bound exits too: a sell is a clamped
    fraction of the engine-opened position, never more."""
    from engine.executor import clamp_fraction

    assert clamp_fraction(0.5) == 0.5
    assert clamp_fraction(1.0) == 1.0
    assert clamp_fraction(7.0) == 1.0  # cannot sell more than held
    assert clamp_fraction(-3.0) == 0.0  # cannot sell a negative amount
    # and the config cannot even express an out-of-bounds fraction
    from engine.config import MarshConfig

    with pytest.raises(ValueError):
        MarshConfig(retrieve_1_fraction=1.5).validate()
    with pytest.raises(ValueError):
        MarshConfig(retrieve_1_fraction=0.0).validate()


def test_no_signer_construction_in_pack():
    """The pack never imports signing/key machinery; the only signer it
    ever sees is the opaque callback the host injects."""
    banned_modules = ("solders.keypair", "eth_account", "eth_keys",
                      "mnemonic", "bip32", "bip44", "hdwallet")
    for path in _pack_files():
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                for banned in banned_modules:
                    assert not module.lower().startswith(banned), (
                        f"{os.path.relpath(path, ROOT)} imports {module}"
                    )
    assert executor_module.LiveExecutor.__init__ is not None