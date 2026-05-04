"""`python -m free_range` CLI for the 3-phase scanner.

Usage:
    python -m free_range
    python -m free_range --user-tickers AAPL NVDA
    python -m free_range --no-options-check
    python -m free_range --json

Outputs the baseline + user + free-range phases as a formatted table by
default, or as JSON with --json. No persistence — the scanner is stateless
and the user is expected to act on the live read.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from free_range.scanner import run_free_range_scan
from free_range.snapshot import CandidateSnapshot, FreeRangeScan


def _format_snapshot(s: CandidateSnapshot) -> str:
    """Single line describing a candidate."""
    price = f"${s.current_price:.2f}" if s.current_price is not None else "$?"
    etf = " ETF" if s.is_etf else ""
    return (
        f"  [{s.tier:<3}] {s.ticker:<6}{etf:<4} {price:<10} "
        f"{s.direction.upper():<6} score={s.score:<4}  {s.why_now}"
    )


def _format_phase(name: str, snaps: list[CandidateSnapshot]) -> list[str]:
    if not snaps:
        return [f"\n{name}: (none)"]
    out = [f"\n{name} ({len(snaps)}):"]
    out.extend(_format_snapshot(s) for s in snaps)
    return out


def _format_scan(scan: FreeRangeScan) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append(f"FREE-RANGE SCAN  ({scan.scan_time_utc})")
    lines.append(f"Universe: {scan.universe_size} tickers — free-range cap {scan.free_range_cap}")
    lines.append("=" * 78)

    lines.extend(_format_phase("BASELINE (QQQ + GLD)", scan.baseline))
    lines.extend(_format_phase("USER-SUBMITTED", scan.user_submitted))
    lines.extend(_format_phase("FREE-RANGE TOP", scan.free_range))

    if scan.notes:
        lines.append("\nNOTES:")
        for n in scan.notes:
            lines.append(f"  - {n}")
    if scan.errors:
        lines.append("\nERRORS:")
        for tkr, msg in scan.errors.items():
            lines.append(f"  {tkr:<6} {msg}")

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="free_range",
        description="3-phase free-range scan: QQQ+GLD baseline → user → free-range top 5",
    )
    parser.add_argument(
        "--user-tickers",
        nargs="*",
        default=[],
        help="Explicit tickers to surface (bypass price band)",
    )
    parser.add_argument(
        "--cap",
        type=int,
        default=5,
        help="Hard cap on free-range candidates (default 5; orchestrator rule 12)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit raw JSON instead of formatted table",
    )
    args = parser.parse_args(argv)

    try:
        scan = run_free_range_scan(
            user_tickers=args.user_tickers,
            free_range_cap=args.cap,
        )
    except Exception as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(scan.to_dict(), indent=2, default=str))
    else:
        print(_format_scan(scan))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
