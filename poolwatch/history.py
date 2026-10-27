"""Event history for the dashboard: one record per processed image batch or pump change.

The monitor process appends to data/events.jsonl and rewrites data/state.json;
the dashboard only reads them, so it can run in the same process or a separate one.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import date, datetime
from pathlib import Path


def _default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()  # browsers parse ISO 8601 reliably; str() isn't
    if isinstance(obj, (tuple, set)):
        return list(obj)
    return str(obj)


class EventStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.dir = Path(data_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events_path = self.dir / "events.jsonl"
        self.state_path = self.dir / "state.json"
        self._lock = threading.Lock()
        self._next_id = self._last_id() + 1

    def _last_id(self) -> int:
        last = self.recent(1)
        return int(last[0]["id"]) if last else 0

    # --- writing ----------------------------------------------------------
    def append(self, event: dict) -> dict:
        with self._lock:
            event = {"id": self._next_id, **event}
            self._next_id += 1
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, default=_default) + "\n")
        return event

    def write_state(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, default=_default, indent=1), encoding="utf-8")
        # Atomic, so readers never see half a file. On Windows the swap fails while the
        # dashboard has the file open for a moment, so retry briefly.
        for attempt in range(20):
            try:
                os.replace(tmp, self.state_path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)

    def reset(self) -> None:
        """Start a fresh history (used when a demo replay begins)."""
        with self._lock:
            for p in (self.events_path, self.state_path):
                p.unlink(missing_ok=True)
            self._next_id = 1

    # --- reading ----------------------------------------------------------
    def _rows(self):
        if not self.events_path.exists():
            return
        with self.events_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line still being written

    def recent(self, limit: int = 50) -> list[dict]:
        """Newest first."""
        tail = deque(self._rows(), maxlen=max(1, limit))
        return list(reversed(tail))

    def get(self, event_id: int) -> dict | None:
        for row in self._rows():
            if row.get("id") == event_id:
                return row
        return None

    def neighbors(self, event_id: int) -> tuple[int | None, int | None]:
        """(previous id, next id) around an event, for prev/next buttons."""
        prev = nxt = None
        found = False
        for row in self._rows():
            rid = row.get("id")
            if found:
                nxt = rid
                break
            if rid == event_id:
                found = True
            else:
                prev = rid
        return (prev if found else None), nxt

    def state(self) -> dict | None:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None
