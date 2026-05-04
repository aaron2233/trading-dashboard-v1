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

from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from crypto.scanner import (
    CryptoTimeframeRead,
    classify_crypto_confluence,
    scan_crypto_setup,
)
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


# ─── Confluence classification (trading-edge cross-TF matrix) ─────────────────


def _read(tf: str = "1d", stack: str | None = None,
          k: float | None = None, d: float | None = None,
          signal: str | None = None, regime: str | None = None,
          error: str | None = None) -> CryptoTimeframeRead:
    return CryptoTimeframeRead(
        timeframe=tf, error=error, bar_date="2026-05-13", close=100.0,
        ma_stack_state=stack, stoch_k=k, stoch_d=d,
        stoch_zone=None, stoch_signal=signal,
        sqn_regime=regime, sqn_value=1.0,
    )


def test_confluence_daily_chop_skip():
    daily = _read("1d", stack="chop")
    weekly = _read("1wk", stack="full_bull")
    four = _read("4h", stack="full_bull")
    two = _read("2h", signal="bull_cross_oversold")
    c, dirn, _, _ = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "skip_chop"
    assert dirn == "none"


def test_confluence_daily_unavailable_skip():
    daily = _read("1d", error="yfinance dead")
    weekly = _read("1wk", stack="full_bull")
    four = _read("4h", stack="full_bull")
    two = _read("2h", signal="bull_cross_oversold")
    c, _, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "skip_chop"
    assert any("unusable" in b for b in blockers)


def test_confluence_high_conviction_long():
    weekly = _read("1wk", stack="full_bull")
    daily  = _read("1d",  stack="full_bull")
    four   = _read("4h",  stack="full_bull")
    two    = _read("2h",  signal="bull_cross_oversold", k=25, d=22)
    c, dirn, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "high_conviction_long"
    assert dirn == "long"
    assert blockers == []


def test_confluence_high_conviction_short():
    weekly = _read("1wk", stack="full_bear")
    daily  = _read("1d",  stack="full_bear")
    four   = _read("4h",  stack="full_bear")
    two    = _read("2h",  signal="bear_cross_overbought", k=75, d=78)
    c, dirn, _, _ = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "high_conviction_short"
    assert dirn == "short"


def test_confluence_counter_weekly_flag():
    weekly = _read("1wk", stack="full_bear")
    daily  = _read("1d",  stack="full_bull")
    four   = _read("4h",  stack="full_bull")
    two    = _read("2h",  signal="bull_cross_oversold", k=25, d=22)
    c, dirn, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "counter_weekly"
    assert dirn == "long"
    assert any("Weekly full_bear opposes" in b for b in blockers)


def test_confluence_wait_when_no_trigger():
    weekly = _read("1wk", stack="full_bull")
    daily  = _read("1d",  stack="full_bull")
    four   = _read("4h",  stack="full_bull")
    two    = _read("2h",  k=30, d=40, signal=None)
    c, _, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "wait"
    assert any("hasn't fired" in b for b in blockers)


def test_confluence_4h_opposes_skips_as_trap():
    weekly = _read("1wk", stack="full_bull")
    daily  = _read("1d",  stack="full_bull")
    four   = _read("4h",  stack="full_bear")
    two    = _read("2h",  signal="bull_cross_oversold", k=25, d=22)
    c, _, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "skip_no_setup"
    assert any("mean-reversion trap" in b for b in blockers)


def test_confluence_4h_chop_requires_clarification():
    weekly = _read("1wk", stack="full_bull")
    daily  = _read("1d",  stack="full_bull")
    four   = _read("4h",  stack="chop")
    two    = _read("2h",  signal="bull_cross_oversold", k=25, d=22)
    c, _, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "wait"
    assert any("4H" in b and "clarification" in b for b in blockers)


def test_confluence_developing_stack_medium_conviction():
    weekly = _read("1wk", stack="bull_developing")
    daily  = _read("1d",  stack="bull_developing")
    four   = _read("4h",  stack="bull_developing")
    two    = _read("2h",  signal="bull_cross_oversold", k=25, d=22)
    c, dirn, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "medium_conviction_long"
    assert dirn == "long"
    assert any("1-2% size" in b for b in blockers)


def test_confluence_2h_unusable_falls_back_to_4h():
    weekly = _read("1wk", stack="full_bull")
    daily  = _read("1d",  stack="full_bull")
    four   = _read("4h",  stack="full_bull",
                   signal="bull_cross_oversold", k=25, d=22)
    two    = _read("2h",  error="2H data unavailable")
    c, _, _, blockers = classify_crypto_confluence(weekly, daily, four, two)
    assert c == "high_conviction_long"
    assert any("4H Stoch as fallback" in b for b in blockers)


# ─── scan_crypto_setup wiring ────────────────────────────────────────────────


def _scan_row(stack: str = "full_bull", signal: str | None = None,
              k: float = 50, d: float = 50) -> dict[str, Any]:
    return {
        "ticker": "BTC_USDT", "timeframe": "1d", "bar_date": "2026-05-13",
        "close": 47000.0,
        "ma_ribbon": {"stack_state": stack, "ma_10": 46000, "ma_20": 45000,
                      "ma_50": 44000, "ma_200": 40000},
        "stochastic": {"k": k, "d": d, "zone": None, "signal": signal},
        "sqn": {"sqn_value": 1.0, "regime": "bull",
                "sqn_20_value": 0.5, "regime_20": "bull", "diagnostic": "ok"},
    }


def _ok_ticker(symbol: str) -> dict[str, Any]:
    return {
        "instrument_name": symbol, "last_price": 47500.0,
        "bid": 47499.0, "ask": 47501.0, "change_24h_pct": 0.025,
        "high_24h": 48000.0, "low_24h": 46500.0,
        "volume_24h": 12345.6, "source_timestamp_ms": 1746000000000,
    }


def test_scan_crypto_setup_assembles_full_read():
    def fake_scan(symbol: str, tf: str) -> dict[str, Any]:
        return _scan_row(
            stack="full_bull",
            signal="bull_cross_oversold" if tf == "2h" else None,
            k=25 if tf == "2h" else 50, d=22 if tf == "2h" else 50,
        )
    setup = scan_crypto_setup("BTC_USDT", scan_fn=fake_scan, ticker_fn=_ok_ticker)
    assert setup.confluence == "high_conviction_long"
    assert setup.direction == "long"
    assert setup.ticker.last_price == 47500.0
    assert set(setup.reads.keys()) == {"1wk", "1d", "4h", "2h"}


def test_scan_crypto_setup_handles_ticker_failure_gracefully():
    def bad_ticker(symbol: str) -> dict[str, Any]:
        raise CryptoFetchError("ticker endpoint down")
    setup = scan_crypto_setup(
        "BTC_USDT",
        scan_fn=lambda s, tf: _scan_row(stack="full_bull"),
        ticker_fn=bad_ticker,
    )
    assert setup.ticker is None
    assert any("Ticker fetch failed" in n for n in setup.notes)


def test_scan_crypto_setup_handles_per_tf_failures():
    def fake_scan(symbol: str, tf: str) -> dict[str, Any]:
        if tf == "2h":
            raise RuntimeError("2H bars unavailable")
        return _scan_row(stack="full_bull")
    setup = scan_crypto_setup("BTC_USDT", scan_fn=fake_scan, ticker_fn=_ok_ticker)
    assert setup.reads["2h"].error == "2H bars unavailable"


def test_scan_crypto_setup_serialises_to_dict():
    def fake_scan(symbol: str, tf: str) -> dict[str, Any]:
        return _scan_row(stack="full_bull",
                         signal="bull_cross_oversold" if tf == "2h" else None)
    setup = scan_crypto_setup("BTC_USDT", scan_fn=fake_scan, ticker_fn=_ok_ticker)
    d = setup.to_dict()
    assert d["symbol"] == "BTC_USDT"
    assert d["ticker"]["last_price"] == 47500.0
    assert "confluence" in d


# ─── API integration ─────────────────────────────────────────────────────────


def test_api_crypto_scan_endpoint(monkeypatch):
    def fake_scan_ticker(ticker, period=None, timeframe="1d"):
        return _scan_row(
            stack="full_bull",
            signal="bull_cross_oversold" if timeframe == "2h" else None,
            k=25 if timeframe == "2h" else 50,
            d=22 if timeframe == "2h" else 50,
        )
    monkeypatch.setattr("scan.scan_ticker", fake_scan_ticker)
    monkeypatch.setattr("data.crypto_loader.fetch_ticker", _ok_ticker)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/crypto/scan/BTC_USDT")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["symbol"] == "BTC_USDT"
    assert body["confluence"] == "high_conviction_long"
    assert body["ticker"]["last_price"] == 47500.0
    assert set(body["reads"].keys()) == {"1wk", "1d", "4h", "2h"}


def test_api_crypto_scan_rejects_non_underscore_symbol():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/crypto/scan/AAPL")
    assert resp.status_code == 400
    assert "underscore form" in resp.json()["detail"]


def test_api_crypto_instruments_endpoint(monkeypatch):
    def fake_instruments(*args, **kwargs):
        return [
            {"instrument_name": "BTC_USDT", "base_ccy": "BTC", "quote_ccy": "USDT"},
            {"instrument_name": "ETH_USDT", "base_ccy": "ETH", "quote_ccy": "USDT"},
        ]
    monkeypatch.setattr("api.app.fetch_instruments", fake_instruments)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/crypto/instruments")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "BTC_USDT" in body["common"]
    assert {i["instrument_name"] for i in body["instruments"]} == {"BTC_USDT", "ETH_USDT"}


def test_api_crypto_instruments_degrades_when_fetch_fails(monkeypatch):
    def boom(*args, **kwargs):
        raise CryptoFetchError("crypto.com down")
    monkeypatch.setattr("api.app.fetch_instruments", boom)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/crypto/instruments")
    assert resp.status_code == 200
    body = resp.json()
    assert "BTC_USDT" in body["common"]
    assert body["instruments"] == []
