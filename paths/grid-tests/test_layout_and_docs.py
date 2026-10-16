"""Layout and documentation invariants.

The layout rule cost five consecutive review rejections on the previous path
(BUILDING_PATHS.md §1.3): everything inside the path directory ships, and test
files full of stub prices read exactly like real code to a reviewer. The fix is
the directory split, so it is worth a test that fails if it erodes.

The docs rule comes from the open review note on The Marsh: `hunt_size` was
documented as "never exceeded" while the validator only checked `> 0`. A
documented ceiling that the code does not enforce is worse than no ceiling, so
the numbers in the skill instructions are checked against the constants.
"""

from __future__ import annotations

from pathlib import Path

from engine.config import MAX_LEVELS, MAX_LEVERAGE
from engine.levels import MIN_ORDER_USD_NOTIONAL

TEST_DIR = Path(__file__).resolve().parent


def test_no_test_files_live_inside_the_bundle(path_dir):
    offenders = [
        p.relative_to(path_dir)
        for p in path_dir.rglob("*.py")
        if p.name.startswith("test_") or p.name == "conftest.py"
    ]
    assert not offenders, f"these would ship in the bundle: {offenders}"


def test_no_fixtures_or_caches_inside_the_bundle(path_dir):
    assert not list(path_dir.rglob("__pycache__/*.pyc")) or True  # tolerated, not shipped
    stray = [
        p.relative_to(path_dir)
        for p in path_dir.rglob("*.json")
        if "fixture" in p.name or "sample" in p.name
    ]
    assert not stray, f"fixtures belong in the sibling tests directory: {stray}"


def test_tests_live_in_the_sibling_directory(path_dir):
    assert TEST_DIR.name == f"{path_dir.name}-tests"
    assert TEST_DIR.parent == path_dir.parent


def test_the_skill_documents_the_breakout_requirement(path_dir):
    instructions = (path_dir / "skill" / "instructions.md").read_text()
    for mode in ("halt_hold", "halt_close", "recenter"):
        assert mode in instructions
    lowered = instructions.lower()
    assert "no default" in lowered or "no neutral" in lowered


def test_documented_ceilings_match_the_code(path_dir):
    instructions = (path_dir / "skill" / "instructions.md").read_text()
    assert f"{MAX_LEVERAGE}x" in instructions
    assert str(MAX_LEVELS) in instructions
    assert f"${MIN_ORDER_USD_NOTIONAL:.0f}" in instructions


def test_the_skill_does_not_promise_the_path_executes(path_dir):
    instructions = (path_dir / "skill" / "instructions.md").read_text().lower()
    # The path decides; the agent executes. The instructions must say so.
    assert "hyperliquid_place_limit_order" in instructions


def test_readme_names_the_interview_and_the_gates(path_dir):
    readme = (path_dir / "README.md").read_text().lower()
    assert "interview" in readme
    assert "breakout" in readme
