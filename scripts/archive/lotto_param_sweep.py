"""Parameter sweep for the lotto exit ladder.

Re-runs the focused-universe backtest with varying:
  - target_delta (strike selection): 0.20 / 0.30 / 0.40 / 0.50
  - target_gain_mult: 1.5x / 2.0x / 3.0x (i.e. +50% / +100% / +200% target)
  - entry_dte: 10 / 14 / 21

LONG-only on the focused $10-$30 universe (the productive cohort from
the previous focused backtest). Goal: find a combination that lifts win
rate to 40-50% while keeping profit factor positive.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

warnings.filterwarnings("ignore")

from data.yfinance_loader import load_bars  # noqa: E402
from lotto_signal_history import _fires, cluster, CLUSTER_GAP_DAYS  # noqa: E402
from lotto_options_backtest import (  # noqa: E402
    bs_price, hv_at,
    HARD_STOP_FRAC, TIME_STOP_FRAC, IV_MARKUP, MIN_SIGMA, MAX_SIGMA,
)
from math import exp, sqrt

# Hand-rolled z-value for arbitrary deltas (since scipy isn't available).
# Solve d1 = N^-1(target_delta) for calls; need inverse-normal CDF.
# Approximation via Beasley-Springer-Moro:
def _norm_ppf(p: float) -> float:
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425
    if p < p_low:
        q = sqrt(-2 * np.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= p_high:
        q = p - 0.5
        r = q*q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = sqrt(-2 * np.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def select_strike_for_delta(S: float, sigma: float, T: float, kind: str,
                             target_delta: float, r: float = 0.04) -> float:
    """Solve K such that the BS delta = target_delta (call) or -target_delta (put)."""
    if kind == "call":
        d1_target = _norm_ppf(target_delta)
        return S * exp(-d1_target * sigma * sqrt(T) + (r + 0.5*sigma*sigma) * T)
    else:
        d1_target = _norm_ppf(1 - target_delta)
        return S * exp(-d1_target * sigma * sqrt(T) + (r + 0.5*sigma*sigma) * T)


def simulate_with_params(
    entry_ts: pd.Timestamp, direction: str,
    bars_2h: pd.DataFrame, daily_close: pd.Series,
    *,
    target_delta: float,
    target_gain_mult: float,
    entry_dte: int,
    hard_stop_frac: float = HARD_STOP_FRAC,
    time_stop_frac: float = TIME_STOP_FRAC,
) -> dict | None:
    """Same lotto exit ladder but with overridable parameters."""
    kind = "call" if direction == "long" else "put"
    close_col = "close" if "close" in bars_2h.columns else "Close"
    idx = bars_2h.index
    pos_arr = np.where(idx == entry_ts)[0]
    if len(pos_arr) == 0:
        bar_pos = idx.searchsorted(entry_ts)
        if bar_pos >= len(idx):
            return None
    else:
        bar_pos = int(pos_arr[0])

    S_entry = float(bars_2h.iloc[bar_pos][close_col])
    sigma_raw = hv_at(daily_close, entry_ts)
    if sigma_raw is None:
        return None
    sigma = float(np.clip(sigma_raw + IV_MARKUP, MIN_SIGMA, MAX_SIGMA))
    T_entry = entry_dte / 365.0
    K = select_strike_for_delta(S_entry, sigma, T_entry, kind, target_delta)
    P_entry = bs_price(S_entry, K, T_entry, sigma, kind)
    if P_entry <= 0.01:
        return None

    hard_stop_level = P_entry * hard_stop_frac
    target_level = P_entry * target_gain_mult
    R = P_entry * hard_stop_frac

    P_max = P_entry
    realized_pnl = 0.0
    remaining_size = 1.0
    half_taken = False
    exit_reason = None
    exit_ts = None

    for j in range(bar_pos + 1, len(bars_2h)):
        ts = idx[j]
        S = float(bars_2h.iloc[j][close_col])
        elapsed_days = (ts - entry_ts).total_seconds() / 86400.0
        T_rem = max(T_entry - elapsed_days / 365.0, 0.0)
        P_now = bs_price(S, K, T_rem, sigma, kind)
        P_max = max(P_max, P_now)

        if not half_taken and P_now <= hard_stop_level:
            realized_pnl += (P_now - P_entry) * remaining_size
            exit_reason, exit_ts = "hard_stop", ts
            break
        if not half_taken and P_now >= target_level:
            realized_pnl += (P_now - P_entry) * 0.5
            remaining_size -= 0.5
            half_taken = True
            P_max = P_now
            continue
        if half_taken:
            trail = 0.5 * P_max + 0.5 * P_entry
            if P_now <= trail:
                realized_pnl += (P_now - P_entry) * remaining_size
                exit_reason, exit_ts = "trail_stop", ts
                break
        if elapsed_days >= entry_dte * time_stop_frac:
            realized_pnl += (P_now - P_entry) * remaining_size
            exit_reason = "time_stop_half_dte" if not half_taken else "time_stop_after_target"
            exit_ts = ts
            break
        if T_rem <= 0:
            realized_pnl += (P_now - P_entry) * remaining_size
            exit_reason, exit_ts = "expiry", ts
            break

    if exit_reason is None:
        ts = idx[-1]
        S = float(bars_2h.iloc[-1][close_col])
        elapsed_days = (ts - entry_ts).total_seconds() / 86400.0
        T_rem = max(T_entry - elapsed_days / 365.0, 0.0)
        P_now = bs_price(S, K, T_rem, sigma, kind)
        realized_pnl += (P_now - P_entry) * remaining_size
        exit_reason, exit_ts = "end_of_data", ts

    return {
        "P_entry": P_entry,
        "R_multiple": realized_pnl / R if R > 0 else 0.0,
        "S_entry": S_entry,
        "exit_reason": exit_reason,
        "target_hit": half_taken,
    }


TICKERS = ["RDW","RGTI","MARA","BBAI","RIOT","CLSK","CIFR","IONQ","NIO","SOFI",
           "LCID","WULF","SOUN","ACHR","JOBY","PLTR","HOOD","F","GME","AMC","AFRM","DKNG"]
WINDOW_START = "2024-05-14"
WINDOW_END = "2026-05-14"


def main():
    # Build the fires + clusters ONCE; reuse across all parameter variants
    print("Building fire list across focused universe...")
    all_events: list[dict] = []
    for t in TICKERS:
        fires, daily_close = _fires(t, WINDOW_START, WINDOW_END)
        if fires.empty:
            continue
        events = cluster(fires, daily_close, gap_days=CLUSTER_GAP_DAYS)
        # Load 2H bars directly (yfinance)
        raw_2h = load_bars(t, period="2y", interval="2h")
        if raw_2h is None or raw_2h.empty:
            continue
        bars_2h = raw_2h.copy()
        bars_2h.index = pd.to_datetime(bars_2h.index)
        bars_2h = bars_2h.sort_index()
        bars_2h.rename(columns={c: c.lower() for c in bars_2h.columns}, inplace=True)
        if bars_2h.index.tz is not None and len(events) and events["entry_ts"].iloc[0].tz is None:
            events["entry_ts"] = events["entry_ts"].dt.tz_localize(bars_2h.index.tz)
        # Filter longs only and entry within $10-$30 band
        for _, ev in events.iterrows():
            if ev["direction"] != "long":
                continue
            entry_close = float(ev["entry_close"])
            if not (10 <= entry_close <= 30):
                continue
            all_events.append({
                "ticker": t,
                "entry_ts": ev["entry_ts"],
                "direction": ev["direction"],
                "bars_2h": bars_2h,
                "daily_close": daily_close,
            })
    print(f"  {len(all_events)} long-only events in $10-$30 band")

    # Parameter variants to sweep
    variants = [
        # baseline (current production)
        dict(name="BASELINE (0.20d, +200%, 10DTE)", target_delta=0.20, target_gain_mult=3.0, entry_dte=10),
        # lower the target — should raise WR
        dict(name="0.20d, +100%, 10DTE",            target_delta=0.20, target_gain_mult=2.0, entry_dte=10),
        dict(name="0.20d,  +50%, 10DTE",            target_delta=0.20, target_gain_mult=1.5, entry_dte=10),
        # higher delta (closer to ATM)
        dict(name="0.30d, +200%, 10DTE",            target_delta=0.30, target_gain_mult=3.0, entry_dte=10),
        dict(name="0.30d, +100%, 10DTE",            target_delta=0.30, target_gain_mult=2.0, entry_dte=10),
        dict(name="0.30d,  +50%, 10DTE",            target_delta=0.30, target_gain_mult=1.5, entry_dte=10),
        dict(name="0.40d, +100%, 10DTE",            target_delta=0.40, target_gain_mult=2.0, entry_dte=10),
        dict(name="0.40d,  +50%, 10DTE",            target_delta=0.40, target_gain_mult=1.5, entry_dte=10),
        dict(name="0.50d,  +50%, 10DTE",            target_delta=0.50, target_gain_mult=1.5, entry_dte=10),
        # longer DTE (more time = fewer time-stops)
        dict(name="0.20d, +200%, 21DTE",            target_delta=0.20, target_gain_mult=3.0, entry_dte=21),
        dict(name="0.30d, +100%, 21DTE",            target_delta=0.30, target_gain_mult=2.0, entry_dte=21),
        dict(name="0.40d, +100%, 21DTE",            target_delta=0.40, target_gain_mult=2.0, entry_dte=21),
        dict(name="0.30d,  +50%, 14DTE",            target_delta=0.30, target_gain_mult=1.5, entry_dte=14),
    ]

    rows = []
    print("\nRunning sweep...")
    for v in variants:
        results = []
        for ev in all_events:
            r = simulate_with_params(
                ev["entry_ts"], ev["direction"],
                ev["bars_2h"], ev["daily_close"],
                target_delta=v["target_delta"],
                target_gain_mult=v["target_gain_mult"],
                entry_dte=v["entry_dte"],
            )
            if r is None:
                continue
            results.append(r)
        if not results:
            continue
        df = pd.DataFrame(results)
        R = df["R_multiple"]
        wins = R[R > 0]
        losses = R[R < 0]
        pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
        rows.append({
            "variant": v["name"],
            "n": len(df),
            "WR%": round((R > 0).mean() * 100, 1),
            "meanR": round(R.mean(), 2),
            "medianR": round(R.median(), 2),
            "PF": round(pf, 2) if pf != float("inf") else "inf",
            "avgW": round(wins.mean(), 2) if not wins.empty else 0,
            "avgL": round(losses.mean(), 2) if not losses.empty else 0,
            "hits%": round(df["target_hit"].mean() * 100, 1),
            "best": round(R.max(), 2),
            "worst": round(R.min(), 2),
        })

    out = pd.DataFrame(rows)
    print("\n══ Parameter sweep — focused $10-$30 LONG-only universe ══")
    print(out.to_string(index=False))
    out.to_csv("scripts/lotto_param_sweep.csv", index=False)
    print(f"\nWrote: scripts/lotto_param_sweep.csv")


if __name__ == "__main__":
    main()
