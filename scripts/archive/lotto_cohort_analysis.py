"""Cohort analysis on the lotto options backtest output.

Reads scripts/lotto_options_backtest_2y.csv (or any CSV produced by
lotto_options_backtest.py) and slices the trades by:
  - direction
  - SQN(100) regime at entry
  - daily MA stack at entry
  - SQN(20) band at entry (using the CLAUDE.md calibrated bands)
  - paired cohorts (regime × stack, regime × sqn20-band)

Output: per-cohort R-multiple expectancy, win rate, target-hit rate, and
profit factor. The goal is to identify which (regime, stack, sqn20)
combinations the production lotto_verdict() lets through that actually
have positive expectancy vs. which it lets through that bleed money —
so the gate can be tightened.

Usage:
    .venv/bin/python scripts/lotto_cohort_analysis.py \\
        --csv scripts/lotto_options_backtest_2y.csv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


# CLAUDE.md SQN(20) bands (SPY 1995-2026 calibrated)
SQN20_BANDS = [
    ("strong_bear",  -np.inf, -1.9),
    ("bear",         -1.9,    -1.1),
    ("neutral",      -1.1,    +0.5),
    ("bull",         +0.5,    +1.4),
    ("strong_bull",  +1.4,    +np.inf),
]


def _band(v: float) -> str | None:
    if v is None or pd.isna(v):
        return None
    for name, lo, hi in SQN20_BANDS:
        if lo <= v < hi:
            return name
    return None


def _agg(group: pd.DataFrame) -> pd.Series:
    """Standard expectancy metrics for a cohort."""
    R = group["R_multiple"]
    wins = R[R > 0]
    losses = R[R < 0]
    pf = (wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else (
        np.inf if not wins.empty else 0.0
    )
    return pd.Series({
        "n": len(R),
        "WR%": round((R > 0).mean() * 100, 1),
        "avgR": round(R.mean(), 2),
        "medR": round(R.median(), 2),
        "target_hits%": round(group["target_hit"].mean() * 100, 1),
        "best": round(R.max(), 2),
        "worst": round(R.min(), 2),
        "PF": round(pf, 2) if pf != np.inf else "inf",
    })


def _print_cohort(df: pd.DataFrame, by: str | list[str], min_n: int = 1,
                  title: str | None = None) -> None:
    grp = df.groupby(by, dropna=False).apply(_agg).reset_index()
    grp = grp.sort_values("avgR", ascending=False).reset_index(drop=True)
    grp = grp[grp["n"] >= min_n]
    if title:
        print(f"\n══ {title} ══")
    print(grp.to_string(index=False))


# ─── Main ────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, required=True)
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    df["target_hit"] = df["target_hit"].astype(bool)
    df["sqn20_band"] = df["sqn20_at_entry"].apply(_band)

    print(f"Loaded {len(df)} trades from {args.csv}")
    print(f"  long: {(df['direction']=='long').sum()}, short: {(df['direction']=='short').sum()}")

    # ── Baseline ──
    print("\n══ Baseline (all trades) ══")
    print(_agg(df).to_string())

    # ── Single-dimension slices ──
    _print_cohort(df, "direction", title="By direction")
    _print_cohort(df, "regime_at_entry", min_n=5, title="By SQN(100) regime at entry (n≥5)")
    _print_cohort(df, "stack_at_entry", min_n=5, title="By daily MA stack at entry (n≥5)")
    _print_cohort(df, "sqn20_band", min_n=5, title="By SQN(20) band at entry (n≥5)")
    _print_cohort(df, "exit_reason", title="By exit reason")

    # ── Two-dimension slices — LONG only (where most trades live) ──
    longs = df[df["direction"] == "long"].copy()
    print(f"\n══ LONG trades only: {len(longs)} of {len(df)} ══")

    _print_cohort(longs, ["regime_at_entry", "stack_at_entry"], min_n=10,
                  title="LONG: regime × stack (n≥10)")
    _print_cohort(longs, ["regime_at_entry", "sqn20_band"], min_n=10,
                  title="LONG: regime × sqn20_band (n≥10)")
    _print_cohort(longs, ["stack_at_entry", "sqn20_band"], min_n=10,
                  title="LONG: stack × sqn20_band (n≥10)")

    # ── Shorts ──
    shorts = df[df["direction"] == "short"].copy()
    if len(shorts) >= 10:
        print(f"\n══ SHORT trades only: {len(shorts)} of {len(df)} ══")
        _print_cohort(shorts, ["regime_at_entry", "stack_at_entry"], min_n=5,
                      title="SHORT: regime × stack (n≥5)")

    # ── Winning vs losing cohort recommendation ──
    print("\n══ Gate-refinement candidates (LONG, n≥10) ══")
    grouped = longs.groupby(
        ["regime_at_entry", "stack_at_entry", "sqn20_band"],
        dropna=False
    ).apply(_agg).reset_index()
    grouped = grouped[grouped["n"] >= 10].sort_values("avgR", ascending=False)
    if not grouped.empty:
        print("\nProductive cohorts (avgR > +0.30):")
        keep = grouped[grouped["avgR"].astype(float) > 0.30]
        print(keep.to_string(index=False) if not keep.empty else "  (none)")
        print("\nMoney-losing cohorts (avgR < -0.30):")
        drop = grouped[grouped["avgR"].astype(float) < -0.30]
        print(drop.to_string(index=False) if not drop.empty else "  (none)")

    # ── Target-hit cohorts ──
    print("\n══ Target-hit rate by cohort (LONG, n≥10) — where the home runs live ══")
    target_view = longs.groupby(["regime_at_entry", "stack_at_entry"],
                                 dropna=False).apply(_agg).reset_index()
    target_view = target_view[target_view["n"] >= 10].sort_values(
        "target_hits%", ascending=False
    )
    print(target_view[["regime_at_entry", "stack_at_entry", "n",
                       "target_hits%", "avgR", "WR%"]].to_string(index=False))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
