"""QQQ−SPY SQN(100) spread — how stretched is today's tech/broad divergence?

Companion to explore_historical_sqn.py — same data pull, but focused on the
QQQ-minus-SPY SQN(100) spread over time. Answers "where does today's
tech-vs-broad divergence rank vs history, including the famous bubble peaks?"

Themes are shared with explore_historical_sqn.py and select which event
dates show up in the "spread at notable peaks" section at the bottom.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/explore_sqn_spread.py
    PYTHONPATH=src python3 scripts/explore_sqn_spread.py --theme melt-up-2018
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from data.yfinance_loader import load_bars  # noqa: E402
from indicators.sqn_regime import SQN_100_BANDS, SQNRegime  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)


def _normalize(bars: pd.DataFrame) -> pd.DataFrame:
    close_col = "close" if "close" in bars.columns else "Close"
    return pd.DataFrame(
        {
            "open": bars[bars.columns[bars.columns.str.lower().str.startswith("open")][0]].astype(float),
            "high": bars[bars.columns[bars.columns.str.lower().str.startswith("high")][0]].astype(float),
            "low": bars[bars.columns[bars.columns.str.lower().str.startswith("low")][0]].astype(float),
            "close": bars[close_col].astype(float),
        },
        index=bars.index,
    )


THEMES: dict[str, list[tuple[str, str]]] = {
    "default": [
        ("2000-03-10", "Nasdaq dot-com peak"),
        ("2007-10-09", "Pre-GFC peak"),
        ("2018-01-23", "All-time SPY SQN max"),
        ("2020-02-19", "Pre-COVID peak"),
        ("2021-11-19", "Post-COVID meme/AI rally peak"),
        ("2024-07-16", "Pre-Aug-2024 carry-unwind peak"),
    ],
    "melt-up-2018": [
        ("2016-11-08", "Trump election day"),
        ("2017-05-25", "QQQ all-time SQN(100) max"),
        ("2017-12-04", "Year-end '17 melt-up"),
        ("2018-01-23", "SPY all-time SQN(100) max"),
        ("2018-01-26", "SPY price peak"),
        ("2018-02-02", "Last Friday before Volmageddon"),
        ("2018-02-05", "Volmageddon Monday"),
        ("2018-02-08", "Bottom of initial drop"),
        ("2018-04-02", "April retest low"),
        ("2018-09-20", "Pre-Q4-2018 peak"),
    ],
    "pre-2000-spy": [
        ("1995-07-19", "Mid-1995 (Netscape IPO)"),
        ("1997-07-21", "1997 peak"),
        ("1997-10-27", "Asian crisis crash day"),
        ("1998-10-08", "LTCM bottom"),
        ("1999-01-04", "Start of '99 melt-up"),
        ("1999-12-31", "Year-end 1999"),
        ("2000-03-10", "Nasdaq dot-com peak"),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--theme", default="default",
        help=f"Event-list theme. Available: {sorted(THEMES)}",
    )
    args = ap.parse_args()
    if args.theme not in THEMES:
        print(f"Unknown theme '{args.theme}'. Available: {sorted(THEMES)}",
              file=sys.stderr)
        return 2

    print(f"Theme: {args.theme}")
    print("Loading SPY + QQQ daily (max), computing SQN(100), aligning...")
    spy = _normalize(load_bars("SPY", period="max", interval="1d"))
    qqq = _normalize(load_bars("QQQ", period="max", interval="1d"))
    spy_s100 = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(spy)["sqn_value"]
    qqq_s100 = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(qqq)["sqn_value"]

    df = pd.concat(
        [spy_s100.rename("spy"), qqq_s100.rename("qqq")], axis=1
    ).dropna()
    df["spread"] = df["qqq"] - df["spy"]

    print(f"  Aligned sample: {df.index[0].date()} → {df.index[-1].date()} "
          f"({len(df)} trading days)\n")

    today = df.iloc[-1]
    spread = today["spread"]
    pct = (df["spread"] < spread).mean() * 100  # percentile rank

    # Distribution stats
    p_50 = df["spread"].quantile(0.50)
    p_75 = df["spread"].quantile(0.75)
    p_90 = df["spread"].quantile(0.90)
    p_95 = df["spread"].quantile(0.95)
    p_99 = df["spread"].quantile(0.99)
    p_max = df["spread"].max()
    p_max_date = df["spread"].idxmax().date()

    print(f"━━━ Current reading ({df.index[-1].date()}) ━━━")
    print(f"  SPY SQN(100) = {today['spy']:+5.2f}")
    print(f"  QQQ SQN(100) = {today['qqq']:+5.2f}")
    print(f"  QQQ − SPY    = {spread:+5.2f}  "
          f"(percentile rank: {pct:5.1f}% of all history)")
    print()

    print("━━━ Distribution stats (all aligned history) ━━━")
    print(f"  median       = {p_50:+5.2f}")
    print(f"  p75          = {p_75:+5.2f}")
    print(f"  p90          = {p_90:+5.2f}")
    print(f"  p95          = {p_95:+5.2f}")
    print(f"  p99          = {p_99:+5.2f}")
    print(f"  max          = {p_max:+5.2f}  (on {p_max_date})")
    print()

    print("━━━ Recent trend ━━━")
    for n_days, label in [(30, "30 trading days"), (90, "90"), (180, "180")]:
        recent = df["spread"].iloc[-n_days:]
        print(f"  last {label:>20}: mean {recent.mean():+5.2f}, "
              f"min {recent.min():+5.2f}, max {recent.max():+5.2f}")
    print()

    print("━━━ Spread at theme events ━━━")
    for date_str, label in THEMES[args.theme]:
        ts = pd.Timestamp(date_str)
        if df.index.tz is not None:
            ts = ts.tz_localize(df.index.tz)
        candidates = df.index[df.index <= ts]
        if len(candidates) == 0:
            print(f"  {date_str}  {label}: no data")
            continue
        idx = candidates[-1]
        r = df.loc[idx]
        print(f"  {date_str}  {label:>32}  "
              f"SPY {r['spy']:+5.2f}  QQQ {r['qqq']:+5.2f}  "
              f"spread {r['spread']:+5.2f}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
