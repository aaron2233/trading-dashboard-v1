"""A/B/C backtest: production rolling-extreme "divergence" vs real pivot-based
divergence in the lotto strategy.

Context
-------
The production Stochastic indicator (src/indicators/stochastic.py) labels
bullish_divergence / bearish_divergence when the CLOSE is at its own 14-bar
rolling extreme while D is not at its 14-bar extreme. A QC audit found this is
a trend-persistence signal (fires on 42-64% of bars in sustained trends), not
divergence. These labels are full-strength BUY triggers in lotto_verdict.

Arms
----
  A  (baseline)   : production signals as-is.
  B  (pivot, N=3) : rolling-extreme divergence replaced by pivot-based
                    divergence (confirmed N bars after the pivot; entry at
                    the confirmation bar's close — no look-ahead). Crosses /
                    continuations unchanged.
  B5 (pivot, N=5) : same as B with pivot width N=5 (robustness check).
  C  (no-div)     : divergence labels removed entirely; only crosses /
                    continuations trade. Isolates what divergence adds.

Everything else is identical across arms: same tickers, same data (loaded
once per ticker), same lotto_verdict gates, same clustering, same option sim
(10 DTE 0.20-delta BS-priced on HV20+5pp, 0.5x hard stop, 3x target sell-half
+ trail, half-DTE time stop) from scripts/lotto_options_backtest.py.

Pivot-divergence spec (Arm B)
-----------------------------
  - Pivot low at bar i: LOW[i] strictly below the lows of the N bars on each
    side. Pivot high mirrored on HIGH. Confirmed at bar i+N.
  - Bullish divergence: two consecutive pivot lows i1 < i2 with i2-i1 <= 30
    bars, price lower low (LOW[i2] < LOW[i1]) but Stochastic D higher low
    (D[i2] > D[i1]). D is used (matches production's divergence line choice).
    Signal fires at bar i2+N (the confirmation bar). Bearish mirrored.
  - Production priority preserved: a K/D cross on the fire bar suppresses the
    divergence label (cross > divergence), exactly as in stochastic.py.

Usage (from repo root):
    .venv/bin/python scripts/divergence_pivot_backtest.py \
        [--tickers AAPL,NVDA,...] [--csv-prefix scripts/divergence_backtest]
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
sys.path.insert(0, str(REPO / "scripts"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from measure_lotto_signal_rate import (  # noqa: E402
    compute_daily_indicators, attach_parent_daily, _normalize_ohlc,
)
from lotto_signal_history import cluster, CLUSTER_GAP_DAYS  # noqa: E402
from lotto_options_backtest import simulate_trade  # noqa: E402
from data.yfinance_loader import load_bars  # noqa: E402
from indicators.stochastic import Stochastic  # noqa: E402
from scan_verdict import lotto_verdict  # noqa: E402
from lotto import LOTTO_HIGH_VOL_WATCHLIST  # noqa: E402


PIVOT_LOOKBACK_BARS = 30      # max bar gap between the two pivots
DIV_LABELS = {"bullish_divergence", "bearish_divergence"}
ALL_TRIGGER_LABELS = {
    "bull_cross_oversold", "bull_continuation", "bullish_divergence",
    "bear_cross_overbought", "bear_continuation", "bearish_divergence",
}


# ─── Pivot-based divergence ─────────────────────────────────────────────────


def _pivot_indices(vals: np.ndarray, n: int, kind: str) -> list[int]:
    """Bar indices whose value is a strict local extreme vs N bars each side."""
    out: list[int] = []
    for i in range(n, len(vals) - n):
        left = vals[i - n : i]
        right = vals[i + 1 : i + n + 1]
        c = vals[i]
        if np.isnan(c) or np.isnan(left).any() or np.isnan(right).any():
            continue
        if kind == "low":
            if c < left.min() and c < right.min():
                out.append(i)
        else:
            if c > left.max() and c > right.max():
                out.append(i)
    return out


def pivot_divergence_masks(
    ohlc: pd.DataFrame, d_line: pd.Series, n: int,
    lookback: int = PIVOT_LOOKBACK_BARS,
) -> tuple[np.ndarray, np.ndarray]:
    """(bullish_mask, bearish_mask) — True at the CONFIRMATION bar (pivot+N).

    No look-ahead: pivot at i needs bars i-n..i+n, so it is knowable exactly
    at bar i+n, where the mask is set.
    """
    lows = ohlc["low"].to_numpy(dtype=float)
    highs = ohlc["high"].to_numpy(dtype=float)
    d = d_line.to_numpy(dtype=float)
    L = len(lows)
    bull = np.zeros(L, dtype=bool)
    bear = np.zeros(L, dtype=bool)

    plows = _pivot_indices(lows, n, "low")
    for j in range(1, len(plows)):
        i1, i2 = plows[j - 1], plows[j]
        if i2 - i1 > lookback:
            continue
        if np.isnan(d[i1]) or np.isnan(d[i2]):
            continue
        if lows[i2] < lows[i1] and d[i2] > d[i1]:
            conf = i2 + n
            if conf < L:
                bull[conf] = True

    phighs = _pivot_indices(highs, n, "high")
    for j in range(1, len(phighs)):
        i1, i2 = phighs[j - 1], phighs[j]
        if i2 - i1 > lookback:
            continue
        if np.isnan(d[i1]) or np.isnan(d[i2]):
            continue
        if highs[i2] > highs[i1] and d[i2] < d[i1]:
            conf = i2 + n
            if conf < L:
                bear[conf] = True

    return bull, bear


# ─── Per-ticker signal construction ─────────────────────────────────────────


def build_ticker_frames(ticker: str, pivot_ns: tuple[int, ...]) -> dict | None:
    """Load data ONCE, compute production stoch + all arm signal columns,
    attach parent-daily filters. Returns dict with 'merged', 'ohlc2h',
    'daily_close', 'label_counts' or None on load failure."""
    daily_raw = load_bars(ticker, period="max", interval="1d")
    bars_2h_raw = load_bars(ticker, period="2y", interval="2h")
    if daily_raw is None or daily_raw.empty or bars_2h_raw is None or bars_2h_raw.empty:
        return None

    daily = compute_daily_indicators(daily_raw)
    ohlc2h = _normalize_ohlc(bars_2h_raw)
    stoch = Stochastic(length=14, smooth_k=7, smooth_d=7).compute(ohlc2h)

    k, d = stoch["k"], stoch["d"]
    k_prev, d_prev = k.shift(1), d.shift(1)
    bull_cross = (k_prev <= d_prev) & (k > d)
    bear_cross = (k_prev >= d_prev) & (k < d)

    sig_a = stoch["signal"]
    # Arm C: strip rolling-extreme divergence labels → neutral
    sig_c = sig_a.mask(sig_a.isin(DIV_LABELS), "neutral")

    frame = ohlc2h.copy()
    frame["stoch_zone"] = stoch["zone"]
    frame["sig_a"] = sig_a
    frame["sig_c"] = sig_c

    label_counts: dict[str, dict[str, int]] = {
        "a": sig_a[sig_a.isin(DIV_LABELS)].value_counts().to_dict(),
    }

    for n in pivot_ns:
        bull_m, bear_m = pivot_divergence_masks(ohlc2h, d, n)
        sig_b = sig_c.copy()
        # Production priority: cross bars suppress divergence; only bars that
        # otherwise carry no label can take the divergence label. NA stays NA.
        can = sig_c.eq("neutral") & ~bull_cross.fillna(False) & ~bear_cross.fillna(False)
        sig_b = sig_b.mask(can & pd.Series(bull_m, index=sig_b.index), "bullish_divergence")
        sig_b = sig_b.mask(can & pd.Series(bear_m, index=sig_b.index), "bearish_divergence")
        col = f"sig_b{n}"
        frame[col] = sig_b
        label_counts[f"b{n}"] = sig_b[sig_b.isin(DIV_LABELS)].value_counts().to_dict()

    merged = attach_parent_daily(frame, daily).dropna(
        subset=["stack_state", "sqn_regime", "stoch_zone"], how="any"
    )

    close_col = "close" if "close" in daily.columns else "Close"
    daily_close = daily[close_col].copy()
    daily_close.index = pd.to_datetime(daily_close.index).normalize()
    daily_close = daily_close[~daily_close.index.duplicated(keep="last")].sort_index()

    return {
        "merged": merged,
        "ohlc2h": ohlc2h,
        "daily_close": daily_close,
        "label_counts": label_counts,
    }


# ─── Fires + trades per arm ─────────────────────────────────────────────────


def scan_fires(merged: pd.DataFrame, sig_col: str, ticker: str) -> pd.DataFrame:
    """Every 2H bar where production lotto_verdict()=='buy' with this arm's
    signal column substituted for h2_signal. Trigger label recorded per fire
    (no post-hoc guessing)."""
    sub = merged[merged[sig_col].isin(ALL_TRIGGER_LABELS)]
    rows: list[dict] = []
    for ts, row in sub.iterrows():
        stack = None if pd.isna(row["stack_state"]) else str(row["stack_state"])
        regime = None if pd.isna(row["sqn_regime"]) else str(row["sqn_regime"])
        zone = None if pd.isna(row["stoch_zone"]) else str(row["stoch_zone"])
        sig = str(row[sig_col])
        sqn20 = row.get("sqn20_value")
        sqn20_v = None if pd.isna(sqn20) else float(sqn20)
        for direction in ("long", "short"):
            v = lotto_verdict(
                daily_stack=stack, sqn_100_regime=regime, sqn_20_value=sqn20_v,
                h2_signal=sig, h2_zone=zone, direction=direction,
            )
            if v.verdict == "buy":
                rows.append({
                    "ticker": ticker, "direction": direction, "timestamp": ts,
                    "entry_close": float(row["close"]), "stack": stack,
                    "regime": regime, "sqn20": sqn20_v,
                    "stoch_sig": sig, "stoch_zone": zone,
                })
    return pd.DataFrame(rows)


def run_arm_for_ticker(frames: dict, sig_col: str, ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (fires_df, trades_df) for one ticker under one arm."""
    fires = scan_fires(frames["merged"], sig_col, ticker)
    if fires.empty:
        return fires, pd.DataFrame()
    events = cluster(fires, frames["daily_close"], gap_days=CLUSTER_GAP_DAYS)
    trades: list[dict] = []
    for _, ev in events.iterrows():
        tr = simulate_trade(
            entry_ts=ev["entry_ts"], direction=ev["direction"],
            bars_2h=frames["ohlc2h"], daily_close=frames["daily_close"],
        )
        if tr is None:
            continue
        tr["ticker"] = ticker
        tr["trigger"] = ev["stoch_sig"]       # entry-fire label — explicit, not guessed
        tr["stack_at_entry"] = ev["stack"]
        tr["regime_at_entry"] = ev["regime"]
        tr["sqn20_at_entry"] = ev["sqn20"]
        tr["fires_in_cluster"] = ev["fires_in_cluster"]
        trades.append(tr)
    return fires, pd.DataFrame(trades)


# ─── Stats ──────────────────────────────────────────────────────────────────


def stats(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "wr": None, "avg_r": None, "med_r": None, "pf": None}
    r = df["R_multiple"]
    wins, losses = r[r > 0], r[r < 0]
    pf = (wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else None
    return {
        "n": len(r),
        "wr": (r > 0).mean(),
        "avg_r": r.mean(),
        "med_r": r.median(),
        "pf": pf,
    }


def fmt_stats(s: dict) -> str:
    if s["n"] == 0:
        return "n=0     —"
    pf = f"{s['pf']:.2f}" if s["pf"] is not None else "inf"
    return (f"n={s['n']:<4d} WR={s['wr']*100:5.1f}%  avgR={s['avg_r']:+.3f}  "
            f"medR={s['med_r']:+.3f}  PF={pf}")


# ─── Main ───────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(LOTTO_HIGH_VOL_WATCHLIST))
    ap.add_argument("--csv-prefix", default="scripts/divergence_backtest")
    ap.add_argument("--pivot-ns", default="3,5")
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    pivot_ns = tuple(int(x) for x in args.pivot_ns.split(","))

    arms = {"a": "sig_a"}
    for n in pivot_ns:
        arms[f"b{n}" if n != pivot_ns[0] else "b"] = f"sig_b{n}"
    arms["c"] = "sig_c"

    all_fires: dict[str, list[pd.DataFrame]] = {k: [] for k in arms}
    all_trades: dict[str, list[pd.DataFrame]] = {k: [] for k in arms}
    div_label_bar_counts: dict[str, int] = {}
    skipped: list[str] = []
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []

    for t in tickers:
        print(f"[{t}] loading + building signals...", flush=True)
        try:
            frames = build_ticker_frames(t, pivot_ns)
        except Exception as e:
            print(f"  {t}: load failed ({type(e).__name__}: {e}) — skipped")
            skipped.append(t)
            continue
        if frames is None or frames["merged"].empty:
            print(f"  {t}: no usable data — skipped")
            skipped.append(t)
            continue

        m = frames["merged"]
        windows.append((m.index[0], m.index[-1]))
        for key, counts in frames["label_counts"].items():
            div_label_bar_counts[key] = div_label_bar_counts.get(key, 0) + sum(counts.values())

        for arm, col in arms.items():
            fires, trades = run_arm_for_ticker(frames, col, t)
            if not fires.empty:
                all_fires[arm].append(fires)
            if not trades.empty:
                all_trades[arm].append(trades)
        n_a = len(all_trades["a"][-1]) if all_trades["a"] and all_trades["a"][-1]["ticker"].iloc[0] == t else 0
        print(f"  {t}: bars={len(m)}  armA_trades={n_a}", flush=True)

    fires_df = {k: (pd.concat(v, ignore_index=True) if v else pd.DataFrame()) for k, v in all_fires.items()}
    trades_df = {k: (pd.concat(v, ignore_index=True).sort_values("entry_ts").reset_index(drop=True)
                     if v else pd.DataFrame()) for k, v in all_trades.items()}

    # ── CSVs ──
    for arm, df in trades_df.items():
        if df.empty:
            continue
        out = df.copy()
        out["entry_ts"] = out["entry_ts"].apply(lambda v: v.strftime("%Y-%m-%d %H:%M"))
        out["exit_ts"] = out["exit_ts"].apply(
            lambda v: v.strftime("%Y-%m-%d %H:%M") if v is not None else None)
        path = Path(f"{args.csv_prefix}_{arm}.csv")
        out.to_csv(path, index=False)
        print(f"Wrote {path} ({len(out)} rows)")

    # ── Report ──
    if windows:
        w_start = min(w[0] for w in windows)
        w_end = max(w[1] for w in windows)
        print(f"\nWindow (union across tickers): {w_start} → {w_end}")
    print(f"Tickers requested: {len(tickers)}  used: {len(tickers) - len(skipped)}  "
          f"skipped: {skipped if skipped else 'none'}")

    print("\n══ Divergence-labeled 2H bars (pre-gate signal counts) ══")
    for key, n in div_label_bar_counts.items():
        print(f"  arm {key:<3}: {n} bars labeled divergence")

    print("\n══ Raw BUY fires by trigger label (pre-clustering) ══")
    for arm, df in fires_df.items():
        print(f"  Arm {arm.upper()}: total fires={len(df)}")
        if not df.empty:
            for label, n in df["stoch_sig"].value_counts().items():
                print(f"    {label:<24} {n}")

    print("\n══ Trade outcomes per arm (all trades) ══")
    for arm, df in trades_df.items():
        print(f"  Arm {arm.upper():<3} {fmt_stats(stats(df))}")

    print("\n══ Per-direction ══")
    for arm, df in trades_df.items():
        for direction in ("long", "short"):
            sub = df[df["direction"] == direction] if not df.empty else pd.DataFrame()
            print(f"  Arm {arm.upper():<3} {direction:<5} {fmt_stats(stats(sub))}")

    print("\n══ Divergence-triggered cohort only ══")
    for arm, df in trades_df.items():
        sub = df[df["trigger"].isin(DIV_LABELS)] if not df.empty else pd.DataFrame()
        print(f"  Arm {arm.upper():<3} {fmt_stats(stats(sub))}")
        if not sub.empty:
            for direction in ("long", "short"):
                dsub = sub[sub["direction"] == direction]
                print(f"      {direction:<5} {fmt_stats(stats(dsub))}")

    print("\n══ Non-divergence cohort (crosses/continuations — should match across arms ~C) ══")
    for arm, df in trades_df.items():
        sub = df[~df["trigger"].isin(DIV_LABELS)] if not df.empty else pd.DataFrame()
        print(f"  Arm {arm.upper():<3} {fmt_stats(stats(sub))}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
