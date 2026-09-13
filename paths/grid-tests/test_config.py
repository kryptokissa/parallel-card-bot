"""Config completeness and the clamping rule.

The open review note on The Marsh's PR found `hunt_size` documented as "never
exceeded" while `validate()` only checked `> 0`. These tests pin the opposite
behaviour for the grid: caps clamp, and every clamp is reported.
"""

from __future__ import annotations

import pytest

from engine.config import (
    MAX_LEVELS,
    MAX_LEVERAGE,
    GridConfig,
    resolve,
)


def test_empty_config_names_every_missing_answer(base_config):
    missing = GridConfig().required_fields_missing()
    assert set(missing) == {
        "market",
        "sz_decimals",
        "lower",
        "upper",
        "levels",
        "capital_usd",
        "breakout",
    }


def test_grid_refuses_to_start_without_a_breakout_choice(base_config):
    unanswered = GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=94000.0,
        upper=106000.0,
        levels=6,
        capital_usd=5000.0,
        breakout=None,
    )
    with pytest.raises(ValueError, match="breakout"):
        unanswered.validate()


def test_leverage_above_the_ceiling_is_clamped_not_accepted(base_config):
    resolved = resolve(base_config.with_updates(leverage=MAX_LEVERAGE))
    assert resolved.config.leverage == MAX_LEVERAGE

    # A config asking for more than the ceiling cannot even be built through
    # with_updates (it validates), so construct it directly — the path a stored
    # config file or a delegated proposal would take.
    over = GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=94000.0,
        upper=106000.0,
        levels=6,
        capital_usd=5000.0,
        leverage=50.0,
        breakout="halt_hold",
    )
    resolved = resolve(over)
    assert resolved.config.leverage == MAX_LEVERAGE
    assert resolved.was_clamped
    assert any("leverage" in note for note in resolved.adjustments)


def test_level_count_above_the_ceiling_is_clamped():
    over = GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=94000.0,
        upper=106000.0,
        levels=MAX_LEVELS + 49,
        capital_usd=5000.0,
        breakout="halt_hold",
    )
    resolved = resolve(over)
    assert resolved.config.levels == MAX_LEVELS
    assert any("levels" in note for note in resolved.adjustments)


def test_clamping_is_never_silent():
    over = GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=94000.0,
        upper=106000.0,
        levels=6,
        capital_usd=5000.0,
        leverage=99.0,
        breakout="halt_hold",
        min_fee_coverage=0.1,
        min_liquidation_buffer=0.5,
    )
    resolved = resolve(over)
    # Three separate ceilings were hit; all three are reported.
    assert len(resolved.adjustments) == 3
    resolved.config.validate()


def test_a_clamped_config_validates_afterwards():
    over = GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=94000.0,
        upper=106000.0,
        levels=500,
        capital_usd=5000.0,
        leverage=100.0,
        breakout="halt_hold",
    )
    resolve(over).config.validate()


def test_inverted_range_is_a_contradiction_not_a_clamp(base_config):
    with pytest.raises(ValueError, match="above"):
        GridConfig(
            market="BTC-USDC",
            sz_decimals=5,
            lower=106000.0,
            upper=94000.0,
            levels=6,
            capital_usd=5000.0,
            breakout="halt_hold",
        ).validate()


def test_unknown_breakout_and_spacing_are_rejected(base_config):
    with pytest.raises(ValueError, match="breakout"):
        base_config.with_updates(breakout="panic")
    with pytest.raises(ValueError, match="spacing"):
        base_config.with_updates(spacing="logarithmic")


def test_drawdown_limit_must_be_a_fraction(base_config):
    with pytest.raises(ValueError, match="max_edge_drawdown"):
        base_config.with_updates(max_edge_drawdown=0.0)
    resolved = resolve(
        GridConfig(
            market="BTC-USDC",
            sz_decimals=5,
            lower=94000.0,
            upper=106000.0,
            levels=6,
            capital_usd=5000.0,
            breakout="halt_hold",
            max_edge_drawdown=3.0,
        )
    )
    assert resolved.config.max_edge_drawdown == 1.0
