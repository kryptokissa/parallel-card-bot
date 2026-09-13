"""The daily loss limit, and what it costs to actually enforce one.

A limit the code never reads is worse than no limit: it reads as a guarantee in
the config and the docs while doing nothing. So this module measures the day's
loss from account equity, which is the one number that already includes fills,
fees and funding — the path does not have to replicate the venue's accounting to
know how much money the day has cost.

Equity comes from the exchange on every step. That means a configured limit
makes `--equity` mandatory: without it there is nothing to compare, and silently
continuing would put the fake limit straight back.

The day is a UTC calendar day. Rolling it resets the opening mark, and the roll
is logged so a run that spans midnight is explicable afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from engine.config import GridConfig


def utc_day(now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).date().isoformat()


@dataclass(frozen=True)
class DayRoll:
    """The day's opening equity mark, and whether this step started a new day."""

    day: str
    open_equity: float
    rolled: bool


def roll_day(
    *,
    stored_day: str,
    stored_open_equity: float,
    equity: float,
    now: datetime | None = None,
) -> DayRoll:
    """Return the day and opening equity to work from.

    A new UTC day, or a store with no day recorded, re-marks the opening equity
    to the current value.
    """
    today = utc_day(now)
    if stored_day != today or stored_open_equity <= 0:
        return DayRoll(day=today, open_equity=equity, rolled=stored_day != today)
    return DayRoll(day=today, open_equity=stored_open_equity, rolled=False)


@dataclass(frozen=True)
class DailyLoss:
    day: str
    open_equity: float
    equity: float
    loss: float
    limit: float | None
    breached: bool

    @property
    def headroom(self) -> float | None:
        if self.limit is None:
            return None
        return max(0.0, self.limit - self.loss)

    def summary(self) -> str:
        if self.limit is None:
            return f"no daily loss limit set; the day is {self.loss:+,.2f}"
        if self.breached:
            return (
                f"daily loss limit hit: down ${self.loss:,.2f} today against a "
                f"${self.limit:,.2f} limit (equity ${self.equity:,.2f}, opened at "
                f"${self.open_equity:,.2f})"
            )
        return (
            f"down ${self.loss:,.2f} of a ${self.limit:,.2f} daily limit, "
            f"${self.headroom:,.2f} of headroom"
        )


def assess(config: GridConfig, *, open_equity: float, equity: float) -> DailyLoss:
    """Measure the day against the configured limit.

    Loss is positive when money was lost, so a profitable day reports a negative
    loss and can never breach.
    """
    loss = open_equity - equity
    limit = config.daily_loss_limit_usd
    return DailyLoss(
        day=utc_day(),
        open_equity=open_equity,
        equity=equity,
        loss=loss,
        limit=limit,
        breached=limit is not None and loss >= limit,
    )


def requires_equity(config: GridConfig) -> bool:
    """True when a step cannot be taken without an equity reading."""
    return config.daily_loss_limit_usd is not None


def position_action_on_stop(config: GridConfig) -> str:
    """What to do with the position when the daily limit stops the grid.

    The user already chose what happens to a position when the grid stops, so
    that choice is reused rather than inventing a second policy they were never
    asked about. `recenter` is the exception: rebuilding the grid after hitting a
    loss limit is chasing the loss, so it degrades to holding.
    """
    if config.breakout == "halt_close":
        return "halt_close"
    return "halt_hold"
