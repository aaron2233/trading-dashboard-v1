"""Does the SQN(100) regime gate separate the stochastic-oversold edge on QQQ/SPY?

Companion to stoch_oversold_backtest.py. That run showed blindly buying
Stoch(14,7,7) oversold has no durable edge (it pools bull-pullbacks with
bear-knife-catches). This conditions each oversold signal on the SQN(100) regime
AT SIGNAL TIME and tests whether the gate cleanly separates the two.

Regime is the PRODUCTION daily SQN(100) (rule 1: daily macro gatekeeper), aligned
as-of each signal bar's date — so weekly oversold bars get the daily SQN(100)
reading as of that week's close (no lookahead; both close on the same bar).

Buckets (SQN_100_BANDS): BULL = bull+strong_bull, NEUT = neutral, BEAR = bear+strong_bear.
Rule cells tested:
  DIP-BUY (rule 12 zone)  : oversold & SQN100 in {bull,strong_bull,neutral}
  CAP-DIP (rule 12 exact) : DIP-BUY & SQN20 < -1.9   (capitulation reset inside non-bear)
  RULE-18 SKIP            : oversold & ( SQN100 strong_bear OR (bear & SQN20 < -1.9) )

Usage (from repo root):
    PYTHONPATH=src python3 scripts/stoch_oversold_sqn_conditioned.py
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
from indicators.sqn_regime import SQN_20_BANDS, SQN_100_BANDS, SQNRegime  # noqa: E402
from indicators.stochastic import Stochastic  # noqa: E402

warnings.filterwarnings("ignore")

TICKERS = ["SPY", "QQQ"]
STOCH = Stochastic()
SQN100 = SQNRegime(lookback=100, bands=SQN_100_BANDS)
SQN20 = SQNRegime(lookback=20, bands=SQN_20_BANDS)

HORIZONS = {
    "daily": [21, 63, 126, 252],   # 1m, 3m, 6m, 12m
    "weekly": [4, 13, 26, 52],     # 1m, 3m, 6m, 12m
}
HLABEL = {21: "1mo", 63: "3mo", 126: "6mo", 252: "12mo",
          4: "1mo", 13: "3mo", 26: "6mo", 52: "12mo"}


def resample_ohlc(daily: pd.DataFrame, rule: str) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": daily["open"].resample(rule).first(),
            "high": daily["high"].resample(rule).max(),
            "low": daily["low"].resample(rule).min(),
            "close": daily["close"].resample(rule).last(),
        }
    ).dropna()


def fwd(close: pd.Series, h: int) -> pd.Series:
    return close.shift(-h) / close - 1.0


def cell(fr: pd.Series, mask: pd.Series) -> tuple[int, float, float]:
    s = fr[mask].dropna()
    if len(s) == 0:
        return 0, np.nan, np.nan
    return len(s), s.mean() * 100, (s > 0).mean() * 100


def fmt(n: int, mean: float, pos: float) -> str:
    if n == 0:
        return f"{'-':>5}{'  n/a':>8}{'  n/a':>7}"
    return f"{n:>5}{mean:>+8.1f}{pos:>+7.1f}"


def run_ticker(ticker: str) -> None:
    daily = load_bars(ticker, period="max", interval="1d", auto_adjust=True)
    reg100_d = SQN100.compute(daily)["regime"]
    sqn20_d = SQN20.compute(daily)
    reg20_d, val20_d = sqn20_d["regime"], sqn20_d["sqn_value"]

    print("\n" + "=" * 104)
    print(f"{ticker}   {daily.index[0].date()} -> {daily.index[-1].date()}")
    print("=" * 104)

    for tf in ("daily", "weekly"):
        frame = daily if tf == "daily" else resample_ohlc(daily, "W-FRI")
        st = STOCH.compute(frame)
        osz = st["zone"] == "oversold"

        reg100 = reg100_d.reindex(frame.index, method="ffill")
        reg20 = reg20_d.reindex(frame.index, method="ffill")
        val20 = val20_d.reindex(frame.index, method="ffill")

        bull = reg100.isin(["bull", "strong_bull"])
        neut = reg100 == "neutral"
        bear = reg100.isin(["bear", "strong_bear"])

        masks = {
            "ALL (baseline)": pd.Series(True, index=frame.index),
            "oversold·BULL": osz & bull,
            "oversold·NEUT": osz & neut,
            "oversold·BEAR": osz & bear,
            "DIP-BUY (bull/neut)": osz & (bull | neut),
            "  +CAP-DIP (SQN20<-1.9)": osz & (bull | neut) & (val20 < -1.9),
            "RULE-18 SKIP": osz & ((reg100 == "strong_bear") | (bear & (val20 < -1.9))),
        }

        print(f"\n  --- {ticker} {tf.upper()} : oversold forward return by SQN(100) regime ---")
        hdr = f"  {'bucket':<24}" + "".join(
            f"{'│ ' + HLABEL[h] + '  n  mean% pos%':<22}" for h in HORIZONS[tf]
        )
        # simpler aligned header
        print(f"  {'bucket':<24}" + "".join(f"│{HLABEL[h]:>5}{'n':>5}{'mean%':>8}{'pos%':>7} " for h in HORIZONS[tf]))
        for name, m in masks.items():
            row = f"  {name:<24}"
            for h in HORIZONS[tf]:
                fr = fwd(frame["close"], h)
                n, mean, pos = cell(fr, m)
                row += "│" + " " * 5 + fmt(n, mean, pos) + " "
            print(row)


def main() -> None:
    for t in TICKERS:
        run_ticker(t)
    print(
        "\nDIP-BUY = oversold while SQN(100) Bull/Neutral (rule 12 zone). "
        "CAP-DIP = that + SQN(20)<-1.9 (rule 12 exact capitulation reset). "
        "RULE-18 SKIP = oversold while strong_bear, or bear+SQN(20)<-1.9 (the skip cell). "
        "Forward total return over N bars of that timeframe.\n"
    )


if __name__ == "__main__":
    main()
