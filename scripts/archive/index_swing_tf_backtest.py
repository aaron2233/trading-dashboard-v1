"""Index-swing timeframe comparison backtest.

Runs the production breakout detection (5-bar swing high + confluence
checks from src/index_swing/scanner.py) on QQQ/IWM/SPY across three trigger
timeframes — daily, 4-hour, and 2-hour — and reports R-multiples so we can
see if the lower-TF triggers add or subtract edge.

Each detected breakout opens a trade with:
  - Entry: bar's close
  - Stop:  −2% of entry (per skill spec)
  - Target: +4% (2R)
  - Max hold: 60 days
  - Cooldown after exit: 3 trading days before another entry

Exit walks the SAME-TF bars to capture intra-bar stop/target hits. Trades
ranked by:
  - Win rate
  - Mean R-multiple
  - Profit factor
  - Trade count

Universe is hard-locked to the index-swing skill's allowed list
(QQQ/IWM/SPY). 2y window is the natural ceiling because yfinance caps 1h
bars (the 2h/4h source) at 730 days.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/index_swing_tf_backtest.py \\
        --start 2024-05-13 --end 2026-05-13 \\
        --csv scripts/index_swing_tf_backtest.csv
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
from index_swing.scanner import (  # noqa: E402
    detect_swing_high_breakout, DEFAULT_SWING_BARS,
)
from indicators.sqn_regime import SQNRegime, SQN_100_BANDS, SQN_20_BANDS  # noqa: E402


# ── Trade rules (per skill spec) ───────────────────────────────────────────
STOP_PCT = 0.02           # 2% stop below entry
TARGET_R = 2.0            # 2R target = +4% from entry
MAX_HOLD_DAYS = 60
COOLDOWN_TRADING_DAYS = 3

# ── Universe + TF map ──────────────────────────────────────────────────────
DEFAULT_TICKERS = ("QQQ", "IWM", "SPY")
TF_TO_INTERVAL = {"1d": "1d", "4h": "4h", "2h": "2h"}
# yfinance period caps. 1h (and resamples 2h/4h) → 730 days.
# Daily: 5y is plenty for our purposes; "max" available too if we want more.
TF_TO_PERIOD = {"1d": "5y", "4h": "2y", "2h": "2y"}

# Bear-Volatile gate thresholds (per src/index_swing/scanner.py)
SQN_20_BEAR_VOLATILE_CUTOFF = -1.9


def _normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df.rename(columns={c: c.lower() for c in df.columns}, inplace=True)
    needed = {"open", "high", "low", "close", "volume"}
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"missing OHLCV columns: {missing}")
    return df


def _simulate_trade(
    bars: pd.DataFrame, entry_idx: int, max_hold_bars: int,
) -> tuple[str, float, int, pd.Timestamp]:
    """Walk forward bar-by-bar at the trigger TF and exit on stop / target /
    max hold. Returns (exit_reason, r_multiple, bars_held, exit_ts)."""
    entry_close = float(bars["close"].iloc[entry_idx])
    stop = entry_close * (1 - STOP_PCT)
    target = entry_close * (1 + STOP_PCT * TARGET_R)

    for j in range(entry_idx + 1, min(entry_idx + 1 + max_hold_bars, len(bars))):
        bar = bars.iloc[j]
        low = float(bar["low"])
        high = float(bar["high"])
        # If both stop and target hit in the same bar, assume stop first
        # (worst-case conservative).
        if low <= stop:
            r = -1.0
            return "stop", r, j - entry_idx, bars.index[j]
        if high >= target:
            r = TARGET_R
            return "target", r, j - entry_idx, bars.index[j]

    # Max hold reached — close at last bar's close
    last_idx = min(entry_idx + max_hold_bars, len(bars) - 1)
    last_close = float(bars["close"].iloc[last_idx])
    r = (last_close - entry_close) / (entry_close * STOP_PCT)
    return "max_hold", r, last_idx - entry_idx, bars.index[last_idx]


def _compute_daily_sqn_lookup(
    daily_bars: pd.DataFrame,
) -> pd.DataFrame:
    """Return a daily-indexed DataFrame with columns sqn_100_regime,
    sqn_100_value, sqn_20_regime, sqn_20_value. Used by the Bear-Volatile
    gate to classify the regime at each entry's calendar date."""
    df = daily_bars.copy()
    sqn_100 = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(df)
    sqn_20 = SQNRegime(lookback=20, bands=SQN_20_BANDS).compute(df)
    out = pd.DataFrame({
        "sqn_100_regime": sqn_100["regime"],
        "sqn_100_value": sqn_100["sqn_value"],
        "sqn_20_regime": sqn_20["regime"],
        "sqn_20_value": sqn_20["sqn_value"],
    }, index=df.index)
    # Normalize index to date-only (no tz) for lookup against intraday bars
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out


def _is_bear_volatile(
    sqn_lookup: pd.DataFrame, entry_ts: pd.Timestamp,
) -> tuple[bool, str | None, float | None]:
    """Return (skip, sqn_100_regime, sqn_20_value) for the daily SQN regime
    on the entry's calendar date. Skip = True when:
      (a) SQN(100) = Strong Bear, OR
      (b) SQN(100) = Bear AND SQN(20) < -1.9 (capitulation extreme).
    """
    date_key = pd.Timestamp(entry_ts).tz_localize(None).normalize() if entry_ts.tz is not None else pd.Timestamp(entry_ts).normalize()
    # Use the most recent prior daily SQN (entry happens after the daily
    # close that informs the regime).
    valid_idx = sqn_lookup.index[sqn_lookup.index <= date_key]
    if len(valid_idx) == 0:
        return False, None, None
    row = sqn_lookup.loc[valid_idx[-1]]
    regime_100 = row["sqn_100_regime"]
    sqn_20_val = row["sqn_20_value"]
    sqn_20_val_f = float(sqn_20_val) if pd.notna(sqn_20_val) else None
    is_sb = regime_100 == "strong_bear"
    is_bv = (
        regime_100 == "bear"
        and sqn_20_val_f is not None
        and sqn_20_val_f < SQN_20_BEAR_VOLATILE_CUTOFF
    )
    return (is_sb or is_bv), regime_100, sqn_20_val_f


def _walk_breakouts(
    bars: pd.DataFrame,
    tf: str,
    *,
    warmup_bars: int = 60,
    max_hold_bars: int,
    cooldown_bars: int,
    sqn_lookup: pd.DataFrame | None = None,
    apply_gate: bool = False,
) -> list[dict]:
    """Walk forward through bars looking for breakouts. Each breakout opens a
    trade; we honor a post-exit cooldown so we don't re-fire on the same
    formation repeatedly. When apply_gate=True, skip entries during
    structural Bear-Volatile regimes on the daily SQN (lookup table).
    """
    trades: list[dict] = []
    next_eligible = warmup_bars
    gated_out = 0
    n = len(bars)
    for i in range(warmup_bars, n - 1):
        if i < next_eligible:
            continue
        history = bars.iloc[: i + 1]
        confluence, breakout, _blockers = detect_swing_high_breakout(
            history, swing_bars=DEFAULT_SWING_BARS,
        )
        if confluence not in ("breakout_high_conviction", "breakout_standard"):
            continue

        entry_ts = bars.index[i]
        sqn_100_regime = None
        sqn_20_val = None
        if apply_gate and sqn_lookup is not None:
            skip, sqn_100_regime, sqn_20_val = _is_bear_volatile(
                sqn_lookup, entry_ts,
            )
            if skip:
                gated_out += 1
                continue
        elif sqn_lookup is not None:
            # Tag the regime even when not gating, so we can split cohorts.
            _, sqn_100_regime, sqn_20_val = _is_bear_volatile(
                sqn_lookup, entry_ts,
            )

        exit_reason, r, bars_held, exit_ts = _simulate_trade(
            bars, i, max_hold_bars,
        )
        trades.append({
            "tf": tf,
            "entry_ts": entry_ts,
            "entry_close": float(bars["close"].iloc[i]),
            "confluence": confluence,
            "swing_high": breakout.swing_high_value if breakout else None,
            "volume_ratio": breakout.volume_ratio if breakout else None,
            "confluence_count": breakout.confluence_count if breakout else None,
            "sqn_100_regime_at_entry": sqn_100_regime,
            "sqn_20_value_at_entry": sqn_20_val,
            "exit_ts": exit_ts,
            "exit_reason": exit_reason,
            "r_multiple": r,
            "bars_held": bars_held,
        })
        next_eligible = i + bars_held + cooldown_bars
    if apply_gate:
        print(f"      gated out: {gated_out} breakouts (Bear-Volatile skip)")
    return trades


def _bars_per_day(tf: str) -> float:
    """Average trading-bars per session for the TF (used to translate
    day-based parameters to bar counts)."""
    return {"1d": 1.0, "4h": 1.625, "2h": 3.25}[tf]
    # NYSE trading day ≈ 6.5h. 6.5/4 ≈ 1.625; 6.5/2 = 3.25.


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    ap.add_argument("--tfs", default="1d,4h,2h",
                    help="comma-separated subset of {1d, 4h, 2h}")
    ap.add_argument("--start", required=True,
                    help="Start of backtest window (applies to intraday TFs).")
    ap.add_argument("--end", required=True)
    ap.add_argument("--daily-start", default=None,
                    help="Optional earlier start for the daily TF (e.g. 2020-01-01 "
                         "to grab 5+ years of daily history while leaving intraday "
                         "capped at the 730-day yfinance ceiling).")
    ap.add_argument("--apply-gate", action="store_true",
                    help="Apply daily Bear-Volatile SQN skip to all entries.")
    ap.add_argument("--csv", type=Path, default=None)
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    tfs = [t.strip() for t in args.tfs.split(",") if t.strip()]
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)
    daily_start_ts = pd.Timestamp(args.daily_start) if args.daily_start else start_ts

    # Pre-compute daily SQN lookup per ticker (cheap — one load + rolling stats).
    print("Loading daily SQN lookup per ticker...")
    sqn_by_ticker: dict[str, pd.DataFrame] = {}
    daily_bars_by_ticker: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        try:
            raw = load_bars(ticker, period="max", interval="1d")
        except Exception as exc:
            print(f"  {ticker}: daily load failed: {exc}")
            continue
        if raw is None or raw.empty:
            continue
        daily = _normalize_bars(raw)
        daily_bars_by_ticker[ticker] = daily
        sqn_by_ticker[ticker] = _compute_daily_sqn_lookup(daily)
        print(f"  {ticker}: {len(daily)} daily bars, "
              f"SQN history {sqn_by_ticker[ticker].index[0].date()} → "
              f"{sqn_by_ticker[ticker].index[-1].date()}")

    all_trades: list[dict] = []
    for tf in tfs:
        bpd = _bars_per_day(tf)
        max_hold_bars = int(MAX_HOLD_DAYS * bpd)
        cooldown_bars = int(COOLDOWN_TRADING_DAYS * bpd)
        # Daily TF gets the extended window if --daily-start was provided.
        tf_start_ts = daily_start_ts if tf == "1d" else start_ts
        for ticker in tickers:
            print(f"  scanning {ticker} @ {tf} "
                  f"(gate={'on' if args.apply_gate else 'off'})...")
            try:
                raw = load_bars(
                    ticker,
                    period=TF_TO_PERIOD[tf],
                    interval=TF_TO_INTERVAL[tf],
                )
            except Exception as exc:
                print(f"    {ticker} {tf}: load failed: {exc}")
                continue
            if raw is None or raw.empty:
                print(f"    {ticker} {tf}: no bars")
                continue
            bars = _normalize_bars(raw)

            # Date filter (preserve TF resolution)
            if bars.index.tz is not None and tf_start_ts.tz is None:
                tz = bars.index.tz
                window = bars.loc[
                    (bars.index >= tf_start_ts.tz_localize(tz))
                    & (bars.index <= end_ts.tz_localize(tz))
                ]
            else:
                window = bars.loc[tf_start_ts:end_ts]

            if len(window) < 80:
                print(f"    {ticker} {tf}: {len(window)} bars — too short to backtest")
                continue

            trades = _walk_breakouts(
                window, tf,
                warmup_bars=60,
                max_hold_bars=max_hold_bars,
                cooldown_bars=cooldown_bars,
                sqn_lookup=sqn_by_ticker.get(ticker),
                apply_gate=args.apply_gate,
            )
            for t in trades:
                t["ticker"] = ticker
            all_trades.extend(trades)
            n = len(trades)
            r = pd.Series([t["r_multiple"] for t in trades])
            if n > 0:
                wr = (r > 0).mean() * 100
                mr = r.mean()
                print(f"    {ticker} {tf}: {n} trades  WR={wr:.0f}%  meanR={mr:+.2f}")
            else:
                print(f"    {ticker} {tf}: 0 trades")

    if not all_trades:
        print("No trades."); return 0

    df = pd.DataFrame(all_trades).sort_values("entry_ts").reset_index(drop=True)

    # ── Per-TF aggregate ──
    print("\n══ Aggregate by trigger TF ══")
    print(f"{'TF':<5} {'n':>4}  {'WR':>5}  {'meanR':>7}  {'medR':>7}  "
          f"{'PF':>5}  {'tgt%':>5}  {'avgW':>6}  {'avgL':>6}  {'days':>5}")
    rows = []
    for tf in tfs:
        sub = df[df["tf"] == tf]
        if sub.empty:
            print(f"{tf:<5}    no trades")
            continue
        r = sub["r_multiple"]
        wins = r[r > 0]
        losses = r[r < 0]
        pf = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
        tgt_pct = (sub["exit_reason"] == "target").mean() * 100
        # days held: convert bars_held to days using bpd
        bpd = _bars_per_day(tf)
        days = sub["bars_held"].mean() / bpd
        print(f"{tf:<5} {len(sub):>4}  {(r>0).mean()*100:>4.0f}%  "
              f"{r.mean():>+7.2f}  {r.median():>+7.2f}  {pf:>5.2f}  "
              f"{tgt_pct:>4.0f}%  {wins.mean() if not wins.empty else 0:>+6.2f}  "
              f"{losses.mean() if not losses.empty else 0:>+6.2f}  {days:>5.1f}")
        rows.append((tf, len(sub), (r > 0).mean()*100, r.mean(), pf))

    # ── Per (TF × ticker) ──
    print("\n══ TF × ticker ══")
    print(f"{'TF':<5} {'tkr':<5} {'n':>4}  {'WR':>5}  {'meanR':>7}  {'PF':>5}")
    for tf in tfs:
        for ticker in tickers:
            sub = df[(df["tf"] == tf) & (df["ticker"] == ticker)]
            if sub.empty:
                continue
            r = sub["r_multiple"]
            wins = r[r > 0]
            losses = r[r < 0]
            pf = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
            print(f"{tf:<5} {ticker:<5} {len(sub):>4}  {(r>0).mean()*100:>4.0f}%  "
                  f"{r.mean():>+7.2f}  {pf:>5.2f}")

    # ── Confluence-level split ──
    print("\n══ TF × confluence (high-conviction vs standard) ══")
    for tf in tfs:
        for conf in ("breakout_high_conviction", "breakout_standard"):
            sub = df[(df["tf"] == tf) & (df["confluence"] == conf)]
            if sub.empty:
                continue
            r = sub["r_multiple"]
            wins = r[r > 0]
            losses = r[r < 0]
            pf = (wins.sum() / abs(losses.sum())) if losses.sum() != 0 else float("inf")
            label = "high_conviction" if conf == "breakout_high_conviction" else "standard       "
            print(f"  {tf:<5} {label}  n={len(sub):>3}  WR={(r>0).mean()*100:>4.0f}%  "
                  f"meanR={r.mean():>+5.2f}  PF={pf:>4.2f}")

    if args.csv:
        out = df.copy()
        out["entry_ts"] = pd.to_datetime(out["entry_ts"]).dt.strftime("%Y-%m-%d %H:%M")
        out["exit_ts"] = pd.to_datetime(out["exit_ts"]).dt.strftime("%Y-%m-%d %H:%M")
        out["r_multiple"] = out["r_multiple"].round(3)
        out.to_csv(args.csv, index=False)
        print(f"\nWrote CSV: {args.csv}  ({len(out)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
