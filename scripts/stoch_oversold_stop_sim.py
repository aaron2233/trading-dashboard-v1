"""Path-dependent stop simulation for the daily Stoch-oversold dip-buy, by SQN(100) regime.

Purpose
-------
The point-to-point study (stoch_oversold_sqn_conditioned.py) showed the edge lives
in SQN(100)=Bull. But point-to-point ignores the PATH — a tight 2-3% underlying
stop can get whipsawed out before the bounce (rule 18's claim). This walks each
trade bar-by-bar on real daily High/Low and applies stops, so we get:

  (a) stop-managed expectancy (R and %), win rate, exit mix, hold time
  (b) the MAE distribution (worst adverse % before exit) per regime
  (c) a bridge to the options -60% premium stop via delta-leverage

Design
------
- Entry at signal-bar CLOSE. Path = bars t+1..t+60 (index-swing 15-60d / 30-60 DTE).
- Intrabar detection: stop hit if bar LOW <= stop; target hit if bar HIGH >= target.
  Same-bar both -> stop first (conservative). Gaps filled at level (slippage ignored).
- Two entry triggers: oversold-zone-entry (first K<20 bar of an episode) and
  reversal-cross (bull_cross_oversold). Both deduped to >10 trading days apart.
- Three exit policies per stop level:
    2R     : stop at -1R, target at +2R (index-swing structure)
    stop   : stop only, otherwise hold to bar 60 (cut losers, let winners run)
    hold   : no stop, hold to bar 60 (point-to-point reference)
- Regime = production daily SQN(100) at the entry bar.

Options -60% bridge: a long call with leverage L (% premium move per 1% underlying)
hits -60% premium at an underlying drop of ~60/L %. Delta-only, first order
(ignores theta -> understates hits; ignores vega). Flagged in output.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/stoch_oversold_stop_sim.py
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from data.yfinance_loader import load_bars  # noqa: E402
from indicators.sqn_regime import SQN_100_BANDS, SQNRegime  # noqa: E402
from indicators.stochastic import Stochastic  # noqa: E402

warnings.filterwarnings("ignore")

TICKERS = ["SPY", "QQQ"]
STOPS = [0.02, 0.03, 0.05, 0.08]
TARGET_R = 2.0
MAX_BARS = 60
STOCH = Stochastic()
SQN100 = SQNRegime(lookback=100, bands=SQN_100_BANDS)
REG_GROUP = {"strong_bull": "BULL", "bull": "BULL", "neutral": "NEUT",
             "bear": "BEAR", "strong_bear": "BEAR"}


def episode_entries(mask: pd.Series, min_gap_days: int = 10) -> list[int]:
    """Integer positions of the first signal bar of each episode (>gap apart)."""
    sel = np.where(mask.values)[0]
    if len(sel) == 0:
        return []
    dates = mask.index
    keep = [sel[0]]
    for p in sel[1:]:
        if (dates[p] - dates[keep[-1]]).days > min_gap_days:
            keep.append(p)
    return keep


def walk(high, low, close, i, stop_pct):
    """Simulate one trade from entry index i. Returns dict of outcomes."""
    e = close[i]
    stop_px = e * (1 - stop_pct)
    tgt_px = e * (1 + stop_pct * TARGET_R)
    end = min(i + MAX_BARS, len(close) - 1)

    exit_2R = None      # (kind, R, ret)
    exit_stop = None    # (kind, ret)
    for j in range(i + 1, end + 1):
        lo, hi = low[j], high[j]
        if exit_2R is None:
            if lo <= stop_px:
                exit_2R = ("stop", -1.0, stop_px / e - 1.0)
            elif hi >= tgt_px:
                exit_2R = ("target", TARGET_R, tgt_px / e - 1.0)
        if exit_stop is None and lo <= stop_px:
            exit_stop = ("stop", stop_px / e - 1.0)
        if exit_2R is not None and exit_stop is not None:
            break
    if exit_2R is None:
        r = close[end] / e - 1.0
        exit_2R = ("time", r / stop_pct, r)
    if exit_stop is None:
        exit_stop = ("time", close[end] / e - 1.0)
    return {"kind_2R": exit_2R[0], "R_2R": exit_2R[1], "ret_2R": exit_2R[2],
            "ret_stop": exit_stop[1]}


def collect(ticker: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    daily = load_bars(ticker, period="max", interval="1d", auto_adjust=True)
    reg = SQN100.compute(daily)["regime"]
    st = STOCH.compute(daily)
    osz = st["zone"] == "oversold"
    osz_entry = osz & ~osz.shift(1, fill_value=False)
    reversal = st["signal"] == "bull_cross_oversold"

    high = daily["high"].values
    low = daily["low"].values
    close = daily["close"].values
    n = len(close)

    trade_rows, leg_rows = [], []
    triggers = {"zone": osz_entry, "reversal": reversal}
    for trig, mask in triggers.items():
        for i in episode_entries(mask):
            if i + 1 >= n:
                continue
            g = REG_GROUP.get(reg.iloc[i]) if pd.notna(reg.iloc[i]) else None
            if g is None:
                continue
            e = close[i]
            end = min(i + MAX_BARS, n - 1)
            seg_lo = low[i + 1:end + 1]
            seg_hi = high[i + 1:end + 1]
            mae = (seg_lo.min() / e - 1.0) if len(seg_lo) else 0.0
            mfe = (seg_hi.max() / e - 1.0) if len(seg_hi) else 0.0
            hold_ret = close[end] / e - 1.0
            trade_rows.append({"ticker": ticker, "trig": trig, "regime": g,
                               "mae": mae, "mfe": mfe, "hold_ret": hold_ret})
            for s in STOPS:
                o = walk(high, low, close, i, s)
                leg_rows.append({"ticker": ticker, "trig": trig, "regime": g,
                                 "stop": s, **o})
    return pd.DataFrame(trade_rows), pd.DataFrame(leg_rows)


def print_policy_table(ticker: str, trig: str, legs: pd.DataFrame, trades: pd.DataFrame) -> None:
    print(f"\n  --- {ticker}  entry={trig}  (max {MAX_BARS}d hold) ---")
    print(f"  {'regime':<6}{'stop':>5}{'n':>5}{'win%':>7}{'%stop':>7}{'%tgt':>7}"
          f"{'expR':>8}{'ret%·2R':>9}{'ret%·stopOnly':>15}{'ret%·hold(noStop)':>19}")
    for g in ["BULL", "NEUT", "BEAR"]:
        tsub = trades[(trades.trig == trig) & (trades.regime == g)]
        hold_ret = tsub["hold_ret"].mean() * 100 if len(tsub) else np.nan
        for s in STOPS:
            sub = legs[(legs.trig == trig) & (legs.regime == g) & (legs.stop == s)]
            if len(sub) == 0:
                continue
            n = len(sub)
            win = (sub.R_2R > 0).mean() * 100
            pst = (sub.kind_2R == "stop").mean() * 100
            ptg = (sub.kind_2R == "target").mean() * 100
            expR = sub.R_2R.mean()
            ret2R = sub.ret_2R.mean() * 100
            retSO = sub.ret_stop.mean() * 100
            print(f"  {g:<6}{int(s*100):>4}%{n:>5}{win:>7.0f}{pst:>7.0f}{ptg:>7.0f}"
                  f"{expR:>+8.2f}{ret2R:>+9.1f}{retSO:>+15.1f}"
                  f"{(f'{hold_ret:+.1f}' if s==STOPS[0] else ''):>19}")


def print_mae(ticker: str, trades: pd.DataFrame) -> None:
    print(f"\n  --- {ticker}  MAE / MFE distribution (entry=reversal, {MAX_BARS}d) ---")
    print(f"  {'regime':<6}{'n':>5}{'MAE med':>9}{'MAE 75p':>9}{'MAE 90p':>9}"
          f"{'MAE max':>9}{'MFE med':>9}")
    for g in ["BULL", "NEUT", "BEAR"]:
        sub = trades[(trades.trig == "reversal") & (trades.regime == g)]
        if len(sub) == 0:
            continue
        depth = -sub["mae"].values * 100  # positive drawdown %
        mfe = sub["mfe"].values * 100
        p = np.percentile(depth, [50, 75, 90])
        print(f"  {g:<6}{len(sub):>5}{p[0]:>8.1f}%{p[1]:>8.1f}%{p[2]:>8.1f}%"
              f"{depth.max():>8.1f}%{np.median(mfe):>8.1f}%")


def print_option_bridge(all_trades: pd.DataFrame) -> None:
    print("\n" + "=" * 92)
    print("OPTIONS −60% PREMIUM STOP — delta-leverage bridge (BULL regime, entry=reversal)")
    print("  -60% premium ≈ underlying −(60/L)%.  Delta-only: ignores theta (→ understates")
    print("  hits) and vega. % = share of BULL dip-buys whose worst path breached that level.")
    print("=" * 92)
    bull = all_trades[(all_trades.trig == "reversal") & (all_trades.regime == "BULL")]
    depth = -bull["mae"].values * 100
    print(f"  BULL dip-buys n={len(depth)}")
    print(f"  {'leverage L':>11}{'~equiv underlying stop':>26}{'% trades that hit -60%':>26}")
    for L in [4, 6, 8, 10, 15, 20]:
        thr = 60.0 / L
        hit = (depth >= thr).mean() * 100
        print(f"  {L:>10}x{('-'+format(thr,'.1f')+'%'):>26}{hit:>24.0f}%")
    print("\n  For comparison, the index-swing 2-3% UNDERLYING stop whipsaw rate is the")
    print("  '%stop' column in the BULL rows above — that is the rule-18 exposure.")


def main() -> None:
    all_trades, all_legs = [], []
    for t in TICKERS:
        trades, legs = collect(t)
        all_trades.append(trades)
        all_legs.append(legs)
        print("\n" + "=" * 92)
        print(f"{t}")
        print("=" * 92)
        for trig in ("reversal", "zone"):
            print_policy_table(t, trig, legs, trades)
        print_mae(t, trades)
    print_option_bridge(pd.concat(all_trades, ignore_index=True))
    print("\nexpR = mean R under stop+2R (stop=-1R, target=+2R). ret%·2R = avg trade "
          "return that policy.\nret%·stopOnly = stop but no target (hold winners to 60d). "
          "ret%·hold = no stop, 60d buy&hold.\n")


if __name__ == "__main__":
    main()
