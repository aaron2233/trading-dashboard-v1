"""Apex options strategy backtest — proper 4H signal generator.

Companion to `apex_options_backtest.py` (which reused lotto's 2H signals).
This script generates setups on **4H bars** as apex was originally specced
("4H bar primary trigger for 14-45 DTE" per ~/CLAUDE.md trading-system).

Pipeline:
  1. Daily MA + SQN(100) + SQN(20) context (same as lotto signal generator).
  2. 4H Stochastic for the trigger TF.
  3. attach_parent_daily — most recent COMPLETED daily bar's context joins
     onto each 4H bar.
  4. Apply lotto_verdict() with the 4H stoch values fed into the h2_signal /
     h2_zone slots. The verdict logic is TF-agnostic; the param names are
     legacy from the lotto module. Gates (chop, SQN regime, Bear-Volatile,
     v2 G2/G3 cohort gates) all apply identically.
  5. Cluster 4H fires into trade events (CLUSTER_GAP_DAYS=3 trading days).
  6. Simulate apex options trade using apex_options_backtest.simulate_trade
     (28 DTE / 0.35 delta / +150% target / -45% stop / 50% DTE time stop).
  7. Walk-forward bar-by-bar on 2H bars for finer hard-stop / target
     resolution than 4H would give.

Caveats inherited from earlier backtests
----------------------------------------
- HV20+5pp as IV proxy; sigma held constant (no vega).
- No slippage / bid-ask.
- No IV-rank gate beyond what lotto v2 gates already imply.
- 4H bars from yfinance are reconstructed; bar boundaries may differ
  slightly from a live scanner's view.

Usage:
    .venv/bin/python scripts/apex_4h_backtest.py \\
        --tickers AAPL,AMD,... \\
        --start 2024-05-22 --end 2026-05-11 \\
        --csv scripts/apex_4h_backtest.csv
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from measure_lotto_signal_rate import (  # noqa: E402
    compute_daily_indicators, attach_parent_daily,
)
from indicators.stochastic import Stochastic  # noqa: E402
from data.yfinance_loader import load_bars  # noqa: E402
from scan_verdict import lotto_verdict  # noqa: E402
from lotto_signal_history import cluster, CLUSTER_GAP_DAYS  # noqa: E402
from apex_options_backtest import simulate_trade  # noqa: E402


def _normalize_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Lowercase OHLC columns to match Stochastic.compute expectations."""
    if df is None or df.empty:
        return df
    df = df.copy()
    df.columns = [c.lower() if isinstance(c, str) else c for c in df.columns]
    return df


def compute_4h_stoch(bars_4h: pd.DataFrame) -> pd.DataFrame:
    """Stoch zone + signal on 4H bars — the apex trigger TF."""
    df = _normalize_ohlc(bars_4h)
    stoch = Stochastic(length=14, smooth_k=7, smooth_d=7).compute(df)
    df["stoch_zone"] = stoch["zone"]
    df["stoch_signal"] = stoch["signal"]
    return df


def _fires_4h(ticker: str, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Generate apex setup fires on 4H bars.

    Returns (fires_df, bars_2h, daily_close). bars_2h is loaded for the
    walk-forward simulator; daily_close powers HV20 + forward-return math.
    """
    daily_raw = load_bars(ticker, period="max", interval="1d")
    bars_4h_raw = load_bars(ticker, period="2y", interval="4h")
    bars_2h_raw = load_bars(ticker, period="2y", interval="2h")
    if any(b is None or b.empty for b in (daily_raw, bars_4h_raw, bars_2h_raw)):
        return pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float)

    daily = compute_daily_indicators(daily_raw)
    bars_4h = compute_4h_stoch(bars_4h_raw)
    merged = attach_parent_daily(bars_4h, daily).dropna(
        subset=["stack_state", "sqn_regime", "stoch_zone"], how="any"
    )

    idx = merged.index
    if hasattr(idx, "tz") and idx.tz is not None:
        merged = merged.loc[
            (idx >= pd.Timestamp(start, tz=idx.tz))
            & (idx <= pd.Timestamp(end, tz=idx.tz))
        ]
    else:
        merged = merged.loc[start:end]

    close_col = "close" if "close" in merged.columns else "Close"
    daily_close_col = "close" if "close" in daily.columns else "Close"
    daily_close = daily[daily_close_col]
    daily_close.index = pd.to_datetime(daily_close.index).normalize()
    daily_close = daily_close[~daily_close.index.duplicated(keep="last")].sort_index()

    rows: list[dict] = []
    for ts, row in merged.iterrows():
        stack = None if pd.isna(row["stack_state"]) else str(row["stack_state"])
        regime = None if pd.isna(row["sqn_regime"]) else str(row["sqn_regime"])
        zone = None if pd.isna(row["stoch_zone"]) else str(row["stoch_zone"])
        sig = None if pd.isna(row["stoch_signal"]) else str(row["stoch_signal"])
        sqn20 = row.get("sqn20_value")
        sqn20_v = None if pd.isna(sqn20) else float(sqn20)

        for direction in ("long", "short"):
            # Feed 4H stoch into the lotto_verdict h2_signal/h2_zone slots —
            # the verdict logic is TF-agnostic, gates are the same.
            v = lotto_verdict(
                daily_stack=stack, sqn_100_regime=regime,
                sqn_20_value=sqn20_v, h2_signal=sig, h2_zone=zone,
                direction=direction,
            )
            if v.verdict == "buy":
                rows.append({
                    "ticker": ticker,
                    "direction": direction,
                    "timestamp": ts,
                    "entry_close": float(row[close_col]),
                    "stack": stack,
                    "regime": regime,
                    "sqn20": sqn20_v,
                    "stoch_sig": sig,
                    "stoch_zone": zone,
                })

    # Load + cache 2H bars for simulator walk-forward
    bars_2h = bars_2h_raw.copy()
    bars_2h.index = pd.to_datetime(bars_2h.index)
    bars_2h = bars_2h.sort_index()

    return pd.DataFrame(rows), bars_2h, daily_close


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
        print(f"Scanning {t} (4H)...")
        try:
            fires, bars_2h, daily_close = _fires_4h(t, args.start, args.end)
        except Exception as e:
            print(f"  {t}: load failed ({type(e).__name__}: {e}); skipping")
            continue
        if fires.empty:
            print(f"  {t}: no 4H fires")
            continue
        events = cluster(fires, daily_close, gap_days=args.gap_days)
        if bars_2h is None or bars_2h.empty:
            print(f"  {t}: no 2H walk-forward data")
            continue
        if bars_2h.index.tz is not None and events["entry_ts"].iloc[0].tz is None:
            events["entry_ts"] = events["entry_ts"].dt.tz_localize(bars_2h.index.tz)
        elif bars_2h.index.tz is None and events["entry_ts"].iloc[0].tz is not None:
            events["entry_ts"] = events["entry_ts"].dt.tz_localize(None)

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
        print(f"  {t}: {len(events)} 4H events → {len(trades_for_t)} simulated trades")

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
