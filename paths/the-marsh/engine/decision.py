"""What the dog hands the hunter's agent to execute.

On Wayfinder a path decides and an agent executes. The pack holds no
keys and is never given a signer; the agent has its own wallet and its
own swap tool. So a live shot is not something this code performs — it
is something it *specifies*, precisely enough that the agent can run
it without judgement of its own, and precisely enough that what comes
back can be checked against what was asked for.

A decision is written to disk as pending and cleared when the fill is
recorded or the shot is abandoned. Nothing here signs, sends, or names
a recipient.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

SOL_MINT = "So11111111111111111111111111111111111111112"

# A quote goes stale. Handing an agent a ten-minute-old route and
# calling it the plan is how a 3% shot becomes a 30% one.
DECISION_TTL_SECONDS = 180


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


@dataclass
class Decision:
    """One swap, fully specified, for the agent's own swap tool."""

    kind: str                     # "buy" (take a duck) | "sell" (exit)
    token: str                    # the duck's mint
    symbol: str
    chain: str
    amount: str                   # human units, decimal point required
    slippage_bps: int
    from_token: str
    to_token: str
    reason: str = ""              # which rule asked for it
    hunt_id: str = ""
    position_id: str = ""
    heat: float = 0.0
    biome: str = ""
    price_impact_pct: float = 0.0
    expected_price_usd: float = 0.0
    sell_fraction: float = 0.0    # sells only: how much of the position
    created_at: str = field(default_factory=lambda: _now().isoformat())

    def expired(self, *, now: datetime | None = None) -> bool:
        now = now or _now()
        try:
            made = datetime.fromisoformat(self.created_at)
        except ValueError:
            return True
        return now - made > timedelta(seconds=DECISION_TTL_SECONDS)

    def to_dict(self) -> dict:
        return asdict(self)

    def agent_call(self) -> dict:
        """Exactly the onchain_swap arguments, and nothing else.

        wallet_label is deliberately absent: the agent knows its own
        wallets, and this pack must never depend on what they are
        called. Labels are generated per install.
        """
        amount = self.amount
        if not amount:
            # A sell is a fraction of a balance this pack cannot read;
            # the agent can. Say so in the field itself rather than
            # shipping an empty string that reads like a bug.
            pct = int(round((self.sell_fraction or 1.0) * 100))
            amount = f"<{pct}% of your ${self.symbol} balance, in tokens>"
        return {
            "tool": "onchain_swap",
            "from_token": self.from_token,
            "to_token": self.to_token,
            "amount": amount,
            "slippage_bps": self.slippage_bps,
            "wallet_label": "<your satchel wallet label>",
        }


def buy_decision(duck, size_native: float, slippage_bps: int, *,
                 hunt_id: str = "", impact_pct: float = 0.0,
                 price_usd: float = 0.0) -> Decision:
    return Decision(
        kind="buy", token=duck.token, symbol=duck.symbol, chain=duck.chain,
        # onchain_swap rejects integer-looking amounts, so the decimal
        # point is not cosmetic.
        amount=f"{size_native:.9f}".rstrip("0").ljust(
            len(f"{int(size_native)}") + 2, "0"),
        slippage_bps=slippage_bps, from_token=SOL_MINT, to_token=duck.token,
        reason="passed every gate", hunt_id=hunt_id, heat=duck.heat,
        biome=duck.biome, price_impact_pct=impact_pct,
        expected_price_usd=price_usd,
    )


def sell_decision(position, fraction: float, rule: str,
                  slippage_bps: int, *, amount_tokens: str = "") -> Decision:
    return Decision(
        kind="sell", token=position.token, symbol=position.symbol,
        chain=position.chain, amount=amount_tokens,
        slippage_bps=slippage_bps, from_token=position.token,
        to_token=SOL_MINT, reason=rule, position_id=position.position_id,
        sell_fraction=fraction,
    )


def pending_path(log_path: str) -> str:
    return log_path + ".pending.json"


def write_pending(log_path: str, decision: Decision) -> str:
    path = pending_path(log_path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(decision.to_dict(), fh, indent=2)
    return path


def read_pending(log_path: str) -> Decision | None:
    path = pending_path(log_path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return Decision(**json.load(fh))
    except (OSError, ValueError, TypeError):
        return None


def clear_pending(log_path: str) -> None:
    path = pending_path(log_path)
    if os.path.exists(path):
        os.remove(path)
