"""Tests for crypto data loader + scan routing."""
from unittest.mock import patch

import pandas as pd
import pytest

from data.crypto_loader import (
    CryptoFetchError,
    _bars_from_payload,
    is_crypto_symbol,
    load_crypto_bars,
)


# ─── Symbol detection ─────────────────────────────────────────────────────────


def test_is_crypto_symbol_underscore():
    assert is_crypto_symbol("BTC_USDT")
    assert is_crypto_symbol("ETH_USD")


def test_is_crypto_symbol_no_underscore():
    assert not is_crypto_symbol("SPY")
    assert not is_crypto_symbol("AAPL")
    assert not is_crypto_symbol("BRK.A")


# ─── Payload parsing ──────────────────────────────────────────────────────────


_DEFAULT_ROWS = [
    {"t": 1700000000000, "o": "47000", "h": "48000",
     "l": "46500", "c": "47500", "v": "1234.5"},
    {"t": 1700086400000, "o": "47500", "h": "48500",
     "l": "47000", "c": "48000", "v": "987.6"},
]


def _good_payload(rows=None):
    if rows is None:
        rows = _DEFAULT_ROWS
    return {
        "code": 0,
        "result": {
            "instrument_name": "BTC_USDT",
            "interval": "1D",
            "data": rows,
        },
    }


def test_parse_good_payload():
    df = _bars_from_payload(_good_payload())
    assert len(df) == 2
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df["close"].iloc[0] == 47500.0
    assert df["close"].iloc[1] == 48000.0
    # Index is sorted ascending
    assert df.index[0] < df.index[1]


def test_parse_empty_data_raises():
    payload = _good_payload(rows=[])
    with pytest.raises(CryptoFetchError, match="empty"):
        _bars_from_payload(payload)


def test_parse_error_code_raises():
    with pytest.raises(CryptoFetchError, match="error code"):
        _bars_from_payload({"code": 1001, "message": "bad request"})


def test_parse_missing_result_raises():
    with pytest.raises(CryptoFetchError, match="missing 'result'"):
        _bars_from_payload({"code": 0})


def test_parse_malformed_row_raises():
    payload = _good_payload(rows=[
        {"t": 1, "o": "47000", "h": "x"},  # missing l, c, v + bad h
    ])
    with pytest.raises(CryptoFetchError, match="Malformed"):
        _bars_from_payload(payload)


def test_parse_non_object_raises():
    with pytest.raises(CryptoFetchError, match="not an object"):
        _bars_from_payload([])  # type: ignore[arg-type]


# ─── load_crypto_bars ─────────────────────────────────────────────────────────


def test_load_crypto_bars_happy_path():
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _good_payload()

    df = load_crypto_bars("BTC_USDT", timeframe="1d", count=300, fetch=fake_fetch)
    assert len(df) == 2
    # Verify URL has correct params
    assert "instrument_name=BTC_USDT" in captured["url"]
    assert "timeframe=1D" in captured["url"]
    assert "count=300" in captured["url"]


def test_load_crypto_bars_uppercases_symbol():
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _good_payload()

    load_crypto_bars("btc_usdt", fetch=fake_fetch)
    assert "instrument_name=BTC_USDT" in captured["url"]


def test_load_crypto_bars_rejects_equity_symbol():
    with pytest.raises(CryptoFetchError, match="underscore"):
        load_crypto_bars("SPY", fetch=lambda url: _good_payload())


def test_load_crypto_bars_rejects_invalid_count():
    with pytest.raises(CryptoFetchError, match="count"):
        load_crypto_bars("BTC_USDT", count=400, fetch=lambda url: _good_payload())
    with pytest.raises(CryptoFetchError, match="count"):
        load_crypto_bars("BTC_USDT", count=0, fetch=lambda url: _good_payload())


def test_load_crypto_bars_rejects_unsupported_timeframe():
    with pytest.raises(CryptoFetchError, match="Unsupported"):
        load_crypto_bars("BTC_USDT", timeframe="3d",
                         fetch=lambda url: _good_payload())


def test_timeframe_mapping():
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _good_payload()

    load_crypto_bars("BTC_USDT", timeframe="1wk", fetch=fake_fetch)
    assert "timeframe=7D" in captured["url"]

    load_crypto_bars("BTC_USDT", timeframe="4h", fetch=fake_fetch)
    assert "timeframe=4h" in captured["url"]


# ─── scan_ticker routing ──────────────────────────────────────────────────────


@patch("scan.load_crypto_bars")
@patch("scan.load_bars")
def test_scan_routes_equity_to_yfinance(mock_yf, mock_crypto):
    # Need full 200+ bars for indicators to compute; use a synthetic series
    bars = pd.DataFrame({
        "open": [100.0 + i * 0.1 for i in range(250)],
        "high": [100.5 + i * 0.1 for i in range(250)],
        "low": [99.5 + i * 0.1 for i in range(250)],
        "close": [100.0 + i * 0.1 for i in range(250)],
        "volume": 1_000_000,
    }, index=pd.bdate_range("2024-01-02", periods=250))
    mock_yf.return_value = bars

    from scan import scan_ticker
    scan_ticker("SPY")
    assert mock_yf.called
    assert not mock_crypto.called


@patch("scan.load_crypto_bars")
@patch("scan.load_bars")
def test_scan_routes_crypto_to_crypto_com(mock_yf, mock_crypto):
    bars = pd.DataFrame({
        "open": [47000.0 + i for i in range(250)],
        "high": [47100.0 + i for i in range(250)],
        "low": [46900.0 + i for i in range(250)],
        "close": [47000.0 + i for i in range(250)],
        "volume": 1234.5,
    }, index=pd.date_range("2024-01-02", periods=250, freq="D"))
    mock_crypto.return_value = bars

    from scan import scan_ticker
    result = scan_ticker("BTC_USDT")
    assert mock_crypto.called
    assert not mock_yf.called
    assert result["ticker"] == "BTC_USDT"
    # Indicators ran on crypto bars
    assert result["ma_ribbon"]["stack_state"] is not None


# ─── fetch_ticker (Module 9) ──────────────────────────────────────────────────

from data.crypto_loader import fetch_instruments, fetch_ticker


def _ticker_payload(**overrides) -> dict:
    base = {
        "code": 0,
        "result": {
            "data": [{
                "i": "BTC_USDT",
                "a": "47500.5", "b": "47499.0", "k": "47501.0",
                "c": "0.0234", "h": "48000.0", "l": "46500.0",
                "v": "12345.6", "t": 1746000000000,
            }],
        },
    }
    base.update(overrides)
    return base


def test_fetch_ticker_happy_path():
    captured: dict[str, str] = {}

    def fake_fetch(url: str) -> dict:
        captured["url"] = url
        return _ticker_payload()

    out = fetch_ticker("BTC_USDT", fetch=fake_fetch)
    assert "instrument_name=BTC_USDT" in captured["url"]
    assert out["last_price"] == 47500.5
    assert out["bid"] == 47499.0
    assert out["ask"] == 47501.0
    assert out["change_24h_pct"] == 0.0234
    assert out["volume_24h"] == 12345.6
    assert out["source_timestamp_ms"] == 1746000000000


def test_fetch_ticker_handles_missing_optional_fields():
    payload = {"code": 0, "result": {"data": [{"i": "ETH_USDT", "a": "3000.0"}]}}
    out = fetch_ticker("ETH_USDT", fetch=lambda url: payload)
    assert out["last_price"] == 3000.0
    assert out["bid"] is None
    assert out["change_24h_pct"] is None


def test_fetch_ticker_rejects_non_crypto_symbol():
    with pytest.raises(CryptoFetchError, match="doesn't look like"):
        fetch_ticker("AAPL", fetch=lambda url: _ticker_payload())


def test_fetch_ticker_raises_on_api_error_code():
    payload = {"code": 10001, "message": "rate limited"}
    with pytest.raises(CryptoFetchError, match="rate limited"):
        fetch_ticker("BTC_USDT", fetch=lambda url: payload)


def test_fetch_ticker_raises_on_empty_data():
    payload = {"code": 0, "result": {"data": []}}
    with pytest.raises(CryptoFetchError, match="No ticker data"):
        fetch_ticker("BTC_USDT", fetch=lambda url: payload)


# ─── fetch_instruments ────────────────────────────────────────────────────────


def _instruments_payload(rows: list[dict]) -> dict:
    return {"code": 0, "result": {"data": rows}}


def test_fetch_instruments_filters_to_usdt_usd_by_default():
    rows = [
        {"symbol": "BTC_USDT", "base_ccy": "BTC", "quote_ccy": "USDT"},
        {"symbol": "ETH_USDT", "base_ccy": "ETH", "quote_ccy": "USDT"},
        {"symbol": "ETH_BTC",  "base_ccy": "ETH", "quote_ccy": "BTC"},
        {"symbol": "BTC_USD",  "base_ccy": "BTC", "quote_ccy": "USD"},
        {"symbol": "FOO_BAR",  "base_ccy": "FOO", "quote_ccy": "BAR"},
    ]
    out = fetch_instruments(fetch=lambda url: _instruments_payload(rows))
    assert [i["instrument_name"] for i in out] == ["BTC_USD", "BTC_USDT", "ETH_USDT"]


def test_fetch_instruments_skips_malformed_entries():
    rows = [
        {"symbol": "BTC_USDT", "base_ccy": "BTC", "quote_ccy": "USDT"},
        {"symbol": ""},
        {"symbol": "NODELIM"},
        {},
    ]
    out = fetch_instruments(fetch=lambda url: _instruments_payload(rows))
    assert [i["instrument_name"] for i in out] == ["BTC_USDT"]


def test_fetch_instruments_no_filter_returns_all():
    rows = [
        {"symbol": "BTC_USDT", "base_ccy": "BTC", "quote_ccy": "USDT"},
        {"symbol": "ETH_BTC",  "base_ccy": "ETH", "quote_ccy": "BTC"},
    ]
    out = fetch_instruments(
        fetch=lambda url: _instruments_payload(rows), quote_filter=None,
    )
    assert len(out) == 2
