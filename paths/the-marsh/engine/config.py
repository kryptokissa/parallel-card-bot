"""The Marsh — engine configuration.

Gates and limits live here and only here. They change between hunts via
explicit configuration updates (logged as config_changed events), never
mid-hunt and never by conversational persuasion: the engine snapshots
the config at hunt start and works from the frozen snapshot.

Game code never imports this module (Design Law 2 — enforced by
tests/test_isolation.py).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

CHAINS = ("solana", "robinhood")

DEFAULT_HUNT_SIZE = {"solana": 0.1, "robinhood": 0.05}  # SOL / ETH


@dataclass(frozen=True)
class MarshConfig:
    """User-tunable knobs; defaults per the published listing."""

    chain: str = "solana"
    hunt_size: float = 0.1  # max native units per shot; never exceeded
    max_open_positions: int = 1
    daily_hunt_limit: int = 3
    # The daily limit rations risk, so only a shot spends a trip.
    # Set True to ration trips to the marsh instead, where a
    # no-duck scout costs the same as a shot.
    daily_limit_counts_refusals: bool = False
    stop_loss_pct: float = -50.0
    retrieve_1_pct: float = 100.0  # sell 50% here
    retrieve_1_fraction: float = 0.5
    retrieve_2_pct: float = 300.0  # sell remainder here
    time_stop_hours: float = 24.0
    time_stop_low_pct: float = -20.0
    time_stop_high_pct: float = 50.0
    min_liquidity_usd: float = 25_000.0
    min_heat: float = 70.0
    age_min_minutes: float = 15.0
    age_max_hours: float = 48.0
    max_top10_holders_pct: float = 60.0
    max_price_impact_pct: float = 3.0
    reentry_lockout_days: float = 7.0

    # Game flags — read by the game/applet layer only; the engine ignores
    # every one of them (verified by tests/test_isolation.py).
    game_enabled: bool = True
    veteran_mode: bool = False
    dog_name: str = "Biscuit"
    lodge_private: bool = False

    def validate(self) -> None:
        if self.chain not in CHAINS:
            raise ValueError(f"chain must be one of {CHAINS}")
        if self.hunt_size <= 0:
            raise ValueError("hunt_size must be positive")
        if self.max_open_positions < 1 or self.daily_hunt_limit < 1:
            raise ValueError("position and hunt limits must be at least 1")
        if not -100.0 < self.stop_loss_pct < 0.0:
            raise ValueError("stop_loss_pct must be a negative percentage")
        if self.retrieve_2_pct <= self.retrieve_1_pct:
            raise ValueError("retrieve_2 must sit above retrieve_1")
        if not 0.0 < self.retrieve_1_fraction <= 1.0:
            raise ValueError("retrieve_1_fraction must be within (0, 1]")

    def with_updates(self, **changes: Any) -> "MarshConfig":
        updated = replace(self, **changes)
        updated.validate()
        return updated
