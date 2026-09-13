"""Where the grid's config and run state live between invocations.

Every command runs as a fresh process, so the frozen config, the re-centre
count and the event log have to survive on disk. Two rules learned the hard
way elsewhere in this repo:

- **Never default to a file inside the path directory.** Everything under the
  path directory ships in the published bundle (BUILDING_PATHS.md §1.3), and a
  state file full of real positions is the last thing that should.
- **Always report which file was read.** A state command that does not name its
  save file is unfalsifiable when two runs disagree.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.config import GridConfig

DEFAULT_STORE_DIR = Path.home() / ".wayfinder" / "grid"


def default_store_path(market: str = "default") -> Path:
    """Store location outside the path directory, overridable by env."""
    override = os.environ.get("GRID_STATE_PATH")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("WAYFINDER_CONFIG_PATH")
    root = Path(base).expanduser().parent / "grid" if base else DEFAULT_STORE_DIR
    safe = market.replace("/", "_").replace(":", "_") or "default"
    return root / f"{safe}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GridState:
    """Everything that persists across invocations."""

    config: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, str] = field(default_factory=dict)
    adjustments: list[str] = field(default_factory=list)
    started: bool = False
    recenters_used: int = 0
    halted: bool = False
    halt_reason: str = ""
    # UTC day and the equity it opened at, for the daily loss limit.
    day: str = ""
    day_open_equity: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)
    store_path: str = ""

    def grid_config(self) -> GridConfig | None:
        if not self.config:
            return None
        fields = {f for f in GridConfig.__dataclass_fields__}
        return GridConfig(**{k: v for k, v in self.config.items() if k in fields})

    def log(self, kind: str, **detail: Any) -> None:
        """Append an event. The log is the record of intent, not of state."""
        self.events.append({"at": _now(), "event": kind, **detail})


def load(path: Path) -> GridState:
    if not path.exists():
        return GridState(store_path=str(path))
    raw = json.loads(path.read_text())
    known = {f for f in GridState.__dataclass_fields__}
    state = GridState(**{k: v for k, v in raw.items() if k in known})
    state.store_path = str(path)
    return state


def save(state: GridState, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    state.store_path = str(path)
    payload = asdict(state)
    # Keep the log bounded; the last 500 events are plenty to explain a run.
    payload["events"] = payload["events"][-500:]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path
