from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.yfinance_loader import load_bars
from indicators import IndicatorProtocol
from indicators.stochastic import Stochastic
from testing.accuracy_harness import check_accuracy, load_truth_csv


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "truth"
TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GLD"]


def _bars(highs: list[float], lows: list[float], closes: list[float],
          start: str = "2025-01-02") -> pd.DataFrame:
    assert len(highs) == len(lows) == len(closes)
    dates = pd.bdate_range(start=start, periods=len(closes))
    return pd.DataFrame(
        {
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": 1_000_000,
        },
        index=dates,
    )


def test_stochastic_satisfies_protocol():
    ind = Stochastic()
    assert isinstance(ind, IndicatorProtocol)
    assert ind.name == "stochastic"
    assert set(ind.inputs) == {"high", "low", "close"}


def test_k_at_top_of_range_yields_100():
    # 30 bars, price rising linearly, last bar is at the exact top of the 14-bar window
    closes = [100.0 + i for i in range(30)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))
    # K_raw at last bar: 100 * (close - LL14) / (HH14 - LL14)
    # LL14 of last 14 bars' lows: low at bar 30-14+1 = bar 17: close was 100+16=116, low=115.9
    # HH14: high at bar 30: close=129, high=129.1
    # close=129 → K_raw = 100 * (129 - 115.9) / (129.1 - 115.9) = 100 * 13.1/13.2 ≈ 99.24
    # After two 7-period SMAs on a monotone rise the value stays near the top.
    assert out["k"].iloc[-1] > 90.0


def test_k_at_bottom_of_range_yields_low():
    closes = [130.0 - i for i in range(30)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))
    assert out["k"].iloc[-1] < 10.0


def test_flat_price_yields_nan_range():
    # If HH==LL, K_raw is undefined (div by zero). We expect NaN.
    closes = [100.0] * 30
    highs = [100.0] * 30
    lows = [100.0] * 30
    out = Stochastic().compute(_bars(highs, lows, closes))
    assert pd.isna(out["k"].iloc[-1])
    assert pd.isna(out["signal"].iloc[-1])


def test_zone_classification():
    closes = list(np.linspace(100, 200, 60))
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))

    # After a long rise, K should be near 100 → overbought zone
    assert out["zone"].iloc[-1] == "overbought"

    # After reversing, K should drop
    closes_down = closes + list(np.linspace(200, 100, 60))
    highs_down = [c + 0.5 for c in closes_down]
    lows_down = [c - 0.5 for c in closes_down]
    out2 = Stochastic().compute(_bars(highs_down, lows_down, closes_down))
    assert out2["zone"].iloc[-1] == "oversold"


def test_returns_expected_columns():
    closes = [100.0 + i * 0.1 for i in range(50)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))
    assert list(out.columns) == ["k", "d", "zone", "signal"]


def test_warmup_rows_are_nan():
    closes = [100.0 + i * 0.1 for i in range(50)]
    highs = [c + 0.1 for c in closes]
    lows = [c - 0.1 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))
    # First K_raw at bar 13 (length-1), then 7-smooth starts at bar 19, then 7-smooth for D at bar 25
    # So first 25 rows should have NaN K/D.
    assert pd.isna(out["k"].iloc[10])
    assert pd.isna(out["d"].iloc[20])


def test_bullish_cross_from_oversold_detected():
    # Construct a V-bottom: price drops then rises sharply
    closes = [150.0 - i * 0.5 for i in range(30)] + [135.0 + i * 1.5 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))
    signals = out["signal"].dropna().tolist()
    assert "bull_cross_oversold" in signals


def test_bearish_cross_from_overbought_detected():
    # Inverted V: rise then fall
    closes = [100.0 + i * 0.5 for i in range(30)] + [115.0 - i * 1.5 for i in range(30)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    out = Stochastic().compute(_bars(highs, lows, closes))
    signals = out["signal"].dropna().tolist()
    assert "bear_cross_overbought" in signals


@pytest.mark.parametrize("ticker", TICKERS)
def test_stochastic_accuracy_against_tradingview(ticker):
    fixture = FIXTURE_DIR / f"{ticker}_stochastic.csv"
    if not fixture.exists():
        pytest.skip(f"No fixture yet — populate {fixture.name} from TradingView")

    expected = load_truth_csv(fixture)
    bars = load_bars(ticker, period="2y")
    actual = Stochastic().compute(bars)

    result = check_accuracy(
        expected,
        actual,
        numeric_tolerance=0.01,
        categorical_columns=["zone", "signal"],
    )
    assert result.ok(0.95), result.report()
