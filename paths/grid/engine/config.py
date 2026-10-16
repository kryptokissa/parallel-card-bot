"""Frozen grid configuration.

Two rules govern this module.

**Nothing starts without a complete config.** `breakout` has no default. A
grid whose breakout behaviour was never chosen is a grid that holds a
directional position forever, unhedged (BUILDING_PATHS.md §4.3), so
`validate()` refuses it rather than picking for the user.

**Caps clamp, they do not merely range-check.** The open review note on The
Marsh's PR found that `hunt_size` was documented as "never exceeded" while
`validate()` only checked `> 0` — so any trigger or config could raise the
ceiling it advertised. Here `resolve()` clamps every capped field down to its
limit and records the clamp in `adjustments`, which the caller must surface.
A cap that silently fails open is decoration; a cap that silently clamps is a
different bug. Both are avoided by clamping loudly.

The config is snapshotted at grid start and never mutated mid-run. Changes
happen between runs, as explicit `config_changed` events.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

Spacing = Literal["geometric", "arithmetic"]
Breakout = Literal["halt_hold", "halt_close", "recenter"]

SPACINGS: tuple[Spacing, ...] = ("geometric", "arithmetic")
BREAKOUTS: tuple[Breakout, ...] = ("halt_hold", "halt_close", "recenter")

# Hard ceilings. Clamped to, never exceeded, whoever asked.
MAX_LEVERAGE = 5.0
MAX_LEVELS = 50
MIN_LEVELS = 2

# Cost of one side of a round trip, in basis points.
#   5.0  builder fee — DEFAULT_HYPERLIQUID_BUILDER_FEE_TENTHS_BP = 50 tenths-bp
#   1.5  HL maker fee, the fee a resting grid order pays when it fills
# A round trip pays this twice. Spacing that does not clear 2x this loses
# money on every successful cycle (BUILDING_PATHS.md §4.3).
DEFAULT_FEE_BPS_PER_SIDE = 6.5

# Fraction of notional HL holds as maintenance margin. Asset-specific and
# conservative here; the buffer gate multiplies it further.
DEFAULT_MAINTENANCE_MARGIN_FRACTION = 0.02

# Funding, as a signed per-hour fraction of notional. The SDK's convention:
# `cashflow = -position * mid * funding_rate`, so a positive rate means longs
# pay (core/perps/handlers/backtest.py), and rates are per hour
# (core/perps/reconciler.py). 1.25e-5/hour is roughly 11% a year, typical for a
# major. Pass the observed rate from `MarketHandler.funding(symbol)` instead of
# relying on this.
DEFAULT_FUNDING_RATE_PER_HOUR = 1.25e-5

# How long a rung's inventory is expected to sit before the opposite rung fills
# and closes the cycle. Funding is charged for this long.
DEFAULT_EXPECTED_HOLD_HOURS = 8.0


@dataclass(frozen=True)
class GridConfig:
    """Every knob the engine reads. Frozen for the life of a run."""

    # --- market identity -------------------------------------------------
    market: str = ""
    sz_decimals: int = -1  # per-market; required, cannot be guessed

    # --- the grid --------------------------------------------------------
    lower: float = 0.0
    upper: float = 0.0
    levels: int = 0
    spacing: Spacing = "geometric"

    # --- money -----------------------------------------------------------
    capital_usd: float = 0.0
    leverage: float = 1.0

    # --- the decision with no safe default ------------------------------
    breakout: Breakout | None = None
    # recenter only: how many times the grid may re-centre before halting.
    # Unbounded re-centring chases price down forever.
    max_recenters: int = 3

    # --- gate parameters -------------------------------------------------
    fee_bps_per_side: float = DEFAULT_FEE_BPS_PER_SIDE
    # Signed, per hour, as a fraction of notional. See the constant above.
    funding_rate_per_hour: float = DEFAULT_FUNDING_RATE_PER_HOUR
    expected_hold_hours: float = DEFAULT_EXPECTED_HOLD_HOURS
    maintenance_margin_fraction: float = DEFAULT_MAINTENANCE_MARGIN_FRACTION
    # Required equity-to-maintenance ratio at the worst point in the range.
    # A hard gate, not a preference (BUILDING_PATHS.md §4.4).
    min_liquidation_buffer: float = 2.0
    # Spacing must clear round-trip fees by this multiple.
    min_fee_coverage: float = 1.5
    # Largest fraction of capital that may sit unrealized-down when the grid is
    # fully loaded at the worst edge of its range.
    #
    # This is a separate gate from the liquidation buffer, and it is the one
    # that binds. HL holds roughly 2% maintenance margin, so a grid can lose
    # 80% of capital inside its own range and still show a healthy multiple of
    # maintenance. Surviving liquidation and being worth running are different
    # questions; this gate asks the second one.
    max_edge_drawdown: float = 0.35

    # --- optional stops --------------------------------------------------
    daily_loss_limit_usd: float | None = None

    @property
    def fee_cost_bps(self) -> float:
        """Exchange and builder fees for one completed round trip."""
        return 2.0 * self.fee_bps_per_side

    @property
    def funding_cost_bps(self) -> float:
        """Funding charged against a rung's inventory over one cycle.

        The magnitude is used, not the signed value. A grid holds long inventory
        below the mark and short inventory above it over its life, so whether the
        current sign happens to favour the side it is holding now is not
        something a floor should depend on.
        """
        return abs(self.funding_rate_per_hour) * self.expected_hold_hours * 1e4

    @property
    def round_trip_cost_bps(self) -> float:
        """Everything a completed cycle costs, in basis points of notional."""
        return self.fee_cost_bps + self.funding_cost_bps

    @property
    def required_spacing_bps(self) -> float:
        """The tightest gap that is worth trading."""
        return self.round_trip_cost_bps * self.min_fee_coverage

    def required_fields_missing(self) -> list[str]:
        """Fields with no safe default that the interview must still fill."""
        missing: list[str] = []
        if not self.market:
            missing.append("market")
        if self.sz_decimals < 0:
            missing.append("sz_decimals")
        if self.lower <= 0:
            missing.append("lower")
        if self.upper <= 0:
            missing.append("upper")
        if self.levels < MIN_LEVELS:
            missing.append("levels")
        if self.capital_usd <= 0:
            missing.append("capital_usd")
        if self.breakout is None:
            missing.append("breakout")
        return missing

    def validate(self) -> None:
        """Raise unless this config is complete and internally coherent.

        Does not clamp — `resolve()` clamps first, then calls this. Anything
        still wrong here is a contradiction the user has to resolve.
        """
        missing = self.required_fields_missing()
        if missing:
            raise ValueError(
                "grid cannot start with unanswered questions: "
                + ", ".join(missing)
                + ". Run the interview first."
            )
        if self.breakout not in BREAKOUTS:
            raise ValueError(f"breakout must be one of {BREAKOUTS}")
        if self.spacing not in SPACINGS:
            raise ValueError(f"spacing must be one of {SPACINGS}")
        if self.upper <= self.lower:
            raise ValueError(
                f"upper ({self.upper}) must sit above lower ({self.lower})"
            )
        if self.levels > MAX_LEVELS:
            raise ValueError(f"levels must not exceed {MAX_LEVELS}")
        if not 0.0 < self.leverage <= MAX_LEVERAGE:
            raise ValueError(f"leverage must be within (0, {MAX_LEVERAGE}]")
        if self.fee_bps_per_side < 0:
            raise ValueError("fee_bps_per_side cannot be negative")
        if self.expected_hold_hours < 0:
            raise ValueError("expected_hold_hours cannot be negative")
        if not 0.0 <= self.maintenance_margin_fraction < 1.0:
            raise ValueError("maintenance_margin_fraction must be within [0, 1)")
        if self.min_liquidation_buffer < 1.0:
            raise ValueError(
                "min_liquidation_buffer below 1.0 permits starting a grid that "
                "is already past maintenance margin at the range edge"
            )
        if self.min_fee_coverage < 1.0:
            raise ValueError(
                "min_fee_coverage below 1.0 permits spacing that loses money on "
                "every completed cycle"
            )
        if not 0.0 < self.max_edge_drawdown <= 1.0:
            raise ValueError("max_edge_drawdown must be within (0, 1]")
        if self.max_recenters < 0:
            raise ValueError("max_recenters cannot be negative")
        if self.daily_loss_limit_usd is not None and self.daily_loss_limit_usd <= 0:
            raise ValueError("daily_loss_limit_usd must be positive when set")

    def with_updates(self, **changes: Any) -> "GridConfig":
        """Between-run config change. Validates the result."""
        updated = replace(self, **changes)
        updated.validate()
        return updated


@dataclass
class ResolvedConfig:
    """A clamped config plus the record of what clamping did."""

    config: GridConfig
    adjustments: list[str] = field(default_factory=list)

    @property
    def was_clamped(self) -> bool:
        return bool(self.adjustments)


def resolve(config: GridConfig) -> ResolvedConfig:
    """Clamp capped fields down to their ceilings, recording every clamp.

    Clamping happens whoever asked — user, delegated AI proposal, or a stored
    config file. The ceiling is the ceiling. Returns the clamped config and
    the human-readable list of what changed; callers surface that list rather
    than applying it quietly.
    """
    adjustments: list[str] = []
    changes: dict[str, Any] = {}

    if config.leverage > MAX_LEVERAGE:
        adjustments.append(
            f"leverage {config.leverage}x exceeds the {MAX_LEVERAGE}x ceiling — "
            f"clamped to {MAX_LEVERAGE}x"
        )
        changes["leverage"] = MAX_LEVERAGE

    if config.levels > MAX_LEVELS:
        adjustments.append(
            f"levels {config.levels} exceeds the {MAX_LEVELS} ceiling — "
            f"clamped to {MAX_LEVELS}"
        )
        changes["levels"] = MAX_LEVELS

    if config.min_liquidation_buffer < 1.0:
        adjustments.append(
            f"min_liquidation_buffer {config.min_liquidation_buffer} raised to the "
            "1.0 floor — below that the grid starts past maintenance margin"
        )
        changes["min_liquidation_buffer"] = 1.0

    if config.min_fee_coverage < 1.0:
        adjustments.append(
            f"min_fee_coverage {config.min_fee_coverage} raised to the 1.0 floor — "
            "below that every completed cycle loses money"
        )
        changes["min_fee_coverage"] = 1.0

    if config.max_edge_drawdown > 1.0:
        adjustments.append(
            f"max_edge_drawdown {config.max_edge_drawdown} exceeds 1.0 — clamped to "
            "1.0; a grid cannot risk more than the capital behind it"
        )
        changes["max_edge_drawdown"] = 1.0

    resolved = replace(config, **changes) if changes else config
    return ResolvedConfig(config=resolved, adjustments=adjustments)
