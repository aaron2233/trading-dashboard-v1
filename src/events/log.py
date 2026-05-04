"""Event log for shadow-trade and stress-time instrumentation.

Events are appended as JSONL (one JSON object per line) to
~/.trading-dashboard/events.jsonl. This is the raw instrumentation surface
Sally called out in round 4:

  - flag          : scan emitted an actionable signal for a ticker
  - shadow_trade  : user took a trade outside the dashboard (manual)
  - resolved      : user marked a prior flag as resolved (manual)

Time-from-flag-to-resolved is derivable by joining flag events to the next
resolved event for the same ticker.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVENTS_PATH = Path.home() / ".trading-dashboard" / "events.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(event_type: str, ticker: str, payload: dict[str, Any] | None = None,
              path: Path = EVENTS_PATH) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "ts": _now_iso(),
        "type": event_type,
        "ticker": ticker.upper(),
    }
    if payload:
        event["payload"] = payload
    with path.open("a") as fh:
        fh.write(json.dumps(event) + "\n")
    return event


def log_flag(ticker: str, payload: dict[str, Any] | None = None,
             path: Path = EVENTS_PATH) -> dict[str, Any]:
    return log_event("flag", ticker, payload, path=path)


def log_shadow_trade(ticker: str, note: str | None = None,
                     path: Path = EVENTS_PATH) -> dict[str, Any]:
    payload = {"note": note} if note else None
    return log_event("shadow_trade", ticker, payload, path=path)


def log_resolved(ticker: str, note: str | None = None,
                 path: Path = EVENTS_PATH) -> dict[str, Any]:
    payload = {"note": note} if note else None
    return log_event("resolved", ticker, payload, path=path)


def read_events(path: Path = EVENTS_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events
