"""The declared component must actually run.

The host starts this path by executing the component named in
wfpath.yaml — `python strategy.py <args>`. For five versions strategy.py
had no __main__ block, so the host ran it, got nothing, and correctly
reported that no path code ran. These tests hold that door open.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPONENT = os.path.join(ROOT, "strategy.py")
# Every command these tests may run, written out in full. There is no
# argument spreading, concatenation or formatting anywhere below: each
# entry is a complete, literal argv list, and _run can only execute one
# of them by name. The interpreter and the component path are fixed
# constants. Nothing user-supplied or environment-derived can reach a
# command line from here. calm_day is a practice marsh that ships in
# engine/practice.py.
COMMANDS: dict[str, list[str]] = {
    "contract": [sys.executable, COMPONENT],
    "hunt": [sys.executable, COMPONENT, "hunt", "--ghost",
             "--fixture", "calm_day"],
    "go": [sys.executable, COMPONENT, "go", "--ghost",
           "--fixture", "calm_day"],
    "ape": [sys.executable, COMPONENT, "ape", "--ghost",
            "--fixture", "calm_day"],
    "state": [sys.executable, COMPONENT, "state"],
}


def _run(name, tmp_path, timeout=120):
    """Run one of the fixed commands above, by name, in a child process.

    No shell (passed explicitly rather than left to the default, so its
    absence is visible), and the argv list comes straight out of
    COMMANDS -- it is never assembled here.
    """
    env = dict(os.environ)
    env["MARSH_EVENT_LOG"] = str(tmp_path / "events.jsonl")
    return subprocess.run(
        COMMANDS[name],
        shell=False, cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def test_only_declared_commands_can_run(tmp_path):
    """A name with no entry cannot become a command line."""
    import pytest

    with pytest.raises(KeyError):
        _run("hunt; rm -rf /", tmp_path)


def test_component_is_executable_and_reports_contract(tmp_path):
    """No arguments: the host gets the page contract as JSON."""
    proc = _run("contract", tmp_path)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload) == {"meta", "state", "decision"}
    assert payload["meta"]["kind"] == "strategy"
    assert "status" in payload["state"]


def test_component_runs_a_hunt(tmp_path):
    """The host's actual ask: run a hunt through the component."""
    proc = _run("hunt", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "🐕" in proc.stdout, proc.stdout
    events = [json.loads(line) for line in
              open(tmp_path / "events.jsonl", encoding="utf-8") if line.strip()]
    assert any(e["type"] == "ghost_shot" for e in events)


def test_trigger_words_reach_the_dog(tmp_path):
    """'go' and 'ape' are the spoken triggers, not just 'hunt'."""
    for word in ("go", "ape"):
        proc = _run(word, tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "🐕" in proc.stdout


def test_state_survives_a_missing_log(tmp_path):
    """A hunter who has never gone out is not an error."""
    proc = _run("state", tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["hunter"]["level"] == 1


def test_skill_instructions_use_the_supported_wrapper(tmp_path):
    """scripts/wf_run.py is the supported route. An earlier version of
    these instructions told hosts to avoid it, on the strength of a
    failure that turned out to be `poetry run` eating the `--`
    separator, not a fault in the wrapper."""
    text = open(os.path.join(ROOT, "skill", "instructions.md"),
                encoding="utf-8").read()
    machinery = text[text.index("## Machinery"):]
    assert "python scripts/wf_run.py hunt" in machinery
    assert "Do not route these through" not in machinery
