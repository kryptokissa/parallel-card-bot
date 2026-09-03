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
FIXTURE = "calm_day"  # a practice marsh that ships with the pack


def _run(args, tmp_path, timeout=120):
    env = dict(os.environ)
    env["MARSH_EVENT_LOG"] = str(tmp_path / "events.jsonl")
    return subprocess.run(
        [sys.executable, COMPONENT, *args],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )


def test_component_is_executable_and_reports_contract(tmp_path):
    """No arguments: the host gets the page contract as JSON."""
    proc = _run([], tmp_path)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert set(payload) == {"meta", "state", "decision"}
    assert payload["meta"]["kind"] == "strategy"
    assert "status" in payload["state"]


def test_component_runs_a_hunt(tmp_path):
    """The host's actual ask: run a hunt through the component."""
    proc = _run(["hunt", "--ghost", "--fixture", FIXTURE], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "🐕" in proc.stdout, proc.stdout
    events = [json.loads(line) for line in
              open(tmp_path / "events.jsonl", encoding="utf-8") if line.strip()]
    assert any(e["type"] == "ghost_shot" for e in events)


def test_trigger_words_reach_the_dog(tmp_path):
    """'go' and 'ape' are the spoken triggers, not just 'hunt'."""
    for word in ("go", "ape"):
        proc = _run([word, "--ghost", "--fixture", FIXTURE], tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "🐕" in proc.stdout


def test_state_survives_a_missing_log(tmp_path):
    """A hunter who has never gone out is not an error."""
    proc = _run(["state"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["hunter"]["level"] == 1


def test_skill_instructions_do_not_point_at_the_broken_wrapper(tmp_path):
    """scripts/wf_run.py cannot start a component under click >= 8.3
    (it sends a `--` that `wayfinder path exec` rejects), so the dog's
    instructions must not route through it."""
    text = open(os.path.join(ROOT, "skill", "instructions.md"),
                encoding="utf-8").read()
    machinery = text[text.index("## Machinery"):]
    assert "python path/strategy.py hunt" in machinery
    assert "Do not route these through `scripts/wf_run.py`" in machinery
