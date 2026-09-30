from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MarkerLog:
    """Wall-clock + monotonic markers for latency / run evidence."""

    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.events: list[dict[str, Any]] = []

    def mark(self, name: str, **extra: Any) -> dict[str, Any]:
        event = {
            "name": name,
            "iso8601_utc": utc_iso(),
            "monotonic_ms": int((time.monotonic() - self.t0) * 1000),
            **extra,
        }
        self.events.append(event)
        print(f"MARKER {name} @ {event['monotonic_ms']}ms")
        return event
