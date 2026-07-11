"""G4 trigger-bar filter analysis on top of the v2 lotto backtest cohort.

Reads `scripts/lotto_options_backtest_2y_v2.csv` (the 419-trade, 25-ticker,
2y cohort that established v2). For each trade, looks up the 2H entry bar's
open and close on yfinance, classifies the bar (green/red/doji), and tags
G4-pass / G4-fail. Then re-aggregates the cohort to show what v2+G4 would
have produced if G4 had filtered the same trades.

Caveats (read these before trusting the numbers):
  1. Post-hoc filter, not a re-run. The v2 cohort was clustered with
     CLUSTER_GAP_DAYS; if G4 drops a cluster's entry bar, the production
     behavior would be "no entry until the next fire passes G4" — that
     next fire might be N bars later and could land on a different trade
     setup. This analysis simply *drops* the trade rather than promoting
     a later bar. The result is an upper bound on filter strictness; in
     production, some of the dropped trades would be replaced by later
     in-cluster fires.
  2. Doji handling: treated as G4-fail (SKILL.md says "doji = skip").
  3. yfinance 2h bars are reconstructed; the historical open/close at the
     exact entry timestamp may differ from what the scanner saw live. Bar
     boundaries depend on the bar-builder; we accept some slop.
  4. Same Black-Scholes / HV20 / no-vega assumptions as the v2 backtest.

Usage:
    .venv/bin/python scripts/lotto_g4_trigger_bar_backtest.py \\
        --csv-in scripts/lotto_options_backtest_2y_v2.csv \\
        --csv-out scripts/lotto_g4_trigger_bar_results.csv
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from data.yfinance_loader import load_bars  # noqa: E402


DOJI_TOLERANCE_FRAC = 0.0005  # |close-open|/open < 0.05% → doji


def classify_bar(open_: float, close_: float) -> str:
    """green / red / doji."""
    if open_ is None or close_ is None or open_ <= 0:
        return "missing"
    move_frac = abs(close_ - open_) / open_
    if move_frac < DOJI_TOLERANCE_FRAC:
        return "doji"
    return "green" if close_ > open_ else "red"


def g4_pass(direction: str, bar_color: str) -> bool:
    """G4: long needs green, short needs red; doji = fail."""
    if bar_color == "green" and direction == "long":
        return True
    if bar_color == "red" and direction == "short":
        return True
    return False


def load_2h_bars(ticker: str) -> pd.DataFrame | None:
    """Load 2h bars with open + close, sorted index."""
    raw = load_bars(ticker, period="2y", interval="2h")
    if raw is None or raw.empty:
        return None
    df = raw.copy()
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    # Normalise column names — yfinance returns title-case
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    if "open" not in df.columns or "close" not in df.columns:
        return None
    return df


def lookup_entry_bar(
    bars: pd.DataFrame, entry_ts_str: str
) -> tuple[float | None, float | None, pd.Timestamp | None]:
    """Locate the bar matching entry_ts. Falls back to nearest preceding."""
    ts = pd.to_datetime(entry_ts_str)
    idx = bars.index
    # Make ts tz-aware to match bars if needed
    if idx.tz is not None and ts.tz is None:
        ts = ts.tz_localize(idx.tz)
    elif idx.tz is None and ts.tz is not None:
        ts = ts.tz_localize(None)
    # Exact match first
    if ts in idx:
        row = bars.loc[ts]
        return float(row["open"]), float(row["close"]), ts
    # Nearest preceding bar (the scanner reads "most recently closed")
    pos = idx.searchsorted(ts, side="right") - 1
    if pos < 0 or pos >= len(idx):
        return None, None, None
    nearest = idx[pos]
    # Don't fall back more than 4 hours away (2 bars) — beyond that we're
    # likely missing data and shouldn't pretend we found the bar
    if abs((nearest - ts).total_seconds()) > 4 * 3600:
        return None, None, None
    row = bars.iloc[pos]
    return float(row["open"]), float(row["close"]), nearest


def summarise(df: pd.DataFrame, label: str) -> dict:
    R = df["R_multiple"]
    wins = R[R > 0]
    losses = R[R < 0]
    pf = (wins.sum() / abs(losses.sum())) if (not losses.empty and losses.sum() != 0) else float("nan")
    return {
        "cohort": label,
        "n": len(df),
        "win_rate": (R > 0).mean() if len(df) else float("nan"),
        "mean_R": R.mean() if len(df) else float("nan"),
        "median_R": R.median() if len(df) else float("nan"),
        "best_R": R.max() if len(df) else float("nan"),
        "worst_R": R.min() if len(df) else float("nan"),
        "target_hit_rate": df["target_hit"].mean() if len(df) else float("nan"),
        "avg_win_R": wins.mean() if not wins.empty else float("nan"),
        "avg_loss_R": losses.mean() if not losses.empty else float("nan"),
        "profit_factor": pf,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-in", required=True, type=Path)
    ap.add_argument("--csv-out", type=Path, default=None)
    args = ap.parse_args()

    df = pd.read_csv(args.csv_in)
    print(f"Loaded {len(df)} trades from {args.csv_in.name}")
    print(f"  Universe: {sorted(df.ticker.unique())}")
    print(f"  Date range: {df.entry_ts.min()} → {df.entry_ts.max()}")
    print(f"  Direction split: {df.direction.value_counts().to_dict()}\n")

    # Cache bars per ticker
    bars_cache: dict[str, pd.DataFrame | None] = {}
    for t in sorted(df.ticker.unique()):
        bars_cache[t] = load_2h_bars(t)
        if bars_cache[t] is None:
            print(f"  ⚠️  {t}: no 2H data")

    df = df.copy()
    bar_opens, bar_closes, bar_colors, g4_passes, bar_found = [], [], [], [], []
    for _, row in df.iterrows():
        bars = bars_cache.get(row["ticker"])
        if bars is None:
            bar_opens.append(None); bar_closes.append(None)
            bar_colors.append("missing")
            g4_passes.append(False); bar_found.append(False)
            continue
        o, c, ts = lookup_entry_bar(bars, row["entry_ts"])
        if o is None or c is None:
            bar_opens.append(None); bar_closes.append(None)
            bar_colors.append("missing")
            g4_passes.append(False); bar_found.append(False)
            continue
        color = classify_bar(o, c)
        bar_opens.append(o); bar_closes.append(c)
        bar_colors.append(color)
        g4_passes.append(g4_pass(row["direction"], color))
        bar_found.append(True)

    df["bar_open"] = bar_opens
    df["bar_close"] = bar_closes
    df["bar_color"] = bar_colors
    df["g4_pass"] = g4_passes
    df["bar_found"] = bar_found

    found = df["bar_found"].sum()
    print(f"Bar lookup: {found}/{len(df)} matched ({found/len(df)*100:.1f}%)\n")

    # ── Bar-color distribution overall ──
    color_dist = df["bar_color"].value_counts()
    print("Bar color distribution (entry bar):")
    for color, n in color_dist.items():
        print(f"  {color:<8} {n:>4}  ({n/len(df)*100:5.1f}%)")
    print()

    # ── Bar color × direction × outcome ──
    print("Bar color × direction × outcome:")
    grp = df.groupby(["direction", "bar_color"]).agg(
        n=("R_multiple", "count"),
        win_rate=("R_multiple", lambda s: (s > 0).mean()),
        mean_R=("R_multiple", "mean"),
        target_hits=("target_hit", "sum"),
    ).reset_index()
    for _, r in grp.iterrows():
        in_direction = (
            (r["direction"] == "long" and r["bar_color"] == "green")
            or (r["direction"] == "short" and r["bar_color"] == "red")
        )
        marker = "  G4✓" if in_direction else "  G4✗"
        print(
            f"  {r['direction']:<5} {r['bar_color']:<8} n={int(r['n']):<3} "
            f"WR={r['win_rate']*100:4.0f}%  avgR={r['mean_R']:+5.2f}  "
            f"hits={int(r['target_hits']):<2}{marker}"
        )
    print()

    # ── v2 baseline vs v2+G4 ──
    summaries = [
        summarise(df, "v2_baseline (all 419 / surviving cohort)"),
        summarise(df[df["g4_pass"]], "v2+G4 (trigger-bar in-direction only)"),
        summarise(df[~df["g4_pass"] & df["bar_found"]], "v2_G4_FAIL_only (bar against direction or doji)"),
    ]
    print("═══ COHORT COMPARISON ═══")
    print(
        f"  {'cohort':<55}{'n':>5}{'WR':>7}{'avgR':>8}{'medR':>8}"
        f"{'PF':>7}{'targetHit%':>13}"
    )
    for s in summaries:
        wr = f"{s['win_rate']*100:>5.1f}%" if not np.isnan(s["win_rate"]) else "    --"
        avgR = f"{s['mean_R']:>+7.3f}" if not np.isnan(s["mean_R"]) else "      --"
        medR = f"{s['median_R']:>+7.3f}" if not np.isnan(s["median_R"]) else "      --"
        pf = f"{s['profit_factor']:>6.2f}" if not np.isnan(s["profit_factor"]) else "    --"
        thr = f"{s['target_hit_rate']*100:>10.1f}%" if not np.isnan(s["target_hit_rate"]) else "        --"
        print(f"  {s['cohort']:<55}{s['n']:>5}{wr:>7}{avgR:>8}{medR:>8}{pf:>7}{thr:>13}")
    print()

    # ── Per-ticker comparison ──
    print("Per-ticker (v2_baseline → v2+G4):")
    print(f"  {'ticker':<7}{'n_v2':>6}{'n_G4':>6}{'WR_v2':>8}{'WR_G4':>8}"
          f"{'avgR_v2':>10}{'avgR_G4':>10}")
    for t in sorted(df.ticker.unique()):
        sub = df[df.ticker == t]
        sub_g4 = sub[sub["g4_pass"]]
        n_v2 = len(sub)
        n_g4 = len(sub_g4)
        if n_v2 == 0:
            continue
        wr_v2 = (sub.R_multiple > 0).mean() * 100
        wr_g4 = (sub_g4.R_multiple > 0).mean() * 100 if n_g4 else float("nan")
        ar_v2 = sub.R_multiple.mean()
        ar_g4 = sub_g4.R_multiple.mean() if n_g4 else float("nan")
        wr_g4_str = f"{wr_g4:>6.1f}%" if not np.isnan(wr_g4) else "     --"
        ar_g4_str = f"{ar_g4:>+8.2f}" if not np.isnan(ar_g4) else "      --"
        print(f"  {t:<7}{n_v2:>6}{n_g4:>6}{wr_v2:>7.1f}%{wr_g4_str:>8}"
              f"{ar_v2:>+9.2f}{ar_g4_str:>10}")
    print()

    if args.csv_out:
        df.to_csv(args.csv_out, index=False)
        print(f"Wrote {args.csv_out} ({len(df)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
