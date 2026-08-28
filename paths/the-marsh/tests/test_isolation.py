"""Design Law 2 — the game is strictly read-only over the engine.

Two checks: the game package must not import engine (or any trading)
code, and the engine must behave identically with game_enabled False.
"""

from __future__ import annotations

import ast
import os

from engine.config import MarshConfig
from helpers import run_day

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_game_imports_nothing_from_engine():
    game_dir = os.path.join(ROOT, "game")
    for fname in os.listdir(game_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(game_dir, fname), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            for module in modules:
                head = module.split(".")[0]
                assert head not in ("engine", "wayfinder_paths"), (
                    f"{fname} imports {module}: the game may not touch "
                    f"trading code or config"
                )


def _strip_variable(events):
    cleaned = []
    for event in events:
        cleaned.append({
            k: v for k, v in event.items()
            if k not in ("ts", "tx", "position_id", "hunt_id")
        })
    return cleaned


def test_engine_identical_with_game_disabled(tmp_path):
    _, with_game = run_day(tmp_path / "a", "calm_day.json",
                           config=MarshConfig(game_enabled=True))
    _, without_game = run_day(tmp_path / "b", "calm_day.json",
                              config=MarshConfig(game_enabled=False))
    assert _strip_variable(with_game) == _strip_variable(without_game)


def test_game_flags_do_not_change_gates(tmp_path):
    _, veteran = run_day(tmp_path / "a", "no_duck_day.json",
                         config=MarshConfig(veteran_mode=True,
                                            dog_name="Ranger"))
    _, default = run_day(tmp_path / "b", "no_duck_day.json")
    keep = ("no_duck", "duck_scouted", "shot")
    assert (_strip_variable([e for e in veteran if e["type"] in keep])
            == _strip_variable([e for e in default if e["type"] in keep]))
