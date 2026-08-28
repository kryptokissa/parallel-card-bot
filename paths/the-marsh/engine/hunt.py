"""The hunt engine — Scout → Gates → Shot → Retrieve plan → Exits.

Every hard constraint from the spec is enforced here, in code:

- hunt_size is a ceiling; a shot never exceeds it
- max_open_positions and daily_hunt_limit are checked before scouting
- no averaging down: an open or once-held token can't be bought again
  while open, and a stopped token is locked out for reentry_lockout_days
- gates and limits come from a config snapshot taken at hunt start;
  nothing mutates them mid-hunt
- only the satchel wallet ever executes (see executor module)

The engine emits events; the game folds them. Nothing in this package
reads game state (Design Law 2).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import MarshConfig
from .events import EventLog, utcnow
from .feed import Duck, DuckFeed, Weather
from .gates import GATE_NAMES, run_gates


@dataclass
class Position:
    position_id: str
    token: str
    symbol: str
    chain: str
    entry_price_usd: float
    size_native: float
    opened_at: datetime
    graduated: bool
    weather: str
    retrieve_1_done: bool = False
    closed: bool = False


@dataclass
class HuntResult:
    hunt_id: str
    shot: bool
    position: Position | None = None
    scouted: int = 0
    failures: dict[str, int] = field(default_factory=dict)
    refusal_reason: str = ""


class HuntEngine:
    def __init__(self, config: MarshConfig, feed: DuckFeed, executor,
                 log: EventLog, *, ghost: bool = False):
        config.validate()
        self._config = config
        self.feed = feed
        self.executor = executor
        self.log = log
        self.ghost = ghost
        self.positions: dict[str, Position] = {}
        self.stopped_tokens: dict[str, datetime] = {}
        self.hunts_today: list[datetime] = []
        self.bankroll_native: float = 0.0

    # -- state restoration -------------------------------------------------

    def restore_from_log(self) -> None:
        """Rebuild working state from the canonical event log.

        The log is the save file for the engine too: bankroll, open
        positions, re-entry lockouts, and the daily hunt count are all
        recoverable, so a fresh process picks up exactly where the last
        one stopped.
        """
        for event in self.log.read():
            etype = event.get("type")
            if event.get("ghost") and not self.ghost:
                continue
            if etype == "config_changed":
                try:
                    self._config = self._config.with_updates(
                        **{str(event.get("field")): event.get("new")})
                except (TypeError, ValueError):
                    continue
            elif etype == "kitted_up":
                self.bankroll_native += float(event.get("amount", 0))
            elif etype == "hunt_started":
                self.hunts_today.append(
                    datetime.fromisoformat(str(event.get("ts"))))
            elif etype in ("shot", "ghost_shot"):
                self.bankroll_native -= float(event.get("size", 0))
                self.positions[str(event.get("position_id"))] = Position(
                    position_id=str(event.get("position_id")),
                    token=str(event.get("token")),
                    symbol=str(event.get("symbol")),
                    chain=str(event.get("chain")),
                    entry_price_usd=float(event.get("entry_price_usd", 0)),
                    size_native=float(event.get("size", 0)),
                    opened_at=datetime.fromisoformat(str(event.get("ts"))),
                    graduated=bool(event.get("graduated")),
                    weather=str(event.get("weather", "Calm")),
                )
            elif etype == "partial_retrieve":
                position = self.positions.get(str(event.get("position_id")))
                if position:
                    position.retrieve_1_done = True
                    fraction = float(event.get("sold_fraction", 0.5))
                    gain = float(event.get("gain_pct", 0))
                    self.bankroll_native += (position.size_native * fraction
                                             * (1.0 + gain / 100.0))
            elif etype in ("stopped", "retrieved", "walked"):
                position = self.positions.get(str(event.get("position_id")))
                if position and not position.closed:
                    position.closed = True
                    gain = float(event.get("gain_pct", 0))
                    remaining = (1.0 - self._config.retrieve_1_fraction
                                 if position.retrieve_1_done else 1.0)
                    self.bankroll_native += (position.size_native * remaining
                                             * (1.0 + gain / 100.0))
                    if etype == "stopped":
                        self.stopped_tokens[position.token] = \
                            datetime.fromisoformat(str(event.get("ts")))
            elif etype == "walked_out":
                self.bankroll_native -= float(event.get("amount", 0))
        cutoff = utcnow() - timedelta(hours=24)
        self.hunts_today = [t for t in self.hunts_today if t > cutoff]

    # -- configuration ----------------------------------------------------

    @property
    def config(self) -> MarshConfig:
        return self._config

    def update_config(self, **changes: Any) -> MarshConfig:
        """The only mutation door, usable only between hunts.

        Emits config_changed per field so the change is on the record;
        the game layer reads those events for tilt flags and no-duck
        grace — the engine itself does not care why you changed it.
        """
        old = self._config
        new = old.with_updates(**changes)
        for key, value in changes.items():
            self.log.emit("config_changed", field=key,
                          old=getattr(old, key), new=value)
        self._config = new
        return new

    # -- funding ----------------------------------------------------------

    def kit_up(self, amount_native: float) -> None:
        self.bankroll_native += amount_native
        self.log.emit("kitted_up", amount=amount_native,
                      bankroll=self.bankroll_native, ghost=self.ghost)

    # -- the hunt ---------------------------------------------------------

    async def run_hunt(self) -> HuntResult:
        config = self._config  # frozen snapshot for this hunt
        hunt_id = uuid.uuid4().hex[:10]
        now = utcnow()

        weather = await self.feed.weather(config.chain)
        self.log.emit("weather", state=weather.state)

        limit_reason = self._limits_block_hunt(config, now)
        if limit_reason:
            self.log.emit("hunt_refused", hunt_id=hunt_id, reason=limit_reason,
                          ghost=self.ghost)
            return HuntResult(hunt_id=hunt_id, shot=False,
                              refusal_reason=limit_reason)

        self._count_hunt(now)
        self.log.emit("hunt_started", hunt_id=hunt_id, chain=config.chain,
                      weather=weather.state, ghost=self.ghost)

        ducks = await self.feed.scout(config.chain, limit=25)
        safety_is_free = getattr(self.feed, "safety_is_free", True)
        passed: list[Duck] = []
        failures: dict[str, int] = {}
        held_tokens = {p.token for p in self.positions.values() if not p.closed}
        for duck in sorted(ducks, key=lambda d: -d.heat):
            if duck.token in held_tokens:
                failed = ["already_holding"]
            elif self._reentry_locked(duck.token, config, now):
                failed = ["reentry_lockout"]
            else:
                failed = run_gates(duck, config, include_safety=False)
                if not failed:
                    if safety_is_free or not passed:
                        # live feeds pay per safety check, so once a
                        # duck has fully passed, the rest stay watched
                        duck = await self.feed.safety_check(duck)
                        failed = run_gates(duck, config)
                    else:
                        self.log.emit("duck_scouted", hunt_id=hunt_id,
                                      token=duck.token, symbol=duck.symbol,
                                      heat=duck.heat, verdict="watched",
                                      ghost=self.ghost)
                        continue
            if failed:
                gate = failed[0]
                name = GATE_NAMES.get(gate, gate)
                failures[name] = failures.get(name, 0) + 1
                self.log.emit("duck_scouted", hunt_id=hunt_id, token=duck.token,
                              symbol=duck.symbol, heat=duck.heat,
                              verdict="refused", gate=gate, gate_name=name,
                              ghost=self.ghost)
            else:
                passed.append(duck)
                self.log.emit("duck_scouted", hunt_id=hunt_id, token=duck.token,
                              symbol=duck.symbol, heat=duck.heat,
                              verdict="passed", ghost=self.ghost)

        if not passed:
            self.log.emit("no_duck", hunt_id=hunt_id, scouted=len(ducks),
                          failures=failures, ghost=self.ghost)
            if self.ghost:
                self.log.emit("ghost_hunt", hunt_id=hunt_id, ghost=True)
            return HuntResult(hunt_id=hunt_id, shot=False, scouted=len(ducks),
                              failures=failures, refusal_reason="no duck passed")

        # SHOT — highest-heat passer, at min(hunt_size, bankroll), never more.
        duck = max(passed, key=lambda d: d.heat)
        size = min(config.hunt_size, self.bankroll_native)
        if size <= 0:
            self.log.emit("hunt_refused", hunt_id=hunt_id, reason="empty satchel",
                          ghost=self.ghost)
            return HuntResult(hunt_id=hunt_id, shot=False, scouted=len(ducks),
                              failures=failures, refusal_reason="empty satchel")

        impact = await self.executor.quote_impact_pct(duck.token, config.chain,
                                                      size)
        if impact > config.max_price_impact_pct:
            failures[GATE_NAMES["liquidity"]] = \
                failures.get(GATE_NAMES["liquidity"], 0) + 1
            self.log.emit("no_duck", hunt_id=hunt_id, scouted=len(ducks),
                          failures=failures, aborted_on="price_impact",
                          ghost=self.ghost)
            return HuntResult(hunt_id=hunt_id, shot=False, scouted=len(ducks),
                              failures=failures,
                              refusal_reason="price impact too deep")

        max_slippage = min(
            config.max_price_impact_pct,
            duck.recommended_slippage_pct or config.max_price_impact_pct,
        )
        fill = await self.executor.buy(duck.token, config.chain, size,
                                       max_slippage)
        if not fill.ok:
            self.log.emit("no_duck", hunt_id=hunt_id, scouted=len(ducks),
                          failures=failures, aborted_on=fill.reason,
                          ghost=self.ghost)
            return HuntResult(hunt_id=hunt_id, shot=False, scouted=len(ducks),
                              failures=failures, refusal_reason=fill.reason)

        if not self._expedition_active():
            self.log.emit("expedition_started",
                          id=self._next_expedition_id(),
                          starting_bankroll=self.bankroll_native,
                          ghost=self.ghost)

        self.bankroll_native -= size
        position = Position(
            position_id=uuid.uuid4().hex[:10],
            token=duck.token, symbol=duck.symbol, chain=config.chain,
            entry_price_usd=fill.price_usd, size_native=size,
            opened_at=now, graduated=duck.graduated, weather=weather.state,
        )
        self.positions[position.position_id] = position
        event_type = "ghost_shot" if self.ghost else "shot"
        self.log.emit(event_type, hunt_id=hunt_id,
                      position_id=position.position_id, token=duck.token,
                      symbol=duck.symbol, chain=config.chain,
                      entry_price_usd=fill.price_usd, size=size, tx=fill.tx,
                      heat=duck.heat, biome=duck.biome,
                      graduated=duck.graduated, weather=weather.state)
        self.log.emit("retrieve_plan", position_id=position.position_id,
                      token=duck.token,
                      rules={
                          "retrieve_1": {"gain_pct": config.retrieve_1_pct,
                                         "sell_fraction":
                                             config.retrieve_1_fraction},
                          "retrieve_2": {"gain_pct": config.retrieve_2_pct},
                          "stop_loss": {"gain_pct": config.stop_loss_pct},
                          "time_stop": {"hours": config.time_stop_hours,
                                        "low_pct": config.time_stop_low_pct,
                                        "high_pct": config.time_stop_high_pct},
                      })
        if self.ghost:
            self.log.emit("ghost_hunt", hunt_id=hunt_id, ghost=True)
        return HuntResult(hunt_id=hunt_id, shot=True, position=position,
                          scouted=len(ducks), failures=failures)

    # -- exits ------------------------------------------------------------

    async def check_positions(self, *, now: datetime | None = None) -> list[dict]:
        """Apply the retrieve plan. Called on schedule (the whistle)."""
        config = self._config
        now = now or utcnow()
        emitted: list[dict] = []
        for position in list(self.positions.values()):
            if position.closed:
                continue
            price = await self.feed.price(position.token, position.chain)
            if price <= 0 or position.entry_price_usd <= 0:
                continue
            gain = (price / position.entry_price_usd - 1.0) * 100.0
            held_hours = (now - position.opened_at).total_seconds() / 3600.0

            if gain <= config.stop_loss_pct:
                emitted.append(await self._close(position, "stopped", gain, 1.0))
                self.stopped_tokens[position.token] = now
            elif gain >= config.retrieve_2_pct:
                emitted.append(await self._close(position, "retrieved", gain, 1.0))
            elif gain >= config.retrieve_1_pct and not position.retrieve_1_done:
                fill = await self.executor.sell(
                    position.token, position.chain, config.retrieve_1_fraction,
                    config.max_price_impact_pct)
                position.retrieve_1_done = True
                event = self.log.emit(
                    "partial_retrieve", position_id=position.position_id,
                    token=position.token, symbol=position.symbol,
                    gain_pct=round(gain, 1),
                    sold_fraction=config.retrieve_1_fraction, tx=fill.tx,
                    ghost=self.ghost)
                self.bankroll_native += (position.size_native
                                         * config.retrieve_1_fraction
                                         * (1.0 + gain / 100.0))
                emitted.append(event)
            elif (held_hours >= config.time_stop_hours
                  and config.time_stop_low_pct <= gain
                  <= config.time_stop_high_pct):
                emitted.append(await self._close(position, "walked", gain, 1.0))

        self._check_bust()
        return emitted

    async def _close(self, position: Position, event_type: str,
                     gain: float, fraction: float) -> dict:
        fill = await self.executor.sell(position.token, position.chain, fraction,
                                        self._config.max_price_impact_pct)
        position.closed = True
        remaining = (1.0 - self._config.retrieve_1_fraction
                     if position.retrieve_1_done else 1.0)
        self.bankroll_native += (position.size_native * remaining
                                 * (1.0 + gain / 100.0))
        return self.log.emit(event_type, position_id=position.position_id,
                             token=position.token, symbol=position.symbol,
                             gain_pct=round(gain, 1), tx=fill.tx,
                             ghost=self.ghost)

    # -- expedition bookkeeping -------------------------------------------

    def walk_out(self, amount_native: float) -> dict:
        """Bank loot to the main wallet — the win condition."""
        amount = min(amount_native, self.bankroll_native)
        self.bankroll_native -= amount
        return self.log.emit("walked_out", amount=amount,
                             bankroll=self.bankroll_native, ghost=self.ghost)

    def make_camp(self) -> dict:
        return self.log.emit("make_camp", bankroll=self.bankroll_native,
                             ghost=self.ghost)

    def _check_bust(self) -> None:
        open_positions = [p for p in self.positions.values() if not p.closed]
        if not open_positions and 0 <= self.bankroll_native \
                < self._config.hunt_size and self._expedition_active():
            self.log.emit("bust", bankroll=self.bankroll_native,
                          ghost=self.ghost)

    # -- hard-constraint helpers ------------------------------------------

    def _limits_block_hunt(self, config: MarshConfig, now: datetime) -> str:
        open_count = sum(1 for p in self.positions.values() if not p.closed)
        if open_count >= config.max_open_positions:
            return "satchel full"  # max_open_positions reached
        recent = [t for t in self.hunts_today if now - t < timedelta(hours=24)]
        if len(recent) >= config.daily_hunt_limit:
            return "day's done"  # daily_hunt_limit reached
        return ""

    def _count_hunt(self, now: datetime) -> None:
        self.hunts_today = [t for t in self.hunts_today
                            if now - t < timedelta(hours=24)]
        self.hunts_today.append(now)

    def _reentry_locked(self, token: str, config: MarshConfig,
                        now: datetime) -> bool:
        stopped_at = self.stopped_tokens.get(token)
        if stopped_at is None:
            return False
        return now - stopped_at < timedelta(days=config.reentry_lockout_days)

    def _expedition_active(self) -> bool:
        state = {"active": False, "id": 0}
        for event in self.log.read():
            if event.get("type") == "expedition_started":
                state = {"active": True, "id": event.get("id", 0)}
            elif event.get("type") in ("walked_out", "make_camp", "bust"):
                state["active"] = False
        return bool(state["active"])

    def _next_expedition_id(self) -> int:
        last = 0
        for event in self.log.read():
            if event.get("type") == "expedition_started":
                last = int(event.get("id", 0))
        return last + 1
