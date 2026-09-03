"""Lodge signals — §7.

Formats engine events into the short lines other hunters see, and
emits them in-process through the SDK's Paths API client
(``PathsClient.emit_signal``) — no shell, no subprocess, no dynamic
command construction. Amounts stay private by default; lodge_private
mutes everything. All strings here are player-facing and pass the
fiction lint.
"""

from __future__ import annotations

from typing import Any

SLUG = "the-marsh"
VERSION = "0.1.7"


def format_signal(event: dict[str, Any], dog_name: str = "Biscuit"
                  ) -> tuple[str, str] | None:
    """Return (signal_name, message) for lodge-worthy events, else None."""
    etype = event.get("type")
    if etype == "shot":
        return ("shot_fired",
                f"🦆 {dog_name} took a shot in {event.get('biome')}: "
                f"${event.get('symbol')}, level {int(event.get('heat', 0))} duck.")
    if etype in ("retrieved", "partial_retrieve"):
        banded = "Banded duck" if event.get("graduated") else "Duck"
        return ("duck_retrieved",
                f"🏆 {banded} retrieved: ${event.get('symbol')} "
                f"+{event.get('gain_pct'):g}%.")
    if etype == "stopped":
        return ("winged",
                f"🩹 Winged: ${event.get('symbol')}, clean stop, streak intact.")
    if etype == "walked_out":
        return ("walked_out", "💰 Expedition over. Walked out.")
    if etype == "no_duck":
        failures = event.get("failures", {})
        detail = ", ".join(f"{count} {name}" for name, count in failures.items())
        return ("dog_wont_fetch",
                f"Passed on {event.get('scouted', 0)} ducks: {detail}.")
    if etype == "trophy":
        return ("trophy", f"🏅 {event.get('name')}, season {event.get('season', 1)}.")
    return None


def emit(event: dict[str, Any], *, dog_name: str = "Biscuit",
         lodge_private: bool = False, dry_run: bool = False) -> str | None:
    if lodge_private or event.get("ghost"):
        return None
    formatted = format_signal(event, dog_name)
    if formatted is None:
        return None
    name, message = formatted
    if not dry_run:
        try:
            # Imported inside the guard on purpose: on a runtime without
            # the SDK (or a release that moves this client), a missing
            # import must cost at most one lodge signal, never the hunt.
            from wayfinder_paths.paths.client import PathsApiClient

            PathsApiClient().emit_signal(
                slug=SLUG, path_version=VERSION, title=name, message=message
            )
        except Exception:
            # a lost lodge signal never blocks or retries the hunt
            return None
    return message
