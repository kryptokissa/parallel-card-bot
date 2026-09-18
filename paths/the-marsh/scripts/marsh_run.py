#!/usr/bin/env python3
"""Run The Marsh from a terminal: hunt, whistle, recap, state.

Ghost mode needs nothing at all: the practice marshes ship
in engine/practice.py. Live mode needs a configured
Wayfinder API key and a satchel wallet in config.json; without them it
refuses politely rather than guessing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from engine.config import MarshConfig  # noqa: E402
from engine.events import EventLog  # noqa: E402
from engine.executor import SimExecutor  # noqa: E402
from engine.feed import FixtureFeed  # noqa: E402
from engine.hunt import HuntEngine  # noqa: E402
from engine.practice import load as load_marsh  # noqa: E402
from engine.narrator import narrate, recap as tell_recap  # noqa: E402
from game import marsh_engine  # noqa: E402

DEFAULT_LOG = os.path.join(ROOT, ".wayfinder_runs", "marsh_events.jsonl")
GHOST_LOG = os.path.join(ROOT, ".wayfinder_runs", "ghost_events.jsonl")


def _log_path(ghost: bool) -> str:
    return os.environ.get("MARSH_EVENT_LOG", GHOST_LOG if ghost else DEFAULT_LOG)


def _remembered_fixture(log: EventLog) -> str | None:
    """Which practice marsh this save file was last hunted on."""
    for event in reversed(log.read()):
        name = event.get("fixture")
        if name:
            return str(name)
    return None


def _print_events(events: list[dict]) -> None:
    for event in events:
        line = narrate(event)
        if line:
            print(f"  🐕 {line}")


async def _build_engine(args) -> HuntEngine:
    log = EventLog(_log_path(args.ghost))
    config = MarshConfig()
    if args.live_feed:
        # real marsh, practice ammunition: live scouting and safety
        # checks, simulated fills, no funds anywhere near the water
        from engine.feed import WayfinderFeed  # noqa: PLC0415

        feed = WayfinderFeed()
        engine = HuntEngine(config, feed, SimExecutor(feed), log, ghost=True)
        engine.restore_from_log()
        if engine.bankroll_native <= 0:
            engine.kit_up(args.kit if args.kit else config.hunt_size * 3)
        return engine
    if args.ghost or args.fixture:
        # The whistle has to hunt the same marsh the shot came from. It
        # is a separate invocation with no --fixture of its own, so
        # without this it silently fell back to the default marsh,
        # found no price for a duck from a different one, and printed
        # nothing at all -- which read as "nothing happened yet" rather
        # than "wrong marsh". The log remembers instead.
        fixture = args.fixture or _remembered_fixture(log) or None
        feed = FixtureFeed(load_marsh(fixture),
                           cursor_file=_log_path(args.ghost) + ".cursor")
        executor = SimExecutor(feed)
        engine = HuntEngine(config, feed, executor, log, ghost=args.ghost)
        engine.restore_from_log()
        if args.size is not None:
            # A spoken size is a config change: logged, so raising it
            # right after a stop shows up as what it is (tilt).
            engine.update_config(hunt_size=args.size)
        if engine.bankroll_native <= 0:
            engine.kit_up(args.kit if args.kit else config.hunt_size * 3)
        if args.fixture:
            log.emit("practice_marsh", fixture=args.fixture, ghost=True)
        return engine
    # Live, fund-moving runs are host-mediated by design: LiveExecutor
    # refuses to exist without the signing callback that only the SDK
    # strategy runner injects after the hunter authorizes live trading.
    # This standalone script never has that callback, so it never has
    # trade authority — it can only offer the practice range.
    print("Live hunts run under the host runner, which holds the "
          "satchel signer the hunter authorized — this script has no "
          "trade authority by design. Use --ghost for the practice "
          "range or --live-feed to scout the real marsh without funds.")
    raise SystemExit(1)


async def cmd_hunt(args) -> None:
    engine = await _build_engine(args)
    before = len(engine.log.read())
    result = await engine.run_hunt()
    _print_events(engine.log.read()[before:])
    if not result.shot and result.refusal_reason:
        pass  # the narration above already told it straight


async def cmd_live(args) -> None:
    """Scout the real marsh and hand the hunter's agent a shot to run.

    This pack cannot execute and does not pretend to. It scouts, holds
    every gate, sizes the shot against a real BRAP quote, and stops --
    printing exactly what to swap. The agent runs it with its own
    wallet and its own tool; nothing here signs or sends. No position
    opens until a real fill is recorded against the decision.
    """
    from engine.decision import write_pending  # noqa: PLC0415
    from engine.executor import QuoteOnlyExecutor  # noqa: PLC0415
    from engine.feed import WayfinderFeed  # noqa: PLC0415

    log = EventLog(_log_path(False))
    config = MarshConfig()
    feed = WayfinderFeed(quote_wallet=args.satchel)
    engine = HuntEngine(config, feed, QuoteOnlyExecutor(feed), log, ghost=False)
    engine.restore_from_log()
    if engine.bankroll_native <= 0:
        engine.kit_up(args.kit if args.kit else config.hunt_size * 3)

    before = len(log.read())
    result = await engine.run_hunt(decide_only=True)
    _print_events(log.read()[before:])

    if result.decision is None:
        return
    d = result.decision
    write_pending(_log_path(False), d)
    print()
    print(f"  \U0001f415 That one's worth a shell. ${d.symbol} in {d.biome}, "
          f"level {int(d.heat)} duck.")
    print("     I can't pull the trigger — you hold the gun. Run this:")
    print()
    print(json.dumps(d.agent_call(), indent=2))
    print()
    print(f"     Price impact on that route: {d.price_impact_pct:.2f}%. "
          f"Quote goes stale in 3 minutes.")
    print("     When it lands, tell me:")
    print("       python scripts/wf_run.py record --tx <signature> "
          "[--price-usd <fill price>]")
    print("     If you don't take it:  "
          "python scripts/wf_run.py record --abandon")


def cmd_record(args) -> None:
    """Record what the agent's swap actually did, or abandon the shot.

    The position opens here and nowhere else. A decision is a proposal
    until a real transaction is named against it, which keeps the log
    honest about what was actually executed rather than what was
    suggested.
    """
    from engine.decision import clear_pending, read_pending  # noqa: PLC0415

    log_path = _log_path(False)
    decision = read_pending(log_path)
    if decision is None:
        print("  \U0001f415 Nothing pending. No shot to record.")
        raise SystemExit(1)

    if args.abandon:
        clear_pending(log_path)
        print(f"  \U0001f415 Left ${decision.symbol} on the water. "
              f"Nothing spent.")
        return

    if not args.tx:
        print("  \U0001f415 I need the transaction to record a shot: "
              "--tx <signature>")
        raise SystemExit(2)

    log = EventLog(log_path)
    config = MarshConfig()
    price = args.price_usd if args.price_usd else decision.expected_price_usd
    if price <= 0:
        # Exits are measured against entry; a zero entry disables the
        # whole retrieve plan silently. Refuse rather than open a
        # position with no stop.
        print("  \U0001f415 I need the fill price to set the plan: "
              "--price-usd <usd per token>. Without it there's no stop.")
        raise SystemExit(2)

    position_id = uuid.uuid4().hex[:10]
    size = float(decision.amount)
    log.emit("shot", hunt_id=decision.hunt_id, position_id=position_id,
             token=decision.token, symbol=decision.symbol,
             chain=decision.chain, entry_price_usd=price, size=size,
             tx=args.tx, heat=decision.heat, biome=decision.biome,
             graduated=False, weather="Calm")
    log.emit("retrieve_plan", position_id=position_id, token=decision.token,
             rules={
                 "retrieve_1": {"gain_pct": config.retrieve_1_pct,
                                "sell_fraction": config.retrieve_1_fraction},
                 "retrieve_2": {"gain_pct": config.retrieve_2_pct},
                 "stop_loss": {"gain_pct": config.stop_loss_pct},
                 "time_stop": {"hours": config.time_stop_hours,
                               "low_pct": config.time_stop_low_pct,
                               "high_pct": config.time_stop_high_pct},
             })
    clear_pending(log_path)
    print(f"  \U0001f415 Marked. ${decision.symbol} at {price:g}, "
          f"{size:g} in. {args.tx}")
    print(f"     The plan, plain: half at +{config.retrieve_1_pct:g}%, "
          f"everything at +{config.retrieve_2_pct:g}%, "
          f"bail at {config.stop_loss_pct:g}%, and if it's floating there "
          f"in {config.time_stop_hours:g} hours, I walk.")
    print("     Whistle me when you want the plan checked: "
          "python scripts/wf_run.py whistle")


async def cmd_whistle(args) -> None:
    if not args.ghost and not args.live_feed:
        # A real position still needs its stop, its targets and its
        # time stop. This pack cannot sell, so the whistle reports the
        # exits the hunter's agent must run -- leaving them to someone
        # watching a chart is how a rules-based hunt becomes a
        # discretionary one.
        from engine.executor import QuoteOnlyExecutor  # noqa: PLC0415
        from engine.feed import WayfinderFeed  # noqa: PLC0415

        log = EventLog(_log_path(False))
        feed = WayfinderFeed(quote_wallet=args.satchel)
        engine = HuntEngine(MarshConfig(), feed, QuoteOnlyExecutor(feed),
                            log, ghost=False)
        engine.restore_from_log()
        before = len(log.read())
        decisions = await engine.decide_exits()
        _print_events(log.read()[before:])
        if not decisions:
            print("  \U0001f415 Checked the plan. Nothing to do yet.")
            return
        for d in decisions:
            what = ("all of it" if d.reason != "partial_retrieve"
                    else "half of it")
            print()
            print(f"  \U0001f415 ${d.symbol}: {d.reason}, {d.heat:+g}%. "
                  f"Time to bring {what} back.")
            print("     You hold the gun. Run this:")
            print(json.dumps(d.agent_call(), indent=2))
            print("     amount is the token quantity to sell — your agent "
                  "knows the balance; sell "
                  f"{'100%' if d.reason != 'partial_retrieve' else '50%'} "
                  "of the position.")
        return
    engine = await _build_engine(args)
    before = len(engine.log.read())
    await engine.check_positions()
    _print_events(engine.log.read()[before:])


def _store_label(args) -> tuple[str, str]:
    """Which save file this command is about to read, and its name.

    Practice and live keep separate logs on purpose -- a practice bust
    should never dent a real record. But a report that does not say
    which one it read turns "nothing happened here" into "nothing ever
    happened", which is how a full practice history and an empty live
    sheet came to look like a contradiction.
    """
    path = _log_path(args.ghost)
    if os.environ.get("MARSH_EVENT_LOG"):
        return path, "the save file you named"
    return path, "the practice range" if args.ghost else "the real marsh"


def cmd_recap(args) -> None:
    path, label = _store_label(args)
    events = marsh_engine.load_events(path)
    print(f"  🐕 {tell_recap(events, args.expedition)}")
    print(f"     ({label}: {path})")


def _configured_wallets() -> tuple[list[str], str]:
    """Wallet labels the RUNNER can resolve, asked the runner's way.

    The pack never holds keys. The host runner resolves a wallet by
    label and hands the engine a signing callback for it; the satchel
    is that wallet. So this has to ask the same question the runner
    asks -- ``load_wallets()``, which is the local config file PLUS
    the remote wallets Wayfinder custodies for this instance.

    Reading only the local config was wrong: it reported no satchel
    while two managed wallets sat there, reachable, because they live
    on the remote side.
    """
    labels: list[str] = []
    try:
        import asyncio  # noqa: PLC0415

        from wayfinder_paths.core.utils.wallets import load_wallets  # noqa: PLC0415

        wallets = asyncio.run(load_wallets())
        labels = sorted({str(w.get("label")) for w in wallets if w.get("label")})
        if labels:
            return labels, "local config + wallets Wayfinder holds for you"
    except Exception as exc:  # no SDK, no network, no credentials
        note = f"could not ask the runner ({type(exc).__name__})"
    else:
        note = "local config + wallets Wayfinder holds for you"

    # Fall back to the config file alone, and say so, rather than
    # claiming nothing exists when we simply could not look.
    try:
        from wayfinder_paths.core.config import CONFIG  # noqa: PLC0415
    except Exception:
        path = (os.environ.get("WAYFINDER_CONFIG_PATH")
                or os.environ.get("WAYFINDER_CONFIG"))
        if not path or not os.path.exists(path):
            return [], f"{note}; no runtime config found either"
        try:
            with open(path, "r", encoding="utf-8") as fh:
                CONFIG = json.load(fh)
        except (OSError, ValueError) as exc:
            return [], f"{note}; config unreadable ({exc})"
    wallets = CONFIG.get("wallets") or []
    labels = sorted({str(w.get("label")) for w in wallets
                     if isinstance(w, dict) and w.get("label")})
    return labels, note


def cmd_preflight(args) -> None:
    """Can this runtime actually take a live shot? Answered honestly.

    Three separate questions, because passing one says nothing about
    the others, and a hunter reading a single "ready" would be right
    to assume it meant all three:

      1. can the runtime broadcast a Solana transaction at all
      2. is there a satchel wallet for it to spend from
      3. does the caller have trade authority

    The third is always no from here: this wrapper never receives the
    signing callback, by design. Live shots run under the host runner.
    """
    from engine.executor import solana_submission_available

    can_send, reason = solana_submission_available()
    have_key = bool(os.environ.get("WAYFINDER_API_KEY"))
    labels, source = _configured_wallets()

    print("  \U0001f415 Checking the kit before we go out.")
    print(f"     Scouting the real marsh:  "
          f"{'yes' if have_key else 'no API key set'}")
    print("     Practice range:           yes, always")

    if can_send:
        print("     Runtime can broadcast:    yes")
    else:
        print(f"     Runtime can broadcast:    NO - {reason}")

    if labels:
        print(f"     Satchel wallet:           {len(labels)} configured "
              f"({', '.join(sorted(labels))}) via {source}")
    else:
        print(f"     Satchel wallet:           none found - {source}")

    print()
    print("     This wrapper cannot take a live shot in any case: it")
    print("     holds no keys and is never given the host runner's")
    print("     signing callback. Live shots run under the runner,")
    print("     with a satchel you have authorised there.")

    if not can_send or not labels:
        print()
        print("     Not ready for real money. Do not fund a satchel yet.")
        raise SystemExit(2)

    print()
    print("     Runtime and satchel both look ready. Nothing here has")
    print("     ever moved real funds, so make the first live shot a")
    print("     token one (~0.01) and watch it settle before any size.")
    raise SystemExit(0)


def cmd_state(args) -> None:
    path, label = _store_label(args)
    events = marsh_engine.load_events(path)
    state = marsh_engine.replay(events)
    # Name the save file in the output. A character sheet that does
    # not say whose it is invites the reader to assume it is the only
    # one, and there are two.
    payload = {"save_file": path, "save_file_is": label,
               "events_read": len(events), **state.to_dict()}
    print(json.dumps(payload, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(prog="marsh_run")
    parser.add_argument("command",
                        choices=["hunt", "whistle", "recap", "state",
                                 "preflight", "live", "record"])
    parser.add_argument("--ghost", action="store_true",
                        help="practice range: fixture feed, no funds")
    parser.add_argument("--live-feed", action="store_true",
                        help="ghost hunt over the real feed: live "
                             "scouting, simulated fills, no funds")
    parser.add_argument("--fixture", default=None,
                        help="practice marsh: a name (calm_day, "
                             "no_duck_day, storm_bust) or a file path")
    parser.add_argument("--size", type=float, default=None,
                        help="shot size for this hunt (capped by config)")
    parser.add_argument("--kit", type=float, default=None,
                        help="ghost-mode satchel size")
    parser.add_argument("--expedition", type=int, default=1)
    parser.add_argument("--satchel", default=None,
                        help="satchel address, so the quote prices the "
                             "route your agent will actually swap")
    parser.add_argument("--tx", default=None,
                        help="the signature your agent's swap returned")
    parser.add_argument("--price-usd", type=float, default=None,
                        dest="price_usd",
                        help="USD per token actually filled at")
    parser.add_argument("--abandon", action="store_true",
                        help="drop the pending shot without taking it")
    args = parser.parse_args()
    if args.live_feed:
        args.ghost = True  # live-feed runs are practice: ghost log, sim fills

    if args.command == "hunt":
        asyncio.run(cmd_hunt(args))
    elif args.command == "whistle":
        asyncio.run(cmd_whistle(args))
    elif args.command == "recap":
        cmd_recap(args)
    elif args.command == "state":
        cmd_state(args)
    elif args.command == "preflight":
        cmd_preflight(args)
    elif args.command == "live":
        asyncio.run(cmd_live(args))
    elif args.command == "record":
        cmd_record(args)


if __name__ == "__main__":
    main()
