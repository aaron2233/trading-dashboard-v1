"""Tests for multi-timeframe data loading + kill sheet integration."""
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from data.yfinance_loader import _resample, load_bars
from kill_sheet.multi_tf import extract_tf, pullback_status, weekly_alignment


# ─── Resample helper ──────────────────────────────────────────────────────────


def _hourly(prices: list[float], start: str = "2026-04-21 09:30") -> pd.DataFrame:
    idx = pd.date_range(start=start, periods=len(prices), freq="1h")
    return pd.DataFrame(
        {
            "open": prices,
            "high": [p + 0.5 for p in prices],
            "low": [p - 0.5 for p in prices],
            "close": prices,
            "volume": 100_000,
        },
        index=idx,
    )


def test_resample_4h_aggregates_correctly():
    bars = _hourly([100, 101, 102, 103, 104, 105, 106, 107])
    out = _resample(bars, "4h")
    assert len(out) == 2
    # First 4h bucket: bars 0-3
    assert out["open"].iloc[0] == 100
    assert out["high"].iloc[0] == 103.5
    assert out["low"].iloc[0] == 99.5
    assert out["close"].iloc[0] == 103
    assert out["volume"].iloc[0] == 400_000


def test_resample_drops_empty_buckets():
    bars = _hourly([100.0])  # only one hourly bar
    out = _resample(bars, "4h")
    assert len(out) == 1
    assert out["close"].iloc[0] == 100.0


def test_resample_empty_input_returns_empty():
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    out = _resample(empty, "4h")
    assert out.empty


# ─── load_bars dispatch ───────────────────────────────────────────────────────


@patch("data.yfinance_loader._load_native")
def test_load_bars_4h_fetches_1h_and_resamples(mock_native):
    mock_native.return_value = _hourly([100, 101, 102, 103, 104, 105, 106, 107])
    out = load_bars("FAKE", interval="4h")
    args, kwargs = mock_native.call_args
    assert kwargs["interval"] == "1h"
    assert kwargs["period"] == "730d"  # default for 1h base
    assert len(out) == 2  # resampled to 4h


@patch("data.yfinance_loader._load_native")
def test_load_bars_weekly_uses_native_call(mock_native):
    mock_native.return_value = pd.DataFrame(
        {"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]},
        index=pd.to_datetime(["2026-01-05"]),
    )
    load_bars("FAKE", interval="1wk")
    _, kwargs = mock_native.call_args
    assert kwargs["interval"] == "1wk"
    assert kwargs["period"] == "10y"  # default for weekly


@patch("data.yfinance_loader._load_native")
def test_load_bars_explicit_period_overrides_default(mock_native):
    mock_native.return_value = pd.DataFrame(
        {"open": [1], "high": [1], "low": [1], "close": [1], "volume": [1]},
        index=pd.to_datetime(["2026-01-05"]),
    )
    load_bars("FAKE", period="5y", interval="1wk")
    _, kwargs = mock_native.call_args
    assert kwargs["period"] == "5y"


# ─── multi_tf helpers ─────────────────────────────────────────────────────────


def test_weekly_alignment_long_with_bull_stack():
    assert weekly_alignment("full_bull", "long") == "With trade"
    assert weekly_alignment("bull_developing", "long") == "With trade"


def test_weekly_alignment_long_against_bear_is_counter_trend():
    assert weekly_alignment("full_bear", "long") == "Counter-trend"


def test_weekly_alignment_chop_or_compression_is_neutral():
    assert weekly_alignment("chop", "long") == "Neutral"
    assert weekly_alignment("compression", "short") == "Neutral"
    assert weekly_alignment(None, "long") == "Neutral"


def test_pullback_status_above():
    assert pullback_status(105.0, 100.0) == "Price above 20 MA"


def test_pullback_status_below():
    assert pullback_status(95.0, 100.0) == "Price below 20 MA"


def test_pullback_status_at_within_threshold():
    assert pullback_status(100.3, 100.0) == "Price at 20 MA"


def test_pullback_status_handles_none():
    assert pullback_status(None, 100.0) is None
    assert pullback_status(100.0, None) is None
    assert pullback_status(100.0, 0.0) is None


def test_extract_tf_returns_row_when_present():
    multi = {"1wk": {"ticker": "SPY", "ma_ribbon": {"stack_state": "full_bull"}}}
    out = extract_tf(multi, "1wk")
    assert out is not None
    assert out["ma_ribbon"]["stack_state"] == "full_bull"


def test_extract_tf_returns_none_for_error_row():
    multi = {"1wk": {"ticker": "SPY", "error": "no data"}}
    assert extract_tf(multi, "1wk") is None


def test_extract_tf_returns_none_for_missing_key():
    assert extract_tf({}, "1wk") is None
