"""QQQM-core monitor state for the Core strategy view.

The core's two-state signal (weekly close > 40WMA AND SQN(100) >= +0.7 →
LONG; exit on close < 40WMA OR SQN <= -0.7) is computed by the daily monitor
job (scripts/beat_market_monitor.py — locally a launchd wrapper runs a copy
and points QQQM_CORE_JSON_OUT at the file below). The dashboard does NOT
recompute the signal: one computation source, read-only here.

GET /api/v1/core/state merges three things:
- the monitor doc (signal, actions, levels) from latest.json
- open positions in the core sleeve from the journal, with DTE
- sleeve sizing from config (premium target = risk_per_trade "high" fraction)
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from config.loader import Config
from positions.model import Position

DEFAULT_MONITOR_JSON = (
    Path.home() / ".trading-dashboard" / "qqqm_core_monitor" / "latest.json"
)

# Sleeve key for the core strategy's capital. Not in the baked-in default
# accounts — users add it in config.yaml; the view degrades without it.
CORE_ACCOUNT_KEY = "beatmarket"

# Monitor runs weekdays pre-open; anything older than 4 days (weekend + one
# missed run) means the feed is broken, not resting.
STALE_AFTER_DAYS = 4

# Roll thresholds from the qqqm-core skill / anti-patterns: never hold the
# core below 60 DTE; plan the roll from 75.
ROLL_DTE_FLOOR = 60
ROLL_DTE_WARN = 75


def load_monitor_state(
    path: Path = DEFAULT_MONITOR_JSON,
) -> tuple[dict | None, str | None]:
    """Return (monitor_doc, error). Missing/corrupt file degrades to an error
    string so the view can explain itself instead of 500ing."""
    if not path.exists():
        return None, f"no monitor output ({path.name} missing — run the qqqm-core monitor job)"
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"unreadable monitor output: {exc}"
    if not isinstance(doc, dict):
        return None, "monitor output is not a JSON object"
    return doc, None


def monitor_is_stale(doc: dict, *, today: date | None = None) -> bool:
    today = today or date.today()
    try:
        generated = date.fromisoformat(str(doc.get("generated")))
    except ValueError:
        return True
    return (today - generated).days > STALE_AFTER_DAYS


def _dte(expiry: str | None, *, today: date) -> int | None:
    if not expiry:
        return None
    try:
        return (datetime.strptime(expiry, "%Y-%m-%d").date() - today).days
    except ValueError:
        return None


def summarize_core_positions(
    positions: list[Position], *, today: date | None = None
) -> list[dict]:
    """Open core-sleeve positions with DTE + roll status for the view."""
    today = today or date.today()
    out: list[dict] = []
    for p in positions:
        if p.status != "open" or p.account_key != CORE_ACCOUNT_KEY:
            continue
        dte = _dte(p.expiry, today=today)
        roll_status = None
        if dte is not None:
            if dte <= ROLL_DTE_FLOOR:
                roll_status = "roll_now"
            elif dte <= ROLL_DTE_WARN:
                roll_status = "roll_window"
        out.append({
            "id": p.id,
            "ticker": p.ticker,
            "strike": p.strike,
            "expiry": p.expiry,
            "dte": dte,
            "roll_status": roll_status,
            "total_cost_usd": p.total_cost_usd,
        })
    return out


def core_sleeve(config: Config) -> dict | None:
    """Sleeve capital + premium target from config; None when unconfigured."""
    acct = config.accounts.get(CORE_ACCOUNT_KEY)
    if acct is None:
        return None
    return {
        "key": CORE_ACCOUNT_KEY,
        "name": acct.name,
        "balance_usd": acct.balance_usd,
        "premium_target_usd": acct.max_loss_for("high"),
    }
