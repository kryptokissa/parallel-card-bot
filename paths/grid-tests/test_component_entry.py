"""The component entry point, exercised the way the runtime invokes it.

`wayfinder path exec` runs `python strategy.py <args>` with the path directory
on PYTHONPATH and as cwd. These tests do the same thing directly.

Every argument list below is a literal. Nothing is built from a variable, an
environment value, or a format string — a reviewer reads the bundle and the
tests, and a subprocess call assembled at runtime is indistinguishable from one
that could take arbitrary input.
"""

from __future__ import annotations

import json
from pathlib import Path
import os
import subprocess
import sys

import pytest

ANSWERS_COMPLETE = json.dumps(
    {
        "market": "BTC-USDC",
        "sz_decimals": 5,
        "lower": 94000.0,
        "upper": 106000.0,
        "levels": 6,
        "capital_usd": 5000.0,
        "leverage": 2.0,
        "breakout": "halt_hold",
    }
)
ANSWERS_NO_BREAKOUT = json.dumps(
    {
        "market": "BTC-USDC",
        "sz_decimals": 5,
        "lower": 94000.0,
        "upper": 106000.0,
        "levels": 6,
        "capital_usd": 5000.0,
        "leverage": 2.0,
    }
)


def _run(path_dir, store_path, args: list[str]):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(path_dir)
    env["GRID_STATE_PATH"] = str(store_path)
    completed = subprocess.run(
        [sys.executable, "strategy.py", *args],
        cwd=str(path_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return completed, json.loads(completed.stdout)


def test_questions_lists_the_whole_interview(path_dir, store_path):
    completed, payload = _run(path_dir, store_path, ["questions"])
    assert completed.returncode == 0
    assert payload["ok"]
    keys = [q["key"] for q in payload["result"]["ask_in_order"]]
    assert keys == [
        "market",
        "lower",
        "upper",
        "levels",
        "spacing",
        "capital_usd",
        "leverage",
        "breakout",
    ]


def test_start_refuses_without_a_breakout_choice_and_places_nothing(
    path_dir, store_path
):
    completed, payload = _run(
        path_dir,
        store_path,
        ["start", "--mark", "100000", "--answers", ANSWERS_NO_BREAKOUT],
    )
    assert completed.returncode == 1
    assert not payload["ok"]
    assert payload["result"]["placed_nothing"] is True
    assert "breakout" in payload["result"]["error"]
    assert not store_path.exists()


def test_start_places_every_rung_and_reports_its_gates(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        ["start", "--mark", "100000", "--answers", ANSWERS_COMPLETE],
    )
    assert completed.returncode == 0
    result = payload["result"]
    assert result["status"] == "opening"
    assert len(result["tool_calls"]) == 6
    assert all(
        call["tool"] == "hyperliquid_place_limit_order" for call in result["tool_calls"]
    )
    assert all(gate["passed"] for gate in result["gates"])
    assert result["ttl_seconds"] > 0


def test_the_state_command_names_the_file_it_read(path_dir, store_path):
    _run(path_dir, store_path, ["start", "--mark", "100000", "--answers", ANSWERS_COMPLETE])
    _completed, payload = _run(path_dir, store_path, ["state", "--market", "BTC-USDC"])
    assert payload["result"]["save_file"] == str(store_path)
    assert payload["result"]["started"] is True


def test_gates_command_touches_no_state(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        ["gates", "--mark", "100000", "--answers", ANSWERS_COMPLETE],
    )
    assert completed.returncode == 0
    assert payload["result"]["passed"] is True
    assert not store_path.exists()


def test_gates_command_exits_non_zero_when_a_gate_fails(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        [
            "gates",
            "--mark",
            "100000",
            "--answers",
            json.dumps(
                {
                    "market": "BTC-USDC",
                    "sz_decimals": 5,
                    "lower": 99000.0,
                    "upper": 101000.0,
                    "levels": 40,
                    "capital_usd": 5000.0,
                    "leverage": 2.0,
                    "breakout": "halt_hold",
                }
            ),
        ],
    )
    assert completed.returncode == 1
    assert payload["result"]["passed"] is False


def test_step_refills_only_the_rungs_that_are_gone(path_dir, store_path):
    _run(path_dir, store_path, ["start", "--mark", "100000", "--answers", ANSWERS_COMPLETE])
    _completed, payload = _run(
        path_dir,
        store_path,
        [
            "step",
            "--market",
            "BTC-USDC",
            "--mark",
            "100000",
            "--position",
            "0.03",
            "--open-orders",
            json.dumps(
                [
                    {
                        "cloid": "grid-0-buy",
                        "side": "B",
                        "limitPx": 94000.0,
                        "sz": 0.01773,
                        "oid": 1,
                    }
                ]
            ),
        ],
    )
    result = payload["result"]
    assert result["status"] == "working"
    assert len(result["tool_calls"]) == 5
    # Long position: the sell rungs are genuine exits and carry reduce_only.
    sells = [c for c in result["tool_calls"] if c["is_buy"] is False]
    assert sells and all(c["reduce_only"] for c in sells)


def test_step_with_no_configured_grid_fails_clearly(path_dir, store_path):
    completed, payload = _run(
        path_dir, store_path, ["step", "--market", "ETH-USDC", "--mark", "3000"]
    )
    assert completed.returncode == 1
    assert "no grid configured" in payload["result"]["error"]


def test_a_breakout_halts_and_the_halt_persists(path_dir, store_path):
    _run(path_dir, store_path, ["start", "--mark", "100000", "--answers", ANSWERS_COMPLETE])
    _completed, payload = _run(
        path_dir,
        store_path,
        ["step", "--market", "BTC-USDC", "--mark", "112000", "--position", "0.04"],
    )
    assert payload["result"]["status"] == "halted"
    assert payload["result"]["breakout"] == "halt_hold"

    _again, second = _run(
        path_dir,
        store_path,
        ["step", "--market", "BTC-USDC", "--mark", "100000", "--position", "0.04"],
    )
    # Once halted, the grid does not quietly resume when price comes back.
    assert second["result"]["status"] == "halted"
    assert second["result"]["tool_calls"] == []


def test_propose_returns_a_value_and_a_reason_for_every_field(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        [
            "propose",
            "--market",
            "ETH-USDC",
            "--sz-decimals",
            "4",
            "--mark",
            "3000",
            "--capital",
            "2000",
            "--volatility",
            "5",
        ],
    )
    assert completed.returncode == 0
    result = payload["result"]
    assert set(result["proposal"]) == set(result["reasoning"])
    assert result["proposal"]["breakout"] in {"halt_hold", "halt_close", "recenter"}


def test_propose_refuses_capital_that_cannot_make_a_grid(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        [
            "propose",
            "--market",
            "BTC-USDC",
            "--sz-decimals",
            "5",
            "--mark",
            "100000",
            "--capital",
            "15",
        ],
    )
    assert completed.returncode == 1
    assert not payload["ok"]


def test_a_delegated_proposal_needs_the_confirm_flag(path_dir, store_path):
    delegated = json.dumps(
        {
            "market": {"value": "BTC-USDC", "source": "delegated"},
            "sz_decimals": {"value": 5, "source": "user"},
            "lower": {"value": 94000.0, "source": "delegated"},
            "upper": {"value": 106000.0, "source": "delegated"},
            "levels": {"value": 6, "source": "delegated"},
            "capital_usd": {"value": 5000.0, "source": "user"},
            "leverage": {"value": 2.0, "source": "delegated"},
            "breakout": {"value": "halt_close", "source": "delegated"},
        }
    )
    completed, payload = _run(
        path_dir, store_path, ["start", "--mark", "100000", "--answers", delegated]
    )
    assert completed.returncode == 1
    assert "confirmation" in payload["result"]["error"]

    confirmed, ok_payload = _run(
        path_dir,
        store_path,
        ["start", "--mark", "100000", "--answers", delegated, "--confirm"],
    )
    assert confirmed.returncode == 0
    assert ok_payload["result"]["provenance"]["breakout"] == "delegated"


def test_clamped_leverage_is_surfaced_in_the_start_payload(path_dir, store_path):
    _completed, payload = _run(
        path_dir,
        store_path,
        [
            "start",
            "--mark",
            "100000",
            "--answers",
            json.dumps(
                {
                    "market": "BTC-USDC",
                    "sz_decimals": 5,
                    "lower": 94000.0,
                    "upper": 106000.0,
                    "levels": 6,
                    "capital_usd": 5000.0,
                    "leverage": 50.0,
                    "breakout": "halt_hold",
                }
            ),
        ],
    )
    assert payload["result"]["adjustments"]
    assert any("leverage" in note for note in payload["result"]["adjustments"])


ANSWERS_WITH_DAILY_LIMIT = json.dumps(
    {
        "market": "BTC-USDC",
        "sz_decimals": 5,
        "lower": 94000.0,
        "upper": 106000.0,
        "levels": 6,
        "capital_usd": 5000.0,
        "leverage": 2.0,
        "breakout": "halt_close",
        "daily_loss_limit_usd": 250.0,
    }
)


def test_a_daily_limit_makes_equity_mandatory_on_every_step(path_dir, store_path):
    _run(
        path_dir,
        store_path,
        ["start", "--mark", "100000", "--answers", ANSWERS_WITH_DAILY_LIMIT],
    )
    completed, payload = _run(
        path_dir, store_path, ["step", "--market", "BTC-USDC", "--mark", "100000"]
    )
    assert completed.returncode == 1
    assert "--equity is required" in payload["result"]["error"]


def test_a_step_inside_the_daily_limit_keeps_working(path_dir, store_path):
    _run(
        path_dir,
        store_path,
        ["start", "--mark", "100000", "--answers", ANSWERS_WITH_DAILY_LIMIT],
    )
    _completed, payload = _run(
        path_dir,
        store_path,
        [
            "step",
            "--market",
            "BTC-USDC",
            "--mark",
            "100000",
            "--equity",
            "5000",
        ],
    )
    assert payload["result"]["status"] == "working"
    assert "headroom" in payload["result"]["daily_loss"]


def test_breaching_the_daily_limit_halts_and_stays_halted(path_dir, store_path):
    _run(
        path_dir,
        store_path,
        ["start", "--mark", "100000", "--answers", ANSWERS_WITH_DAILY_LIMIT],
    )
    _first, opened = _run(
        path_dir,
        store_path,
        ["step", "--market", "BTC-USDC", "--mark", "100000", "--equity", "5000"],
    )
    assert opened["result"]["status"] == "working"

    _second, breached = _run(
        path_dir,
        store_path,
        [
            "step",
            "--market",
            "BTC-USDC",
            "--mark",
            "100000",
            "--equity",
            "4600",
            "--position",
            "0.01",
        ],
    )
    assert breached["result"]["status"] == "halted"
    assert breached["result"]["breakout"] == "daily_loss_limit"
    # halt_close was chosen, so the position is flattened with reduce_only.
    flatten = [
        c for c in breached["result"]["tool_calls"] if c["tool"].endswith("limit_order")
    ]
    assert len(flatten) == 1 and flatten[0]["reduce_only"] is True

    _third, after = _run(
        path_dir,
        store_path,
        ["step", "--market", "BTC-USDC", "--mark", "100000", "--equity", "5200"],
    )
    # Recovering equity does not un-halt a grid that hit its limit.
    assert after["result"]["status"] == "halted"
    assert after["result"]["tool_calls"] == []


PRICES_FIXTURE = str(Path(__file__).resolve().parent / "btc_1h_sample.json")


def test_simulate_reports_the_components_separately(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        [
            "simulate",
            "--answers",
            json.dumps(
                {
                    "market": "BTC-USDC",
                    "sz_decimals": 5,
                    "lower": 59000.0,
                    "upper": 66500.0,
                    "levels": 8,
                    "capital_usd": 5000.0,
                    "leverage": 2.0,
                    "breakout": "halt_close",
                }
            ),
            "--prices",
            PRICES_FIXTURE,
        ],
    )
    assert completed.returncode == 0
    result = payload["result"]
    assert result["cycles"] >= 0
    # Cycling, inventory and breakout-exit PnL are never merged into one number.
    for key in ("cycle_edge_usd", "unrealized_pnl_usd", "exit_pnl_usd"):
        assert key in result
    assert "not a forecast" in result["caveat"]
    assert not store_path.exists()


def test_sweep_ranks_by_cycling_edge_and_says_so(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        [
            "sweep",
            "--answers",
            json.dumps(
                {
                    "market": "BTC-USDC",
                    "sz_decimals": 5,
                    "lower": 59000.0,
                    "upper": 66500.0,
                    "levels": 8,
                    "capital_usd": 5000.0,
                    "leverage": 2.0,
                    "breakout": "halt_close",
                }
            ),
            "--prices",
            PRICES_FIXTURE,
            "--max-levels",
            "10",
        ],
    )
    assert completed.returncode == 0
    result = payload["result"]
    assert result["best"] is not None
    assert "cycling edge" in result["ranked_by"]
    passed = [row for row in result["ranked"] if row["gates_passed"]]
    assert passed == sorted(
        passed, key=lambda row: row["cycle_edge_usd"], reverse=True
    )
    assert not store_path.exists()


def test_simulate_refuses_an_empty_price_series(path_dir, store_path):
    completed, payload = _run(
        path_dir,
        store_path,
        [
            "simulate",
            "--answers",
            ANSWERS_COMPLETE,
            "--prices",
            "[]",
        ],
    )
    assert completed.returncode == 1
    assert "empty" in payload["result"]["error"]
