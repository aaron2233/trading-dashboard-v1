"""Draft fixture CSV generator for the v0.1.0 accuracy gate.

Fetches recent daily bars via the production yfinance loader
(auto_adjust=False, matching scan.py), runs MA Ribbon and Stochastic, and
emits CSVs pre-filled with the deterministic fields (MA values, K, D, zone).

The judgment fields — stack_state for MA Ribbon, signal for Stochastic — are
left blank for the user to fill from TradingView's Data Window. Those are
the cross-checks the v0.1.0 accuracy gate exists for, so pre-filling them
from our Python implementation would be circular.

⚠ Set TradingView to UNADJUSTED prices before reading truth values:
   Settings (gear) → Symbol → uncheck both "Adjustment for dividends"
   and "Adjustment for splits". Our yfinance loader uses auto_adjust=False;
   adjusted truth values won't match raw bars.

Usage:
    python -m fixtures_draft SPY                   # both indicators to stdout
    python -m fixtures_draft SPY --indicator ma_ribbon
    python -m fixtures_draft SPY --days 30
    python -m fixtures_draft SPY --write           # writes to tests/fixtures/truth/<TICKER>_*.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

import pandas as pd

from data.yfinance_loader import load_bars
from indicators.ma_ribbon import MARibbon
from indicators.stochastic import Stochastic


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "truth"
DEFAULT_DAYS = 25


def _format_numeric(value: float) -> str:
    if pd.isna(value):
        return ""
    return f"{value:.2f}"


def draft_ma_ribbon(ticker: str, days: int = DEFAULT_DAYS) -> str:
    bars = load_bars(ticker, period="2y", interval="1d")
    output = MARibbon().compute(bars).tail(days)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["date", "ma_10", "ma_20", "ma_50", "ma_200", "stack_state"])
    for date, row in output.iterrows():
        writer.writerow([
            date.strftime("%Y-%m-%d"),
            _format_numeric(row["ma_10"]),
            _format_numeric(row["ma_20"]),
            _format_numeric(row["ma_50"]),
            _format_numeric(row["ma_200"]),
            "",  # stack_state — fill from TradingView
        ])
    return buf.getvalue()


def draft_stochastic(ticker: str, days: int = DEFAULT_DAYS) -> str:
    bars = load_bars(ticker, period="1y", interval="1d")
    output = Stochastic().compute(bars).tail(days)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["date", "k", "d", "zone", "signal"])
    for date, row in output.iterrows():
        zone = row["zone"] if not pd.isna(row["zone"]) else ""
        writer.writerow([
            date.strftime("%Y-%m-%d"),
            _format_numeric(row["k"]),
            _format_numeric(row["d"]),
            zone,
            "",  # signal — fill from TradingView
        ])
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fixtures_draft",
        description=(
            "Generate draft accuracy fixture CSVs from yfinance bars. "
            "Numerics are pre-filled; categorical truth fields (stack_state, signal) "
            "are left blank for you to fill from TradingView's Data Window."
        ),
    )
    parser.add_argument("ticker", help="Ticker symbol (e.g. SPY)")
    parser.add_argument(
        "--indicator",
        choices=["ma_ribbon", "stochastic", "both"],
        default="both",
        help="Which indicator(s) to draft (default: both)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Most recent daily bars to emit (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help=(
            f"Write directly to {FIXTURE_DIR}/<TICKER>_<indicator>.csv "
            "(overwrites any existing rows)"
        ),
    )

    args = parser.parse_args(argv)
    ticker = args.ticker.upper()

    sections: list[tuple[str, str]] = []
    if args.indicator in ("ma_ribbon", "both"):
        sections.append(("ma_ribbon", draft_ma_ribbon(ticker, args.days)))
    if args.indicator in ("stochastic", "both"):
        sections.append(("stochastic", draft_stochastic(ticker, args.days)))

    if args.write:
        for indicator, content in sections:
            path = FIXTURE_DIR / f"{ticker}_{indicator}.csv"
            path.write_text(content)
            print(f"Wrote {path}", file=sys.stderr)
    else:
        for indicator, content in sections:
            print(f"# {ticker}_{indicator}.csv")
            print(content)

    indicators_to_label = [s[0] for s in sections]
    blank_cols = []
    if "ma_ribbon" in indicators_to_label:
        blank_cols.append("stack_state")
    if "stochastic" in indicators_to_label:
        blank_cols.append("signal")
    print(
        f"\nNext: open the CSV(s) and fill {' / '.join(blank_cols)} "
        "for each row from TradingView (unadjusted prices).\n"
        "\nNote: numeric columns are yfinance/Python output, not TradingView "
        "values. yfinance and TradingView differ by <1% on most bars (within\n"
        "test tolerance). Using these as truth makes the gate a regression "
        "test (yfinance->Python consistency) plus a categorical accuracy\n"
        "test (your stack_state/signal labels vs Python). To keep the gate "
        "as full third-party accuracy, hand-verify each numeric against\n"
        "TradingView's Data Window before saving.",
        file=sys.stderr,
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
