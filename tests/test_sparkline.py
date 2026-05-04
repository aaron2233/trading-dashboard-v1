"""Tests for GET /api/v1/sparkline/{ticker}."""
from __future__ import annotations

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.app import create_app


def _make_bars(n: int = 50, start_price: float = 100.0) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame for n daily bars."""
    dates = pd.date_range("2026-01-01", periods=n, freq="D")
    closes = [start_price + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "open": closes,
        "high": [c + 1 for c in closes],
        "low": [c - 1 for c in closes],
        "close": closes,
        "volume": [1_000_000] * n,
    }, index=dates)


def test_sparkline_returns_default_30_bars(monkeypatch):
    bars = _make_bars(50)
    monkeypatch.setattr("data.yfinance_loader.load_bars",
                        lambda ticker, interval="1d", **kw: bars)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/sparkline/AAPL")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["timeframe"] == "1d"
    assert len(body["closes"]) == 30  # default count
    assert len(body["dates"]) == 30
    # Most recent close from the synthetic series
    assert body["closes"][-1] == bars["close"].iloc[-1]


def test_sparkline_respects_count_param(monkeypatch):
    bars = _make_bars(100)
    monkeypatch.setattr("data.yfinance_loader.load_bars",
                        lambda ticker, interval="1d", **kw: bars)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/sparkline/AAPL?count=10")
    assert resp.status_code == 200
    assert len(resp.json()["closes"]) == 10


def test_sparkline_routes_crypto_symbols(monkeypatch):
    bars = _make_bars(50, start_price=47000.0)
    captured: dict[str, str] = {}

    def fake_crypto(symbol, timeframe="1d", count=300, **kw):
        captured["symbol"] = symbol
        captured["timeframe"] = timeframe
        return bars

    monkeypatch.setattr("data.crypto_loader.load_crypto_bars", fake_crypto)

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/sparkline/BTC_USDT?count=20")
    assert resp.status_code == 200
    assert captured["symbol"] == "BTC_USDT"
    body = resp.json()
    assert body["ticker"] == "BTC_USDT"
    assert len(body["closes"]) == 20


def test_sparkline_404_when_empty_bars(monkeypatch):
    monkeypatch.setattr("data.yfinance_loader.load_bars",
                        lambda ticker, interval="1d", **kw: pd.DataFrame())
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/sparkline/AAPL")
    assert resp.status_code == 404


def test_sparkline_502_on_loader_error(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("yfinance dead")
    monkeypatch.setattr("data.yfinance_loader.load_bars", boom)
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/v1/sparkline/AAPL")
    assert resp.status_code == 502
    assert "yfinance dead" in resp.json()["detail"]


def test_sparkline_count_validation():
    """count clamped to 5-300 per Query constraint."""
    app = create_app()
    client = TestClient(app)
    # Below floor → 422 from FastAPI validation
    resp = client.get("/api/v1/sparkline/AAPL?count=2")
    assert resp.status_code == 422
    # Above ceiling → 422
    resp = client.get("/api/v1/sparkline/AAPL?count=500")
    assert resp.status_code == 422
