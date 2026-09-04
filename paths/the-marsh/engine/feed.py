"""Duck feed — trending-token discovery and per-token facts.

Live implementation sits on the Wayfinder API (verified against the
real endpoints):

- ``/blockchain/tokens/discover/`` with ``dimension=active`` is the
  live trenches feed on Solana (pump.fun launches with age, liquidity,
  and momentum). ``dimension=trending`` is a board of established
  tokens, all far older than the age window, so it is only a fallback
  for a chain whose active feed comes back empty.
- ``/blockchain/tokens/detail/`` (``chain_id`` required; solana=900)
  carries identity flags: ``suspicious`` and ``verification`` feed the
  decoy gate, ``current_price`` feeds exit checks.
- ``/blockchain/rpc/<chain_id>/`` proxies Solana RPC:
  ``getAccountInfo`` gives mint/freeze authority and Token-2022
  extensions; ``getTokenLargestAccounts`` + supply gives top-10 holder
  concentration.

The feed has no native heat score, so heat is derived 0-100 from
momentum with a published formula (see ``_derive_heat``).

FixtureFeed replays canned marshes for ghost hunts, tests, and dry
runs; its facts are inline so its safety checks are free.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

BIOMES = {
    "solana": {
        "pumpfun": "the Pump Flats",
        "pump.fun": "the Pump Flats",
        "moonshot": "the Moonlit Shallows",
        "bonkfun": "Bonk Hollow",
        "bonk.fun": "Bonk Hollow",
        "letsbonk": "Bonk Hollow",
    },
    "robinhood": {
        "noxa": "the Iron Fen",
        "virtuals": "the Verdant Banks",
        "bankr": "the Vault Reeds",
        "flap": "Flap Water",
        "pons": "the Old Crossing",
    },
}

CHAIN_IDS = {"solana": 900}

# Token-2022 extensions that are fine on a duck; anything else on the
# mint is fine print in the feathers (bad water).
BENIGN_EXTENSIONS = {"metadataPointer", "tokenMetadata"}
DANGEROUS_EXTENSIONS = {
    "transferFeeConfig", "transferFeeAmount", "transferHook",
    "permanentDelegate", "defaultAccountState", "pausableConfig",
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
    top10_holders_pct: float = 0.0
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
        score = launches_per_hour / 4.0 + volatility
        if score >= 2.0:
            return cls("Storm")
        if score >= 1.0:
            return cls("Brisk")
        return cls("Calm")


class DuckFeed(Protocol):
    # True when safety facts cost nothing to read (fixtures); the
    # engine safety-checks every duck then. Live feeds set False and
    # the engine checks lazily, best duck first, to spare API quota.
    safety_is_free: bool

    async def scout(self, chain: str, limit: int) -> list[Duck]: ...

    async def safety_check(self, duck: Duck) -> Duck: ...

    async def weather(self, chain: str) -> Weather: ...

    async def price(self, token: str, chain: str) -> float: ...


def _derive_heat(row: dict[str, Any]) -> float:
    """Duck level, 0-100, from momentum. Published formula:

    35% how many hunters are on it   (buyers last hour / 300, capped)
    30% how hard the water churns    (volume last hour / $250k, capped)
    20% the hour's climb             (1h price change / +40%, capped)
    15% the last-five-minutes spark  (5m price change / +8%, capped)
    """

    def _num(key: str) -> float:
        try:
            return float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    buyers = min(1.0, _num("buyers_h1") / 300.0)
    churn = min(1.0, _num("volume_h1_usd") / 250_000.0)
    climb = min(1.0, max(0.0, _num("price_change_h1_pct")) / 40.0)
    spark = min(1.0, max(0.0, _num("price_change_m5_pct")) / 8.0)
    return round(100.0 * (0.35 * buyers + 0.30 * churn
                          + 0.20 * climb + 0.15 * spark), 1)


def _age_minutes(row: dict[str, Any]) -> float | None:
    created = (row.get("pool_created_at") or row.get("created_at")
               or row.get("launch_time"))
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


class WayfinderFeed:
    """Live feed over the Wayfinder API (see module docstring)."""

    safety_is_free = False

    def __init__(self) -> None:
        # No SDK client objects are held: the feed talks to the
        # Wayfinder REST API directly (see _get/_rpc). That keeps the
        # pack working across SDK releases instead of binding to a
        # client surface that moves between versions.
        self._base_url: str | None = None

    # -- raw API helpers ---------------------------------------------------

    def _api_base(self) -> str:
        if self._base_url is None:
            try:
                from wayfinder_paths.core.config import get_api_base_url

                self._base_url = str(get_api_base_url()).rstrip("/")
            except Exception:
                self._base_url = os.environ.get(
                    "WAYFINDER_API_BASE_URL", "https://wayfinder.ai/api/v1"
                ).rstrip("/")
        return self._base_url

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get("WAYFINDER_API_KEY")
        if not api_key:
            try:
                from wayfinder_paths.core.config import get_api_key

                api_key = get_api_key()
            except Exception:
                api_key = None
        if api_key:
            headers["X-API-KEY"] = api_key
        return headers

    async def _get(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = f"{self._api_base()}/{endpoint.lstrip('/')}"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url, headers=self._headers(), params=params)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, dict) else {}

    async def _discover(self, chain: str, dimension: str,
                        limit: int) -> list[dict[str, Any]]:
        raw = await self._get("blockchain/tokens/discover/", {
            "chain_code": chain, "dimension": dimension, "limit": limit,
        })
        return raw.get("tokens") or raw.get("rows") or []

    async def _detail(self, token: str, chain: str) -> dict[str, Any]:
        raw = await self._get("blockchain/tokens/detail/", {
            "query": token, "market_data": "true",
            "chain_id": CHAIN_IDS.get(chain, ""),
        })
        details = raw.get("data", raw)
        return details if isinstance(details, dict) else {}

    async def _rpc(self, chain: str, method: str, params: list[Any]) -> Any:
        import httpx

        url = f"{self._api_base()}/blockchain/rpc/{CHAIN_IDS[chain]}/"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=self._headers(), json={
                "jsonrpc": "2.0", "id": 1, "method": method, "params": params,
            })
            resp.raise_for_status()
            return resp.json().get("result")

    # -- DuckFeed ----------------------------------------------------------

    async def scout(self, chain: str, limit: int = 25) -> list[Duck]:
        # ``active`` is the trenches: fresh launches, minutes to days
        # old, which is the only water this hunt fishes. ``trending``
        # is a popularity board of established tokens — BONK, PENGU,
        # FARTCOIN, ages measured in years — so every duck on it flies
        # straight through the age window. It was empty on Solana when
        # this was first written and the preference sat the other way
        # round; once it filled up, scouting quietly stopped seeing any
        # launches at all. Trending is now only a fallback for a chain
        # whose active feed is dark.
        rows = await self._discover(chain, "active", limit)
        if not rows:
            rows = await self._discover(chain, "trending", limit)
        ducks = [self._normalize(row, chain) for row in rows]
        return [d for d in ducks if d.token]

    async def safety_check(self, duck: Duck) -> Duck:
        detail = await self._detail(duck.token, duck.chain)
        identity = detail.get("identity") or {}
        duck.copycat_flag = bool(identity.get("suspicious"))
        if identity.get("protected_claim"):
            duck.copycat_flag = True
        price = detail.get("current_price")
        if price:
            duck.price_usd = float(price)

        if duck.chain == "solana":
            await self._solana_mint_check(duck)
            await self._solana_holder_check(duck)
        else:
            # robinhood chain: needs the sell-simulation service; until
            # that endpoint is wired the duck stays unverified and the
            # safety gate refuses it (fail closed, never open)
            duck.sell_simulation_ok = None
        return duck

    async def _solana_mint_check(self, duck: Duck) -> None:
        result = await self._rpc(
            "solana", "getAccountInfo",
            [duck.token, {"encoding": "jsonParsed"}],
        )
        value = (result or {}).get("value") or {}
        parsed = ((value.get("data") or {}).get("parsed") or {})
        info = parsed.get("info") or {}
        if parsed.get("type") != "mint":
            duck.mint_authority_revoked = None  # unknown = fail closed
            return
        duck.mint_authority_revoked = info.get("mintAuthority") is None
        duck.freeze_authority_revoked = info.get("freezeAuthority") is None
        owner = str(value.get("owner") or "")
        if owner == "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA":
            duck.token2022_clean = True  # standard SPL, no extensions
        else:
            names = {str(e.get("extension")) for e in info.get("extensions", [])}
            duck.token2022_clean = not (names & DANGEROUS_EXTENSIONS) and \
                names <= (BENIGN_EXTENSIONS | set())

    async def _solana_holder_check(self, duck: Duck) -> None:
        largest = await self._rpc(
            "solana", "getTokenLargestAccounts", [duck.token]
        )
        accounts = (largest or {}).get("value") or []
        supply_info = await self._rpc(
            "solana", "getTokenSupply", [duck.token]
        )
        supply = float(((supply_info or {}).get("value") or {})
                       .get("uiAmount") or 0.0)
        if supply <= 0 or not accounts:
            duck.top10_holders_pct = 100.0  # unknown = fail closed
            return
        amounts = sorted(
            (float(a.get("uiAmount") or 0.0) for a in accounts), reverse=True
        )
        # the single largest account is almost always the pool/bonding
        # vault, not a hunter; exclude it from both sides of the ratio
        pool = amounts[0]
        rest = amounts[1:11]
        effective_supply = max(supply - pool, 1e-9)
        duck.top10_holders_pct = round(100.0 * sum(rest) / effective_supply, 1)

    async def weather(self, chain: str) -> Weather:
        rows = await self._discover(chain, "active", 25)
        recent = sum(
            1 for r in rows if (_age_minutes(r) or 1e9) <= 60
        )
        changes = sorted(
            abs(float(r.get("price_change_h1_pct") or 0.0)) / 100.0
            for r in rows
        )
        vol = changes[len(changes) // 2] if changes else 0.0
        return Weather.from_feed(launches_per_hour=float(recent),
                                 volatility=vol)

    async def price(self, token: str, chain: str) -> float:
        detail = await self._detail(token, chain)
        return float(detail.get("current_price") or 0.0)

    def _normalize(self, row: dict[str, Any], chain: str) -> Duck:
        launchpad = str(row.get("launchpad") or "").lower()
        dex = str(row.get("dex") or "").lower()
        bonding = str(row.get("bonding_state") or "").lower()
        # a pump.fun duck now trading on pumpswap has left the bonding
        # curve: that's a graduated (banded) duck
        graduated = bool(
            bonding in ("graduated", "complete")
            or (launchpad == "pumpfun" and dex == "pumpswap")
        )
        return Duck(
            token=str(row.get("address") or ""),
            symbol=str(row.get("symbol") or row.get("name") or "?"),
            chain=chain,
            heat=_derive_heat(row),
            liquidity_usd=float(row.get("liquidity_usd") or 0.0),
            age_minutes=float(_age_minutes(row) or 0.0),
            graduated=graduated,
            launchpad=launchpad,
            price_usd=(float(row.get("price_usd") or 0.0) or None),
        )


class FixtureFeed:
    """Deterministic feed for ghost hunts, dry runs, and tests.

    Fixture files are JSON: {"weather": {...}, "scout": [duck dicts],
    "prices": {"MINT": [p0, p1, ...]}} — each price() call for a token
    advances one step, so exits can be exercised end-to-end.
    """

    safety_is_free = True

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
