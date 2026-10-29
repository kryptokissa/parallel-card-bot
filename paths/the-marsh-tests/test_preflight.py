"""Preflight must state readiness exactly -- neither way.

The first version answered one question -- can the runtime import the
Solana submission helpers -- and exited 0 on that alone. Read by
someone about to fund a wallet, exit 0 says "cleared to trade", which
it never meant: a runtime that can broadcast is useless without a
satchel to spend from.

Over-correcting was just as wrong. Once the pack decided shots and the
agent executed them, nothing here broadcast anything, so gating on
whether this wrapper *could* broadcast refused hunts the agent was
perfectly able to take. Both failures read as fact to someone holding
real money. These pin each answer to what it actually governs.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "the-marsh")
COMPONENT = [sys.executable, os.path.join(ROOT, "strategy.py"), "preflight"]


def _preflight(tmp_path, wallets=None, api_key=None):
    env = dict(os.environ)
    env.pop("WAYFINDER_API_KEY", None)
    env.pop("WAYFINDER_CONFIG", None)
    if api_key:
        env["WAYFINDER_API_KEY"] = api_key
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"wallets": wallets or []}), encoding="utf-8")
    env["WAYFINDER_CONFIG_PATH"] = str(cfg)
    return subprocess.run(COMPONENT, shell=False, cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)


def test_no_satchel_is_not_ready_even_when_the_runtime_can_broadcast(tmp_path):
    proc = _preflight(tmp_path, wallets=[], api_key="wk_test")
    assert proc.returncode == 2, proc.stdout
    assert "none found" in proc.stdout
    assert "no satchel wallet the runner can resolve" in proc.stdout
    assert "Do not fund a satchel yet" in proc.stdout
    assert "ready for real money" not in proc.stdout.replace(
        "Not ready for real money", "")


def test_no_api_key_blocks_because_it_cannot_even_scout(tmp_path):
    proc = _preflight(tmp_path,
                      wallets=[{"label": "the-marsh", "address": "Sat11111"}])
    assert proc.returncode == 2, proc.stdout
    assert "cannot even be scouted" in proc.stdout


def test_a_wrapper_that_cannot_broadcast_does_not_block(tmp_path):
    """The agent broadcasts, so this wrapper's inability must not gate.

    The published runtime ships no svm modules, so for most hunters
    ``can_send`` is False. Gating on it made every one of them read
    "Not ready for real money" while their agent could fire fine.
    """
    proc = _preflight(tmp_path,
                      wallets=[{"label": "the-marsh", "address": "Sat11111"}],
                      api_key="wk_test")
    assert proc.returncode == 0, proc.stdout
    flat = " ".join(proc.stdout.split())
    assert "Broadcast is not a blocker: your agent does it." in flat
    assert "your agent executes it with onchain_swap" in flat


def test_a_configured_satchel_is_reported(tmp_path):
    proc = _preflight(tmp_path,
                      wallets=[{"label": "the-marsh", "address": "Sat11111"}],
                      api_key="wk_test")
    assert "the-marsh" in proc.stdout
    assert "1 configured" in proc.stdout


def test_it_always_says_this_route_cannot_trade(tmp_path):
    """True whatever the runtime reports, so it must always be said."""
    for wallets in ([], [{"label": "the-marsh", "address": "Sat11111"}]):
        proc = _preflight(tmp_path, wallets=wallets)
        assert "cannot take a live shot" in proc.stdout
        assert "signing callback" in proc.stdout


def test_a_ready_runtime_still_warns_about_the_first_shot(tmp_path):
    """No real fill has ever executed; exit 0 must not imply otherwise.

    This no longer skips on a runtime that cannot broadcast: that is
    precisely the runtime most hunters have, and the warning is for
    them.
    """
    proc = _preflight(tmp_path,
                      wallets=[{"label": "the-marsh", "address": "Sat11111"}],
                      api_key="wk_test")
    assert proc.returncode == 0, proc.stdout
    # The banner wraps, so compare on collapsed whitespace rather than
    # on a phrase that happens to survive the line breaks today.
    flat = " ".join(proc.stdout.split())
    assert "Nothing here has ever moved real funds" in flat
    assert "first live shot a token one (~0.01)" in flat
