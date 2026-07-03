"""Backtest: what does buying QQQ / SPY into a Stochastic(14,7,7) oversold reading yield?

Reuses the PRODUCTION Stochastic indicator (src/indicators/stochastic.py) and the
yfinance loader, so the math matches what the live skills fire on.

Approach
--------
- Load max-history DAILY adjusted bars for each ticker once.
- Build three timeframes from that one source: daily, weekly (W-FRI), monthly (ME).
  Higher TFs are true OHLC aggregations of the adjusted daily series (same way
  TradingView builds a weekly/monthly bar from the underlying data).
- Compute the production Stochastic on each timeframe.
- Two signal definitions (both oversold flavors):
    OS-zone  : bars where K < 20 (zone == 'oversold')  -> "while it READS oversold"
    Reversal : signal == 'bull_cross_oversold' (K crosses above D while K < 30)
               -> the disciplined "momentum turning up FROM oversold" entry
- Forward total return at several horizons (in that TF's own bars), compared
  against the UNCONDITIONAL baseline (every bar). The baseline matters: QQQ/SPY
  trend up secularly, so any buy point is positive long-horizon. Edge = whether
  oversold beats just-buying.

Usage (from repo root):
    PYTHONPATH=src python3 scripts/stoch_oversold_backtest.py
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
from indicators.stochastic import Stochastic  # noqa: E402

warnings.filterwarnings("ignore")

TICKERS = ["SPY", "QQQ"]

# horizons are in BARS of the given timeframe
HORIZONS = {
    "daily":   [5, 10, 21, 63, 126, 252],   # ~1w,2w,1m,3m,6m,12m
    "weekly":  [1, 4, 8, 13, 26, 52],        # 1w,1m,2m,3m,6m,12m
    "monthly": [1, 3, 6, 12],                # 1m,3m,6m,12m
}
HORIZON_LABEL = {
    ("daily", 5): "1w", ("daily", 10): "2w", ("daily", 21): "1mo",
    ("daily", 63): "3mo", ("daily", 126): "6mo", ("daily", 252): "12mo",
    ("weekly", 1): "1w", ("weekly", 4): "1mo", ("weekly", 8): "2mo",
    ("weekly", 13): "3mo", ("weekly", 26): "6mo", ("weekly", 52): "12mo",
    ("monthly", 1): "1mo", ("monthly", 3): "3mo", ("monthly", 6): "6mo",
    ("monthly", 12): "12mo",
}
RESAMPLE_RULE = {"weekly": "W-FRI", "monthly": "ME"}

STOCH = Stochastic()


def resample_ohlc(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": daily["open"].resample(rule).first(),
            "high": daily["high"].resample(rule).max(),
            "low": daily["low"].resample(rule).min(),
            "close": daily["close"].resample(rule).last(),
        }
    ).dropna()


def fwd_returns(close: pd.Series, horizons: list[int]) -> dict[int, pd.Series]:
    """Positional forward total return: close[t+h]/close[t]-1 (TF-native bars)."""
    return {h: close.shift(-h) / close - 1.0 for h in horizons}


def stats(fr: pd.Series, mask: pd.Series | None) -> dict:
    s = fr if mask is None else fr[mask]
    s = s.dropna()
    if len(s) == 0:
        return {"n": 0, "mean": np.nan, "med": np.nan, "pos": np.nan}
    return {
        "n": len(s),
        "mean": s.mean() * 100,
        "med": s.median() * 100,
        "pos": (s > 0).mean() * 100,
    }


def pct(x: float) -> str:
    return "   n/a" if (x is None or np.isnan(x)) else f"{x:+6.1f}"


def run_tf(ticker: str, tf: str, bars: pd.DataFrame) -> None:
    st = STOCH.compute(bars)
    close = bars["close"]
    k = st["k"]
    zone = st["zone"]
    sig = st["signal"]

    os_zone = zone == "oversold"                     # K < 20, every such bar
    os_entry = os_zone & ~os_zone.shift(1, fill_value=False)  # first bar into oversold
    reversal = sig == "bull_cross_oversold"          # K crosses above D, K < 30

    fr = fwd_returns(close, HORIZONS[tf])

    n_bars = int(st["k"].notna().sum())
    print(f"\n  --- {tf.upper()}  ({n_bars} bars with valid stoch) ---")
    print(
        f"  oversold-zone bars: {int(os_zone.sum()):>4}   "
        f"oversold-entry events: {int(os_entry.sum()):>4}   "
        f"bull_cross_oversold signals: {int(reversal.sum()):>4}"
    )

    header = (
        f"  {'horizon':<8}"
        f"{'BASE n':>8}{'mean%':>8}{'pos%':>7}   "
        f"{'OSzone n':>9}{'mean%':>8}{'med%':>8}{'pos%':>7}{'Δmean':>8}{'Δpos':>7}   "
        f"{'REV n':>6}{'mean%':>8}{'med%':>8}{'pos%':>7}{'Δmean':>8}{'Δpos':>7}"
    )
    print(header)
    for h in HORIZONS[tf]:
        base = stats(fr[h], None)
        osz = stats(fr[h], os_zone)
        rev = stats(fr[h], reversal)
        lbl = HORIZON_LABEL[(tf, h)]
        dmean_os = osz["mean"] - base["mean"] if osz["n"] else np.nan
        dpos_os = osz["pos"] - base["pos"] if osz["n"] else np.nan
        dmean_rev = rev["mean"] - base["mean"] if rev["n"] else np.nan
        dpos_rev = rev["pos"] - base["pos"] if rev["n"] else np.nan
        print(
            f"  {lbl:<8}"
            f"{base['n']:>8}{pct(base['mean'])}{pct(base['pos'])}   "
            f"{osz['n']:>9}{pct(osz['mean'])}{pct(osz['med'])}{pct(osz['pos'])}{pct(dmean_os)}{pct(dpos_os)}   "
            f"{rev['n']:>6}{pct(rev['mean'])}{pct(rev['med'])}{pct(rev['pos'])}{pct(dmean_rev)}{pct(dpos_rev)}"
        )


def main() -> None:
    for ticker in TICKERS:
        daily = load_bars(ticker, period="max", interval="1d", auto_adjust=True)
        cov = f"{daily.index[0].date()} -> {daily.index[-1].date()}  ({len(daily)} daily bars)"
        print("\n" + "=" * 120)
        print(f"{ticker}   coverage: {cov}")
        print("=" * 120)

        frames = {
            "daily": daily,
            "weekly": resample_ohlc(daily, RESAMPLE_RULE["weekly"]),
            "monthly": resample_ohlc(daily, RESAMPLE_RULE["monthly"]),
        }
        for tf in ("daily", "weekly", "monthly"):
            run_tf(ticker, tf, frames[tf])

    print(
        "\nLegend: BASE = every bar (unconditional). OSzone = bars with K<20. "
        "REV = bull_cross_oversold (K crosses above D while K<30). "
        "Δmean/Δpos = signal minus baseline. Returns are forward total return "
        "(auto-adjusted) over N bars of that timeframe.\n"
    )


if __name__ == "__main__":
    main()
