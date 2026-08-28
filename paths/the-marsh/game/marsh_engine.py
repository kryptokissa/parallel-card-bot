"""The Marsh — game layer.

Pure functions over the engine event log. No side effects, no network,
no imports from trading/strategy code (Design Law 2: the game is
strictly read-only over the engine; it can never modify hunt_size,
gates, stops, or limits).

The log is the save file (Law 3): every piece of game state here is a
pure function of the ordered list of engine events, recomputable and
auditable at any time. Events are plain dicts as read from the engine's
JSONL log; this module never writes.

XP rewards process only (Law 1): no row in XP_TABLE keys off PnL, size,
or volume. Outcomes earn trophies, which carry zero XP.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

DAY = timedelta(hours=24)
NO_DUCK_GRACE = timedelta(hours=24)  # gate-loosening inside this voids the XP
TILT_WINDOW = timedelta(hours=24)  # hunt_size raise inside this after a stop
RATING_WINDOW_DAYS = 30
SEASON_WEEKS = 4

# XP table — §6. Process only; every row maps to an engine log event.
XP_COMPLETED_HUNT = 10  # capped at daily_hunt_limit per day
XP_NO_DUCK_ACCEPTED = 10  # capped at daily_hunt_limit per day
XP_CLEAN_STOP = 15
XP_RETRIEVE_1_PER_PLAN = 15
XP_FULL_RULE_CLOSE = 25
XP_WALKED_OUT = 30  # once per expedition
XP_GHOST_HUNT = 3  # capped at 5 per day
XP_WEEKLY_QUEST = 50
GHOST_HUNT_DAILY_CAP = 5

LEVELS = [
    # (level, title, xp threshold, unlocks)
    (1, "Pup", 0, ["pump_flats", "ghost_hunts", "hunt_log"]),
    (2, "Yard Dog", 150, ["senses_1"]),
    (3, "Flats Regular", 400, ["moonlit_shallows", "bonk_hollow", "rename_dog"]),
    (4, "Bird Dog", 800, ["lodge_posting", "public_trophy_wall"]),
    (5, "Fen Runner", 1400, ["robinhood_biomes"]),
    (6, "Night Hunter", 2200, ["night_hunts"]),
    (7, "Pack Leader", 3200, ["senses_2", "found_pack"]),
    (8, "Marsh Warden", 4500, ["warden_cosmetics", "lodge_benchmark"]),
]

GATE_FIELDS = {
    # config fields where a move in the named direction loosens a gate
    # (short aliases included so hand-written logs read naturally)
    "min_liquidity_usd": "down",
    "min_liquidity": "down",
    "min_heat": "down",
    "max_top10_holders_pct": "up",
    "max_top10_holders": "up",
    "max_price_impact_pct": "up",
    "max_price_impact": "up",
    "stop_loss_pct": "down",  # more negative stop = looser
    "stop_loss": "down",
    "daily_hunt_limit": "up",
    "max_open_positions": "up",
}


def _ts(event: dict[str, Any]) -> datetime:
    raw = event.get("ts")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    dt = datetime.fromisoformat(str(raw))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _day_key(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _is_gate_loosening(event: dict[str, Any]) -> bool:
    if event.get("type") != "config_changed":
        return False
    fieldname = event.get("field")
    direction = GATE_FIELDS.get(str(fieldname))
    if direction is None:
        return False
    try:
        old = float(event.get("old"))
        new = float(event.get("new"))
    except (TypeError, ValueError):
        return False
    return new > old if direction == "up" else new < old


def _is_size_raise(event: dict[str, Any]) -> bool:
    if event.get("type") != "config_changed":
        return False
    if event.get("field") != "hunt_size":
        return False
    try:
        return float(event.get("new")) > float(event.get("old"))
    except (TypeError, ValueError):
        return False


@dataclass
class PositionTrace:
    """Everything the game needs to score one position, by position_id."""

    position_id: str
    opened_at: datetime
    token: str = ""
    graduated: bool = False
    weather: str = "Calm"
    retrieve_1_done: bool = False
    manual_actions: int = 0
    closed: bool = False
    close_type: str = ""  # stopped | retrieved | walked | manual
    close_gain_pct: float | None = None


@dataclass
class GameState:
    """Derived state — §6. Cacheable; on doubt, recompute (the log is canonical)."""

    dog_name: str = "Biscuit"
    xp: int = 0
    level: int = 1
    title: str = "Pup"
    hunter_rating: int | None = None  # None = Unranked
    veteran_mode: bool = False
    unlocks: list[str] = field(default_factory=lambda: list(LEVELS[0][3]))
    discipline_streak: int = 0
    best_discipline_streak: int = 0
    expedition: dict[str, Any] = field(default_factory=dict)
    lifetime: dict[str, int] = field(
        default_factory=lambda: {
            "hunts": 0,
            "shots": 0,
            "no_ducks": 0,
            "clean_stops": 0,
            "plan_completions": 0,
            "extractions": 0,
            "busts": 0,
            "flinches": 0,
            "tilt_flags": 0,
            "ghost_hunts": 0,
        }
    )
    trophies: list[dict[str, Any]] = field(default_factory=list)
    season: dict[str, Any] = field(default_factory=lambda: {"n": 1, "quests": []})
    weather: str = "Calm"
    xp_events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hunter": {
                "dog_name": self.dog_name,
                "level": self.level,
                "title": self.title,
                "xp": self.xp,
                "hunter_rating": self.hunter_rating,
                "veteran_mode": self.veteran_mode,
            },
            "streaks": {
                "discipline": self.discipline_streak,
                "best_discipline": self.best_discipline_streak,
            },
            "expedition": self.expedition,
            "lifetime": dict(self.lifetime),
            "trophies": list(self.trophies),
            "season": self.season,
            "unlocks": list(self.unlocks),
            "weather": self.weather,
        }


def xp_for(event: dict[str, Any], *, context: dict[str, Any] | None = None) -> int:
    """XP for a single event given its already-computed context flags.

    Process only. Events describing PnL, size, or volume alone always
    return 0. Context carries cap counters and cleanliness flags that
    the replay fold computes; absent context, caps are assumed unspent.
    """
    ctx = context or {}
    etype = event.get("type")
    if etype == "shot":
        return 0 if ctx.get("daily_hunt_cap_spent") else XP_COMPLETED_HUNT
    if etype == "no_duck":
        if ctx.get("daily_hunt_cap_spent") or ctx.get("gate_loosened_within_24h"):
            return 0
        return XP_NO_DUCK_ACCEPTED
    if etype == "stopped":
        return XP_CLEAN_STOP if ctx.get("clean") else 0
    if etype == "partial_retrieve":
        return XP_RETRIEVE_1_PER_PLAN if ctx.get("per_plan") else 0
    if etype in ("retrieved", "walked"):
        return XP_FULL_RULE_CLOSE if ctx.get("zero_manual_actions") else 0
    if etype == "walked_out":
        return 0 if ctx.get("already_awarded_this_expedition") else XP_WALKED_OUT
    if etype == "ghost_hunt":
        return 0 if ctx.get("ghost_cap_spent") else XP_GHOST_HUNT
    if etype == "quest_completed":
        return XP_WEEKLY_QUEST
    return 0


def level_for(xp: int) -> tuple[int, str, list[str]]:
    level, title, unlocks = 1, "Pup", list(LEVELS[0][3])
    acc: list[str] = []
    for lvl, name, threshold, lvl_unlocks in LEVELS:
        if xp >= threshold:
            level, title = lvl, name
            acc.extend(lvl_unlocks)
    return level, title, acc or unlocks


def rating(events: list[dict[str, Any]], *, now: datetime | None = None) -> int | None:
    """Hunter Rating 0-100 — §6, formula published, 30-day rolling.

    Never ingests PnL. Returns None ("Unranked") under 5 hunts in window.
    """
    if not events:
        return None
    end = now or _ts(events[-1])
    start = end - timedelta(days=RATING_WINDOW_DAYS)
    window = [e for e in events if start <= _ts(e) <= end]

    hunts = sum(1 for e in window if e.get("type") in ("shot", "no_duck"))
    if hunts < 5:
        return None

    ended = [e for e in window if e.get("type") in ("walked_out", "make_camp", "bust")]
    walkouts = sum(1 for e in ended if e.get("type") == "walked_out")
    exits = [
        e for e in window if e.get("type") in ("stopped", "retrieved", "walked",
                                               "manual_sell")
    ]
    rule_exits = sum(1 for e in exits if e.get("type") != "manual_sell")
    tilt_flags = sum(1 for e in window if e.get("type") == "tilt_flag")
    no_ducks = [e for e in window if e.get("type") == "no_duck"]
    clean_no_ducks = sum(1 for e in no_ducks if not e.get("_gate_loosened_after"))

    walkout_term = walkouts / len(ended) if ended else 0.0
    rule_term = rule_exits / len(exits) if exits else 1.0
    tilt_term = 1.0 - (tilt_flags / hunts)
    no_duck_term = clean_no_ducks / len(no_ducks) if no_ducks else 1.0

    score = 100.0 * (
        0.35 * walkout_term + 0.30 * rule_term + 0.20 * tilt_term + 0.15 * no_duck_term
    )
    return int(round(max(0.0, min(100.0, score))))


TROPHY_DEFS = {
    "first_blood": "First Blood",
    "walked_out": "Walked Out",
    "big_duck": "Big Duck",
    "golden_mallard": "Golden Mallard",
    "banded": "Banded",
    "storm_hunter": "Storm Hunter",
    "tough_bird": "Tough Bird",
    "back_from_the_bank": "Back From the Bank",
    "marathon": "Marathon",
    "clean_season": "Clean Season",
    "old_dog": "Old Dog",
}


def _award(state: GameState, trophy_id: str, when: datetime, **extra: Any) -> None:
    if any(t["id"] == trophy_id for t in state.trophies):
        return
    state.trophies.append(
        {"id": trophy_id, "name": TROPHY_DEFS[trophy_id],
         "earned_at": when.isoformat(), **extra}
    )


def replay(events: Iterable[dict[str, Any]]) -> GameState:
    """Fold the engine event log into GameState.

    Deterministic and idempotent: same log in, same state out.
    """
    events = sorted(list(events), key=_ts)
    state = GameState()

    # Pre-pass: mark no_duck events voided by later gate-loosening (§6),
    # possible only because the log is canonical and we replay the whole
    # thing (Law 3).
    loosenings = [_ts(e) for e in events if _is_gate_loosening(e)]
    for e in events:
        if e.get("type") == "no_duck":
            t = _ts(e)
            e["_gate_loosened_after"] = any(
                t <= lt <= t + NO_DUCK_GRACE for lt in loosenings
            )

    positions: dict[str, PositionTrace] = {}
    hunt_xp_by_day: dict[str, int] = {}
    ghost_by_day: dict[str, int] = {}
    last_stop_at: datetime | None = None
    expedition_awarded_walkout = False
    expedition_shots = 0
    expedition_start: datetime | None = None
    expedition_poacher = False
    stops_last_7d: list[datetime] = []
    daily_hunt_limit = 3  # narrative default; refreshed from config_changed events

    def grant(amount: int, event: dict[str, Any], reason: str) -> None:
        if amount <= 0:
            return
        state.xp += amount
        state.xp_events.append(
            {"ts": event.get("ts"), "xp": amount, "reason": reason,
             "event_type": event.get("type")}
        )

    for e in events:
        etype = e.get("type")
        # Practice-range events never touch real scoring; only the
        # ghost_hunt completion itself earns its capped XP.
        if e.get("ghost") and etype != "ghost_hunt":
            continue
        when = _ts(e)
        day = _day_key(when)

        if etype == "config_changed":
            if e.get("field") == "daily_hunt_limit":
                try:
                    daily_hunt_limit = int(e.get("new"))
                except (TypeError, ValueError):
                    pass
            if e.get("field") == "dog_name":
                state.dog_name = str(e.get("new"))
            if e.get("field") == "veteran_mode":
                state.veteran_mode = bool(e.get("new"))
            if _is_size_raise(e) and last_stop_at is not None \
                    and when - last_stop_at <= TILT_WINDOW:
                state.lifetime["tilt_flags"] += 1
                state.discipline_streak = 0
                state.xp_events.append(
                    {"ts": e.get("ts"), "xp": 0, "reason": "tilt_flag",
                     "event_type": "tilt_flag"}
                )

        elif etype == "weather":
            state.weather = str(e.get("state", "Calm"))

        elif etype == "expedition_started":
            state.expedition = {
                "active": True,
                "id": e.get("id"),
                "started_at": str(e.get("ts")),
                "starting_bankroll": e.get("starting_bankroll"),
                "high_water": e.get("starting_bankroll"),
                "shots": 0,
                "open_positions": [],
            }
            expedition_awarded_walkout = False
            expedition_shots = 0
            expedition_start = when
            expedition_poacher = False

        elif etype == "poacher":
            expedition_poacher = True

        elif etype == "ghost_hunt":
            state.lifetime["ghost_hunts"] += 1
            spent = ghost_by_day.get(day, 0) >= GHOST_HUNT_DAILY_CAP
            amount = xp_for(e, context={"ghost_cap_spent": spent})
            if amount:
                ghost_by_day[day] = ghost_by_day.get(day, 0) + 1
            grant(amount, e, "ghost_hunt")

        elif etype == "shot":
            state.lifetime["hunts"] += 1
            state.lifetime["shots"] += 1
            expedition_shots += 1
            if state.expedition.get("active"):
                state.expedition["shots"] = expedition_shots
                state.expedition.setdefault("open_positions", []).append(
                    e.get("token")
                )
            spent = hunt_xp_by_day.get(day, 0) >= daily_hunt_limit
            amount = xp_for(e, context={"daily_hunt_cap_spent": spent})
            if amount:
                hunt_xp_by_day[day] = hunt_xp_by_day.get(day, 0) + 1
            grant(amount, e, "completed_hunt")
            state.discipline_streak += 1
            pid = str(e.get("position_id", e.get("token", "")))
            positions[pid] = PositionTrace(
                position_id=pid,
                opened_at=when,
                token=str(e.get("token", "")),
                graduated=bool(e.get("graduated")),
                weather=str(e.get("weather", state.weather)),
            )

        elif etype == "no_duck":
            state.lifetime["hunts"] += 1
            state.lifetime["no_ducks"] += 1
            spent = hunt_xp_by_day.get(day, 0) >= daily_hunt_limit
            amount = xp_for(
                e,
                context={
                    "daily_hunt_cap_spent": spent,
                    "gate_loosened_within_24h": e.get("_gate_loosened_after", False),
                },
            )
            if amount:
                hunt_xp_by_day[day] = hunt_xp_by_day.get(day, 0) + 1
            grant(amount, e, "no_duck_accepted")
            state.discipline_streak += 1

        elif etype == "manual_sell":
            pid = str(e.get("position_id", ""))
            trace = positions.get(pid)
            if trace is not None:
                trace.manual_actions += 1
                if not trace.retrieve_1_done and not trace.closed:
                    # Flinch — §6: costs nothing, earns nothing, tracked once.
                    state.lifetime["flinches"] += 1
                if not trace.closed and e.get("closes_position"):
                    trace.closed = True
                    trace.close_type = "manual"

        elif etype == "partial_retrieve":
            pid = str(e.get("position_id", ""))
            trace = positions.get(pid)
            per_plan = bool(trace) and trace.manual_actions == 0
            if trace is not None:
                trace.retrieve_1_done = True
            grant(xp_for(e, context={"per_plan": per_plan}), e, "retrieve_1")
            if per_plan:
                _award(state, "first_blood", when, token=e.get("token"))

        elif etype in ("stopped", "retrieved", "walked"):
            pid = str(e.get("position_id", ""))
            trace = positions.get(pid)
            zero_manual = bool(trace) and trace.manual_actions == 0
            gain = e.get("gain_pct")
            if trace is not None:
                trace.closed = True
                trace.close_type = etype
                trace.close_gain_pct = gain
            if state.expedition.get("active"):
                open_pos = state.expedition.get("open_positions", [])
                token = trace.token if trace else None
                if token in open_pos:
                    open_pos.remove(token)
            if etype == "stopped":
                last_stop_at = when
                stops_last_7d = [t for t in stops_last_7d if when - t <= DAY * 7]
                stops_last_7d.append(when)
                if zero_manual:
                    state.lifetime["clean_stops"] += 1
                    grant(xp_for(e, context={"clean": True}), e, "clean_stop")
            else:
                if zero_manual:
                    state.lifetime["plan_completions"] += 1
                    grant(
                        xp_for(e, context={"zero_manual_actions": True}),
                        e,
                        "full_rule_close",
                    )
            # Outcome trophies (zero XP — Law 1).
            if gain is not None:
                if gain >= 1000:
                    _award(state, "golden_mallard", when, token=e.get("token"),
                           stat=f"+{gain:g}%")
                elif gain >= 300:
                    _award(state, "big_duck", when, token=e.get("token"),
                           stat=f"+{gain:g}%")
                if trace and trace.graduated and gain > 0:
                    _award(state, "banded", when, token=e.get("token"),
                           stat=f"+{gain:g}%")
                if trace and trace.weather == "Storm" and zero_manual \
                        and etype == "retrieved":
                    _award(state, "storm_hunter", when, token=e.get("token"))

        elif etype == "walked_out":
            state.lifetime["extractions"] += 1
            amount = xp_for(
                e,
                context={
                    "already_awarded_this_expedition": expedition_awarded_walkout
                },
            )
            expedition_awarded_walkout = True
            grant(amount, e, "walked_out")
            _award(state, "walked_out", when)
            if len(stops_last_7d) >= 3:
                _award(state, "tough_bird", when)
            if expedition_poacher:
                _award(state, "back_from_the_bank", when)
            if expedition_start and when - expedition_start >= DAY * 7:
                _award(state, "marathon", when)
            state.expedition = {"active": False, "id": state.expedition.get("id")}

        elif etype == "make_camp":
            state.expedition = {"active": False, "id": state.expedition.get("id")}

        elif etype == "bust":
            state.lifetime["busts"] += 1
            state.expedition = {"active": False, "id": state.expedition.get("id")}

        elif etype == "quest_completed":
            grant(xp_for(e), e, f"quest:{e.get('quest_id', '?')}")

        state.best_discipline_streak = max(
            state.best_discipline_streak, state.discipline_streak
        )

    state.level, state.title, state.unlocks = level_for(state.xp)
    if state.level >= 8:
        last = _ts(events[-1]) if events else datetime.now(tz=timezone.utc)
        _award(state, "old_dog", last)
    state.hunter_rating = rating(events)
    return state


def trophies(state: GameState) -> list[dict[str, Any]]:
    return list(state.trophies)


def load_events(log_path: str) -> list[dict[str, Any]]:
    """Read the engine's JSONL event log. Read-only by construction."""
    out: list[dict[str, Any]] = []
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
