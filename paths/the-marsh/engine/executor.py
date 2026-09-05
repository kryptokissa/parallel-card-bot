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


def best_quote(quote: dict | None) -> dict:
    """The chosen route out of a BRAP quote envelope."""
    if not isinstance(quote, dict):
        return {}
    best = quote.get("best_quote")
    if isinstance(best, dict):
        return best
    routes = quote.get("quotes") or quote.get("all_quotes") or []
    return routes[0] if routes and isinstance(routes[0], dict) else {}


def quote_impact_pct_from(best: dict) -> float:
    """Price impact as a positive percentage.

    BRAP reports impact on the provider quote, and the two field names
    are the opposite way round from what they look like: ``priceImpact``
    is already a percentage (-1.1065 means 1.1%), while
    ``priceImpactPct`` is a fraction ("-0.011065"). Both are signed.
    Reading either as the other is a factor of a hundred, so this reads
    the percentage field first and only scales the fraction.
    """
    inner = best.get("quote") if isinstance(best.get("quote"), dict) else {}
    raw = inner.get("priceImpact")
    if raw is not None:
        try:
            return abs(float(raw))
        except (TypeError, ValueError):
            pass
    for key, scale in (("priceImpactPct", 100.0), ("price_impact", 100.0)):
        raw = inner.get(key, best.get(key))
        if raw is None:
            continue
        try:
            return abs(float(raw)) * scale
        except (TypeError, ValueError):
            continue
    # No impact in the quote means we cannot show the shot is shallow
    # enough. Report something no sane max_price_impact will admit
    # rather than a reassuring zero.
    return float("inf")


def unit_price_usd_from(best: dict) -> float:
    """USD price of ONE token, matching the feed's price scale.

    The engine measures gains as price / entry_price - 1 against
    feed.price(), which is a per-token price. A total swap value here
    would make every stop and retrieve level meaningless, so this
    returns a unit price or zero, never a total.
    """
    validation = best.get("output_validation")
    if isinstance(validation, dict):
        try:
            price = float(validation.get("price_usd") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        if price > 0:
            return price
        # Fall back to deriving it, which also cross-checks the above.
        try:
            decimals = int(validation.get("decimals"))
            amount = int(best.get("output_amount"))
            usd = float(best.get("output_amount_usd"))
            units = amount / (10 ** decimals)
            if units > 0 and usd > 0:
                return usd / units
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return 0.0


def solana_submission_available() -> tuple[bool, str]:
    """Whether this runtime can broadcast a Solana transaction.

    Submission needs the SDK's svm helpers, which are absent from the
    published 0.11.0 wheel — it ships no svm modules at all. Checking
    up front means a live hunt refuses before it quotes, instead of
    discovering it after the hunter has funded a satchel.
    """
    try:
        from wayfinder_paths.core.utils.svm_transaction import (  # noqa: F401
            send_svm_versioned_transaction,
        )
    except ImportError as exc:
        return False, f"solana submission unavailable in this runtime ({exc})"
    try:
        from solders.transaction import VersionedTransaction  # noqa: F401
    except ImportError as exc:
        return False, f"solana transaction types unavailable ({exc})"
    return True, ""


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
        return quote_impact_pct_from(best_quote(quote))

    async def buy(self, token: str, chain: str, size_native: float,
                  max_slippage_pct: float) -> Fill:
        if chain == "solana":
            ok, reason = solana_submission_available()
            if not ok:
                # Refuse before quoting: no funds are committed and the
                # hunter is told why, rather than the hunt dying between
                # a quote and a broadcast.
                return Fill(ok=False, reason=reason)
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
        best = best_quote(quote)
        warnings = best.get("safety_warnings")
        if warnings:
            # The router itself flagged the route. Fail closed.
            return Fill(ok=False, reason=f"route flagged: {warnings}")
        calldata = best.get("calldata") or (
            quote.get("calldata") if isinstance(quote, dict) else None)
        if not calldata:
            return Fill(ok=False, reason="no route")

        price = unit_price_usd_from(best)
        impact = quote_impact_pct_from(best)
        if chain == "solana":
            tx = await self._send_solana(calldata)
        else:
            tx = await self.sign({"calldata": calldata})
        if not tx:
            return Fill(ok=False, reason="not broadcast")
        return Fill(ok=True, tx=str(tx), price_usd=price,
                    price_impact_pct=0.0 if impact == float("inf") else impact)

    async def _send_solana(self, calldata) -> str:
        """Hand the route to the SDK's Solana sender, correctly typed.

        The sender takes a decoded VersionedTransaction and the callback
        under ``sign_callback``; BRAP hands us a dict carrying the
        transaction base64 under ``serializedTransaction``. Passing the
        dict straight through, or naming the argument
        ``signing_callback``, both fail at the call.
        """
        import base64

        ok, reason = solana_submission_available()
        if not ok:
            raise RuntimeError(reason)
        from solders.transaction import VersionedTransaction
        from wayfinder_paths.core.utils.svm_transaction import (
            send_svm_versioned_transaction,
        )

        serialized = calldata.get("serializedTransaction") if isinstance(
            calldata, dict) else calldata
        if not serialized:
            return ""
        tx = VersionedTransaction.from_bytes(base64.b64decode(serialized))
        result = await send_svm_versioned_transaction(
            tx, sign_callback=self.sign,
            chain_id=int(self.chain_ids.get("solana", 900)),
        )
        return str((result or {}).get("signature") or "")


_DECIMALS = {"solana": 9, "robinhood": 18}
_NATIVE_ADDR = {
    "solana": "So11111111111111111111111111111111111111112",
    "robinhood": "0x0000000000000000000000000000000000000000",
}


def _native_to_raw(size_native: float, chain: str) -> str:
    return str(int(size_native * 10 ** _DECIMALS[chain]))


def _native_addr(chain: str) -> str:
    return _NATIVE_ADDR[chain]
