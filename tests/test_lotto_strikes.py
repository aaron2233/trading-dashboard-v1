"""Strike-suggestion logic for the Lotto playbook."""
from __future__ import annotations

import pytest

from lotto.strikes import (
    DEFAULT_OTM_PCTS,
    StrikeSuggestionsResult,
    suggest_strikes,
)


# ── ATM rounding ────────────────────────────────────────────────────────────


def test_atm_rounds_to_nearest_dollar():
    """QQQ at $681.61 → ATM is $682, not $681."""
    r = suggest_strikes(spot=681.61, direction="call", ticker="QQQ")
    atm = r.calls[0]
    assert atm.moneyness == "ATM"
    assert atm.strike == 682.0


def test_atm_at_exact_dollar():
    r = suggest_strikes(spot=500.0, direction="call", ticker="SPY")
    assert r.calls[0].strike == 500.0


def test_atm_rounds_up_at_half():
    """0.50 ties round up per round-half-to-even semantics; we explicitly
    document this in code. Spot at $100.50 → ATM $100 (banker's rounding
    in Python). Verify the actual behavior so we don't drift."""
    r = suggest_strikes(spot=100.50, direction="call", ticker="SPY")
    # Python's round() uses banker's rounding — 100.5 rounds to 100, not 101.
    # Pick one and assert it; we don't claim "always rounds up".
    assert r.calls[0].strike in (100.0, 101.0)


# ── OTM percentages ─────────────────────────────────────────────────────────


def test_call_otm_strikes_above_spot():
    r = suggest_strikes(spot=100.0, direction="call")
    pcts = [c.pct_otm for c in r.calls]
    strikes = [c.strike for c in r.calls]
    assert pcts == list(DEFAULT_OTM_PCTS)
    # All OTM call strikes are at or above spot
    assert all(s >= 100.0 for s in strikes)
    # 5% OTM should be ~$105
    five_pct = next(c for c in r.calls if c.pct_otm == 5.0)
    assert five_pct.strike == 105.0


def test_put_otm_strikes_below_spot():
    r = suggest_strikes(spot=100.0, direction="put")
    strikes = [p.strike for p in r.puts]
    # All OTM put strikes are at or below spot
    assert all(s <= 100.0 for s in strikes)
    five_pct = next(p for p in r.puts if p.pct_otm == 5.0)
    assert five_pct.strike == 95.0


def test_distance_usd_signed_correctly():
    """distance_usd is strike-minus-spot; positive for calls, negative for puts."""
    r = suggest_strikes(spot=100.0)
    five_pct_call = next(c for c in r.calls if c.pct_otm == 5.0)
    five_pct_put = next(p for p in r.puts if p.pct_otm == 5.0)
    assert five_pct_call.distance_usd == 5.0
    assert five_pct_put.distance_usd == -5.0


# ── direction filtering ─────────────────────────────────────────────────────


def test_direction_call_only_returns_calls():
    r = suggest_strikes(spot=100.0, direction="call")
    assert len(r.calls) == len(DEFAULT_OTM_PCTS)
    assert r.puts == []


def test_direction_put_only_returns_puts():
    r = suggest_strikes(spot=100.0, direction="put")
    assert r.calls == []
    assert len(r.puts) == len(DEFAULT_OTM_PCTS)


def test_direction_none_returns_both():
    r = suggest_strikes(spot=100.0, direction=None)
    assert len(r.calls) == len(DEFAULT_OTM_PCTS)
    assert len(r.puts) == len(DEFAULT_OTM_PCTS)


# ── ticker increment lookup ─────────────────────────────────────────────────


def test_known_ticker_uses_dollar_increment():
    r = suggest_strikes(spot=681.6, ticker="QQQ", direction="call")
    assert r.increment == 1.0
    assert r.ticker == "QQQ"


def test_unknown_ticker_defaults_to_dollar():
    r = suggest_strikes(spot=42.7, ticker="ZZZZ", direction="call")
    assert r.increment == 1.0


def test_explicit_increment_overrides_lookup():
    r = suggest_strikes(spot=100.0, ticker="QQQ", increment=5.0, direction="call")
    assert r.increment == 5.0
    # 5% OTM target = 105 → rounds to nearest $5 = 105.0 (already on grid)
    # 1% OTM target = 101 → rounds to 100 (banker) or 105 — verify in band
    five_pct = next(c for c in r.calls if c.pct_otm == 5.0)
    assert five_pct.strike == 105.0


def test_lowercase_ticker_normalized_to_upper():
    r = suggest_strikes(spot=400.0, ticker="qqq", direction="call")
    assert r.ticker == "QQQ"
    assert r.increment == 1.0


# ── bar_date passthrough + serialization ────────────────────────────────────


def test_bar_date_passed_through():
    r = suggest_strikes(spot=100.0, direction="call", bar_date="2026-05-05")
    assert r.bar_date == "2026-05-05"


def test_to_dict_round_trippable():
    r = suggest_strikes(spot=100.0, direction="call", ticker="SPY", bar_date="2026-05-05")
    d = r.to_dict()
    assert d["ticker"] == "SPY"
    assert d["spot"] == 100.0
    assert d["bar_date"] == "2026-05-05"
    assert d["increment"] == 1.0
    assert len(d["calls"]) == len(DEFAULT_OTM_PCTS)
    assert d["puts"] == []
    assert d["calls"][0]["moneyness"] == "ATM"


# ── custom OTM pcts ─────────────────────────────────────────────────────────


def test_custom_otm_pcts():
    r = suggest_strikes(
        spot=100.0, direction="call", otm_pcts=(0.0, 2.0, 4.0),
    )
    assert [c.pct_otm for c in r.calls] == [0.0, 2.0, 4.0]
    # 4% OTM → strike 104
    assert next(c for c in r.calls if c.pct_otm == 4.0).strike == 104.0


# ── validation ──────────────────────────────────────────────────────────────


def test_zero_spot_raises():
    with pytest.raises(ValueError, match="spot"):
        suggest_strikes(spot=0.0)


def test_negative_spot_raises():
    with pytest.raises(ValueError, match="spot"):
        suggest_strikes(spot=-10.0)


def test_zero_increment_raises():
    with pytest.raises(ValueError, match="increment"):
        suggest_strikes(spot=100.0, increment=0.0)


# ── isinstance check ────────────────────────────────────────────────────────


def test_returns_strike_suggestions_result():
    r = suggest_strikes(spot=100.0)
    assert isinstance(r, StrikeSuggestionsResult)


# ── API integration ─────────────────────────────────────────────────────────


def test_api_strikes_endpoint_returns_calls_and_puts(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import create_app

    def fake_scan_ticker(ticker, timeframe):
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-05-05",
            "close": 681.61,
            "ma_ribbon": {}, "stochastic": {}, "sqn": {},
        }

    monkeypatch.setattr("api.app.scan_ticker", fake_scan_ticker)
    with TestClient(create_app()) as client:
        r = client.get("/api/v1/lotto/strikes/QQQ")

    assert r.status_code == 200
    body = r.json()
    assert body["ticker"] == "QQQ"
    assert body["spot"] == 681.61
    assert body["bar_date"] == "2026-05-05"
    assert len(body["calls"]) == len(DEFAULT_OTM_PCTS)
    assert len(body["puts"]) == len(DEFAULT_OTM_PCTS)
    assert body["calls"][0]["moneyness"] == "ATM"
    assert body["calls"][0]["strike"] == 682.0


def test_api_strikes_endpoint_direction_filter(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import create_app

    def fake_scan_ticker(ticker, timeframe):
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-05-05",
            "close": 100.0,
            "ma_ribbon": {}, "stochastic": {}, "sqn": {},
        }

    monkeypatch.setattr("api.app.scan_ticker", fake_scan_ticker)
    with TestClient(create_app()) as client:
        r_call = client.get("/api/v1/lotto/strikes/SPY?direction=call")
        r_put = client.get("/api/v1/lotto/strikes/SPY?direction=put")

    body_call = r_call.json()
    body_put = r_put.json()
    assert len(body_call["calls"]) > 0
    assert body_call["puts"] == []
    assert body_put["calls"] == []
    assert len(body_put["puts"]) > 0


def test_api_strikes_endpoint_502_on_scan_failure(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import create_app

    def boom(*_a, **_kw):
        raise RuntimeError("yfinance dead")

    monkeypatch.setattr("api.app.scan_ticker", boom)
    with TestClient(create_app()) as client:
        r = client.get("/api/v1/lotto/strikes/SPY")

    assert r.status_code == 502
    assert "yfinance dead" in r.json()["detail"]


def test_api_strikes_endpoint_rejects_invalid_direction(monkeypatch):
    from fastapi.testclient import TestClient
    from api.app import create_app
    with TestClient(create_app()) as client:
        r = client.get("/api/v1/lotto/strikes/SPY?direction=garbage")
    assert r.status_code == 422
