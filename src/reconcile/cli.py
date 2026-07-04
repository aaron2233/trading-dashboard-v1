"""CLI for `python -m reconcile`.

Usage:
  python -m reconcile <robinhood-report.csv | statement.pdf> [--json <out>]

Accepts either the on-demand Robinhood CSV report or the automatic
monthly-statement PDF (dispatch by file extension) and diffs it against
~/.trading-dashboard/positions.json. Exit codes: 0 = clean or
medium/info only, 1 = high-severity findings (ghost trades, stale
opens), 2 = bad input. Never modifies positions.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from positions.store import PositionStore
from reconcile.engine import HIGH, ReconcileReport, reconcile
from reconcile.robinhood_csv import RobinhoodCsvError, parse_report_csv
from reconcile.statement_pdf import parse_statement_pdf

_SEVERITY_ORDER = {"high": 0, "medium": 1, "info": 2}


def format_report(report: ReconcileReport) -> str:
    lines: list[str] = []
    if report.fills_count == 0:
        lines.append("No trade fills found in the CSV — nothing to reconcile.")
    else:
        lines.append(
            f"Reconciled {report.fills_count} fill(s), "
            f"{report.window_start} → {report.window_end}"
        )
    if not report.findings:
        if report.fills_count > 0:
            lines.append("✓ Journal matches the broker for this window.")
    else:
        lines.append(f"✗ {len(report.findings)} discrepancy(ies):")
        ordered = sorted(
            report.findings, key=lambda f: _SEVERITY_ORDER.get(f.severity, 9)
        )
        for f in ordered:
            ids = f" [{', '.join(f.position_ids)}]" if f.position_ids else ""
            lines.append(f"  [{f.severity.upper():<6}] {f.category}: "
                         f"{f.contract}{ids}")
            lines.append(f"           {f.detail}")
    for w in report.warnings:
        lines.append(f"  [INFO  ] parser: {w}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="reconcile",
        description="Diff a Robinhood report CSV against the position journal.",
    )
    p.add_argument("csv_path", metavar="path",
                   help="Robinhood report CSV or monthly-statement PDF")
    p.add_argument("--json", dest="json_path",
                   help="Also write the report as JSON to this path")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"error: {csv_path} does not exist", file=sys.stderr)
        return 2
    try:
        if csv_path.suffix.lower() == ".pdf":
            parsed = parse_statement_pdf(csv_path)
        else:
            parsed = parse_report_csv(csv_path)
    except (RobinhoodCsvError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    positions = PositionStore().list_all()
    report = reconcile(parsed.fills, positions,
                       parser_warnings=parsed.warnings)

    print(format_report(report))
    if parsed.skipped_rows:
        print(f"  (skipped {len(parsed.skipped_rows)} non-trade row(s): "
              f"{', '.join(sorted(set(parsed.skipped_rows)))})")

    if args.json_path:
        out = Path(args.json_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report.to_dict(), indent=2))
        print(f"Wrote JSON report to {out}")

    return 1 if report.has_high_severity else 0


if __name__ == "__main__":
    raise SystemExit(main())
