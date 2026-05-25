"""Head-to-head backtest: SQZ PROB v3 as the lotto entry trigger vs. the
current MA/Stoch/SQN stack.

Reuses the exact same exit ladder (Black-Scholes options sim) as
`scripts/lotto_options_backtest.py`, so the only thing different is the
entry signal. This isolates the indicator question from every other
variable in the strategy.

Entry rule (same as the indicator's HIGH alert in Pine):
  - LONG  fire = bull_composite crosses from <60 to >=60
  - SHORT fire = bear_composite crosses from <60 to >=60

Same clustering window as lotto: 3 trading days. Same DTE, delta band, stops,
target, trail, time stop. Same 25-ticker / 2y universe.

VIX is included via yfinance `^VIX` daily, forward-filled onto each ticker's
2H index. Wrapper-ETF flagging is on for GLD/SLV/USO (the only 3 in our
universe per `WRAPPER_ETFS`). Short-interest inputs are left empty (Pine
defaults; weights renormalize over the remaining 5 components).

Usage:
    PYTHONPATH=src .venv/bin/python scripts/sqz_prob_options_backtest.py \\
      --tickers SPY,QQQ,IWM,TQQQ,SOXL,AAPL,MSFT,NVDA,TSLA,META,GOOGL,AMZN,AMD,AVGO,SMH,GLD,SLV,USO,GDX,COIN,MSTR,IONQ,PLTR,XLE,XLF \\
      --start 2024-05-12 --end 2026-05-11 \\
      --csv scripts/sqz_prob_options_backtest_2y.csv
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
warnings.filterwarnings("ignore", category=RuntimeWarning)

from data.yfinance_loader import load_bars  # noqa: E402
from _sqz_prob_lib import compute_sqz_prob_v3, WRAPPER_ETFS  # noqa: E402
from lotto_signal_history import cluster, CLUSTER_GAP_DAYS  # noqa: E402
from lotto_options_backtest import simulate_trade  # noqa: E402


HIGH_THRESHOLD = 60.0  # Pine alert: "Bullish Probability HIGH (60+)"
EXTREME_THRESHOLD = 80.0  # Pine alert: "Bullish Probability EXTREME (80+)"


def _normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    """Lowercase OHLCV columns and ensure timestamp index."""
    df = raw.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    rename = {c: c.lower() for c in df.columns}
    df.rename(columns=rename, inplace=True)
    return df


def _load_vix() -> pd.Series:
    """Load daily VIX close. Falls back to empty if unavailable."""
    try:
        vix_raw = load_bars("^VIX", period="3y", interval="1d")
        if vix_raw is None or vix_raw.empty:
            return pd.Series(dtype=float)
        vix = vix_raw.copy()
        if not isinstance(vix.index, pd.DatetimeIndex):
            vix.index = pd.to_datetime(vix.index)
        vix.index = vix.index.tz_localize(None)
        close = vix["close"] if "close" in vix.columns else vix["Close"]
        return close.astype(float).sort_index()
    except Exception as exc:
        print(f"  (VIX load failed: {exc}; running without VIX filter)")
        return pd.Series(dtype=float)


def _fires_for_ticker(
    ticker: str,
    bars_2h: pd.DataFrame,
    vix_daily: pd.Series,
    daily_close: pd.Series,
    start: pd.Timestamp,
    end: pd.Timestamp,
    threshold: float = HIGH_THRESHOLD,
) -> pd.DataFrame:
    """Compute SQZ PROB v3 on 2H bars and emit fire rows for the date window."""
    # Reindex VIX onto the 2H bar timestamps (after tz-stripping for fill).
    if vix_daily.empty:
        vix_aligned = None
    else:
        # Convert 2H index to a naive daily key for ffill
        idx_naive = bars_2h.index.tz_localize(None) if bars_2h.index.tz is not None else bars_2h.index
        date_key = idx_naive.normalize()
        vix_lookup = vix_daily.reindex(
            pd.Index(date_key).unique(), method="ffill"
        )
        # Map each bar to its daily VIX value
        vix_aligned = pd.Series(
            vix_lookup.reindex(date_key).values, index=bars_2h.index
        )

    sqz = compute_sqz_prob_v3(
        bars_2h,
        ticker=ticker,
        vix_close=vix_aligned,
    )

    bull = sqz["bull_composite"]
    bear = sqz["bear_composite"]
    # Fresh cross above threshold (not staying-above)
    bull_cross = (bull >= threshold) & (bull.shift(1) < threshold)
    bear_cross = (bear >= threshold) & (bear.shift(1) < threshold)

    close_col = "close" if "close" in bars_2h.columns else "Close"

    rows: list[dict] = []
    idx = bars_2h.index
    for ts, is_long, is_short in zip(idx, bull_cross.values, bear_cross.values):
        ts_naive_compare = ts.tz_convert(start.tz) if (ts.tz is not None and start.tz is not None) else ts
        if start is not None and ts_naive_compare < start:
            continue
        if end is not None and ts_naive_compare > end:
            continue
        if is_long:
            rows.append({
                "ticker": ticker, "direction": "long", "timestamp": ts,
                "entry_close": float(bars_2h.loc[ts, close_col]),
                "bull_composite": float(bull.loc[ts]),
                "bear_composite": float(bear.loc[ts]),
                "stack": None, "regime": None, "sqn20": None,
                "stoch_sig": None, "stoch_zone": None,
            })
        if is_short:
            rows.append({
                "ticker": ticker, "direction": "short", "timestamp": ts,
                "entry_close": float(bars_2h.loc[ts, close_col]),
                "bull_composite": float(bull.loc[ts]),
                "bear_composite": float(bear.loc[ts]),
                "stack": None, "regime": None, "sqn20": None,
                "stoch_sig": None, "stoch_zone": None,
            })

    return pd.DataFrame(rows)


def _load_2h_and_daily(ticker: str) -> tuple[pd.DataFrame, pd.Series] | None:
    raw_2h = load_bars(ticker, period="2y", interval="2h")
    raw_d = load_bars(ticker, period="max", interval="1d")
    if raw_2h is None or raw_2h.empty or raw_d is None or raw_d.empty:
        return None
    bars_2h = _normalize_bars(raw_2h)
    daily = _normalize_bars(raw_d)
    daily_close = daily["close"]
    daily_close.index = pd.to_datetime(daily_close.index).tz_localize(None).normalize()
    daily_close = daily_close[~daily_close.index.duplicated(keep="last")].sort_index()
    return bars_2h, daily_close


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--gap-days", type=int, default=CLUSTER_GAP_DAYS)
    ap.add_argument(
        "--threshold", type=float, default=HIGH_THRESHOLD,
        help=f"Composite-score cross level. HIGH={HIGH_THRESHOLD}, EXTREME={EXTREME_THRESHOLD}.",
    )
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    print(f"Loading VIX (^VIX daily)...")
    vix_daily = _load_vix()
    print(f"  VIX: {len(vix_daily)} daily bars, "
          f"latest {vix_daily.index[-1].date() if len(vix_daily) else 'n/a'}")

    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)

    all_trades: list[dict] = []
    for t in tickers:
        wrapper_tag = " (wrapper)" if t in WRAPPER_ETFS else ""
        print(f"Scanning {t}{wrapper_tag}...")
        loaded = _load_2h_and_daily(t)
        if loaded is None:
            print(f"  {t}: data unavailable")
            continue
        bars_2h, daily_close = loaded

        # Align tz of start/end with bar tz
        if bars_2h.index.tz is not None and start_ts.tz is None:
            tz = bars_2h.index.tz
            start_ts_tz = start_ts.tz_localize(tz)
            end_ts_tz = end_ts.tz_localize(tz)
        else:
            start_ts_tz, end_ts_tz = start_ts, end_ts

        fires = _fires_for_ticker(
            t, bars_2h, vix_daily, daily_close, start_ts_tz, end_ts_tz,
            threshold=args.threshold,
        )
        if fires.empty:
            print(f"  {t}: 0 fires")
            continue

        events = cluster(fires, daily_close, gap_days=args.gap_days)

        # Align tz of entry_ts with bars_2h
        if bars_2h.index.tz is not None and events["entry_ts"].iloc[0].tz is None:
            events["entry_ts"] = events["entry_ts"].dt.tz_localize(bars_2h.index.tz)

        trades_for_t: list[dict] = []
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
            trade["fires_in_cluster"] = ev["fires_in_cluster"]
            trades_for_t.append(trade)
        all_trades.extend(trades_for_t)
        print(f"  {t}: {len(fires)} fires → {len(events)} events → {len(trades_for_t)} trades")

    if not all_trades:
        print("No trades.")
        return 0

    df = pd.DataFrame(all_trades).sort_values("entry_ts").reset_index(drop=True)

    # ── Cohort summary ──
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

    # ── Aggregate ──
    print(f"\n══ Aggregate ({len(df)} trades) ══")
    R = df["R_multiple"]
    print(f"  Win rate:           {(R > 0).mean()*100:.1f}%")
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
        print(f"  {reason:<24} {int(n):<3}  avgR={r_for_reason.mean():+.2f}")

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
