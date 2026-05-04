from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from data.yfinance_loader import load_bars
from indicators import IndicatorProtocol
from indicators.ma_ribbon import MARibbon
from testing.accuracy_harness import check_accuracy, load_truth_csv


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "truth"
TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "GLD"]


def _bars(close_values: list[float], start: str = "2025-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(close_values))
    close = pd.Series(close_values, index=dates, name="close")
    return pd.DataFrame({
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000,
    })


def test_ma_ribbon_satisfies_protocol():
    ind = MARibbon()
    assert isinstance(ind, IndicatorProtocol)
    assert ind.name == "ma_ribbon"
    assert "close" in ind.inputs


def test_sma_values_match_manual_calculation():
    closes = [float(i) for i in range(1, 251)]  # 1..250
    bars = _bars(closes)
    out = MARibbon().compute(bars)

    # SMA at last bar: 10-period avg of [241..250] = 245.5
    assert out["ma_10"].iloc[-1] == pytest.approx(245.5)
    # 20-period avg of [231..250] = 240.5
    assert out["ma_20"].iloc[-1] == pytest.approx(240.5)
    # 50-period avg of [201..250] = 225.5
    assert out["ma_50"].iloc[-1] == pytest.approx(225.5)
    # 200-period avg of [51..250] = 150.5
    assert out["ma_200"].iloc[-1] == pytest.approx(150.5)


def test_constant_uptrend_classifies_full_bull():
    closes = [100.0 + i * 0.5 for i in range(250)]
    bars = _bars(closes)
    out = MARibbon().compute(bars)

    tail_state = out["stack_state"].iloc[-1]
    assert tail_state == "full_bull"


def test_constant_downtrend_classifies_full_bear():
    closes = [200.0 - i * 0.5 for i in range(250)]
    bars = _bars(closes)
    out = MARibbon().compute(bars)

    tail_state = out["stack_state"].iloc[-1]
    assert tail_state == "full_bear"


def test_flat_price_classifies_compression():
    closes = [100.0] * 250
    bars = _bars(closes)
    out = MARibbon().compute(bars)

    tail_state = out["stack_state"].iloc[-1]
    assert tail_state == "compression"


def test_pre_warmup_rows_are_nan_state():
    closes = [100.0 + i for i in range(250)]
    bars = _bars(closes)
    out = MARibbon().compute(bars)

    # First 199 rows should have NaN ma_200 and NaN stack_state
    assert pd.isna(out["ma_200"].iloc[100])
    assert pd.isna(out["stack_state"].iloc[100])


def test_returns_expected_columns():
    closes = [100.0 + i for i in range(250)]
    out = MARibbon().compute(_bars(closes))
    assert list(out.columns) == ["ma_10", "ma_20", "ma_50", "ma_200", "stack_state"]


def test_chop_when_mixed_signals():
    rng = np.random.default_rng(seed=42)
    closes = (100.0 + rng.normal(0, 2, size=250).cumsum()).tolist()
    bars = _bars(closes)
    out = MARibbon().compute(bars)
    # Random walk should produce at least some chop periods
    post_warmup = out["stack_state"].iloc[200:].dropna()
    assert len(post_warmup) > 0
    # Should not ALL be full_bull or full_bear
    assert not (post_warmup == "full_bull").all()
    assert not (post_warmup == "full_bear").all()


@pytest.mark.parametrize("ticker", TICKERS)
def test_ma_ribbon_accuracy_against_tradingview(ticker):
    fixture = FIXTURE_DIR / f"{ticker}_ma_ribbon.csv"
    if not fixture.exists():
        pytest.skip(f"No fixture yet — populate {fixture.name} from TradingView")

    expected = load_truth_csv(fixture)
    if expected.empty:
        pytest.skip(f"{fixture.name} has no data rows — paste TradingView values per fixtures/truth/README.md")
    if "stack_state" in expected.columns and expected["stack_state"].isna().all():
        pytest.skip(
            f"{fixture.name} has numerics drafted but stack_state not yet labeled — "
            "fill from TradingView and rerun"
        )
    bars = load_bars(ticker, period="2y")
    actual = MARibbon().compute(bars)

    result = check_accuracy(
        expected,
        actual,
        numeric_tolerance=0.01,
        categorical_columns=["stack_state"],
    )
    assert result.ok(0.95), result.report()
