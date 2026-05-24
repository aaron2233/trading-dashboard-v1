"""Tests for polygon_loader — mock REST responses, no network calls."""
from __future__ import annotations

import pandas as pd
import pytest

from data.polygon_loader import (
    PolygonFetchError,
    _bars_from_payload,
    _load_env_file,
    _period_to_dates,
    is_available,
    load_bars,
)


def _good_payload() -> dict:
    return {
        "ticker": "QQQ",
        "queryCount": 2,
        "resultsCount": 2,
        "results": [
            {
                "v": 1000.0, "o": 400.0, "h": 401.0, "l": 399.5,
                "c": 400.5, "t": 1700000000000, "n": 1000,
            },
            {
                "v": 1100.0, "o": 400.5, "h": 402.0, "l": 400.0,
                "c": 401.5, "t": 1700086400000, "n": 1100,
            },
        ],
        "status": "OK",
    }


def test_parse_good_payload():
    df = _bars_from_payload(_good_payload(), "QQQ")
    assert len(df) == 2
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["close"].iloc[-1] == 401.5
    assert df["volume"].iloc[0] == 1000.0


def test_parse_unauthorized_raises():
    with pytest.raises(PolygonFetchError, match="NOT_AUTHORIZED"):
        _bars_from_payload(
            {"status": "NOT_AUTHORIZED", "message": "upgrade plan"}, "QQQ"
        )


def test_parse_empty_results_raises():
    with pytest.raises(PolygonFetchError, match="no aggregates"):
        _bars_from_payload({"status": "OK", "results": []}, "QQQ")


def test_parse_malformed_row_raises():
    payload = {
        "status": "OK",
        "results": [{"o": 1.0, "h": 2.0}],  # missing l/c/v/t
    }
    with pytest.raises(PolygonFetchError, match="Malformed"):
        _bars_from_payload(payload, "QQQ")


def test_parse_unknown_status_raises():
    with pytest.raises(PolygonFetchError, match="status"):
        _bars_from_payload({"status": "ERROR", "error": "bad"}, "QQQ")


def test_period_to_dates_years():
    f, t = _period_to_dates("2y")
    assert f < t
    assert len(f) == 10  # YYYY-MM-DD


def test_period_to_dates_days():
    f, t = _period_to_dates("60d")
    assert f < t


def test_period_to_dates_months():
    f, t = _period_to_dates("3mo")
    assert f < t


def test_period_to_dates_max_uses_polygon_floor():
    f, t = _period_to_dates("max")
    assert f.startswith("2003")


def test_period_to_dates_bad_format():
    with pytest.raises(PolygonFetchError, match="Unsupported period"):
        _period_to_dates("3weeks")


def test_load_bars_with_injected_fetch(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test_key_xxx")
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _good_payload()

    df = load_bars("QQQ", period="1y", interval="1d", fetch=fake_fetch)
    assert len(df) == 2
    assert "/v2/aggs/ticker/QQQ/range/1/day/" in captured["url"]
    assert "adjusted=false" in captured["url"]
    assert "sort=desc" in captured["url"]
    assert "apiKey=test_key_xxx" in captured["url"]


def test_load_bars_auto_adjust_passes_through(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "k")
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        return _good_payload()

    load_bars("SPY", period="6mo", interval="1d", auto_adjust=True, fetch=fake_fetch)
    assert "adjusted=true" in captured["url"]


def test_load_bars_unsupported_interval(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "k")
    with pytest.raises(PolygonFetchError, match="Unsupported"):
        load_bars("QQQ", interval="3m", fetch=lambda u: _good_payload())


def test_load_bars_4h_resamples(monkeypatch):
    """4h timeframe pulls 1h bars then resamples — fetch should hit /1/hour/."""
    monkeypatch.setenv("POLYGON_API_KEY", "k")
    captured = {}

    def fake_fetch(url):
        captured["url"] = url
        # Return enough hourly bars across multiple 4h buckets.
        rows = []
        ts = 1700000000000
        for i in range(8):
            rows.append({
                "v": 100 + i, "o": 100.0 + i, "h": 101.0 + i,
                "l": 99.0 + i, "c": 100.5 + i, "t": ts + i * 3_600_000, "n": 10,
            })
        return {"status": "OK", "results": rows}

    df = load_bars("QQQ", period="60d", interval="4h", fetch=fake_fetch)
    assert "/1/hour/" in captured["url"]
    assert not df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_load_bars_missing_key_raises(monkeypatch, tmp_path):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    fake_env = tmp_path / "no.env"
    monkeypatch.setattr("data.polygon_loader.ENV_FILE", fake_env)
    with pytest.raises(PolygonFetchError, match="POLYGON_API_KEY not set"):
        load_bars("QQQ", interval="1d", fetch=lambda u: _good_payload())


def test_is_available_with_env(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "test_key_xxx")
    assert is_available() is True


def test_is_available_without_anything(monkeypatch, tmp_path):
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    fake_env = tmp_path / "no.env"
    monkeypatch.setattr("data.polygon_loader.ENV_FILE", fake_env)
    assert is_available() is False


def test_load_env_file_parses_quoted_values(tmp_path):
    p = tmp_path / "x.env"
    p.write_text(
        "# comment\n"
        "FOO=bar\n"
        'BAZ="qux qux"\n'
        "\n"
        "EMPTY=\n"
    )
    parsed = _load_env_file(p)
    assert parsed["FOO"] == "bar"
    assert parsed["BAZ"] == "qux qux"
    assert parsed["EMPTY"] == ""


def test_load_env_file_missing_returns_empty(tmp_path):
    assert _load_env_file(tmp_path / "nope.env") == {}
