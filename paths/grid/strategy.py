#!/usr/bin/env python3
"""Grid — component entry point.

`wayfinder path exec` runs this file with the path directory on PYTHONPATH, so
every command below is a subcommand of this script and every response is JSON
on stdout for the agent to read.

The path decides; the agent executes (BUILDING_PATHS.md §2.1). Nothing here
signs, sends, or places anything: commands return `tool_calls`, which are the
`hyperliquid_*` calls the agent should make. `wallet_label` is left as a
placeholder on purpose — labels are generated per install (§2.2) and the agent
knows its own.

Order of operations for a new grid:

    questions   ->  what to ask the user
    propose     ->  optional; a concrete grid when the user delegates
    start       ->  validate, clamp, run the hard gates, place the rungs
    step        ->  refill filled rungs, or act on a breakout
    state       ->  what the grid thinks, and which save file it read
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from engine import gates as gates_mod
from engine import plan as plan_mod
from engine import store as store_mod
from engine.config import GridConfig, resolve
from engine.interview import QUESTIONS, Interview, propose, ready_to_start
from engine.levels import GridGeometryError
from engine.reconcile import reconcile
from engine.simulate import best as best_row
from engine.simulate import simulate, sweep
from engine.risk import (
    assess as assess_daily_loss,
)
from engine.risk import (
    position_action_on_stop,
    requires_equity,
    roll_day,
)

WALLET_PLACEHOLDER = "<your grid wallet label>"


# --------------------------------------------------------------------------
# host-facing metadata
# --------------------------------------------------------------------------
def wfpath_meta() -> dict[str, Any]:
    return {
        "name": "Grid",
        "kind": "strategy",
        "ui_mode": "auto",
        "tracking_mode": "hybrid",
    }


def wfpath_state() -> dict[str, Any]:
    state = store_mod.load(store_mod.default_store_path(_market_hint()))
    config = state.grid_config()
    return {
        "status": (
            "halted"
            if state.halted
            else ("working" if state.started else "awaiting_interview")
        ),
        "selection": {"market": config.market} if config else {},
        "metrics": {
            "levels": config.levels if config else 0,
            "recenters_used": state.recenters_used,
            "events": len(state.events),
        },
        "positions": [],
        "save_file": state.store_path,
    }


def wfpath_decision() -> dict[str, Any]:
    state = store_mod.load(store_mod.default_store_path(_market_hint()))
    config = state.grid_config()
    if config is None:
        return {
            "summary": "No grid configured. Run the interview first.",
            "selected": {},
            "candidates": [],
        }
    return {
        "summary": (
            f"{config.levels}-level {config.spacing} grid on {config.market}, "
            f"{config.lower:,.2f}–{config.upper:,.2f}, {config.leverage:g}x, "
            f"breakout={config.breakout}."
        ),
        "selected": {"market": config.market, "breakout": config.breakout},
        "candidates": [],
    }


def _market_hint() -> str:
    """Market used to locate the save file when no argument was supplied."""
    import os

    return os.environ.get("GRID_MARKET", "default")


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _emit(payload: dict[str, Any], *, ok: bool = True, code: int = 0) -> int:
    print(json.dumps({"ok": ok, "result": payload}, indent=2, sort_keys=True))
    return code


def _fail(message: str, **detail: Any) -> int:
    return _emit({"error": message, **detail}, ok=False, code=1)


def _load_json_arg(raw: str | None) -> Any:
    """Accept either inline JSON or a path to a JSON file.

    Inline JSON is detected by its first character, never by asking the
    filesystem: a realistic answers payload is longer than the 255-byte limit
    on a filename, and `Path.exists()` raises OSError rather than returning
    False for one.
    """
    if not raw:
        return {}
    text = raw.strip()
    if text[:1] in "{[":
        return json.loads(text)
    candidate = Path(text).expanduser()
    if candidate.exists():
        return json.loads(candidate.read_text())
    raise ValueError(
        f"{text!r} is neither inline JSON nor a file that exists"
    )


def _load_price_series(raw: str | None) -> list[Any] | None:
    """Read a price series: a bare list, or an object with a `bars` key.

    Accepts floats, (high, low, close) triples, or Hyperliquid candle dicts —
    whatever `engine.simulate.Bar.of` understands.
    """
    if not raw:
        return None
    loaded = _load_json_arg(raw)
    if isinstance(loaded, list):
        series = loaded
    elif isinstance(loaded, dict):
        series = loaded.get("bars") or loaded.get("prices") or []
    else:
        raise ValueError("a price series must be a list, or carry a 'bars' key")
    if not series:
        raise ValueError("the price series is empty")
    return series


def _interview_from_answers(answers: dict[str, Any]) -> Interview:
    """Build an interview from `{key: value}` or `{key: {value, source, ...}}`."""
    interview = Interview()
    for key, entry in answers.items():
        if isinstance(entry, dict) and "value" in entry:
            interview.record(
                key,
                entry["value"],
                entry.get("source", "user"),
                entry.get("rationale", ""),
            )
        else:
            interview.record(key, entry, "user")
    return interview


def _tool_calls(grid_plan: plan_mod.GridPlan, market: str) -> list[dict[str, Any]]:
    return [intent.as_tool_call(market, WALLET_PLACEHOLDER) for intent in grid_plan.intents]


def _plan_payload(grid_plan: plan_mod.GridPlan, market: str) -> dict[str, Any]:
    return {
        "status": grid_plan.status,
        "summary": grid_plan.summary,
        "breakout": grid_plan.breakout,
        "ttl_seconds": grid_plan.ttl_seconds,
        "tool_calls": _tool_calls(grid_plan, market),
    }


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_questions(_: argparse.Namespace) -> int:
    return _emit(
        {
            "ask_in_order": [
                {
                    "key": q.key,
                    "prompt": q.prompt,
                    "kind": q.kind,
                    "options": list(q.options),
                    "note": q.note,
                    "delegable": True,
                    "has_default": q.has_default,
                }
                for q in QUESTIONS
            ],
            "rules": [
                "Ask these before the grid exists. None of them can be inferred.",
                "breakout has no default: the grid will not start until it is "
                "answered, because not choosing means holding a directional "
                "position forever, unhedged.",
                "Any question may be delegated with 'you decide' — call `propose`, "
                "show the user the resolved grid and the reasoning, and get one "
                "explicit confirmation before calling `start --confirm`.",
                "sz_decimals is a market property, not a user opinion: look it up "
                "and pass it in.",
            ],
        }
    )


def cmd_propose(args: argparse.Namespace) -> int:
    try:
        prices = _load_price_series(args.prices)
        proposal = propose(
            market=args.market,
            sz_decimals=args.sz_decimals,
            mark_price=args.mark,
            capital_usd=args.capital,
            volatility_pct=args.volatility,
            max_leverage=args.max_leverage,
            prices=prices,
        )
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    return _emit(
        {
            "proposal": {k: v for k, (v, _) in proposal.items()},
            "reasoning": {k: why for k, (_, why) in proposal.items()},
            "next": (
                "Show every value and its reasoning to the user. If they accept, "
                "call `start` with these answers marked source=delegated and pass "
                "--confirm. Any value they change is source=user."
            ),
        }
    )


def cmd_start(args: argparse.Namespace) -> int:
    try:
        answers = _load_json_arg(args.answers)
    except (ValueError, OSError) as exc:
        return _fail(f"could not read --answers: {exc}")
    if not answers:
        return _fail("no answers supplied — run `questions` and ask the user first")

    interview = _interview_from_answers(answers)
    interview.confirmed = bool(args.confirm)

    decision = ready_to_start(interview, args.mark)
    if not decision.ok:
        return _fail(
            decision.reason,
            blockers=decision.blockers(),
            adjustments=(
                decision.resolved.adjustments if decision.resolved else []
            ),
            placed_nothing=True,
        )

    assert decision.resolved is not None
    config = decision.resolved.config
    try:
        grid_plan = plan_mod.plan_initial(config, args.mark, position_size=args.position)
    except GridGeometryError as exc:
        return _fail(str(exc), placed_nothing=True)

    path = store_mod.default_store_path(config.market)
    state = store_mod.load(path)
    state.config = {
        k: getattr(config, k) for k in GridConfig.__dataclass_fields__
    }
    state.provenance = interview.provenance()
    state.adjustments = decision.resolved.adjustments
    state.started = True
    state.halted = False
    state.halt_reason = ""
    state.recenters_used = 0
    state.log(
        "grid_started",
        market=config.market,
        lower=config.lower,
        upper=config.upper,
        levels=config.levels,
        spacing=config.spacing,
        leverage=config.leverage,
        breakout=config.breakout,
        provenance=interview.provenance(),
        adjustments=decision.resolved.adjustments,
        mark=args.mark,
    )
    store_mod.save(state, path)

    payload = _plan_payload(grid_plan, config.market)
    payload.update(
        {
            "gates": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in (decision.gates.results if decision.gates else [])
            ],
            "adjustments": decision.resolved.adjustments,
            "provenance": interview.provenance(),
            "save_file": state.store_path,
        }
    )
    return _emit(payload)


def cmd_step(args: argparse.Namespace) -> int:
    path = store_mod.default_store_path(args.market)
    state = store_mod.load(path)
    config = state.grid_config()
    if config is None:
        return _fail(
            f"no grid configured for {args.market}", save_file=state.store_path
        )
    if state.halted:
        return _emit(
            {
                "status": "halted",
                "summary": state.halt_reason or "grid is halted",
                "tool_calls": [],
                "save_file": state.store_path,
            }
        )

    # A configured daily loss limit has to be measurable, or it is decoration.
    if requires_equity(config) and args.equity is None:
        return _fail(
            "this grid has a daily loss limit, so --equity is required: pass the "
            "account value the exchange reports, and the limit is measured "
            "against the equity the day opened at",
            save_file=state.store_path,
        )

    daily = None
    if args.equity is not None:
        rolled = roll_day(
            stored_day=state.day,
            stored_open_equity=state.day_open_equity,
            equity=args.equity,
        )
        if rolled.day != state.day or state.day_open_equity != rolled.open_equity:
            state.day = rolled.day
            state.day_open_equity = rolled.open_equity
            state.log(
                "day_opened", day=rolled.day, open_equity=rolled.open_equity
            )
        daily = assess_daily_loss(
            config, open_equity=state.day_open_equity, equity=args.equity
        )

    try:
        open_orders = _load_json_arg(args.open_orders)
    except (ValueError, OSError) as exc:
        return _fail(f"could not read --open-orders: {exc}")
    orders = (
        open_orders
        if isinstance(open_orders, list)
        else open_orders.get("orders", [])
    )

    try:
        recon = reconcile(
            config, args.mark, open_orders=orders, position_size=args.position
        )
    except GridGeometryError as exc:
        return _fail(str(exc), save_file=state.store_path)

    if daily is not None and daily.breached:
        stop = plan_mod.plan_daily_stop(
            config,
            args.mark,
            resting_cloids=set(recon.resting),
            position_size=args.position,
            position_action=position_action_on_stop(config),
            detail=daily.summary() + ".",
        )
        state.halted = True
        state.halt_reason = stop.summary
        state.log(
            "halted",
            reason="daily_loss_limit",
            loss=daily.loss,
            limit=daily.limit,
            equity=daily.equity,
            open_equity=daily.open_equity,
        )
        store_mod.save(state, path)
        payload = _plan_payload(stop, config.market)
        payload["reconciliation"] = recon.summary()
        payload["daily_loss"] = daily.summary()
        payload["save_file"] = state.store_path
        return _emit(payload)

    grid_plan = plan_mod.plan(
        config,
        args.mark,
        resting_cloids=set(recon.resting),
        position_size=args.position,
        recenters_used=state.recenters_used,
        started=state.started,
    )

    # Stale grid orders from a previous range are cancelled before anything else.
    stale = [
        plan_mod.OrderIntent(
            action="cancel", cloid=o.cloid, reason="left over from a previous range"
        )
        for o in recon.unexpected
    ]
    mispriced = [
        plan_mod.OrderIntent(
            action="cancel",
            cloid=cloid,
            reason=f"resting at {actual} but rung is {expected} — cancel and refill",
        )
        for cloid, actual, expected in recon.mispriced
    ]
    grid_plan.intents = stale + mispriced + grid_plan.intents

    payload = _plan_payload(grid_plan, config.market)
    payload["reconciliation"] = recon.summary()
    if daily is not None:
        payload["daily_loss"] = daily.summary()

    if grid_plan.breakout == "recenter":
        recentred = plan_mod.recentred_config(config, args.mark)
        report = gates_mod.evaluate(recentred, args.mark)
        payload["recentred_range"] = [recentred.lower, recentred.upper]
        payload["recentred_gates"] = [
            {"name": r.name, "passed": r.passed, "detail": r.detail}
            for r in report.results
        ]
        if report.passed:
            state.config = {
                k: getattr(recentred, k) for k in GridConfig.__dataclass_fields__
            }
            state.recenters_used += 1
            state.log(
                "recentred",
                lower=recentred.lower,
                upper=recentred.upper,
                mark=args.mark,
                recenters_used=state.recenters_used,
            )
            payload["summary"] += (
                f" New range {recentred.lower:,.2f}–{recentred.upper:,.2f} passes "
                "every gate; call `step` again to place its rungs."
            )
        else:
            state.halted = True
            state.halt_reason = (
                "re-centring refused: the new range fails "
                + report.summary()
            )
            state.log("halted", reason=state.halt_reason)
            payload["status"] = "halted"
            payload["summary"] = state.halt_reason
    elif grid_plan.status == "halted":
        state.halted = True
        state.halt_reason = grid_plan.summary
        state.log("halted", reason=grid_plan.summary, breakout=grid_plan.breakout)

    store_mod.save(state, path)
    payload["save_file"] = state.store_path
    return _emit(payload)


def cmd_state(args: argparse.Namespace) -> int:
    path = store_mod.default_store_path(args.market)
    state = store_mod.load(path)
    config = state.grid_config()
    return _emit(
        {
            "save_file": state.store_path,
            "configured": config is not None,
            "started": state.started,
            "halted": state.halted,
            "halt_reason": state.halt_reason,
            "recenters_used": state.recenters_used,
            "day": state.day,
            "day_open_equity": state.day_open_equity,
            "provenance": state.provenance,
            "adjustments": state.adjustments,
            "config": state.config,
            "recent_events": state.events[-10:],
        }
    )


def cmd_gates(args: argparse.Namespace) -> int:
    """Dry-run the hard gates against a config without touching the store."""
    try:
        answers = _load_json_arg(args.answers)
    except (ValueError, OSError) as exc:
        return _fail(f"could not read --answers: {exc}")
    interview = _interview_from_answers(answers)
    config = interview.to_config()
    resolved = resolve(config)
    try:
        resolved.config.validate()
    except ValueError as exc:
        return _fail(str(exc), adjustments=resolved.adjustments)
    report = gates_mod.evaluate(resolved.config, args.mark)
    return _emit(
        {
            "passed": report.passed,
            "adjustments": resolved.adjustments,
            "gates": [
                {"name": r.name, "passed": r.passed, "detail": r.detail}
                for r in report.results
            ],
        },
        ok=report.passed,
        code=0 if report.passed else 1,
    )


def cmd_simulate(args: argparse.Namespace) -> int:
    """Walk a price series through one configuration. Touches no state."""
    try:
        answers = _load_json_arg(args.answers)
        prices = _load_price_series(args.prices)
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    if prices is None:
        return _fail("--prices is required")

    config = resolve(_interview_from_answers(answers).to_config()).config
    try:
        config.validate()
        outcome = simulate(config, prices, hours_per_bar=args.hours_per_bar)
    except (ValueError, GridGeometryError) as exc:
        return _fail(str(exc))
    return _emit(
        {
            "summary": outcome.summary(),
            "bars": outcome.bars,
            "cycles": outcome.cycles,
            "cycle_edge_usd": round(outcome.cycle_edge_usd, 2),
            "unrealized_pnl_usd": round(outcome.unrealized_pnl_usd, 2),
            "exit_pnl_usd": round(outcome.exit_pnl_usd, 2),
            "net_pnl_usd": round(outcome.net_pnl_usd, 2),
            "fees_paid_usd": round(outcome.fees_paid_usd, 2),
            "funding_paid_usd": round(outcome.funding_paid_usd, 2),
            "worst_drawdown_pct": round(outcome.worst_drawdown_pct, 2),
            "breakout_bar": outcome.breakout_bar,
            "breakout_action": outcome.breakout_action,
            "caveat": (
                "A comparison between configurations over this series, not a "
                "forecast. Fills assume a resting order always fills when price "
                "reaches its level."
            ),
        }
    )


def cmd_sweep(args: argparse.Namespace) -> int:
    """Simulate many level counts over a series and rank them. Touches no state."""
    try:
        answers = _load_json_arg(args.answers)
        prices = _load_price_series(args.prices)
    except (ValueError, OSError) as exc:
        return _fail(str(exc))
    if prices is None:
        return _fail("--prices is required")

    config = resolve(_interview_from_answers(answers).to_config()).config
    try:
        config.validate()
        rows = sweep(
            config,
            prices,
            level_counts=range(2, args.max_levels + 1),
            hours_per_bar=args.hours_per_bar,
        )
    except (ValueError, GridGeometryError) as exc:
        return _fail(str(exc))

    winner = best_row(rows)
    return _emit(
        {
            "ranked": [
                {
                    "levels": row.levels,
                    "spacing": row.spacing,
                    "gates_passed": row.gates_passed,
                    "cycles": row.result.cycles if row.result else None,
                    "cycle_edge_usd": (
                        round(row.result.cycle_edge_usd, 2) if row.result else None
                    ),
                    "net_pnl_usd": (
                        round(row.result.net_pnl_usd, 2) if row.result else None
                    ),
                    "worst_drawdown_pct": (
                        round(row.result.worst_drawdown_pct, 2) if row.result else None
                    ),
                    "reason": row.reason,
                }
                for row in rows
            ],
            "best": (
                {
                    "levels": winner.levels,
                    "spacing": winner.spacing,
                    "summary": winner.result.summary(),
                }
                if winner and winner.result
                else None
            ),
            "ranked_by": (
                "cycling edge, which excludes leftover inventory and any profit "
                "from flattening at a breakout — over a trending window net PnL "
                "would reward a grid for having been accidentally long"
            ),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grid", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("questions", help="the interview, in order").set_defaults(
        func=cmd_questions
    )

    p = sub.add_parser("propose", help="a concrete grid when the user delegates")
    p.add_argument("--market", required=True)
    p.add_argument("--sz-decimals", type=int, required=True, dest="sz_decimals")
    p.add_argument("--mark", type=float, required=True)
    p.add_argument("--capital", type=float, required=True)
    p.add_argument("--volatility", type=float, default=None,
                   help="recent move as a percentage; sizes the range")
    p.add_argument("--max-leverage", type=float, default=1.0, dest="max_leverage")
    p.add_argument("--prices", default=None,
                   help="recent price series for this market (inline JSON or a "
                        "file); tunes the level count by simulation instead of "
                        "taking the most rungs that merely clear the gates")
    p.set_defaults(func=cmd_propose)

    p = sub.add_parser("start", help="validate, gate, and place the rungs")
    p.add_argument("--answers", required=True, help="inline JSON or a path to JSON")
    p.add_argument("--mark", type=float, required=True)
    p.add_argument("--position", type=float, default=0.0,
                   help="signed position already open on this market")
    p.add_argument("--confirm", action="store_true",
                   help="the user confirmed a proposal that the agent chose")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("step", help="refill rungs, or act on a breakout")
    p.add_argument("--market", required=True)
    p.add_argument("--mark", type=float, required=True)
    p.add_argument("--open-orders", default="[]", dest="open_orders",
                   help="inline JSON or a path to JSON: the venue's open orders")
    p.add_argument("--position", type=float, default=0.0)
    p.add_argument("--equity", type=float, default=None,
                   help="account value the exchange reports; required when a "
                        "daily loss limit is configured")
    p.set_defaults(func=cmd_step)

    p = sub.add_parser("state", help="what the grid thinks, and which file it read")
    p.add_argument("--market", default="default")
    p.set_defaults(func=cmd_state)

    p = sub.add_parser("gates", help="dry-run the hard gates, touching nothing")
    p.add_argument("--answers", required=True)
    p.add_argument("--mark", type=float, required=True)
    p.set_defaults(func=cmd_gates)

    p = sub.add_parser("simulate", help="walk a price series through one grid")
    p.add_argument("--answers", required=True)
    p.add_argument("--prices", required=True)
    p.add_argument("--hours-per-bar", type=float, default=1.0,
                   dest="hours_per_bar")
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("sweep", help="rank level counts over a price series")
    p.add_argument("--answers", required=True)
    p.add_argument("--prices", required=True)
    p.add_argument("--max-levels", type=int, default=20, dest="max_levels")
    p.add_argument("--hours-per-bar", type=float, default=1.0,
                   dest="hours_per_bar")
    p.set_defaults(func=cmd_sweep)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
