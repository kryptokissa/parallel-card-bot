"""Test fixtures for the grid path.

These tests live outside `paths/grid/` on purpose: everything inside a path
directory ships in the published bundle, and test files full of stub prices and
placeholder wallets read exactly like real code to a reviewer
(BUILDING_PATHS.md §1.3). The path directory is put on `sys.path` here instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PATH_DIR = Path(__file__).resolve().parent.parent / "grid"
if str(PATH_DIR) not in sys.path:
    sys.path.insert(0, str(PATH_DIR))


@pytest.fixture
def path_dir() -> Path:
    return PATH_DIR


@pytest.fixture
def base_config():
    """A grid that passes every gate: BTC at 100k, 12% range, 2x, $5,000."""
    from engine.config import GridConfig

    return GridConfig(
        market="BTC-USDC",
        sz_decimals=5,
        lower=94000.0,
        upper=106000.0,
        levels=6,
        spacing="geometric",
        capital_usd=5000.0,
        leverage=2.0,
        breakout="halt_hold",
    )


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    """Point the state store at a temp file, never a real save file."""
    target = tmp_path / "grid-state.json"
    monkeypatch.setenv("GRID_STATE_PATH", str(target))
    return target
