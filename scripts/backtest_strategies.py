"""Multi-strategy signal-edge backtest.

Replays Tier 1 (weekly-trend-trader), Tier 2 (lotto-options proxy), and
Tier 4 (qqq-gld-focus / Sunday-scan proxy) signals against historical
SPY/QQQ/GLD/IWM bars from yfinance. Reports per-strategy × per-asset
performance vs. buy-and-hold.

Scope and caveats:
- This is a **signal-edge** test on the underlying (shares only). It does NOT
  model option premiums, IV, IVR, slippage, or commissions. Results show
  whether the entry/exit logic has edge in the underlying.
- weekly-trend-trader signal logic is reused from
  src/weekly_trend/scanner.py::classify_confluence. Faithful to production.
- lotto-options uses DAILY bars as a proxy for the 2H trigger, because
  yfinance hourly history is shallow (~60d). Captures the daily-MA + daily-
  Stoch component but not the 2H trigger itself. Max hold = 10 trading days.
- qqq-gld-focus / Sunday-scan also uses daily bars as 2H proxy. Adds a
  pullback filter (close within 2% of MA20) and longer max hold (21 days,
  proxying the 21-60 DTE band). Strategy is officially QQQ+GLD-only;
  results on SPY/IWM are reference-only for comparison.
- trend-pyramid is intentionally NOT included — strategy was retired
  2026-05-07 (orchestrator scope decision).

Usage (from repo root):
    PYTHONPATH=src python3 scripts/backtest_strategies.py
    PYTHONPATH=src python3 scripts/backtest_strategies.py --json out.json
    PYTHONPATH=src python3 scripts/backtest_strategies.py \
        --tickers SPY,QQQ,GLD,IWM,TLT --json scripts/backtest_strategies_output.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from dataclasses import dataclass, asdict, field
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from data.yfinance_loader import load_bars  # noqa: E402
from indicators.ma_ribbon import MARibbon  # noqa: E402
from indicators.stochastic import Stochastic  # noqa: E402
from indicators.sqn_regime import SQNRegime, SQN_100_BANDS, SQN_20_BANDS  # noqa: E402
from weekly_trend.scanner import classify_confluence  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)


# ─────────────────────────────────────────────────────────────────────────
# Indicator computation
# ─────────────────────────────────────────────────────────────────────────


def _close_col(df: pd.DataFrame) -> str:
    return "close" if "close" in df.columns else "Close"


def compute_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Reuses production indicator classes via their compute(df) APIs.

    Columns added: ma_10, ma_20, ma_50, ma_200, stack_state, stoch_k,
    stoch_d, stoch_signal, sqn_value, sqn_regime, sqn20_value, regime_20.
    """
    close_col = _close_col(bars)
    df = pd.DataFrame({
        "open": bars[bars.columns[bars.columns.str.lower().str.startswith("open")][0]].astype(float),
        "high": bars[bars.columns[bars.columns.str.lower().str.startswith("high")][0]].astype(float),
        "low": bars[bars.columns[bars.columns.str.lower().str.startswith("low")][0]].astype(float),
        "close": bars[close_col].astype(float),
    }, index=bars.index)

    ribbon_out = MARibbon(periods=(10, 20, 50, 200)).compute(df)
    df["ma_10"] = ribbon_out["ma_10"]
    df["ma_20"] = ribbon_out["ma_20"]
    df["ma_50"] = ribbon_out["ma_50"]
    df["ma_200"] = ribbon_out["ma_200"]
    df["stack_state"] = ribbon_out["stack_state"]

    stoch_out = Stochastic(length=14, smooth_k=7, smooth_d=7).compute(df)
    df["stoch_k"] = stoch_out["k"]
    df["stoch_d"] = stoch_out["d"]
    df["stoch_signal"] = stoch_out["signal"]

    sqn100_out = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(df)
    df["sqn_value"] = sqn100_out["sqn_value"]
    df["sqn_regime"] = sqn100_out["regime"]

    sqn20_out = SQNRegime(lookback=20, bands=SQN_20_BANDS).compute(df)
    df["sqn20_value"] = sqn20_out["sqn_value"]
    df["regime_20"] = sqn20_out["regime"]

    return df


def to_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    """Resample daily OHLC to weekly W-FRI bars."""
    cols = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    actual = {}
    for k, v in cols.items():
        col = k if k in daily.columns else k.capitalize()
        if col in daily.columns:
            actual[col] = v
    return daily.resample("W-FRI").agg(actual).dropna(subset=[
        "close" if "close" in actual else "Close"
    ])


# ─────────────────────────────────────────────────────────────────────────
# Trade simulation
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    direction: str  # "long" or "short"
    entry_price: float
    exit_price: float
    return_pct: float
    holding_days: int
    entry_regime: str | None = None
    exit_reason: str = ""


@dataclass
class BacktestResult:
    strategy: str
    ticker: str
    n_trades: int
    win_rate: float
    avg_return_pct: float
    avg_winner_pct: float
    avg_loser_pct: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    sharpe: float
    avg_holding_days: float
    sample_start: str
    sample_end: str
    buy_hold_return_pct: float
    buy_hold_cagr_pct: float
    regime_breakdown: dict[str, dict[str, float]] = field(default_factory=dict)


def simulate(
    bars: pd.DataFrame,
    entries: pd.Series,           # 1 = long entry signal, -1 = short entry signal, 0 = none
    exits: pd.Series,             # boolean exit signal (closes any open position)
    *,
    direction: str = "both",      # "long", "short", or "both"
    max_hold_bars: int | None = None,
) -> tuple[list[Trade], pd.Series]:
    """Walk-forward simulator. One position at a time.

    On entry signal at bar i, enter at bar i's close. On exit signal or
    max_hold_bars elapsed, exit at that bar's close.
    """
    trades: list[Trade] = []
    open_pos: dict | None = None
    equity = [1.0]
    eq_idx = [bars.index[0]]
    close = bars["close"].values
    regimes = bars.get("sqn_regime", pd.Series([None] * len(bars), index=bars.index))

    for i in range(len(bars)):
        date = bars.index[i]
        c = close[i]

        # Check exit on existing position
        if open_pos is not None:
            should_exit = False
            reason = ""
            if exits.iloc[i]:
                should_exit = True
                reason = "exit_signal"
            elif max_hold_bars and (i - open_pos["entry_idx"]) >= max_hold_bars:
                should_exit = True
                reason = "max_hold"
            if should_exit:
                ep = open_pos["entry_price"]
                ret = (c / ep - 1.0) if open_pos["dir"] == "long" else (ep / c - 1.0)
                trades.append(Trade(
                    entry_date=open_pos["entry_date"], exit_date=date,
                    direction=open_pos["dir"], entry_price=ep, exit_price=c,
                    return_pct=ret * 100, holding_days=i - open_pos["entry_idx"],
                    entry_regime=open_pos.get("entry_regime"),
                    exit_reason=reason,
                ))
                equity.append(equity[-1] * (1 + ret))
                eq_idx.append(date)
                open_pos = None

        # Check entry on no-position
        if open_pos is None:
            sig = entries.iloc[i]
            entry_dir = None
            if sig == 1 and direction in ("long", "both"):
                entry_dir = "long"
            elif sig == -1 and direction in ("short", "both"):
                entry_dir = "short"
            if entry_dir is not None:
                open_pos = {
                    "dir": entry_dir, "entry_price": c,
                    "entry_date": date, "entry_idx": i,
                    "entry_regime": regimes.iloc[i] if hasattr(regimes, "iloc") else None,
                }

    # Force-close any open position at last bar
    if open_pos is not None:
        i_last = len(bars) - 1
        ep = open_pos["entry_price"]
        c = close[i_last]
        ret = (c / ep - 1.0) if open_pos["dir"] == "long" else (ep / c - 1.0)
        trades.append(Trade(
            entry_date=open_pos["entry_date"], exit_date=bars.index[i_last],
            direction=open_pos["dir"], entry_price=ep, exit_price=c,
            return_pct=ret * 100, holding_days=i_last - open_pos["entry_idx"],
            entry_regime=open_pos.get("entry_regime"),
            exit_reason="force_close_eos",
        ))
        equity.append(equity[-1] * (1 + ret))
        eq_idx.append(bars.index[i_last])

    # Anchor the equity series to the actual last bar so reports reflect the
    # full sample window (otherwise it ends at the last trade exit).
    if eq_idx[-1] != bars.index[-1]:
        equity.append(equity[-1])
        eq_idx.append(bars.index[-1])

    eq_series = pd.Series(equity, index=eq_idx)
    return trades, eq_series


def summarize(
    trades: list[Trade], equity: pd.Series, bars: pd.DataFrame,
    strategy: str, ticker: str,
) -> BacktestResult:
    if not trades:
        return BacktestResult(
            strategy=strategy, ticker=ticker, n_trades=0, win_rate=0.0,
            avg_return_pct=0.0, avg_winner_pct=0.0, avg_loser_pct=0.0,
            total_return_pct=0.0, cagr_pct=0.0, max_drawdown_pct=0.0,
            sharpe=0.0, avg_holding_days=0.0,
            sample_start=str(bars.index[0].date()),
            sample_end=str(bars.index[-1].date()),
            buy_hold_return_pct=_buy_hold(bars)[0],
            buy_hold_cagr_pct=_buy_hold(bars)[1],
        )
    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    total_ret = (equity.iloc[-1] - 1.0) * 100
    days = (equity.index[-1] - equity.index[0]).days
    years = max(days / 365.25, 1e-6)
    cagr = (equity.iloc[-1] ** (1 / years) - 1.0) * 100

    # Max drawdown on equity
    rolling_max = equity.cummax()
    dd = (equity / rolling_max - 1.0)
    max_dd = float(dd.min() * 100)

    # Trade-level Sharpe (annualized using avg holding days)
    avg_hold = float(np.mean([t.holding_days for t in trades])) or 1.0
    if len(rets) > 1 and np.std(rets, ddof=1) > 0:
        trades_per_year = 252.0 / max(avg_hold, 1.0)
        sharpe = float(np.mean(rets) / np.std(rets, ddof=1) * math.sqrt(trades_per_year))
    else:
        sharpe = 0.0

    bh_return, bh_cagr = _buy_hold(bars)

    regime_breakdown = _regime_breakdown(trades)

    return BacktestResult(
        strategy=strategy, ticker=ticker, n_trades=len(trades),
        win_rate=len(wins) / len(trades) * 100,
        avg_return_pct=float(np.mean(rets)),
        avg_winner_pct=float(np.mean(wins)) if wins else 0.0,
        avg_loser_pct=float(np.mean(losses)) if losses else 0.0,
        total_return_pct=total_ret, cagr_pct=cagr,
        max_drawdown_pct=max_dd, sharpe=sharpe, avg_holding_days=avg_hold,
        sample_start=str(equity.index[0].date()),
        sample_end=str(equity.index[-1].date()),
        buy_hold_return_pct=bh_return, buy_hold_cagr_pct=bh_cagr,
        regime_breakdown=regime_breakdown,
    )


def _buy_hold(bars: pd.DataFrame) -> tuple[float, float]:
    close = bars["close"]
    total = (close.iloc[-1] / close.iloc[0] - 1.0) * 100
    days = (bars.index[-1] - bars.index[0]).days
    years = max(days / 365.25, 1e-6)
    cagr = ((close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1.0) * 100
    return float(total), float(cagr)


def _regime_breakdown(trades: list[Trade]) -> dict[str, dict[str, float]]:
    """Group trades by entry SQN(100) regime; return n + avg return + win rate."""
    by: dict[str, list[float]] = {}
    for t in trades:
        key = t.entry_regime or "unknown"
        by.setdefault(key, []).append(t.return_pct)
    out = {}
    for k, rets in by.items():
        wins = [r for r in rets if r > 0]
        out[k] = {
            "n": len(rets),
            "avg_return_pct": float(np.mean(rets)),
            "win_rate_pct": len(wins) / len(rets) * 100,
        }
    return out


# ─────────────────────────────────────────────────────────────────────────
# Strategy 1 — weekly-trend-trader (Tier 1)
# Source: src/weekly_trend/scanner.py::classify_confluence
# Skill: ~/.claude/skills/user/weekly-trend-trader/SKILL.md
# ─────────────────────────────────────────────────────────────────────────


def signals_weekly_trend(weekly: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Tier 1 weekly-trend-trader.

    Entry: classify_confluence returns high_conviction_long/continuation_long
    (long signal) or high_conviction_short/continuation_short (short signal),
    AND no counter-regime blocker (SQN(100) aligned with direction).
    Exit: weekly close < MA_50 (longs) / > MA_50 (shorts), OR stack state
    degrades to chop/compression.
    """
    entries = pd.Series(0, index=weekly.index, dtype=int)
    exits = pd.Series(False, index=weekly.index)

    for i in range(len(weekly)):
        row = weekly.iloc[i]
        state_val = row.get("stack_state")
        if pd.isna(row.get("ma_50")) or pd.isna(state_val) or state_val is None:
            continue
        confluence, direction, blockers = classify_confluence(
            ma_stack_state=str(state_val),
            stoch_k=None if pd.isna(row.get("stoch_k", float("nan"))) else float(row["stoch_k"]),
            stoch_d=None if pd.isna(row.get("stoch_d", float("nan"))) else float(row["stoch_d"]),
            stoch_signal=None if pd.isna(row.get("stoch_signal")) else row.get("stoch_signal"),
            sqn_regime=None if pd.isna(row.get("sqn_regime")) else row.get("sqn_regime"),
        )
        # Long entry
        if confluence in ("high_conviction_long", "continuation_long") and direction == "long":
            if not any("opposes long bias" in b for b in blockers):
                entries.iloc[i] = 1
        elif confluence in ("high_conviction_short", "continuation_short") and direction == "short":
            if not any("opposes short bias" in b for b in blockers):
                entries.iloc[i] = -1

    # Exit signal — degraded stack OR weekly close vs MA_50
    for i in range(len(weekly)):
        row = weekly.iloc[i]
        state_raw = row.get("stack_state")
        if pd.isna(state_raw) or state_raw is None:
            continue
        state = str(state_raw)
        if state in ("chop", "compression", "bear_developing", "full_bear"):
            exits.iloc[i] = True
        elif state in ("bull_developing", "full_bull"):
            ma50 = row.get("ma_50")
            if not pd.isna(ma50) and row["close"] < ma50:
                exits.iloc[i] = True

    return entries, exits


# ─────────────────────────────────────────────────────────────────────────
# Strategy 2 — lotto-options proxy (Tier 2)
# Source spec: ~/.claude/skills/user/lotto-options/SKILL.md
# Proxy note: 2H trigger replaced by daily-Stoch trigger because yfinance
# hourly history is shallow. Captures daily-MA-stack alignment + extreme-
# Stoch reversal cross only — not the 2H component.
# ─────────────────────────────────────────────────────────────────────────


def signals_lotto_proxy(daily: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Tier 2 lotto-options daily-bars proxy.

    Entry: Daily MA stack full_bull + Stoch %K cross above %D from <30, OR
    full_bear + cross below %D from >70. SQN(100) aligned with direction.
    Exit: Stoch reversal cross (signal flips), or max-hold = 10 trading days.
    """
    entries = pd.Series(0, index=daily.index, dtype=int)
    exits = pd.Series(False, index=daily.index)

    # IMPORTANT: this is a DAILY-bar proxy for a 2H-trigger strategy. Daily
    # `bull_cross_oversold` (deep k<30 cross within a full_bull stack) is
    # almost never observed (zero trades) because uptrending stacks rarely
    # let Stoch fall that deep on daily bars; on 2H bars it happens multiple
    # times per week. The proxy uses any LONG_TRIGGER (oversold or
    # continuation cross) to maintain a measurable sample, with the
    # understanding that real 2H entries are tighter than what's tested here.
    #
    # The chase-warning filter (SQN(20) > +2.5) below is a no-op on these
    # daily-proxy trades — auditing failing QQQ longs showed they entered at
    # SQN(20) ≈ 0.5-0.7 (neutral). Kept here for parity with the production
    # kill-sheet gate (kill_sheet/builder.py).
    LONG_TRIGGERS = {"bull_cross_oversold", "bull_continuation"}
    SHORT_TRIGGERS = {"bear_cross_overbought", "bear_continuation"}
    CHASE_WARNING_THRESHOLD = 2.5  # SQN(20) > +2.5 → no chase (CLAUDE.md rule 13)

    for i in range(len(daily)):
        row = daily.iloc[i]
        state_raw = row.get("stack_state")
        regime_raw = row.get("sqn_regime")
        sig_raw = row.get("stoch_signal")
        state = None if pd.isna(state_raw) else str(state_raw)
        regime = None if pd.isna(regime_raw) else str(regime_raw)
        sig = None if pd.isna(sig_raw) else str(sig_raw)
        sqn20 = row.get("sqn20_value")
        chase_blocked_long = (
            sqn20 is not None
            and not pd.isna(sqn20)
            and sqn20 > CHASE_WARNING_THRESHOLD
        )

        if (state in ("full_bull", "bull_developing") and sig in LONG_TRIGGERS
                and regime in ("bull", "strong_bull")
                and not chase_blocked_long):
            entries.iloc[i] = 1
        elif (state in ("full_bear", "bear_developing") and sig in SHORT_TRIGGERS
                and regime in ("bear", "strong_bear")):
            entries.iloc[i] = -1

        # Exit on any Stoch trigger (force-close any open trade).
        if sig in SHORT_TRIGGERS or sig in LONG_TRIGGERS:
            exits.iloc[i] = True

    return entries, exits


# ─────────────────────────────────────────────────────────────────────────
# Strategy 3 — qqq-gld-focus / Sunday-scan (Tier 4)
# Source spec: ~/.claude/skills/user/qqq-gld-focus/SKILL.md
# Per skill: Daily MA stack = direction filter; Daily SQN = regime gate;
# 2H Stoch cross from <20 (long) or >80 (short) = entry trigger; 21-60 DTE.
# Distinguishing feature vs lotto: pullback to 2H 10/20 MA, longer hold.
# Backtest uses daily bars as 2H proxy (yfinance hourly history is shallow).
# ─────────────────────────────────────────────────────────────────────────


def signals_sunday_scan(daily: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """Tier 4 qqq-gld-focus / Sunday-scan daily-bars proxy.

    Entry (long): full_bull or bull_developing stack + Stoch trigger
    (bull_cross_oversold OR bull_continuation) + pullback proximity (close
    within 3% of MA10 or MA20, proxying "at or near 2H 10/20 MA pullback
    entry") + SQN(100) bull/strong_bull. Mirror for shorts.
    Exit: opposing Stoch trigger OR max hold 21 trading days (~21 DTE proxy).
    """
    LONG_TRIGGERS = {"bull_cross_oversold", "bull_continuation"}
    SHORT_TRIGGERS = {"bear_cross_overbought", "bear_continuation"}
    LONG_STACKS = {"full_bull", "bull_developing"}
    SHORT_STACKS = {"full_bear", "bear_developing"}
    PULLBACK_TOL = 0.03  # close within 3% of MA10 or MA20

    entries = pd.Series(0, index=daily.index, dtype=int)
    exits = pd.Series(False, index=daily.index)

    for i in range(len(daily)):
        row = daily.iloc[i]
        state_raw = row.get("stack_state")
        regime_raw = row.get("sqn_regime")
        sig_raw = row.get("stoch_signal")
        state = None if pd.isna(state_raw) else str(state_raw)
        regime = None if pd.isna(regime_raw) else str(regime_raw)
        sig = None if pd.isna(sig_raw) else str(sig_raw)
        close = row["close"]
        ma10 = row.get("ma_10")
        ma20 = row.get("ma_20")

        def _near(ma):
            return ma is not None and not pd.isna(ma) and abs(close / ma - 1.0) <= PULLBACK_TOL
        pullback_ok = _near(ma10) or _near(ma20)

        if (state in LONG_STACKS and sig in LONG_TRIGGERS
                and regime in ("bull", "strong_bull") and pullback_ok):
            entries.iloc[i] = 1
        elif (state in SHORT_STACKS and sig in SHORT_TRIGGERS
                and regime in ("bear", "strong_bear") and pullback_ok):
            entries.iloc[i] = -1

        # Exit on opposing-direction Stoch trigger
        if sig in SHORT_TRIGGERS or sig in LONG_TRIGGERS:
            exits.iloc[i] = True

    return entries, exits


# ─────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────


def run_one(strategy: str, ticker: str, daily: pd.DataFrame) -> BacktestResult:
    if strategy == "weekly-trend-trader":
        weekly = compute_indicators(to_weekly(daily))
        entries, exits = signals_weekly_trend(weekly)
        trades, equity = simulate(weekly, entries, exits)
        return summarize(trades, equity, weekly, strategy, ticker)
    elif strategy == "lotto-options-proxy":
        d = compute_indicators(daily)
        entries, exits = signals_lotto_proxy(d)
        trades, equity = simulate(d, entries, exits, max_hold_bars=10)
        return summarize(trades, equity, d, strategy, ticker)
    elif strategy == "qqq-gld-focus":
        d = compute_indicators(daily)
        entries, exits = signals_sunday_scan(d)
        trades, equity = simulate(d, entries, exits, max_hold_bars=21)
        return summarize(trades, equity, d, strategy, ticker)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def render(results: list[BacktestResult]) -> str:
    out = []
    out.append("=" * 110)
    out.append("Multi-strategy signal-edge backtest — shares-only, underlying P&L")
    out.append("=" * 110)
    out.append("")
    out.append(
        "⚠️ Tier 2 (lotto-options-proxy) and Tier 4 (qqq-gld-focus) use daily bars\n"
        "   as proxy for the 2H Stoch trigger (yfinance hourly history is shallow).\n"
        "⚠️ qqq-gld-focus is officially QQQ+GLD-only per spec; SPY/IWM rows are\n"
        "   reference-only for cross-asset comparison."
    )
    out.append("")
    by_strategy: dict[str, list[BacktestResult]] = {}
    for r in results:
        by_strategy.setdefault(r.strategy, []).append(r)
    for strat in ("weekly-trend-trader", "lotto-options-proxy", "qqq-gld-focus"):
        if strat not in by_strategy:
            continue
        out.append(f"━━━━━━━━━━━━ {strat.upper()} ━━━━━━━━━━━━")
        out.append(
            f"{'Ticker':>7} {'Sample':>23} {'N':>4} {'Win%':>5} {'AvgRet%':>8} "
            f"{'Total%':>8} {'CAGR%':>7} {'MaxDD%':>7} {'Sharpe':>7} "
            f"{'AvgHold':>7} {'BHRet%':>8} {'BHCAGR%':>8}"
        )
        for r in sorted(by_strategy[strat], key=lambda x: x.ticker):
            out.append(
                f"{r.ticker:>7} {r.sample_start} → {r.sample_end} "
                f"{r.n_trades:>4d} {r.win_rate:>4.1f} {r.avg_return_pct:>+7.2f} "
                f"{r.total_return_pct:>+7.1f} {r.cagr_pct:>+6.2f} {r.max_drawdown_pct:>+6.1f} "
                f"{r.sharpe:>+6.2f} {r.avg_holding_days:>6.1f} "
                f"{r.buy_hold_return_pct:>+7.1f} {r.buy_hold_cagr_pct:>+7.2f}"
            )
        out.append("")
        out.append("Regime decomposition (entry SQN(100) regime → trade returns):")
        for r in sorted(by_strategy[strat], key=lambda x: x.ticker):
            out.append(f"  {r.ticker}:")
            for regime in ("strong_bull", "bull", "neutral", "bear", "strong_bear", "unknown"):
                stats = r.regime_breakdown.get(regime)
                if stats:
                    out.append(
                        f"    {regime:>13}  n={stats['n']:>3d}  "
                        f"avg={stats['avg_return_pct']:>+6.2f}%  "
                        f"win%={stats['win_rate_pct']:>5.1f}"
                    )
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tickers", default="SPY,QQQ,GLD,IWM",
        help="Comma-separated tickers (default SPY,QQQ,GLD,IWM)",
    )
    ap.add_argument(
        "--strategies",
        default="weekly-trend-trader,lotto-options-proxy,qqq-gld-focus",
        help="Comma-separated strategy names",
    )
    ap.add_argument("--json", type=Path, help="Optional JSON output path")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]

    print(f"Loading bars for {len(tickers)} tickers from yfinance...")
    bars_by_ticker: dict[str, pd.DataFrame] = {}
    for tk in tickers:
        try:
            d = load_bars(tk, period="max", interval="1d")
            if d is None or d.empty:
                print(f"  {tk}: no data, skipping")
                continue
            bars_by_ticker[tk] = d
            print(f"  {tk}: {len(d)} bars, {d.index[0].date()} → {d.index[-1].date()}")
        except Exception as e:
            print(f"  {tk}: load failed — {e}")

    print()
    results: list[BacktestResult] = []
    for strat in strategies:
        for tk, d in bars_by_ticker.items():
            print(f"Running {strat} on {tk}...")
            r = run_one(strat, tk, d)
            results.append(r)

    print()
    text = render(results)
    print(text)

    if args.json:
        out = {"results": [asdict(r) for r in results]}
        args.json.write_text(json.dumps(out, indent=2, default=str))
        print(f"\nWrote JSON to: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
