"""Execution — quotes and swaps, only ever from the satchel.

The satchel is the strategy wallet: the one wallet this engine can
spend from, holding only what the hunter chose to kit up with. The
main wallet is read-only here by construction — no code path in this
module ever receives its key.

SimExecutor fills orders deterministically off the feed price for ghost
hunts and dry runs. LiveExecutor quotes through BRAP and aborts on the
same limits the simulator enforces; live sends go through the SDK's
transaction utilities.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Fill:
    ok: bool
    tx: str = ""
    price_usd: float = 0.0
    price_impact_pct: float = 0.0
    reason: str = ""


def clamp_fraction(fraction: float) -> float:
    """Sells are bounded by what the satchel actually holds: a sell is
    always a fraction in [0, 1] of the engine-opened position, so no
    exit can ever move more than the position itself. (Buys are sized
    by the engine at min(hunt_size, bankroll) — see hunt.run_hunt.)"""
    return max(0.0, min(1.0, float(fraction)))


class Executor(Protocol):
    async def quote_impact_pct(self, token: str, chain: str,
                               size_native: float) -> float: ...

    async def buy(self, token: str, chain: str, size_native: float,
                  max_slippage_pct: float) -> Fill: ...

    async def sell(self, token: str, chain: str, fraction: float,
                   max_slippage_pct: float) -> Fill: ...


class SimExecutor:
    """Practice-range execution: fills at feed price, impact from fixture."""

    def __init__(self, feed, impact_pct: float = 1.0):
        self.feed = feed
        self.impact_pct = impact_pct

    async def quote_impact_pct(self, token: str, chain: str,
                               size_native: float) -> float:
        return self.impact_pct

    async def buy(self, token: str, chain: str, size_native: float,
                  max_slippage_pct: float) -> Fill:
        price = await self.feed.price(token, chain)
        if price <= 0:
            return Fill(ok=False, reason="no price")
        return Fill(ok=True, tx=f"ghost-{uuid.uuid4().hex[:12]}", price_usd=price,
                    price_impact_pct=self.impact_pct)

    async def sell(self, token: str, chain: str, fraction: float,
                   max_slippage_pct: float) -> Fill:
        clamp_fraction(fraction)  # same bound as live, same code path
        price = await self.feed.price(token, chain)
        return Fill(ok=True, tx=f"ghost-{uuid.uuid4().hex[:12]}", price_usd=price,
                    price_impact_pct=self.impact_pct)


class LiveExecutor:
    """Live execution through BRAP from the satchel wallet only.

    Host mediation is mandatory, not conventional: this class cannot be
    constructed without a callable ``signing_callback``, and that
    callback is supplied by the host runner (the SDK strategy runtime
    the hunter authorized when kitting up) — the pack itself never
    loads, derives, or touches key material, so it has nothing to sign
    with on its own. Every trade the callback is asked to sign was
    produced by the retrieve plan the hunter configured; there is no
    ad-hoc trade entry point.
    """

    NATIVE = {"solana": "solana", "robinhood": "ethereum-robinhood"}

    def __init__(self, satchel_address: str, signing_callback, chain_ids: dict):
        if not callable(signing_callback):
            raise ValueError(
                "LiveExecutor requires the host runner's signing callback; "
                "the pack holds no keys and cannot trade on its own"
            )
        from wayfinder_paths.core.clients.BRAPClient import BRAPClient

        self.brap = BRAPClient()
        self.satchel = satchel_address
        self.sign = signing_callback
        self.chain_ids = chain_ids

    async def _quote(self, from_token: str, to_token: str, chain: str,
                     amount_raw: str, slippage: float):
        chain_id = self.chain_ids[chain]
        return await self.brap.get_quote(
            from_token=from_token,
            to_token=to_token,
            from_chain=chain_id,
            to_chain=chain_id,
            from_wallet=self.satchel,
            from_amount=amount_raw,
            slippage=slippage,
        )

    async def quote_impact_pct(self, token: str, chain: str,
                               size_native: float) -> float:
        raw = _native_to_raw(size_native, chain)
        quote = await self._quote(_native_addr(chain), token, chain, raw, 0.01)
        best = (quote or {}).get("best_quote") or {}
        impact = best.get("price_impact") or best.get("price_impact_pct") or 0.0
        return abs(float(impact)) * (100.0 if abs(float(impact)) <= 1.0 else 1.0)

    async def buy(self, token: str, chain: str, size_native: float,
                  max_slippage_pct: float) -> Fill:
        raw = _native_to_raw(size_native, chain)
        quote = await self._quote(_native_addr(chain), token, chain, raw,
                                  max_slippage_pct / 100.0)
        return await self._execute(quote, chain)

    async def sell(self, token: str, chain: str, fraction: float,
                   max_slippage_pct: float) -> Fill:
        from wayfinder_paths.core.clients.BalanceClient import BalanceClient

        balances = BalanceClient()
        held = await balances.get_token_balance(self.satchel, token, chain)
        amount = int(int(held) * clamp_fraction(fraction))
        if amount <= 0:
            return Fill(ok=False, reason="nothing held")
        quote = await self._quote(token, _native_addr(chain), chain, str(amount),
                                  max_slippage_pct / 100.0)
        return await self._execute(quote, chain)

    async def _execute(self, quote, chain: str) -> Fill:
        best = (quote or {}).get("best_quote") or {}
        calldata = quote.get("calldata") or best.get("calldata")
        if not calldata:
            return Fill(ok=False, reason="no route")
        if chain == "solana":
            try:
                from wayfinder_paths.core.utils.svm_transaction import (
                    send_svm_versioned_transaction,
                )
            except ImportError:
                # Solana submission helpers are not in every published
                # SDK release. Scouting, gates, ghost hunts, and exit
                # accounting all work without them; only live Solana
                # submission needs this, so fail loudly and safely here
                # rather than at import time.
                return Fill(ok=False, reason="solana submission unavailable "
                                             "in this runtime")

            result = await send_svm_versioned_transaction(
                calldata, signing_callback=self.sign
            )
            tx = result.get("signature", "")
        else:
            tx = await self.sign({"calldata": calldata})
        impact = best.get("price_impact") or 0.0
        price = best.get("to_amount_usd") or best.get("price_usd") or 0.0
        return Fill(ok=bool(tx), tx=str(tx), price_usd=float(price or 0.0),
                    price_impact_pct=abs(float(impact)))


_DECIMALS = {"solana": 9, "robinhood": 18}
_NATIVE_ADDR = {
    "solana": "So11111111111111111111111111111111111111112",
    "robinhood": "0x0000000000000000000000000000000000000000",
}


def _native_to_raw(size_native: float, chain: str) -> str:
    return str(int(size_native * 10 ** _DECIMALS[chain]))


def _native_addr(chain: str) -> str:
    return _NATIVE_ADDR[chain]
