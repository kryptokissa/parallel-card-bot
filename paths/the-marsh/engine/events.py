"""Append-only JSONL event log — the canonical record.

The engine is the only writer. The game layer and the applet consume it
read-only (Design Laws 2 and 3). Timestamps are ISO-8601 UTC.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class EventLog:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

    def emit(self, event_type: str, *, ts: datetime | None = None,
             **payload: Any) -> dict[str, Any]:
        event = {"type": event_type, "ts": (ts or utcnow()).isoformat(), **payload}
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def read(self) -> list[dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        out: list[dict[str, Any]] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
