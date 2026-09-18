from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from game import marsh_engine  # noqa: E402

LOG_ENV = "MARSH_EVENT_LOG"
DEFAULT_LOG = os.path.join(_HERE, ".wayfinder_runs", "marsh_events.jsonl")


def _events() -> list[dict]:
    log_path = os.environ.get(LOG_ENV, DEFAULT_LOG)
    if not os.path.exists(log_path):
        return []
    return marsh_engine.load_events(log_path)


def wfpath_meta() -> dict:
    return {
        "name": "The Marsh 🦆",
        "kind": "strategy",
        "ui_mode": "auto",
        "tracking_mode": "hybrid",
    }


def wfpath_state() -> dict:
    events = _events()
    state = marsh_engine.replay(events)
    open_positions = state.expedition.get("open_positions", []) \
        if state.expedition.get("active") else []
    return {
        "status": "afield" if open_positions else "by the fire",
        "selection": {"open_positions": open_positions},
        "metrics": {
            "level": state.level,
            "xp": state.xp,
            "hunter_rating": state.hunter_rating,
            "discipline_streak": state.discipline_streak,
            "trophies": len(state.trophies),
        },
        "positions": open_positions,
        "game": state.to_dict(),
    }


def wfpath_decision() -> dict:
    events = _events()
    last_hunt: dict = {}
    for event in events:
        if event.get("type") in ("shot", "ghost_shot", "no_duck",
                                 "hunt_refused"):
            last_hunt = event
    if not last_hunt:
        return {
            "summary": "No hunts yet. The dog is by the fire, waiting on 'go now'.",
            "selected": {},
            "candidates": [],
        }
    if last_hunt.get("type") in ("shot", "ghost_shot"):
        summary = (f"Last hunt: took ${last_hunt.get('symbol')} in "
                   f"{last_hunt.get('biome')} — it passed every gate at the "
                   f"highest heat.")
        selected = {"token": last_hunt.get("token"),
                    "symbol": last_hunt.get("symbol")}
    else:
        failures = last_hunt.get("failures", {})
        detail = ", ".join(f"{c} {n}" for n, c in failures.items())
        detail = detail or "nothing worth wading in for"
        summary = (f"Last hunt: dog wouldn't fetch. "
                   f"{last_hunt.get('scouted', 0)} scouted: {detail}.")
        selected = {}
    candidates = [
        {"symbol": e.get("symbol"), "heat": e.get("heat"),
         "verdict": e.get("verdict"), "gate": e.get("gate_name")}
        for e in events
        if e.get("type") == "duck_scouted"
        and e.get("hunt_id") == last_hunt.get("hunt_id")
    ]
    return {"summary": summary, "selected": selected, "candidates": candidates}


# -- component entry point --------------------------------------------------
#
# The host runs this file: `wayfinder path exec --component main -- <args>`,
# which is what `scripts/wf_run.py` ultimately calls. Without a __main__
# block the component defines functions and exits silently, and the host
# correctly reports that no path code ran. Everything below routes the
# host's arguments into the same commands the CLI uses, so there is one
# implementation and two doors into it.

def _run_cli(argv: list[str]) -> None:
    scripts_dir = os.path.join(_HERE, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import marsh_run  # noqa: PLC0415

    saved = sys.argv
    try:
        sys.argv = ["marsh_run", *argv]
        marsh_run.main()
    finally:
        sys.argv = saved


# -- the host runner's door -------------------------------------------
#
# Everything above is the CLI door: practice range, live scouting, the
# page contract. None of it can move funds, by design.
#
# This is the other door. The SDK runner scans the component module for
# a concrete Strategy subclass, instantiates it with the wallet config
# and a signing callback for the satchel, and drives it. Without a
# class here the runner finds nothing and reports no live-shot mode --
# which is exactly what it did, for every version up to this one. The
# LiveExecutor was correct and completely unreachable.

try:  # the base class only exists under the host runner
    from wayfinder_paths.core.strategies.Strategy import Strategy as _Strategy
except Exception:  # pragma: no cover - CLI runs never have it
    _Strategy = None


if _Strategy is not None:

    class MarshStrategy(_Strategy):  # type: ignore[misc,valid-type]
        """The Marsh, driven by the host runner, with real funds.

        The satchel is the strategy wallet the hunter authorised; the
        main wallet is never spent from here and only appears in
        status. Gates, limits and the retrieve plan are the same code
        the practice range runs -- this class supplies an executor
        with trade authority and nothing else. It cannot widen a gate
        or raise a size: those come from MarshConfig, and hunt_size
        remains a ceiling the engine enforces.
        """

        name = "the-marsh"

        def _satchel_address(self) -> str:
            """The wallet the satchel actually is, however it was named.

            Wallet labels are generated per install -- one hunter's
            satchel is "thoughtful-lush-narwhal-of-bliss" -- so nothing
            here may depend on a name. Worse, get_strategy_config
            builds config["strategy_wallet"] from the local config file
            only, so for anyone using Wayfinder-managed wallets (which
            is everyone) that key is simply absent.

            The signing callback knows. It was built for one specific
            wallet and carries its address, and it is the thing that
            will actually sign -- so it is the authoritative answer,
            not a guess from a name or a file.
            """
            callback = self.strategy_wallet_signing_callback
            chain_type = getattr(callback, "chain_type", None)
            if chain_type is not None and str(chain_type).lower() != "solana":
                # An EVM leg of the same ring would sign, and spend the
                # wrong wallet. Refuse rather than trade the wrong leg.
                raise ValueError(
                    "the satchel signer is a "
                    f"{chain_type} wallet; The Marsh hunts Solana. "
                    "Point it at the ring's Solana leg."
                )
            address = getattr(callback, "wallet_address", None)
            if address:
                return str(address)
            # Explicitly configured address, if the host provided one.
            return self._get_strategy_wallet_address()

        def _engine(self, *, live: bool):
            from engine.config import MarshConfig
            from engine.events import EventLog
            from engine.executor import LiveExecutor, SimExecutor
            from engine.feed import CHAIN_IDS, WayfinderFeed
            from engine.hunt import HuntEngine

            config = MarshConfig()
            feed = WayfinderFeed()
            if live:
                # No callback means no trade authority, and LiveExecutor
                # refuses to exist without one -- so a misconfigured
                # runner fails here rather than part-way through a hunt.
                executor = LiveExecutor(
                    self._satchel_address(),
                    self.strategy_wallet_signing_callback,
                    CHAIN_IDS,
                )
            else:
                executor = SimExecutor(feed)
            engine = HuntEngine(
                config, feed, executor,
                EventLog(os.environ.get(LOG_ENV, DEFAULT_LOG)),
                ghost=not live,
            )
            engine.restore_from_log()
            return engine

        async def deposit(self, **kwargs):
            """Kit up: register what the hunter put in the satchel.

            The runner passes the size as ``main_token_amount``; only
            reading ``amount`` meant every deposit through it looked
            like zero and was refused. Both names are accepted now.
            """
            amount = float(
                kwargs.get("main_token_amount")
                or kwargs.get("amount")
                or 0.0
            )
            if amount <= 0:
                return (False, "Nothing to kit up with.")
            engine = self._engine(live=True)
            engine.kit_up(amount)
            return (True, f"Kitted up: {amount:g} in the satchel.")

        async def update(self, **kwargs):
            """One tick: apply the retrieve plan, then hunt if allowed.

            Exits come first on purpose. A tick that both closes a
            position and opens one should bank the old bird before
            reaching for the next.
            """
            engine = self._engine(live=True)
            exits = await engine.check_positions()
            result = await engine.run_hunt()
            if result.shot and result.position:
                return (True, f"Shot ${result.position.symbol}; "
                              f"{len(exits)} exit(s) applied.")
            reason = result.refusal_reason or "no duck passed"
            return (True, f"No shot: {reason}. {len(exits)} exit(s) applied.")

        async def exit(self, **kwargs):
            """Bring every open position back to native and walk out."""
            engine = self._engine(live=True)
            closed = await engine.close_all()
            return (True, f"Walked out. {len(closed)} position(s) closed.")

        async def _status(self, **kwargs):
            engine = self._engine(live=True)
            open_positions = [p for p in engine.positions.values()
                              if not p.closed]
            state = marsh_engine.replay(_events())
            return {
                "portfolio_value": float(engine.bankroll_native),
                "net_deposit": float(engine.bankroll_native),
                "gas_available": float(engine.bankroll_native),
                "gassed_up": engine.bankroll_native > 0,
                "strategy_status": {
                    "open_positions": [
                        {"symbol": p.symbol, "token": p.token,
                         "size_native": p.size_native,
                         "entry_price_usd": p.entry_price_usd}
                        for p in open_positions
                    ],
                    "hunter": state.to_dict().get("hunter", {}),
                },
            }

        @staticmethod
        async def policies() -> list[str]:
            """What the satchel is allowed to do, for the wallet policy."""
            return ["swap"]


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # No arguments: report the path's contract for the page.
        print(json.dumps(
            {"meta": wfpath_meta(), "state": wfpath_state(),
             "decision": wfpath_decision()},
            indent=2, default=str,
        ))
        return
    if argv[0] in ("go", "go-now", "ape"):
        argv[0] = "hunt"  # the trigger words the dog answers to
    _run_cli(argv)


if __name__ == "__main__":
    main()
