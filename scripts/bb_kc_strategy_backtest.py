"""Bollinger Band + Keltner Channel strategy backtest.

Signal generation per Aaron's spec (2026-05-12):
  - Bollinger Band: SMA(20), ±2σ
  - Keltner Channel: SMA(20), ±1.5 × ATR(20)

Two distinct setup types, each with long + short variants:

  1. EXPANSION (volatility breakout)
     - Was in BB-inside-KC squeeze in the last 10 bars
     - Now: close crosses outside the BB (fresh cross above upper / below lower)
     - Trade the breakout direction (long on upper, short on lower)

  2. REVERSION (failed breakout / breakdown to midline)
     - Bar t-1: close was OUTSIDE the BB (above upper or below lower)
     - Bar t: close has come back INSIDE the band
     - Trade the reversion direction (short the failed up-break, long the
       failed down-break), targeting the BB midline (20 SMA)

Each fire is tagged with signal_type so we can split the cohort cleanly.

Exit ladder: same Black-Scholes options simulator as the lotto v2 backtest
(0.20-delta OTM, 10 DTE, −50%/+200%/trail/half-DTE-time-stop). NOTE: the
reversion trade targets the BB midline (1–3% underlying move) which is
structurally smaller than the lotto +200% premium target. We expect that
cohort to underperform on this exit ladder; reporting that honestly is the
point. A follow-up could add a price-based exit when the underlying touches
the midline.

Usage:
    PYTHONPATH=src .venv/bin/python scripts/bb_kc_strategy_backtest.py \\
      --tickers SPY,QQQ,IWM,TQQQ,SOXL,AAPL,MSFT,NVDA,TSLA,META,GOOGL,AMZN,AMD,AVGO,SMH,GLD,SLV,USO,GDX,COIN,MSTR,IONQ,PLTR,XLE,XLF \\
      --start 2024-05-12 --end 2026-05-11 \\
      --csv scripts/bb_kc_strategy_backtest_2y.csv
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
from indicators.sqz_prob import _atr, _sma, _stdev  # noqa: E402
from lotto_signal_history import cluster, CLUSTER_GAP_DAYS  # noqa: E402
from lotto_options_backtest import (  # noqa: E402
    simulate_trade,
    bs_price, select_strike, hv_at,
    ENTRY_DTE, HARD_STOP_FRAC, TARGET_GAIN_MULT, TIME_STOP_FRAC,
    IV_MARKUP, MIN_SIGMA, MAX_SIGMA,
)
import numpy as np  # noqa: E402  (already imported above; restate for clarity)


BB_LEN = 20
BB_MULT = 2.0
KC_LEN = 20
KC_MULT = 1.5
SQUEEZE_LOOKBACK = 10   # "in last N bars" window for expansion qualifier
EXPANSION_WITHIN = 5    # expansion fire must occur within N bars of release


def simulate_reversion_trade(
    entry_ts: pd.Timestamp,
    direction: str,
    bars_2h: pd.DataFrame,
    daily_close: pd.Series,
    *,
    price_target: float,   # BB midline at entry — close full position when underlying reaches this
    price_stop: float,     # original breached BB band — close full position if underlying re-breaches
) -> dict | None:
    """Run the reversion trade through an exit ladder that includes two new
    price-based exits in addition to the standard lotto premium ladder.

    Exit priority (first to trigger wins):
      1. Premium hard stop (premium ≤ 0.5 × entry)        — protective
      2. Underlying re-breach of original band             — invalidation (price stop)
      3. Underlying reaches BB midline                     — thesis complete (price target)
      4. Premium target +200% → sell half, switch to trail — unexpected windfall
      5. Trail stop on remaining half (after target hit)
      6. Time stop at half DTE
      7. Expiry guard
    """
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

    hard_stop_level = P_entry * HARD_STOP_FRAC
    target_level = P_entry * TARGET_GAIN_MULT
    R = P_entry * HARD_STOP_FRAC

    P_max = P_entry
    realized_pnl = 0.0
    remaining_size = 1.0
    half_taken = False
    exit_reason: str | None = None
    exit_ts: pd.Timestamp | None = None

    # Direction-aware price triggers
    def target_hit(S: float) -> bool:
        return (S <= price_target) if direction == "short" else (S >= price_target)

    def stop_hit(S: float) -> bool:
        return (S >= price_stop) if direction == "short" else (S <= price_stop)

    for j in range(bar_pos + 1, len(bars_2h)):
        ts = idx[j]
        S = float(bars_2h.iloc[j][close_col])
        elapsed_days = (ts - entry_ts).total_seconds() / 86400.0
        T_rem = max(T_entry - elapsed_days / 365.0, 0.0)
        P_now = bs_price(S, K, T_rem, sigma, kind)
        P_max = max(P_max, P_now)

        # 1. Premium hard stop
        if not half_taken and P_now <= hard_stop_level:
            realized_pnl += (P_now - P_entry) * remaining_size
            exit_reason, exit_ts = "hard_stop", ts
            break

        # 2. Price stop — underlying re-breached the original band
        if stop_hit(S):
            realized_pnl += (P_now - P_entry) * remaining_size
            exit_reason, exit_ts = "price_stop_band_rebreach", ts
            break

        # 3. Price target — underlying reached BB midline
        if target_hit(S):
            realized_pnl += (P_now - P_entry) * remaining_size
            exit_reason, exit_ts = "price_target_midline", ts
            break

        # 4. Premium target +200% (rare for reversion trades; still honored)
        if not half_taken and P_now >= target_level:
            realized_pnl += (P_now - P_entry) * 0.5
            remaining_size -= 0.5
            half_taken = True
            P_max = P_now
            continue

        # 5. Trail stop on remaining half
        if half_taken:
            trail_stop = 0.5 * P_max + 0.5 * P_entry
            if P_now <= trail_stop:
                realized_pnl += (P_now - P_entry) * remaining_size
                exit_reason, exit_ts = "trail_stop", ts
                break

        # 6. Time stop at half DTE
        if elapsed_days >= ENTRY_DTE * TIME_STOP_FRAC:
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


def _normalize_bars(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df.rename(columns={c: c.lower() for c in df.columns}, inplace=True)
    return df


def _compute_bb_kc(bars: pd.DataFrame) -> pd.DataFrame:
    """Compute BB and KC; return one DataFrame indexed like `bars` with
    bb_upper, bb_lower, bb_mid, kc_upper, kc_lower, squeeze_on."""
    c, h, l = bars["close"], bars["high"], bars["low"]
    bb_mid = _sma(c, BB_LEN)
    bb_std = _stdev(c, BB_LEN)
    bb_upper = bb_mid + BB_MULT * bb_std
    bb_lower = bb_mid - BB_MULT * bb_std

    kc_mid = _sma(c, KC_LEN)
    kc_atr = _atr(h, l, c, KC_LEN)
    kc_upper = kc_mid + KC_MULT * kc_atr
    kc_lower = kc_mid - KC_MULT * kc_atr

    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)

    return pd.DataFrame({
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
        "kc_upper": kc_upper,
        "kc_lower": kc_lower,
        "squeeze_on": squeeze_on.fillna(False),
    }, index=bars.index)


def _detect_fires(
    ticker: str,
    bars: pd.DataFrame,
    bands: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Generate the 4 fire types on this ticker's 2H bars."""
    c = bars["close"]
    upper = bands["bb_upper"]
    lower = bands["bb_lower"]
    mid = bands["bb_mid"]
    sqz = bands["squeeze_on"]

    # Squeeze state: was-recently-squeezed window
    sqz_recent = sqz.rolling(SQUEEZE_LOOKBACK, min_periods=1).max().fillna(0).astype(bool)
    # Squeeze just released: prior bar was on, current bar is off
    sqz_release = sqz.shift(1).fillna(False) & ~sqz.fillna(False)
    # "Released within last EXPANSION_WITHIN bars" — rolling-max of release flag
    sqz_release_recent = sqz_release.rolling(EXPANSION_WITHIN, min_periods=1).max().fillna(0).astype(bool)

    # Fresh cross above upper / below lower (close-based)
    cross_above_upper = (c > upper) & (c.shift(1) <= upper.shift(1))
    cross_below_lower = (c < lower) & (c.shift(1) >= lower.shift(1))

    # Closed outside on prior bar, closed back inside on this bar (failed break)
    prev_outside_up = c.shift(1) > upper.shift(1)
    prev_outside_down = c.shift(1) < lower.shift(1)
    now_inside_from_up = prev_outside_up & (c <= upper)
    now_inside_from_down = prev_outside_down & (c >= lower)

    # Expansion fires: squeeze released recently AND fresh BB cross today
    expansion_long = sqz_release_recent & cross_above_upper
    expansion_short = sqz_release_recent & cross_below_lower

    # Reversion fires: failed break, target midline. EXCLUDE bars that are
    # also expansion fires (those should be expansion not reversion — they're
    # the legitimate squeeze break still resolving).
    reversion_short = now_inside_from_up & ~sqz_release_recent
    reversion_long = now_inside_from_down & ~sqz_release_recent

    idx = bars.index
    rows: list[dict] = []
    for i, ts in enumerate(idx):
        ts_cmp = ts.tz_convert(start.tz) if (ts.tz is not None and start.tz is not None) else ts
        if start is not None and ts_cmp < start:
            continue
        if end is not None and ts_cmp > end:
            continue
        close_val = float(c.iloc[i])
        mid_val = float(mid.iloc[i]) if not pd.isna(mid.iloc[i]) else None
        upper_val = float(upper.iloc[i]) if not pd.isna(upper.iloc[i]) else None
        lower_val = float(lower.iloc[i]) if not pd.isna(lower.iloc[i]) else None
        common = dict(
            ticker=ticker, timestamp=ts, entry_close=close_val,
            bb_mid=mid_val, bb_upper=upper_val, bb_lower=lower_val,
            stack=None, regime=None, sqn20=None,
            stoch_sig=None, stoch_zone=None,
        )
        if expansion_long.iloc[i]:
            rows.append({**common, "direction": "long", "signal_type": "expansion"})
        if expansion_short.iloc[i]:
            rows.append({**common, "direction": "short", "signal_type": "expansion"})
        if reversion_short.iloc[i]:
            rows.append({**common, "direction": "short", "signal_type": "reversion"})
        if reversion_long.iloc[i]:
            rows.append({**common, "direction": "long", "signal_type": "reversion"})

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
    args = ap.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)

    all_trades: list[dict] = []
    for t in tickers:
        print(f"Scanning {t}...")
        loaded = _load_2h_and_daily(t)
        if loaded is None:
            print(f"  {t}: data unavailable")
            continue
        bars_2h, daily_close = loaded
        bands = _compute_bb_kc(bars_2h)

        if bars_2h.index.tz is not None and start_ts.tz is None:
            tz = bars_2h.index.tz
            start_ts_tz = start_ts.tz_localize(tz)
            end_ts_tz = end_ts.tz_localize(tz)
        else:
            start_ts_tz, end_ts_tz = start_ts, end_ts

        fires = _detect_fires(t, bars_2h, bands, start_ts_tz, end_ts_tz)
        if fires.empty:
            print(f"  {t}: 0 fires")
            continue

        # Cluster separately per signal_type so an expansion and a reversion
        # firing within 3 days don't get merged.
        clustered_pieces = []
        for stype in fires["signal_type"].unique():
            piece = fires[fires["signal_type"] == stype].copy()
            cl = cluster(piece, daily_close, gap_days=args.gap_days)
            cl["signal_type"] = stype
            # Carry the BB levels at the cluster's first fire (entry_ts).
            lookup = piece.set_index("timestamp")
            cl["bb_mid"] = lookup["bb_mid"].reindex(cl["entry_ts"]).values
            cl["bb_upper"] = lookup["bb_upper"].reindex(cl["entry_ts"]).values
            cl["bb_lower"] = lookup["bb_lower"].reindex(cl["entry_ts"]).values
            clustered_pieces.append(cl)
        events = pd.concat(clustered_pieces, ignore_index=True)

        if bars_2h.index.tz is not None and len(events) and events["entry_ts"].iloc[0].tz is None:
            events["entry_ts"] = events["entry_ts"].dt.tz_localize(bars_2h.index.tz)

        trades_for_t: list[dict] = []
        for _, ev in events.iterrows():
            if ev["signal_type"] == "reversion":
                # Reversion: target = BB midline, stop = breached band.
                # Direction-aware: short trade was triggered by close coming
                # back inside from above → stop = original BB upper (going
                # back outside up = invalidation); target = mid (below).
                # Long trade mirror: stop = original BB lower; target = mid.
                price_target = ev.get("bb_mid")
                if ev["direction"] == "short":
                    price_stop = ev.get("bb_upper")
                else:
                    price_stop = ev.get("bb_lower")
                if price_target is None or price_stop is None or pd.isna(price_target) or pd.isna(price_stop):
                    # Missing band data — fall through to standard sim
                    trade = simulate_trade(
                        entry_ts=ev["entry_ts"],
                        direction=ev["direction"],
                        bars_2h=bars_2h,
                        daily_close=daily_close,
                    )
                else:
                    trade = simulate_reversion_trade(
                        entry_ts=ev["entry_ts"],
                        direction=ev["direction"],
                        bars_2h=bars_2h,
                        daily_close=daily_close,
                        price_target=float(price_target),
                        price_stop=float(price_stop),
                    )
            else:
                # Expansion: standard lotto exit ladder, ride the breakout
                trade = simulate_trade(
                    entry_ts=ev["entry_ts"],
                    direction=ev["direction"],
                    bars_2h=bars_2h,
                    daily_close=daily_close,
                )
            if trade is None:
                continue
            trade["ticker"] = t
            trade["signal_type"] = ev["signal_type"]
            trade["bb_mid_at_entry"] = ev.get("bb_mid")
            trade["fires_in_cluster"] = ev["fires_in_cluster"]
            trades_for_t.append(trade)
        all_trades.extend(trades_for_t)
        by_type = (
            events.groupby("signal_type")["entry_ts"].count().to_dict()
        )
        print(f"  {t}: {len(fires)} fires → {len(events)} events "
              f"({by_type}) → {len(trades_for_t)} trades")

    if not all_trades:
        print("No trades.")
        return 0

    df = pd.DataFrame(all_trades).sort_values("entry_ts").reset_index(drop=True)

    # ── Split by signal_type ──
    print("\n══ Aggregate by signal_type ══")
    for stype in ("expansion", "reversion"):
        sub = df[df["signal_type"] == stype]
        if sub.empty:
            print(f"  {stype}: 0 trades"); continue
        R = sub["R_multiple"]
        wins = R[R > 0]
        losses = R[R < 0]
        pf = (wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else None
        print(
            f"  {stype:<10} n={len(sub):<3} WR={(R>0).mean()*100:4.1f}%  "
            f"meanR={R.mean():+5.2f}  medianR={R.median():+5.2f}  "
            f"avgW={wins.mean() if not wins.empty else 0:+5.2f}  "
            f"avgL={losses.mean() if not losses.empty else 0:+5.2f}  "
            f"PF={pf:.2f}" + (f"  hits={int(sub['target_hit'].sum())}/{len(sub)}" if 'target_hit' in sub else "")
        )

    # ── Direction × signal_type ──
    print("\n══ Direction × signal_type ══")
    for stype in ("expansion", "reversion"):
        for direction in ("long", "short"):
            sub = df[(df["signal_type"] == stype) & (df["direction"] == direction)]
            if sub.empty:
                continue
            R = sub["R_multiple"]
            print(f"  {stype:<10} {direction:<5} n={len(sub):<3} WR={(R>0).mean()*100:4.0f}%  meanR={R.mean():+5.2f}  hits={int(sub['target_hit'].sum())}/{len(sub)}")

    # ── Aggregate everything ──
    print(f"\n══ Aggregate (all {len(df)} trades) ══")
    R = df["R_multiple"]
    wins = R[R > 0]
    losses = R[R < 0]
    pf = (wins.sum() / abs(losses.sum())) if not losses.empty and losses.sum() != 0 else None
    print(f"  Win rate:        {(R>0).mean()*100:.1f}%")
    print(f"  Mean R:          {R.mean():+.3f}")
    print(f"  Median R:        {R.median():+.3f}")
    print(f"  Best / Worst:    {R.max():+.2f} / {R.min():+.2f}")
    print(f"  Target hits:     {int(df['target_hit'].sum())} / {len(df)} "
          f"({df['target_hit'].mean()*100:.0f}%)")
    print(f"  Avg win / loss:  {wins.mean() if not wins.empty else 0:+.2f} / "
          f"{losses.mean() if not losses.empty else 0:+.2f}")
    if pf is not None:
        print(f"  Profit factor:   {pf:.2f}")

    # ── Top tickers ──
    print("\n══ Top 10 ticker × signal_type cohorts (n≥3, by meanR) ══")
    coh = df.groupby(["ticker", "signal_type", "direction"]).agg(
        n=("R_multiple", "count"),
        WR=("R_multiple", lambda s: round((s > 0).mean() * 100, 0)),
        meanR=("R_multiple", "mean"),
        best=("R_multiple", "max"),
    ).reset_index()
    coh = coh[coh["n"] >= 3].sort_values("meanR", ascending=False).head(10)
    print(coh.to_string(index=False))
    print("\n══ Bottom 10 ticker × signal_type cohorts (n≥3, by meanR) ══")
    bottom = df.groupby(["ticker", "signal_type", "direction"]).agg(
        n=("R_multiple", "count"),
        WR=("R_multiple", lambda s: round((s > 0).mean() * 100, 0)),
        meanR=("R_multiple", "mean"),
        worst=("R_multiple", "min"),
    ).reset_index()
    bottom = bottom[bottom["n"] >= 3].sort_values("meanR").head(10)
    print(bottom.to_string(index=False))

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
        out["bb_mid_at_entry"] = out["bb_mid_at_entry"].round(4)
        out.to_csv(args.csv, index=False)
        print(f"\nWrote CSV: {args.csv}  ({len(out)} rows)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
