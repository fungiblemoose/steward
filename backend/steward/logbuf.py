"""In-memory ring buffer logging handler, surfaced by the UI log viewer.

Keeps the last N structured log records so operators can see what the daemon is
doing without shelling into the box. This is a convenience view, not the system
of record — real deployments should still ship logs off-box.
"""
from __future__ import annotations

import collections
import logging
from typing import Any


class RingLogHandler(logging.Handler):
    def __init__(self, capacity: int = 500) -> None:
        super().__init__()
        self.records: collections.deque[dict[str, Any]] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append({
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            })
        except Exception:  # never let logging crash the app
            pass

    def tail(self, limit: int = 200, level: str | None = None) -> list[dict[str, Any]]:
        items = list(self.records)
        if level:
            wanted = level.upper()
            items = [r for r in items if r["level"] == wanted]
        return items[-limit:][::-1]  # newest first


# Process-wide singleton so any module's logger feeds the same buffer.
ring_handler = RingLogHandler()
