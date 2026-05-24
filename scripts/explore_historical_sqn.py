"""SQN(100) + SQN(20) readings at famous market turning points.

Pulls max-period daily bars for SPY (since 1993) and QQQ (since 1999),
computes both SQN windows, and prints the regime + value at a curated
list of bubble peaks / bear bottoms. Pure curiosity script — not part
of any production code path.

Themes select which event list to scan. `--list-themes` to see them.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/explore_historical_sqn.py
    PYTHONPATH=src python3 scripts/explore_historical_sqn.py --theme melt-up-2018
    PYTHONPATH=src python3 scripts/explore_historical_sqn.py --list-themes
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from data.yfinance_loader import load_bars  # noqa: E402
from indicators.sqn_regime import SQN_100_BANDS, SQN_20_BANDS, SQNRegime  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)


def _normalize(bars: pd.DataFrame) -> pd.DataFrame:
    """Force lowercase OHLC columns."""
    close_col = "close" if "close" in bars.columns else "Close"
    out = pd.DataFrame(
        {
            "open": bars[bars.columns[bars.columns.str.lower().str.startswith("open")][0]].astype(float),
            "high": bars[bars.columns[bars.columns.str.lower().str.startswith("high")][0]].astype(float),
            "low": bars[bars.columns[bars.columns.str.lower().str.startswith("low")][0]].astype(float),
            "close": bars[close_col].astype(float),
        },
        index=bars.index,
    )
    return out


def reading_at(bars: pd.DataFrame, sqn100: pd.DataFrame, sqn20: pd.DataFrame, target: str) -> str:
    """Return a formatted line for the bar closest to (but not after) `target`."""
    target_ts = pd.Timestamp(target)
    # bars.index is tz-naive; coerce target if needed
    if bars.index.tz is not None:
        target_ts = target_ts.tz_localize(bars.index.tz)
    candidates = bars.index[bars.index <= target_ts]
    if len(candidates) == 0:
        return "no data (before ticker inception)"
    bar_date = candidates[-1]
    close = bars.loc[bar_date, "close"]
    s100_val = sqn100.loc[bar_date, "sqn_value"]
    s100_reg = sqn100.loc[bar_date, "regime"]
    s20_val = sqn20.loc[bar_date, "sqn_value"]
    s20_reg = sqn20.loc[bar_date, "regime"]
    return (
        f"close ${close:>8.2f}  "
        f"SQN(100)={s100_val:+5.2f} [{s100_reg:>12}]  "
        f"SQN(20)={s20_val:+5.2f} [{s20_reg:>12}]"
    )


THEMES: dict[str, list[tuple[str, str]]] = {
    # Default: a sampler of recognized peaks + bottoms across 25+ years.
    "default": [
        ("2000-03-10", "Nasdaq dot-com peak"),
        ("2000-09-01", "6 months into dot-com bust"),
        ("2002-10-09", "Dot-com bear bottom"),
        ("2007-10-09", "Pre-GFC peak"),
        ("2008-09-15", "Lehman collapse day"),
        ("2009-03-09", "GFC bear bottom"),
        ("2018-09-20", "Pre-Q4-2018 peak"),
        ("2018-12-24", "Q4 2018 bottom"),
        ("2020-02-19", "Pre-COVID peak"),
        ("2020-03-23", "COVID crash bottom"),
        ("2021-11-19", "Post-COVID meme/AI rally peak"),
        ("2022-10-12", "2022 bear bottom"),
        ("2024-07-16", "Pre-Aug-2024 yen-carry unwind peak"),
        ("2025-02-19", "Early-2025 peak (pre-tariff dip)"),
        ("2026-05-11", "Today"),
    ],
    # 2017-2018 melt-up + Volmageddon — the clearest "sustained-extreme
    # SQN(100) followed by sharp resolution" sequence in the dataset.
    # SPY's all-time SQN(100) max (+3.97) lands inside this window.
    "melt-up-2018": [
        ("2016-11-08", "Trump election day (start of 'Trump bump')"),
        ("2017-03-01", "Early melt-up phase"),
        ("2017-05-25", "QQQ all-time SQN(100) max"),
        ("2017-08-21", "Late-summer breather"),
        ("2017-12-04", "Year-end rally underway"),
        ("2018-01-23", "SPY all-time SQN(100) max"),
        ("2018-01-26", "SPY price peak (close basis)"),
        ("2018-02-02", "Last Friday before Volmageddon"),
        ("2018-02-05", "Volmageddon Monday (XIV blew up)"),
        ("2018-02-08", "Bottom of initial Feb drop"),
        ("2018-04-02", "Re-test low in April"),
        ("2018-09-20", "Pre-Q4-2018 recovery peak"),
    ],
    # 1990s SPY-only run leading into the dot-com bubble (QQQ won't have
    # data before 1999-03-10 — those rows just show 'no data').
    "pre-2000-spy": [
        ("1995-07-19", "Mid-1995 (Netscape IPO month)"),
        ("1996-07-15", "1996 summer correction low"),
        ("1997-07-21", "1997 peak before Asian crisis"),
        ("1997-10-27", "Asian crisis crash day (SPY -7%)"),
        ("1998-07-17", "Mid-1998 peak before LTCM"),
        ("1998-10-08", "LTCM / Russia crisis bottom"),
        ("1999-01-04", "Start of 1999 — late melt-up"),
        ("1999-12-31", "Year-end 1999 (peak melt-up)"),
        ("2000-03-10", "Nasdaq dot-com peak"),
    ],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--theme", default="default",
        help=f"Event-list theme to use. Available: {sorted(THEMES)}",
    )
    ap.add_argument(
        "--list-themes", action="store_true",
        help="Print available themes + their event counts and exit.",
    )
    args = ap.parse_args()

    if args.list_themes:
        for name, events in THEMES.items():
            print(f"  {name}: {len(events)} events "
                  f"({events[0][0]} → {events[-1][0]})")
        return 0

    if args.theme not in THEMES:
        print(f"Unknown theme '{args.theme}'. Available: {sorted(THEMES)}",
              file=sys.stderr)
        return 2

    tickers = {"SPY": "S&P 500", "QQQ": "Nasdaq 100"}
    events = THEMES[args.theme]

    print(f"Theme: {args.theme}  ({len(events)} events)")
    print("Loading SPY + QQQ (max history) and computing SQN windows...")
    print()

    series = {}
    for tk in tickers:
        try:
            raw = load_bars(tk, period="max", interval="1d")
        except Exception as exc:
            print(f"  {tk}: load failed — {exc}")
            continue
        if raw is None or raw.empty:
            print(f"  {tk}: no data")
            continue
        df = _normalize(raw)
        s100 = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(df)
        s20 = SQNRegime(lookback=20, bands=SQN_20_BANDS).compute(df)
        series[tk] = (df, s100, s20)
        print(
            f"  {tk}: {len(df)} bars, "
            f"{df.index[0].date()} → {df.index[-1].date()}"
        )
    print()

    # All-time max/min SQN(100) per ticker — bubble-fingerprint highlights
    print("━━━ All-time extremes (since data inception) ━━━")
    for tk, (df, s100, _s20) in series.items():
        s100_dropna = s100.dropna(subset=["sqn_value"])
        if s100_dropna.empty:
            continue
        max_row = s100_dropna.loc[s100_dropna["sqn_value"].idxmax()]
        min_row = s100_dropna.loc[s100_dropna["sqn_value"].idxmin()]
        print(
            f"  {tk}  max SQN(100) = {max_row['sqn_value']:+5.2f} "
            f"[{max_row['regime']:>12}]  on {s100_dropna['sqn_value'].idxmax().date()}"
        )
        print(
            f"  {tk}  min SQN(100) = {min_row['sqn_value']:+5.2f} "
            f"[{min_row['regime']:>12}]  on {s100_dropna['sqn_value'].idxmin().date()}"
        )
    print()

    # Event-by-event scan
    for date_str, label in events:
        print(f"━━━ {date_str}  {label} ━━━")
        for tk, full_name in tickers.items():
            if tk not in series:
                continue
            df, s100, s20 = series[tk]
            line = reading_at(df, s100, s20, date_str)
            print(f"  {tk} ({full_name:>10}): {line}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
