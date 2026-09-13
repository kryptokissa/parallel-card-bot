"""The bundle-contents gate.

PUBLISHING.md §1 asks the agent to eyeball the listing and fail on tests,
fixtures, caches and scratch files. This is that check made mechanical, so it
needs its own tests — an unchecked gate is the same failure as a documented
limit no code reads.
"""

from __future__ import annotations

import zipfile

import pytest

from check_bundle_contents import offence


def _names(*entries):
    return list(entries)


@pytest.mark.parametrize(
    "name",
    [
        "wfpath.yaml",
        "strategy.py",
        "README.md",
        ".gitignore",
        "engine/config.py",
        "skill/instructions.md",
        "applet/dist/index.html",
        "applet/dist/assets/app.js",
        "scripts/marsh_run.py",
        "game/marsh_engine.py",
    ],
)
def test_legitimate_bundle_entries_pass(name):
    assert offence(name) is None


@pytest.mark.parametrize(
    "name,fragment",
    [
        ("test_levels.py", "test file"),
        ("engine/test_levels.py", "test file"),
        ("levels_test.py", "test file"),
        ("conftest.py", "pytest"),
        ("engine/conftest.py", "pytest"),
        ("engine/__pycache__/config.cpython-312.pyc", "cache"),
        ("engine/config.pyc", "bytecode"),
        ("btc_1h_sample.json", "fixture"),
        ("brap_quote_fixture.json", "fixture"),
        ("notes.bak", "backup"),
        ("config.py.orig", "merge leftover"),
        ("strategy.py~", "editor backup"),
        (".env", "environment"),
        ("signer.pem", "key material"),
        ("run.log", "log"),
        (".build/skills/claude/grid/SKILL.md", "skill exports"),
        ("tests/helpers.py", "'tests/' directory"),
        ("test/helper.py", "'test/' directory"),
        ("node_modules/pkg/index.js", "'node_modules/' directory"),
    ],
)
def test_things_that_must_not_ship_are_caught(name, fragment):
    reason = offence(name)
    assert reason is not None, f"{name} should have been flagged"
    assert fragment in reason


@pytest.mark.parametrize(
    "name",
    [
        "tests/evals/output_shape.yaml",
        "tests/evals/host-coverage.yaml",
        "tests/fixtures/calm_day.json",
    ],
)
def test_sdk_mandated_eval_directories_are_allowed(name):
    """`tests/evals/` is required, not leftover.

    `paths/doctor.py` errors with "Pipeline paths must ship at least 3 evals"
    when it is missing and `paths/evaluator.py` raises "Missing tests/evals", so
    a blanket ban on `tests/` would fail a publish that is correct. Assay's
    published bundle ships two of these.
    """
    assert offence(name) is None


@pytest.mark.parametrize(
    "name",
    ["tests/evals/test_sneak.py", "tests/fixtures/helper.py", "tests/evals/x.pyc"],
)
def test_python_is_still_banned_inside_the_eval_directories(name):
    reason = offence(name)
    assert reason is not None
    assert "eval directory" in reason


def test_the_exemption_does_not_leak_to_other_test_directories():
    # Only those two prefixes are sanctioned.
    assert offence("tests/other/thing.yaml") is not None
    assert offence("engine/tests/evals/thing.yaml") is not None


def test_the_script_exits_non_zero_on_a_dirty_bundle(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    bundle = tmp_path / "dirty.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name in _names("wfpath.yaml", "test_thing.py"):
            archive.writestr(name, "x")

    script = Path(__file__).resolve().parent.parent / "tools" / "check_bundle_contents.py"
    result = subprocess.run(
        [sys.executable, str(script), str(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "MUST NOT SHIP" in result.stdout


def test_the_script_exits_zero_on_a_clean_bundle(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    bundle = tmp_path / "clean.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name in _names("wfpath.yaml", "strategy.py", "tests/evals/shape.yaml"):
            archive.writestr(name, "x")

    script = Path(__file__).resolve().parent.parent / "tools" / "check_bundle_contents.py"
    result = subprocess.run(
        [sys.executable, str(script), str(bundle)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "clean" in result.stdout


def test_usage_error_is_distinct_from_a_failed_check(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "tools" / "check_bundle_contents.py"
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, check=False
    )
    # 2 for misuse, 1 for a bundle that must not ship — a caller can tell them
    # apart.
    assert result.returncode == 2
