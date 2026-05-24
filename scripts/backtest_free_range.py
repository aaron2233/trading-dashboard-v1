"""Free-range scoring backtest — v1 (current) vs v2 (MA de-weighted).

Replays `free_range.filters.score_direction()` over historical daily bars
for QQQ + GLD (default) under both scoring versions and reports the
side-by-side trade metrics. The goal: validate whether de-weighting MA
(Aaron's concern: ribbon snaps in late, so price-action via Stoch +
regime should drive entries) changes win rate, expectancy, or drawdown
on the dashboard's anchor watchlist.

Modeled on `scripts/backtest_strategies.py`; reuses the same simulator,
indicator stack, and report shape so v1/v2 numbers are directly
comparable to the existing strategy backtests.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/backtest_free_range.py
    PYTHONPATH=src python3 scripts/backtest_free_range.py \
        --tickers QQQ,GLD --json scripts/backtest_free_range_output.json
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
from free_range.filters import (  # noqa: E402
    FREE_RANGE_MIN_SCORE,
    ScoringVersion,
    score_direction,
)
from indicators.ma_ribbon import MARibbon  # noqa: E402
from indicators.stochastic import Stochastic  # noqa: E402
from indicators.sqn_regime import SQN_100_BANDS, SQN_20_BANDS, SQNRegime  # noqa: E402

warnings.filterwarnings("ignore", category=FutureWarning)


def _close_col(df: pd.DataFrame) -> str:
    return "close" if "close" in df.columns else "Close"


def compute_indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Mirror of backtest_strategies.compute_indicators but with stoch_zone
    exposed (free-range scoring reads zone, not just signal)."""
    close_col = _close_col(bars)
    df = pd.DataFrame(
        {
            "open": bars[
                bars.columns[bars.columns.str.lower().str.startswith("open")][0]
            ].astype(float),
            "high": bars[
                bars.columns[bars.columns.str.lower().str.startswith("high")][0]
            ].astype(float),
            "low": bars[
                bars.columns[bars.columns.str.lower().str.startswith("low")][0]
            ].astype(float),
            "close": bars[close_col].astype(float),
        },
        index=bars.index,
    )

    ribbon_out = MARibbon(periods=(10, 20, 50, 200)).compute(df)
    df["ma_10"] = ribbon_out["ma_10"]
    df["ma_20"] = ribbon_out["ma_20"]
    df["ma_50"] = ribbon_out["ma_50"]
    df["ma_200"] = ribbon_out["ma_200"]
    df["stack_state"] = ribbon_out["stack_state"]

    stoch_out = Stochastic(length=14, smooth_k=7, smooth_d=7).compute(df)
    df["stoch_k"] = stoch_out["k"]
    df["stoch_d"] = stoch_out["d"]
    df["stoch_zone"] = stoch_out["zone"]
    df["stoch_signal"] = stoch_out["signal"]

    sqn100_out = SQNRegime(lookback=100, bands=SQN_100_BANDS).compute(df)
    df["sqn_value"] = sqn100_out["sqn_value"]
    df["sqn_regime"] = sqn100_out["regime"]

    sqn20_out = SQNRegime(lookback=20, bands=SQN_20_BANDS).compute(df)
    df["sqn20_value"] = sqn20_out["sqn_value"]
    df["regime_20"] = sqn20_out["regime"]

    # v3 price-action signal: close pierces prior 5-bar swing high (long)
    # or swing low (short). `.shift(1)` excludes the current bar so the
    # breakout test compares today's close to the highest/lowest of the
    # previous 5 completed bars (matches the index-swing primitive).
    prior_5bar_high = df["high"].rolling(5).max().shift(1)
    prior_5bar_low = df["low"].rolling(5).min().shift(1)
    df["prior_5bar_high"] = prior_5bar_high
    df["prior_5bar_low"] = prior_5bar_low
    df["breakout_5bar_long"] = df["close"] > prior_5bar_high
    df["breakout_5bar_short"] = df["close"] < prior_5bar_low

    return df


# ─────────────────────────────────────────────────────────────────────────
# Signal generation — free-range score-driven entries
# ─────────────────────────────────────────────────────────────────────────

# Same Stoch-trigger families used by the lotto-options-proxy harness.
# They define what a "fresh momentum trigger" looks like; exit when one
# fires on the current bar (matches the lotto-proxy exit logic).
_LONG_TRIGGERS = {"bull_cross_oversold", "bull_continuation"}
_SHORT_TRIGGERS = {"bear_cross_overbought", "bear_continuation"}


def _scan_row(row: pd.Series) -> dict | None:
    """Build the synthetic dict score_direction expects, from one daily bar."""
    stack = row.get("stack_state")
    if pd.isna(stack) or stack is None:
        return None
    zone = row.get("stoch_zone")
    sig = row.get("stoch_signal")
    regime = row.get("sqn_regime")
    bo_long = row.get("breakout_5bar_long")
    bo_short = row.get("breakout_5bar_short")
    return {
        "ma_ribbon": {"stack_state": str(stack)},
        "stochastic": {
            "zone": None if pd.isna(zone) else str(zone),
            "signal": None if pd.isna(sig) else str(sig),
        },
        "sqn": {"regime": None if pd.isna(regime) else str(regime)},
        "price_action": {
            "breakout_5bar_long": bool(bo_long) if not pd.isna(bo_long) else False,
            "breakout_5bar_short": bool(bo_short) if not pd.isna(bo_short) else False,
        },
    }


def signals_free_range(
    daily: pd.DataFrame,
    *,
    scoring_version: ScoringVersion,
) -> tuple[pd.Series, pd.Series]:
    """Free-range entry signals: pick higher of long/short score; require
    winning score ≥ FREE_RANGE_MIN_SCORE. Exit on any Stoch trigger or
    max_hold_bars (driver applies the latter via the simulator)."""
    entries = pd.Series(0, index=daily.index, dtype=int)
    exits = pd.Series(False, index=daily.index)

    for i in range(len(daily)):
        row = daily.iloc[i]
        scan_row = _scan_row(row)
        if scan_row is None:
            continue

        long_score, _ = score_direction(
            scan_row, "long", scoring_version=scoring_version,
        )
        short_score, _ = score_direction(
            scan_row, "short", scoring_version=scoring_version,
        )
        best_score = max(long_score, short_score)
        if best_score >= FREE_RANGE_MIN_SCORE:
            entries.iloc[i] = 1 if long_score >= short_score else -1

        sig = scan_row["stochastic"]["signal"]
        if sig in _LONG_TRIGGERS or sig in _SHORT_TRIGGERS:
            exits.iloc[i] = True

    return entries, exits


# ─────────────────────────────────────────────────────────────────────────
# Simulator — copy of backtest_strategies.simulate (kept local to avoid
# importing from a sibling script).
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class Trade:
    entry_date: pd.Timestamp
    exit_date: pd.Timestamp
    direction: str
    entry_price: float
    exit_price: float
    return_pct: float
    holding_days: int
    entry_regime: str | None = None
    exit_reason: str = ""


@dataclass
class BacktestResult:
    scoring_version: str
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
    entries: pd.Series,
    exits: pd.Series,
    *,
    max_hold_bars: int | None = None,
) -> tuple[list[Trade], pd.Series]:
    trades: list[Trade] = []
    open_pos: dict | None = None
    equity = [1.0]
    eq_idx = [bars.index[0]]
    close = bars["close"].values
    regimes = bars.get(
        "sqn_regime", pd.Series([None] * len(bars), index=bars.index),
    )

    for i in range(len(bars)):
        date = bars.index[i]
        c = close[i]

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
                ret = (
                    (c / ep - 1.0) if open_pos["dir"] == "long"
                    else (ep / c - 1.0)
                )
                trades.append(
                    Trade(
                        entry_date=open_pos["entry_date"], exit_date=date,
                        direction=open_pos["dir"], entry_price=ep, exit_price=c,
                        return_pct=ret * 100,
                        holding_days=i - open_pos["entry_idx"],
                        entry_regime=open_pos.get("entry_regime"),
                        exit_reason=reason,
                    )
                )
                equity.append(equity[-1] * (1 + ret))
                eq_idx.append(date)
                open_pos = None

        if open_pos is None:
            sig = entries.iloc[i]
            entry_dir = None
            if sig == 1:
                entry_dir = "long"
            elif sig == -1:
                entry_dir = "short"
            if entry_dir is not None:
                open_pos = {
                    "dir": entry_dir, "entry_price": c,
                    "entry_date": date, "entry_idx": i,
                    "entry_regime": (
                        regimes.iloc[i] if hasattr(regimes, "iloc") else None
                    ),
                }

    if open_pos is not None:
        i_last = len(bars) - 1
        ep = open_pos["entry_price"]
        c = close[i_last]
        ret = (
            (c / ep - 1.0) if open_pos["dir"] == "long"
            else (ep / c - 1.0)
        )
        trades.append(
            Trade(
                entry_date=open_pos["entry_date"], exit_date=bars.index[i_last],
                direction=open_pos["dir"], entry_price=ep, exit_price=c,
                return_pct=ret * 100,
                holding_days=i_last - open_pos["entry_idx"],
                entry_regime=open_pos.get("entry_regime"),
                exit_reason="force_close_eos",
            )
        )
        equity.append(equity[-1] * (1 + ret))
        eq_idx.append(bars.index[i_last])

    if eq_idx[-1] != bars.index[-1]:
        equity.append(equity[-1])
        eq_idx.append(bars.index[-1])

    return trades, pd.Series(equity, index=eq_idx)


# ─────────────────────────────────────────────────────────────────────────
# Summarization
# ─────────────────────────────────────────────────────────────────────────


def _buy_hold(bars: pd.DataFrame) -> tuple[float, float]:
    close = bars["close"]
    total = (close.iloc[-1] / close.iloc[0] - 1.0) * 100
    days = (bars.index[-1] - bars.index[0]).days
    years = max(days / 365.25, 1e-6)
    cagr = ((close.iloc[-1] / close.iloc[0]) ** (1 / years) - 1.0) * 100
    return float(total), float(cagr)


def _regime_breakdown(trades: list[Trade]) -> dict[str, dict[str, float]]:
    by: dict[str, list[float]] = {}
    for t in trades:
        key = t.entry_regime or "unknown"
        by.setdefault(key, []).append(t.return_pct)
    out: dict[str, dict[str, float]] = {}
    for k, rets in by.items():
        wins = [r for r in rets if r > 0]
        out[k] = {
            "n": float(len(rets)),
            "avg_return_pct": float(np.mean(rets)),
            "win_rate_pct": len(wins) / len(rets) * 100,
        }
    return out


def summarize(
    trades: list[Trade], equity: pd.Series, bars: pd.DataFrame,
    scoring_version: str, ticker: str,
) -> BacktestResult:
    if not trades:
        bh_return, bh_cagr = _buy_hold(bars)
        return BacktestResult(
            scoring_version=scoring_version, ticker=ticker, n_trades=0,
            win_rate=0.0, avg_return_pct=0.0, avg_winner_pct=0.0,
            avg_loser_pct=0.0, total_return_pct=0.0, cagr_pct=0.0,
            max_drawdown_pct=0.0, sharpe=0.0, avg_holding_days=0.0,
            sample_start=str(bars.index[0].date()),
            sample_end=str(bars.index[-1].date()),
            buy_hold_return_pct=bh_return, buy_hold_cagr_pct=bh_cagr,
        )

    rets = [t.return_pct for t in trades]
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    total_ret = (equity.iloc[-1] - 1.0) * 100
    days = (equity.index[-1] - equity.index[0]).days
    years = max(days / 365.25, 1e-6)
    cagr = (equity.iloc[-1] ** (1 / years) - 1.0) * 100

    rolling_max = equity.cummax()
    dd = equity / rolling_max - 1.0
    max_dd = float(dd.min() * 100)

    avg_hold = float(np.mean([t.holding_days for t in trades])) or 1.0
    if len(rets) > 1 and np.std(rets, ddof=1) > 0:
        trades_per_year = 252.0 / max(avg_hold, 1.0)
        sharpe = float(
            np.mean(rets) / np.std(rets, ddof=1) * math.sqrt(trades_per_year)
        )
    else:
        sharpe = 0.0

    bh_return, bh_cagr = _buy_hold(bars)

    return BacktestResult(
        scoring_version=scoring_version, ticker=ticker, n_trades=len(trades),
        win_rate=len(wins) / len(trades) * 100,
        avg_return_pct=float(np.mean(rets)),
        avg_winner_pct=float(np.mean(wins)) if wins else 0.0,
        avg_loser_pct=float(np.mean(losses)) if losses else 0.0,
        total_return_pct=total_ret, cagr_pct=cagr,
        max_drawdown_pct=max_dd, sharpe=sharpe, avg_holding_days=avg_hold,
        sample_start=str(equity.index[0].date()),
        sample_end=str(equity.index[-1].date()),
        buy_hold_return_pct=bh_return, buy_hold_cagr_pct=bh_cagr,
        regime_breakdown=_regime_breakdown(trades),
    )


# ─────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────


def run_one(
    ticker: str, daily: pd.DataFrame, scoring_version: ScoringVersion,
    *, max_hold_bars: int,
) -> BacktestResult:
    enriched = compute_indicators(daily)
    entries, exits = signals_free_range(enriched, scoring_version=scoring_version)
    trades, equity = simulate(enriched, entries, exits, max_hold_bars=max_hold_bars)
    return summarize(trades, equity, enriched, scoring_version, ticker)


def render(results: list[BacktestResult]) -> str:
    versions_run = sorted({r.scoring_version for r in results})
    out: list[str] = []
    out.append("=" * 118)
    out.append(
        f"Free-range scoring backtest — comparing {', '.join(versions_run)}"
    )
    out.append("=" * 118)
    out.append("")
    out.append(
        "Setup: daily bars; entry when winning side's score ≥ "
        f"{FREE_RANGE_MIN_SCORE}; exit on Stoch trigger or max_hold_bars."
    )
    out.append(
        "v2 halves MA stack contribution (max +15 vs +30) and softens chop "
        "from -25 to -10 — Stoch + SQN unchanged."
    )
    out.append(
        "v3 keeps v1 MA scoring intact and adds a price-action breakout "
        "component (+20 when close pierces the prior 5-bar swing high/low)."
    )
    out.append("")

    by_ticker: dict[str, list[BacktestResult]] = {}
    for r in results:
        by_ticker.setdefault(r.ticker, []).append(r)

    header = (
        f"{'Ticker':>7} {'Ver':>4} {'Sample':>23} {'N':>4} {'Win%':>5} "
        f"{'AvgRet%':>8} {'Total%':>9} {'CAGR%':>7} {'MaxDD%':>7} "
        f"{'Sharpe':>7} {'AvgHold':>8} {'BHRet%':>8} {'BHCAGR%':>8}"
    )
    out.append(header)
    for tk in sorted(by_ticker):
        for r in sorted(by_ticker[tk], key=lambda x: x.scoring_version):
            out.append(
                f"{r.ticker:>7} {r.scoring_version:>4} "
                f"{r.sample_start} → {r.sample_end} "
                f"{r.n_trades:>4d} {r.win_rate:>4.1f} "
                f"{r.avg_return_pct:>+7.2f} {r.total_return_pct:>+8.1f} "
                f"{r.cagr_pct:>+6.2f} {r.max_drawdown_pct:>+6.1f} "
                f"{r.sharpe:>+6.2f} {r.avg_holding_days:>7.1f} "
                f"{r.buy_hold_return_pct:>+7.1f} {r.buy_hold_cagr_pct:>+7.2f}"
            )
        out.append("")

    out.append("Regime decomposition (entry SQN(100) regime → trade returns):")
    for tk in sorted(by_ticker):
        out.append(f"  {tk}:")
        for r in sorted(by_ticker[tk], key=lambda x: x.scoring_version):
            out.append(f"    {r.scoring_version}:")
            for regime in (
                "strong_bull", "bull", "neutral", "bear", "strong_bear", "unknown",
            ):
                stats = r.regime_breakdown.get(regime)
                if stats:
                    out.append(
                        f"      {regime:>13}  n={int(stats['n']):>3d}  "
                        f"avg={stats['avg_return_pct']:>+6.2f}%  "
                        f"win%={stats['win_rate_pct']:>5.1f}"
                    )
        out.append("")

    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--tickers", default="QQQ,GLD",
        help="Comma-separated tickers (default QQQ,GLD — the dashboard anchor)",
    )
    ap.add_argument(
        "--versions", default="v1,v2",
        help="Comma-separated scoring versions to compare (default v1,v2)",
    )
    ap.add_argument(
        "--max-hold-bars", type=int, default=21,
        help="Max trade hold in trading days (default 21, matching the "
        "free-range Tier 2 hold band)",
    )
    ap.add_argument("--json", type=Path, help="Optional JSON output path")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    versions = [v.strip() for v in args.versions.split(",") if v.strip()]
    for v in versions:
        if v not in ("v1", "v2", "v3"):
            print(
                f"Unknown scoring version '{v}' — must be v1, v2, or v3",
                file=sys.stderr,
            )
            return 2

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
    for v in versions:
        for tk, d in bars_by_ticker.items():
            print(f"Running scoring={v} on {tk}...")
            r = run_one(tk, d, v, max_hold_bars=args.max_hold_bars)  # type: ignore[arg-type]
            results.append(r)

    print()
    text = render(results)
    print(text)

    if args.json:
        args.json.write_text(
            json.dumps(
                {"results": [asdict(r) for r in results]},
                indent=2, default=str,
            )
        )
        print(f"\nWrote JSON to: {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
