"""Detection-only earnings-print check for the AI Capex calendar.

Reads `~/.trading-dashboard/config.yaml` regime_health.capex section,
flags tickers whose next_prints date has passed in the last N days
with directions still 'unknown' — i.e., the company has reported but
Aaron hasn't classified the capex direction yet.

Output: structured JSON to stdout (machine-readable for cron pipelines)
or human-readable text. No LLM, no web fetches, no auto-classification.
The user reads the company's press release and updates config.yaml
manually.

Exit codes:
  0 — script ran cleanly (pending list may be empty or non-empty)
  1 — config missing or unreadable
  2 — script error

Usage:
    # Default — human-readable summary
    .venv/bin/python scripts/check_capex_prints.py

    # JSON for cron pipelines
    .venv/bin/python scripts/check_capex_prints.py --json

    # Custom window (default 14 days) for "recently printed but not classified"
    .venv/bin/python scripts/check_capex_prints.py --window-days 7
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from regime_health.tier4_capex import (  # noqa: E402
    _load_capex_config,
    find_pending_capex_updates,
    read_capex_calendar,
)


# Curated IR press-release pages for each ticker — kept here rather than
# in config.yaml because they're stable URLs (refresh only on IR redesign).
IR_PRESS_RELEASE_URLS: dict[str, str] = {
    # Buyers
    "MSFT": "https://www.microsoft.com/en-us/investor/earnings/recent-earnings.aspx",
    "GOOGL": "https://abc.xyz/investor/",
    "META": "https://investor.atmeta.com/financials/quarterly-earnings/",
    "AMZN": "https://ir.aboutamazon.com/quarterly-results/",
    "ORCL": "https://investor.oracle.com/financial-news/default.aspx",
    # Suppliers
    "NVDA": "https://investor.nvidia.com/financial-info/financial-reports/",
    "AVGO": "https://investors.broadcom.com/financial-info/financial-reports",
    "TSM": "https://investor.tsmc.com/english/quarterly-results",
    "ASML": "https://www.asml.com/en/investors/financial-results",
    "MU": "https://investors.micron.com/financial-information/quarterly-results",
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON on stdout (machine-readable).")
    ap.add_argument("--window-days", type=int, default=14,
                    help="How recent a print must be to flag (default 14).")
    args = ap.parse_args()

    cfg = _load_capex_config()
    if cfg is None:
        msg = (
            "❌ No regime_health.capex section in ~/.trading-dashboard/config.yaml. "
            "Cannot run detection."
        )
        if args.json:
            print(json.dumps({"error": msg, "pending": [], "upcoming": []}))
        else:
            print(msg, file=sys.stderr)
        return 1

    today = date.today()
    cutoff = today - timedelta(days=args.window_days)

    # 1. Recently printed but not yet classified — needs action.
    all_pending = find_pending_capex_updates(config=cfg, today=today)
    pending_in_window = [
        p for p in all_pending
        if date.fromisoformat(p["print_date"]) >= cutoff
    ]
    # 2. Older pending (>window_days stale) — overdue.
    overdue = [
        p for p in all_pending
        if date.fromisoformat(p["print_date"]) < cutoff
    ]

    # 3. Upcoming prints — surfaced for FYI.
    cal = read_capex_calendar(config=cfg, today=today)

    # Attach IR URLs for human convenience.
    for p in pending_in_window + overdue:
        p["ir_url"] = IR_PRESS_RELEASE_URLS.get(p["ticker"], "")

    payload = {
        "today": today.isoformat(),
        "window_days": args.window_days,
        "pending_in_window": pending_in_window,
        "overdue": overdue,
        "upcoming_calendar": cal.formatted_value if cal.status == "green" else None,
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    # Human-readable summary.
    if not pending_in_window and not overdue:
        print(f"✓ All capex prints classified. ({today.isoformat()})")
        if payload["upcoming_calendar"]:
            print(f"  Next prints: {payload['upcoming_calendar']}")
        return 0

    if pending_in_window:
        print(f"⚠ {len(pending_in_window)} capex print(s) need classification "
              f"(reported in the last {args.window_days} days):\n")
        for p in pending_in_window:
            line = f"  • {p['ticker']:<6} [{p['cohort']}]  printed {p['print_date']}"
            if p.get("ir_url"):
                line += f"\n            {p['ir_url']}"
            print(line)
        print()

    if overdue:
        print(f"❌ {len(overdue)} capex print(s) OVERDUE "
              f"(reported >{args.window_days} days ago, still unclassified):\n")
        for p in overdue:
            line = f"  • {p['ticker']:<6} [{p['cohort']}]  printed {p['print_date']}"
            if p.get("ir_url"):
                line += f"\n            {p['ir_url']}"
            print(line)
        print()

    print("Edit `~/.trading-dashboard/config.yaml` under regime_health.capex "
          f"to update directions: raised | held | cut.")
    if payload["upcoming_calendar"]:
        print(f"\nNext prints: {payload['upcoming_calendar']}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"❌ check_capex_prints failed: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        sys.exit(2)
