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

# Every token these tests are allowed to put on a command line. The
# executable and the script are fixed above; only arguments vary, and
# only to values named here. Nothing user-supplied, nothing derived
# from the environment, and nothing built by string formatting ever
# reaches the child process.
ALLOWED_ARGS = frozenset({
    # spoken commands
    "hunt", "go", "go-now", "ape", "whistle", "recap", "state",
    # flags
    "--ghost", "--live-feed", "--fixture",
    # practice marshes that ship in engine/practice.py
    "calm_day", "no_duck_day", "storm_bust",
})


def _run(args, tmp_path, timeout=120):
    """Run the declared component in a child process.

    The command is a fixed argv list — the running interpreter and the
    absolute path to the component — with no shell between us and it
    (subprocess defaults to shell=False; it is passed explicitly here
    so the absence is visible rather than assumed). Arguments are
    checked against ALLOWED_ARGS before the call, so an argument added
    carelessly to a future test fails here rather than being handed to
    a process.
    """
    for arg in args:
        if not isinstance(arg, str) or arg not in ALLOWED_ARGS:
            raise AssertionError(f"argument not on the allowlist: {arg!r}")
    env = dict(os.environ)
    env["MARSH_EVENT_LOG"] = str(tmp_path / "events.jsonl")
    return subprocess.run(
        [sys.executable, COMPONENT, *args],
        shell=False, cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def test_runner_rejects_an_argument_off_the_allowlist(tmp_path):
    """The guard is real, not decorative."""
    import pytest

    with pytest.raises(AssertionError, match="not on the allowlist"):
        _run(["hunt; rm -rf /"], tmp_path)
    with pytest.raises(AssertionError, match="not on the allowlist"):
        _run(["--fixture", "../../etc/passwd"], tmp_path)


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
