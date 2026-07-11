"""Apex options strategy backtest — intermediate-DTE single-stock options.

Adapted from `lotto_options_backtest.py`. Same Black-Scholes lifecycle and
HV20+IV-markup volatility model, but the **options structure** swaps from
lotto (10 DTE / 0.20 delta / +200%/-50%/50%DTE) to apex (28 DTE / 0.35 delta
/ +150%/-45%/50%DTE).

Apex was a Tier 4 skill retired 2026-05-10. Spec preserved in
`~/Documents/App Development/trading_pipeline/config/settings.py`:
  - DTE: 14-45 (21-35 for momentum_stock)
  - Delta: 0.20-0.60 by conviction; medium = 0.35-0.45
  - OTM target: +100-300% gain; ATM target +50-100%; ITM +30-50%
  - Stop: -40-50% loss
  - Time stop: 50% of DTE
  - MAX_STOCK_PRICE = $100, MAX_RISK_DOLLARS = $300

For this first-pass we use medium-conviction momentum_stock defaults:
ENTRY_DTE=28, TARGET_DELTA=0.35, STOP_LOSS_FRAC=0.45,
TARGET_GAIN_FRAC=1.50 (sell half at +150%, trail rest), TIME_STOP_FRAC=0.50.

──────────────────────────────────────────────────────────────────────────
SIMPLIFICATION FLAG (read before trusting numbers)
──────────────────────────────────────────────────────────────────────────
Apex was specced with a 4H trigger ("4H bar primary trigger for 14-45 DTE").
The existing signal-fire infrastructure (`lotto_signal_history._fires`)
generates 2H trigger events using lotto's MA+Stoch+SQN+v2 gates. Building
a proper 4H apex signal generator from scratch is a separate piece of work
and not necessary to answer the strategic question.

So this backtest answers: "if you took the same setup signals as lotto but
swapped the options structure to apex's intermediate-DTE / higher-delta
profile, would the strategy be net positive?" — which is what matters for
the "is apex worth bringing back" decision. The signal-generator question
becomes a follow-up if this first-pass shows promise.

──────────────────────────────────────────────────────────────────────────
Other caveats inherited from the lotto backtest
──────────────────────────────────────────────────────────────────────────
- HV20 ≠ true IV. Constant sigma ignores vega; +150% target is harder to
  hit during vol expansion regimes and easier in crush.
- No slippage / bid-ask. Real fills are 5-10% wide on the 0.35-delta band
  but tighter than 0.20-delta lottos.
- No IV-rank gate. Apex spec calls for IVR < 70% on entry; this is
  inherited indirectly via the lotto v2 gates but not enforced explicitly.
- Risk-free rate fixed at 4%.

Usage:
    .venv/bin/python scripts/apex_options_backtest.py \\
        --tickers AAPL,AMD,AMZN,... \\
        --start 2024-05-22 --end 2026-05-11 \\
        --csv scripts/apex_options_backtest.csv
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


# ─── Apex strategy parameters (medium conviction, momentum_stock) ──────────

ENTRY_DTE = 28              # mid of 21-35 momentum_stock band
TARGET_DELTA = 0.35         # mid of 0.35-0.45 medium-conviction band
STOP_LOSS_FRAC = 0.45       # cut at -45% premium
TARGET_GAIN_FRAC = 1.50     # sell half at +150% (premium = 2.5 × entry)
TIME_STOP_FRAC = 0.50       # exit at 14 of 28 days if still open
RISK_FREE = 0.04
HV_LOOKBACK = 20
IV_MARKUP = 0.05
MIN_SIGMA = 0.15
MAX_SIGMA = 1.50

# z such that N(z) = 1 - TARGET_DELTA = 1 - 0.35 = 0.65  →  z ≈ 0.3853
# (For 0.35 delta we want d1 such that N(d1) = 0.35 for calls, N(-d1) = 0.35 for puts.)
# Equivalently z = N⁻¹(0.65) for the symmetric strike-from-delta formula below.
from math import sqrt as _sqrt  # noqa: E402

# Approximate N⁻¹(0.65) using Beasley-Springer/Moro is overkill — use exact
# value from scipy.stats.norm.ppf(0.65) = 0.385320...
Z_TARGET_DELTA = 0.38532

HARD_STOP_LEVEL_FRAC = 1.0 - STOP_LOSS_FRAC   # premium threshold for cut
TARGET_LEVEL_MULT = 1.0 + TARGET_GAIN_FRAC    # premium threshold for target


# ─── Black-Scholes (unchanged from lotto backtest) ─────────────────────────


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
    """Strike targeting ~TARGET_DELTA via the BS delta formula.

    For 0.35 delta calls: K = S × exp(+|z|σ√T + (r+σ²/2)T)
    For 0.35 delta puts:  K = S × exp(-|z|σ√T + (r+σ²/2)T)
    """
    drift = (r + 0.5 * sigma * sigma) * T
    vol_term = Z_TARGET_DELTA * sigma * sqrt(T)
    if kind == "call":
        return S * exp(vol_term + drift)
    return S * exp(-vol_term + drift)


# ─── Volatility estimate (unchanged) ───────────────────────────────────────


def hv_at(daily_close: pd.Series, entry_date: pd.Timestamp,
          lookback: int = HV_LOOKBACK) -> float | None:
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


# ─── Trade simulator — apex exit ladder ────────────────────────────────────


def simulate_trade(
    entry_ts: pd.Timestamp,
    direction: str,
    bars_2h: pd.DataFrame,
    daily_close: pd.Series,
) -> dict | None:
    """Run one apex options trade through the exit ladder."""
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
    T_entry = ENTRY_DTE / 365.0

    K = select_strike(S_entry, sigma, T_entry, kind)
    P_entry = bs_price(S_entry, K, T_entry, sigma, kind)
    if P_entry <= 0.01:
        return None

    hard_stop_level = P_entry * HARD_STOP_LEVEL_FRAC
    target_level = P_entry * TARGET_LEVEL_MULT
    R = P_entry * STOP_LOSS_FRAC  # max loss per contract = 45% of premium

    P_max = P_entry
    realized_pnl = 0.0
    remaining_size = 1.0
    half_taken = False
    exit_reason: str | None = None
    exit_ts: pd.Timestamp | None = None
    bars_held = 0

    for j in range(bar_pos + 1, len(bars_2h)):
        ts = idx[j]
        S = float(bars_2h.iloc[j][close_col])
        elapsed_days = (ts - entry_ts).total_seconds() / 86400.0
        T_rem = max(T_entry - elapsed_days / 365.0, 0.0)
        bars_held = j - bar_pos

        P_now = bs_price(S, K, T_rem, sigma, kind)
        P_max = max(P_max, P_now)

        if not half_taken and P_now <= hard_stop_level:
            realized_pnl += (P_now - P_entry) * remaining_size
            remaining_size = 0.0
            exit_reason = "hard_stop"
            exit_ts = ts
            break

        if not half_taken and P_now >= target_level:
            realized_pnl += (P_now - P_entry) * 0.5
            remaining_size -= 0.5
            half_taken = True
            P_max = P_now
            continue

        if half_taken:
            trail_stop = 0.5 * P_max + 0.5 * P_entry
            if P_now <= trail_stop:
                realized_pnl += (P_now - P_entry) * remaining_size
                remaining_size = 0.0
                exit_reason = "trail_stop"
                exit_ts = ts
                break

        if elapsed_days >= ENTRY_DTE * TIME_STOP_FRAC:
            realized_pnl += (P_now - P_entry) * remaining_size
            remaining_size = 0.0
            exit_reason = "time_stop_half_dte" if not half_taken else "time_stop_after_target"
            exit_ts = ts
            break

        if T_rem <= 0:
            realized_pnl += (P_now - P_entry) * remaining_size
            remaining_size = 0.0
            exit_reason = "expiry"
            exit_ts = ts
            break

    if exit_reason is None:
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


# ─── Main pipeline (parallel to lotto_options_backtest) ────────────────────


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

    print("\n══ Per-cohort outcomes (R-multiples) ══")
    summary = df.groupby(["ticker", "direction"]).agg(
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
