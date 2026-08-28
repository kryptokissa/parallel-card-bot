"""Duck feed — trending-token discovery and per-token facts.

Live implementation sits on the Wayfinder API token endpoints
(`/blockchain/tokens/discover/` for trending, `/blockchain/tokens/detail/`
with market_data for depth). No Trenches adapter ships in the SDK, so
this wrapper is the path's own feed wiring (spec §4).

FixtureFeed replays canned marshes for ghost hunts, tests, and dry runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

BIOMES = {
    "solana": {
        "pump.fun": "the Pump Flats",
        "moonshot": "the Moonlit Shallows",
        "bonk.fun": "Bonk Hollow",
    },
    "robinhood": {
        "noxa": "the Iron Fen",
        "virtuals": "the Verdant Banks",
        "bankr": "the Vault Reeds",
        "flap": "Flap Water",
        "pons": "the Old Crossing",
    },
}


@dataclass
class Duck:
    """One candidate token, normalized from the feed."""

    token: str  # mint / contract address
    symbol: str
    chain: str
    heat: float
    liquidity_usd: float
    age_minutes: float
    top10_holders_pct: float
    copycat_flag: bool = False
    honeypot_flag: bool = False
    red_flags: list[str] = field(default_factory=list)
    graduated: bool = False
    launchpad: str = ""
    # Safety-check facts (chain-specific; None = not yet checked)
    mint_authority_revoked: bool | None = None
    freeze_authority_revoked: bool | None = None
    token2022_clean: bool | None = None  # no fee/hook/delegate/frozen rule
    sell_simulation_ok: bool | None = None
    measured_sell_tax_pct: float | None = None
    recommended_slippage_pct: float | None = None
    price_usd: float | None = None

    @property
    def biome(self) -> str:
        return BIOMES.get(self.chain, {}).get(self.launchpad, "the open marsh")


@dataclass
class Weather:
    """Launch rate + volatility, narration and trophies only (§6)."""

    state: str = "Calm"  # Calm | Brisk | Storm

    @classmethod
    def from_feed(cls, launches_per_hour: float, volatility: float) -> "Weather":
        score = launches_per_hour / 60.0 + volatility
        if score >= 2.0:
            return cls("Storm")
        if score >= 1.0:
            return cls("Brisk")
        return cls("Calm")


class DuckFeed(Protocol):
    async def scout(self, chain: str, limit: int) -> list[Duck]: ...

    async def safety_check(self, duck: Duck) -> Duck: ...

    async def weather(self, chain: str) -> Weather: ...

    async def price(self, token: str, chain: str) -> float: ...


class WayfinderFeed:
    """Live feed over the Wayfinder API token endpoints."""

    def __init__(self) -> None:
        from wayfinder_paths.core.clients.TokenClient import TokenClient

        self._tokens = TokenClient()

    async def scout(self, chain: str, limit: int = 25) -> list[Duck]:
        raw = await self._tokens.discover_tokens(
            chain_code=chain, dimension="trending", limit=limit
        )
        rows = raw.get("rows") or raw.get("data") or raw.get("tokens") or []
        return [self._normalize(row, chain) for row in rows]

    async def safety_check(self, duck: Duck) -> Duck:
        details = await self._tokens.get_token_details(
            f"{duck.chain}_{duck.token}", market_data=True
        )
        return _merge_details(duck, details)

    async def weather(self, chain: str) -> Weather:
        raw = await self._tokens.discover_tokens(
            chain_code=chain, dimension="trending", limit=50
        )
        rows = raw.get("rows") or raw.get("data") or raw.get("tokens") or []
        launches = [r for r in rows if _age_minutes(r) is not None]
        recent = sum(1 for r in launches if (_age_minutes(r) or 1e9) <= 60)
        vol = _median_abs_change(rows)
        return Weather.from_feed(launches_per_hour=float(recent), volatility=vol)

    async def price(self, token: str, chain: str) -> float:
        details = await self._tokens.get_token_details(
            f"{chain}_{token}", market_data=True
        )
        market = details.get("market_data") or details
        price = market.get("price_usd") or market.get("price") or 0.0
        return float(price)

    def _normalize(self, row: dict[str, Any], chain: str) -> Duck:
        market = row.get("market_data") or row
        flags = [str(f) for f in (row.get("risk_flags") or row.get("flags") or [])]
        lowered = [f.lower() for f in flags]
        return Duck(
            token=str(row.get("address") or row.get("mint") or row.get("id") or ""),
            symbol=str(row.get("symbol") or row.get("name") or "?"),
            chain=chain,
            heat=float(row.get("heat") or row.get("trending_score")
                       or row.get("score") or 0.0),
            liquidity_usd=float(market.get("liquidity_usd")
                                or market.get("liquidity") or 0.0),
            age_minutes=float(_age_minutes(row) or 0.0),
            top10_holders_pct=float(row.get("top10_holders_pct")
                                    or row.get("top_10_concentration") or 0.0),
            copycat_flag=any("copycat" in f or "imitat" in f for f in lowered),
            honeypot_flag=any("honeypot" in f for f in lowered),
            red_flags=[f for f in flags
                       if f.lower() not in ("verified", "ok", "none")],
            graduated=bool(row.get("graduated") or row.get("is_graduated")),
            launchpad=str(row.get("launchpad") or row.get("source") or "").lower(),
            price_usd=(float(market.get("price_usd") or market.get("price") or 0)
                       or None),
        )


def _age_minutes(row: dict[str, Any]) -> float | None:
    created = row.get("created_at") or row.get("launch_time") or row.get("launched_at")
    if created is None:
        return None
    try:
        if isinstance(created, (int, float)):
            dt = datetime.fromtimestamp(float(created), tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
        return (datetime.now(tz=timezone.utc) - dt).total_seconds() / 60.0
    except (ValueError, OSError):
        return None


def _median_abs_change(rows: list[dict[str, Any]]) -> float:
    changes = []
    for r in rows:
        market = r.get("market_data") or r
        change = market.get("price_change_1h") or market.get("change_1h")
        if change is not None:
            try:
                changes.append(abs(float(change)) / 100.0)
            except (TypeError, ValueError):
                continue
    if not changes:
        return 0.0
    changes.sort()
    return changes[len(changes) // 2]


def _merge_details(duck: Duck, details: dict[str, Any]) -> Duck:
    security = details.get("security") or details.get("safety") or {}
    ext = details.get("extensions") or {}
    if duck.chain == "solana":
        duck.mint_authority_revoked = _flag(
            security, "mint_authority_revoked", "mint_revoked"
        )
        duck.freeze_authority_revoked = _flag(
            security, "freeze_authority_revoked", "freeze_revoked"
        )
        program = str(details.get("token_program") or "").lower()
        if "2022" in program:
            dirty = any(
                bool(ext.get(k))
                for k in ("transfer_fee", "transfer_hook",
                          "permanent_delegate", "default_frozen")
            )
            duck.token2022_clean = not dirty
        else:
            duck.token2022_clean = True  # standard SPL
    else:
        sim = security.get("sell_simulation") or {}
        duck.sell_simulation_ok = bool(sim.get("success", security.get("sellable")))
        tax = sim.get("sell_tax_pct", security.get("sell_tax"))
        duck.measured_sell_tax_pct = float(tax) if tax is not None else None
        slip = sim.get("recommended_slippage_pct")
        duck.recommended_slippage_pct = float(slip) if slip is not None else None
    return duck


def _flag(container: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key in container:
            return bool(container[key])
    return None


class FixtureFeed:
    """Deterministic feed for ghost hunts, dry runs, and tests.

    Fixture files are JSON: {"weather": {...}, "scout": [duck dicts],
    "prices": {"MINT": [p0, p1, ...]}} — each price() call for a token
    advances one step, so exits can be exercised end-to-end.
    """

    def __init__(self, fixture: dict[str, Any] | str,
                 cursor_file: str | None = None):
        if isinstance(fixture, str):
            with open(fixture, "r", encoding="utf-8") as fh:
                fixture = json.load(fh)
        self.fixture: dict[str, Any] = fixture
        self.cursor_file = cursor_file
        self._price_cursor: dict[str, int] = {}
        if cursor_file:
            try:
                with open(cursor_file, "r", encoding="utf-8") as fh:
                    self._price_cursor = json.load(fh)
            except (OSError, ValueError):
                self._price_cursor = {}

    async def scout(self, chain: str, limit: int = 25) -> list[Duck]:
        ducks = []
        for row in self.fixture.get("scout", [])[:limit]:
            allowed = {f.name for f in Duck.__dataclass_fields__.values()}
            ducks.append(Duck(**{k: v for k, v in row.items() if k in allowed}))
        return ducks

    async def safety_check(self, duck: Duck) -> Duck:
        return duck  # fixtures carry their safety facts inline

    async def weather(self, chain: str) -> Weather:
        return Weather(self.fixture.get("weather", {}).get("state", "Calm"))

    async def price(self, token: str, chain: str) -> float:
        series = self.fixture.get("prices", {}).get(token, [])
        if not series:
            return 0.0
        idx = self._price_cursor.get(token, 0)
        self._price_cursor[token] = min(idx + 1, len(series) - 1)
        if self.cursor_file:
            with open(self.cursor_file, "w", encoding="utf-8") as fh:
                json.dump(self._price_cursor, fh)
        return float(series[idx])
