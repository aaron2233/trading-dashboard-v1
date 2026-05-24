"""Lotto options P&L simulation on top of clustered signal events.

Pipeline:
  1. Reuse `lotto_signal_history` to scan 2H bars with the production
     `lotto_verdict()` and cluster fires into trade events.
  2. For each event, simulate the lotto options trade lifecycle:
       - Entry: 10 DTE OTM call/put at ~0.20 delta, priced via
         Black-Scholes using HV20 (annualized realized vol) as IV proxy,
         with a +5pp markup since IV typically runs above HV.
       - Walk forward on 2H bars, recomputing premium with Black-Scholes
         (vol held constant; pure delta+theta P&L, vega ignored).
       - Exit ladder per CLAUDE.md / lotto-setups.md:
            • Hard stop:  premium <= 0.5 * P_entry
            • Target:     premium >= 3.0 * P_entry → sell 50%, switch to trail
            • Trail:      after target, exit remainder when premium retraces
                          50% of the gain from entry (stop = midpoint of
                          peak and entry)
            • Time stop:  close at half initial DTE if still open
            • Expiry:     close at intrinsic if T → 0
  3. Aggregate as R-multiples (R = max risk = 0.5 * P_entry per lotto rules).
  4. CSV export.

Limitations
-----------
  - HV20 ≠ true IV. Holding sigma constant ignores vega; the +200% target
    is harder to hit in vol-expansion regimes and easier in crush.
  - No slippage / bid-ask. Real lotto fills are 5-10% wide.
  - No IV-rank gate. Some entries here would be blocked live by IVR>70%.
  - Risk-free rate fixed at 4%. Sensitivity is minimal at 10 DTE.

Usage (from repo root):
    .venv/bin/python scripts/lotto_options_backtest.py \\
        --tickers SMH,GLD,USO,SLV,TQQQ,TSLA,IONQ,NVDA,AAPL,MSFT,IWM \\
        --start 2026-01-01 --end 2026-05-11 \\
        --csv scripts/lotto_options_backtest.csv
"""
from __future__ import annotations

import argparse
import sys
import warnings
from math import erf, exp, log, sqrt
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from lotto_signal_history import _fires, cluster, CLUSTER_GAP_DAYS  # noqa: E402
from data.yfinance_loader import load_bars  # noqa: E402


# ─── Lotto strategy parameters ──────────────────────────────────────────────

ENTRY_DTE = 10              # mid of 7-14 DTE band
TARGET_DELTA = 0.20         # 0.15-0.25 OTM band; 0.20 is the middle
HARD_STOP_FRAC = 0.50       # close if premium ≤ 0.5 * entry
TARGET_GAIN_MULT = 3.0      # +200% gain = premium reaches 3x entry
TIME_STOP_FRAC = 0.50       # close at half DTE if still in position
RISK_FREE = 0.04
HV_LOOKBACK = 20
IV_MARKUP = 0.05            # IV typically ~5pp above HV
MIN_SIGMA = 0.15
MAX_SIGMA = 1.50

# z such that N(z) = 1 - TARGET_DELTA = 0.80  →  z ≈ 0.8416
Z_TARGET_DELTA = 0.8416


# ─── Black-Scholes ──────────────────────────────────────────────────────────


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))


def bs_price(S: float, K: float, T: float, sigma: float, kind: str,
             r: float = RISK_FREE) -> float:
    """European call/put price. T in years. Returns intrinsic at T<=0."""
    if T <= 0:
        return max(S - K, 0.0) if kind == "call" else max(K - S, 0.0)
    if sigma <= 0:
        sigma = 0.01
    sqrtT = sqrt(T)
    d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    if kind == "call":
        return S * _norm_cdf(d1) - K * exp(-r * T) * _norm_cdf(d2)
    return K * exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def select_strike(S: float, sigma: float, T: float, kind: str,
                  r: float = RISK_FREE) -> float:
    """Strike targeting ~TARGET_DELTA OTM via the BS delta formula.

    Call: d1 = z₂₀ = N⁻¹(0.20) ≈ -0.8416  → K = S * exp(+|z|σ√T + (r+σ²/2)T)
    Put:  d1 = z₈₀ = N⁻¹(0.80) ≈ +0.8416  → K = S * exp(-|z|σ√T + (r+σ²/2)T)
    """
    drift = (r + 0.5 * sigma * sigma) * T
    vol_term = Z_TARGET_DELTA * sigma * sqrt(T)
    if kind == "call":
        return S * exp(vol_term + drift)
    return S * exp(-vol_term + drift)


# ─── Volatility estimate ────────────────────────────────────────────────────


def hv_at(daily_close: pd.Series, entry_date: pd.Timestamp,
          lookback: int = HV_LOOKBACK) -> float | None:
    """Annualized realized vol from the `lookback` daily log-returns ending
    at the bar prior to `entry_date`. Returns None if insufficient history."""
    idx = daily_close.index
    pos = idx.searchsorted(entry_date.normalize())
    if pos < lookback + 1:
        return None
    window = daily_close.iloc[pos - lookback : pos]
    if len(window) < lookback:
        return None
    log_ret = np.log(window.values[1:] / window.values[:-1])
    if len(log_ret) < 5:
        return None
    sigma_daily = float(np.std(log_ret, ddof=1))
    return sigma_daily * sqrt(252)


# ─── Trade simulator ────────────────────────────────────────────────────────


def simulate_trade(
    entry_ts: pd.Timestamp,
    direction: str,
    bars_2h: pd.DataFrame,
    daily_close: pd.Series,
) -> dict | None:
    """Run one options trade through the lotto exit ladder.

    `bars_2h` must be indexed by timestamp with a 'close' column, sorted
    ascending. Returns a dict of trade metrics or None if the entry bar
    can't be located or volatility can't be estimated."""
    kind = "call" if direction == "long" else "put"
    close_col = "close" if "close" in bars_2h.columns else "Close"

    # Locate the entry bar in the 2H series
    idx = bars_2h.index
    pos_arr = np.where(idx == entry_ts)[0]
    if len(pos_arr) == 0:
        # Fall back to the nearest preceding bar
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
    T_entry = ENTRY_DTE / 365.0

    K = select_strike(S_entry, sigma, T_entry, kind)
    P_entry = bs_price(S_entry, K, T_entry, sigma, kind)
    if P_entry <= 0.01:
        return None  # Degenerate; not a tradable lotto

    hard_stop_level = P_entry * HARD_STOP_FRAC
    target_level = P_entry * TARGET_GAIN_MULT
    R = P_entry * HARD_STOP_FRAC  # max loss = 50% of premium

    # Walk forward bar by bar
    P_max = P_entry
    realized_pnl = 0.0           # $ per contract, on the originally-bought 1.0 unit
    remaining_size = 1.0
    half_taken = False
    exit_reason: str | None = None
    exit_ts: pd.Timestamp | None = None
    bars_held = 0
    target_premium_at_hit: float | None = None

    for j in range(bar_pos + 1, len(bars_2h)):
        ts = idx[j]
        S = float(bars_2h.iloc[j][close_col])
        elapsed_days = (ts - entry_ts).total_seconds() / 86400.0
        T_rem = max(T_entry - elapsed_days / 365.0, 0.0)
        bars_held = j - bar_pos

        P_now = bs_price(S, K, T_rem, sigma, kind)
        P_max = max(P_max, P_now)

        # 1. Hard stop (before any target hit)
        if not half_taken and P_now <= hard_stop_level:
            realized_pnl += (P_now - P_entry) * remaining_size
            remaining_size = 0.0
            exit_reason = "hard_stop"
            exit_ts = ts
            break

        # 2. Target → sell half, switch to trail
        if not half_taken and P_now >= target_level:
            realized_pnl += (P_now - P_entry) * 0.5
            remaining_size -= 0.5
            half_taken = True
            target_premium_at_hit = P_now
            P_max = P_now  # reset peak for trail tracking from this point
            continue

        # 3. Trail stop on remaining half (50% giveback from peak)
        if half_taken:
            trail_stop = 0.5 * P_max + 0.5 * P_entry
            if P_now <= trail_stop:
                realized_pnl += (P_now - P_entry) * remaining_size
                remaining_size = 0.0
                exit_reason = "trail_stop"
                exit_ts = ts
                break

        # 4. Time stop at half DTE
        if elapsed_days >= ENTRY_DTE * TIME_STOP_FRAC:
            realized_pnl += (P_now - P_entry) * remaining_size
            remaining_size = 0.0
            exit_reason = "time_stop_half_dte" if not half_taken else "time_stop_after_target"
            exit_ts = ts
            break

        # 5. Expiry guard (shouldn't hit at half-DTE stop, but safe net)
        if T_rem <= 0:
            realized_pnl += (P_now - P_entry) * remaining_size
            remaining_size = 0.0
            exit_reason = "expiry"
            exit_ts = ts
            break

    if exit_reason is None:
        # Walked off available bars — mark to last bar at current premium
        ts = idx[-1]
        S = float(bars_2h.iloc[-1][close_col])
        elapsed_days = (ts - entry_ts).total_seconds() / 86400.0
        T_rem = max(T_entry - elapsed_days / 365.0, 0.0)
        P_now = bs_price(S, K, T_rem, sigma, kind)
        realized_pnl += (P_now - P_entry) * remaining_size
        exit_reason = "end_of_data"
        exit_ts = ts

    return {
        "entry_ts": entry_ts,
        "exit_ts": exit_ts,
        "direction": direction,
        "kind": kind,
        "S_entry": S_entry,
        "K": K,
        "sigma": sigma,
        "P_entry": P_entry,
        "P_max": P_max,
        "realized_pnl_per_contract": realized_pnl,
        "R_multiple": realized_pnl / R if R > 0 else 0.0,
        "exit_reason": exit_reason,
        "days_held": (exit_ts - entry_ts).total_seconds() / 86400.0 if exit_ts else None,
        "target_hit": half_taken,
    }


# ─── Main pipeline ──────────────────────────────────────────────────────────


def _load_2h(ticker: str) -> pd.DataFrame | None:
    raw = load_bars(ticker, period="2y", interval="2h")
    if raw is None or raw.empty:
        return None
    raw = raw.copy()
    raw.index = pd.to_datetime(raw.index)
    raw = raw.sort_index()
    return raw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--gap-days", type=int, default=CLUSTER_GAP_DAYS)
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    all_trades: list[dict] = []

    for t in tickers:
        print(f"Scanning {t}...")
        try:
            fires, daily_close = _fires(t, args.start, args.end)
        except Exception as e:
            print(f"  {t}: load failed ({type(e).__name__}: {e}); skipping")
            continue
        if fires.empty:
            print(f"  {t}: no fires")
            continue
        events = cluster(fires, daily_close, gap_days=args.gap_days)
        try:
            bars_2h = _load_2h(t)
        except Exception as e:
            print(f"  {t}: 2H load failed ({type(e).__name__}: {e}); skipping")
            continue
        if bars_2h is None or bars_2h.empty:
            print(f"  {t}: no 2H data")
            continue
        # Align timezone — fires timestamps came from the same source so should match
        if bars_2h.index.tz is not None and events["entry_ts"].iloc[0].tz is None:
            events["entry_ts"] = events["entry_ts"].dt.tz_localize(bars_2h.index.tz)

        trades_for_t = []
        for _, ev in events.iterrows():
            trade = simulate_trade(
                entry_ts=ev["entry_ts"],
                direction=ev["direction"],
                bars_2h=bars_2h,
                daily_close=daily_close,
            )
            if trade is None:
                continue
            trade["ticker"] = t
            trade["stack_at_entry"] = ev["stack"]
            trade["regime_at_entry"] = ev["regime"]
            trade["sqn20_at_entry"] = ev["sqn20"]
            trade["fires_in_cluster"] = ev["fires_in_cluster"]
            trades_for_t.append(trade)
        all_trades.extend(trades_for_t)
        print(f"  {t}: {len(events)} events → {len(trades_for_t)} simulated trades")

    if not all_trades:
        print("No trades.")
        return 0

    df = pd.DataFrame(all_trades).sort_values("entry_ts").reset_index(drop=True)

    # ── Per ticker × direction summary ──
    print("\n══ Per-cohort options-trade outcomes (R-multiples) ══")
    grp_cols = ["ticker", "direction"]
    summary = df.groupby(grp_cols).agg(
        n=("R_multiple", "count"),
        win_rate=("R_multiple", lambda s: (s > 0).mean()),
        avg_R=("R_multiple", "mean"),
        median_R=("R_multiple", "median"),
        best_R=("R_multiple", "max"),
        worst_R=("R_multiple", "min"),
        target_hits=("target_hit", "sum"),
        avg_days=("days_held", "mean"),
    ).reset_index()
    for _, r in summary.iterrows():
        print(
            f"  {r['ticker']:<6} {r['direction']:<5} n={int(r['n']):<2} "
            f"WR={r['win_rate']*100:4.0f}%  "
            f"avgR={r['avg_R']:+5.2f}  medR={r['median_R']:+5.2f}  "
            f"best={r['best_R']:+5.2f}  worst={r['worst_R']:+5.2f}  "
            f"hits={int(r['target_hits'])}/{int(r['n'])}  "
            f"days={r['avg_days']:.1f}"
        )

    # ── Aggregate ──
    print(f"\n══ Aggregate ({len(df)} trades) ══")
    R = df["R_multiple"]
    win_rate = (R > 0).mean()
    print(f"  Win rate:           {win_rate*100:.1f}%")
    print(f"  Mean R:             {R.mean():+.3f}")
    print(f"  Median R:           {R.median():+.3f}")
    print(f"  Best / Worst R:     {R.max():+.2f} / {R.min():+.2f}")
    print(f"  Target hits:        {int(df['target_hit'].sum())} / {len(df)} "
          f"({df['target_hit'].mean()*100:.0f}%)")
    print(f"  Mean days held:     {df['days_held'].mean():.1f}")

    wins = R[R > 0]
    losses = R[R < 0]
    avg_win = wins.mean() if not wins.empty else 0.0
    avg_loss = losses.mean() if not losses.empty else 0.0
    pf = (wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else None
    print(f"  Avg win / loss R:   {avg_win:+.2f} / {avg_loss:+.2f}")
    if pf is not None:
        print(f"  Profit factor:      {pf:.2f}")

    print("\n══ Exit reason breakdown ══")
    for reason, n in df["exit_reason"].value_counts().items():
        r_for_reason = df.loc[df["exit_reason"] == reason, "R_multiple"]
        print(f"  {reason:<24} {n:<3}  avgR={r_for_reason.mean():+.2f}")

    if args.csv:
        out = df.copy()
        out["entry_ts"] = out["entry_ts"].dt.strftime("%Y-%m-%d %H:%M")
        out["exit_ts"] = out["exit_ts"].apply(
            lambda v: v.strftime("%Y-%m-%d %H:%M") if v is not None else None
        )
        for col in ("S_entry", "K", "P_entry", "P_max", "realized_pnl_per_contract"):
            out[col] = out[col].round(4)
        out["R_multiple"] = out["R_multiple"].round(3)
        out["sigma"] = out["sigma"].round(4)
        out["days_held"] = out["days_held"].round(2)
        out.to_csv(args.csv, index=False)
        print(f"\nWrote CSV: {args.csv}  ({len(out)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
