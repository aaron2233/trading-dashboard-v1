"""Tests for src/weekly_trend/ + 10 WMA trailing-stop alert.

Covers:
- Confluence classification across all 7 states
- Counter-trend regime is a blocker but doesn't reject the setup
- Penny-stock detection (<$5 → vehicle = shares)
- Ranking: regime > Stoch > MA clarity
- 10 WMA trailing-stop alert (long break, short break, no-trigger)
- API integration
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from positions.alerts import _weekly_trail_alerts, evaluate_alerts
from positions.model import Position
from weekly_trend import (
    PENNY_STOCK_THRESHOLD,
    WeeklySetup,
    classify_confluence,
    scan_weekly_watchlist,
)


# ─────────────────────────────────────────────────────────────────────────
# Confluence classification
# ─────────────────────────────────────────────────────────────────────────


def test_confluence_chop_blocks_trade():
    c, d, blockers = classify_confluence("chop", 50, 50, None, "bull")
    assert c == "chop"
    assert d == "none"
    assert any("MA tangle" in b for b in blockers)


def test_confluence_compression_no_direction():
    c, d, blockers = classify_confluence("compression", 50, 50, None, "bull")
    assert c == "compression"
    assert d == "none"
    assert any("breakout" in b.lower() for b in blockers)


def test_confluence_high_conviction_long():
    """Full bull stack + Stoch %K cross above %D from <30 → high-conviction long."""
    c, d, blockers = classify_confluence(
        "full_bull", 25.0, 22.0, "bull_cross_oversold", "bull",
    )
    assert c == "high_conviction_long"
    assert d == "long"
    assert blockers == []


def test_confluence_continuation_long():
    """Full bull + Stoch in 40-70 zone with K > D → continuation long."""
    c, d, blockers = classify_confluence(
        "full_bull", 55.0, 50.0, "bull_continuation", "bull",
    )
    assert c == "continuation_long"
    assert d == "long"


def test_confluence_high_conviction_short():
    c, d, blockers = classify_confluence(
        "full_bear", 75.0, 78.0, "bear_cross_overbought", "bear",
    )
    assert c == "high_conviction_short"
    assert d == "short"
    assert blockers == []


def test_confluence_counter_trend_long_flagged():
    """Bullish stack + bear regime → setup classified, but counter-trend warning."""
    c, d, blockers = classify_confluence(
        "full_bull", 25.0, 22.0, "bull_cross_oversold", "strong_bear",
    )
    assert c == "high_conviction_long"
    assert d == "long"
    assert any("opposes" in b for b in blockers)


def test_confluence_no_setup_when_stack_bullish_but_no_stoch_signal():
    """Bullish stack but Stoch %K < %D (no cross) → no setup."""
    c, d, blockers = classify_confluence(
        "full_bull", 50.0, 60.0, None, "bull",
    )
    assert c == "no_setup"


def test_confluence_unknown_stack_returns_no_setup():
    c, d, blockers = classify_confluence(None, 50.0, 50.0, None, "bull")
    assert c == "no_setup"
    assert d == "none"


# ─────────────────────────────────────────────────────────────────────────
# Scanner end-to-end (mocked scan_fn)
# ─────────────────────────────────────────────────────────────────────────


def make_row(
    ticker: str = "AAPL", close: float = 30.0, stack: str = "full_bull",
    k: float = 25.0, d: float = 22.0,
    signal: str | None = "bull_cross_oversold",
    regime: str = "bull",
) -> dict[str, Any]:
    return {
        "ticker": ticker, "timeframe": "1wk", "bar_date": "2026-05-10",
        "close": close,
        "ma_ribbon": {"ma_10": 28, "ma_20": 26, "ma_50": 22, "ma_200": 18, "stack_state": stack},
        "stochastic": {"k": k, "d": d, "zone": "oversold" if k < 30 else "neutral", "signal": signal},
        "sqn": {"sqn_value": 1.0, "regime": regime, "sqn_20_value": 0.5,
                "regime_20": regime, "diagnostic": "ok"},
    }


def make_scan_fn(rows: dict[tuple[str, str], dict[str, Any]]):
    def fn(ticker: str, timeframe: str = "1d") -> dict[str, Any]:
        key = (ticker.upper(), timeframe)
        if key not in rows:
            raise ValueError(f"unknown {key}")
        return rows[key]
    return fn


def test_scanner_classifies_each_ticker_and_ranks():
    """Multiple tickers — high-conviction first in top_setups."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        # AAPL: high-conviction long, with-trend
        ("AAPL", "1wk"): make_row("AAPL", close=30, stack="full_bull",
                                   k=25, d=22, signal="bull_cross_oversold"),
        # NVDA: continuation long, with-trend
        ("NVDA", "1wk"): make_row("NVDA", close=40, stack="full_bull",
                                   k=55, d=50, signal="bull_continuation"),
        # CHOP: chop — should not appear in top_setups
        ("CHOP", "1wk"): make_row("CHOP", close=25, stack="chop",
                                   k=50, d=50, signal=None),
    }
    result = scan_weekly_watchlist(
        ["AAPL", "NVDA", "CHOP"], benchmark="SPY",
        scan_fn=make_scan_fn(rows),
    )
    assert result.benchmark_regime == "bull"
    tickers = [s.ticker for s in result.setups]
    assert set(tickers) == {"AAPL", "NVDA", "CHOP"}
    # Ranked: AAPL (high-conviction with-trend) > NVDA (continuation with-trend) > CHOP
    assert result.setups[0].ticker == "AAPL"
    assert result.setups[1].ticker == "NVDA"
    # CHOP excluded from top_setups
    top_tickers = [s.ticker for s in result.top_setups]
    assert "CHOP" not in top_tickers


def test_scanner_blocks_iwm_for_weekly_trend():
    """IWM is on the weekly-trend blocked list (backtest 2026-05-07) — a
    full BUY setup on it must surface as no_go AT SCAN TIME, not only at
    kill-sheet time (previously the scan card could show BUY on IWM)."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("IWM", "1wk"): make_row("IWM", close=220, stack="full_bull",
                                  k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["IWM"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    setup = result.setups[0]
    assert setup.verdict == "no_go"
    assert any("blocked" in b.lower() for b in setup.blockers)


def test_scanner_marks_spy_marginal_but_not_blocked():
    """SPY is marginal (warn-only) for weekly-trend — BUY stands, with a
    warning blocker attached."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("SPY", "1wk"): make_row("SPY", close=560, stack="full_bull",
                                  k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["SPY"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    setup = result.setups[0]
    assert setup.verdict == "buy"
    assert any("marginal" in b.lower() for b in setup.blockers)


def test_scanner_penny_stock_suggests_shares():
    """Close < $5 → suggested_vehicle == 'shares'."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("PENNY", "1wk"): make_row("PENNY", close=2.50, stack="full_bull",
                                    k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["PENNY"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    setup = result.setups[0]
    assert setup.is_penny_stock is True
    assert setup.suggested_vehicle == "shares"


def test_scanner_above_threshold_suggests_options():
    """Close >= $5 → suggested_vehicle == 'options'."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAPL", "1wk"): make_row("AAPL", close=200, stack="full_bull",
                                   k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["AAPL"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    setup = result.setups[0]
    assert setup.is_penny_stock is False
    assert setup.suggested_vehicle == "options"


def test_scanner_at_threshold_is_options():
    """Close == $5 (boundary) → not penny."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("X", "1wk"): make_row("X", close=PENNY_STOCK_THRESHOLD, stack="full_bull",
                                k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["X"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    assert result.setups[0].is_penny_stock is False


def test_scanner_handles_individual_ticker_failures():
    """One bad ticker doesn't fail the whole scan — error captured per-ticker."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAPL", "1wk"): make_row("AAPL", regime="bull"),
        # NVDA missing
    }
    result = scan_weekly_watchlist(
        ["AAPL", "NVDA"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    assert "NVDA" in result.errors
    assert any(s.ticker == "AAPL" for s in result.setups)


def test_scanner_dedupes_tickers():
    """Same ticker submitted twice runs once."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAPL", "1wk"): make_row("AAPL", regime="bull"),
    }
    result = scan_weekly_watchlist(
        ["AAPL", "aapl", "AAPL"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    assert len([s for s in result.setups if s.ticker == "AAPL"]) == 1


def test_scanner_with_trend_outranks_counter_trend():
    """High-conviction with-trend > high-conviction counter-trend."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="strong_bull"),
        # WITH: bullish stack + bull regime + Stoch oversold cross
        ("WITH", "1wk"): make_row("WITH", close=30, stack="full_bull",
                                   k=25, d=22, signal="bull_cross_oversold",
                                   regime="strong_bull"),
        # AGAINST: bearish stack + bull regime → counter-trend short
        ("AGAINST", "1wk"): make_row("AGAINST", close=30, stack="full_bear",
                                      k=75, d=78, signal="bear_cross_overbought",
                                      regime="strong_bull"),
    }
    result = scan_weekly_watchlist(
        ["WITH", "AGAINST"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    assert result.setups[0].ticker == "WITH"
    # WITH has +30 regime bonus, AGAINST has -20 → WITH leads by 50 points
    assert result.setups[0].rank_score > result.setups[1].rank_score


# ─────────────────────────────────────────────────────────────────────────
# Universe sweep mode
# ─────────────────────────────────────────────────────────────────────────


def test_scanner_universe_mode_tags_source_universe(monkeypatch):
    """`universe` arg resolves to tickers via free_range_universe and each
    setup is tagged with the universe it came from."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAA", "1wk"): make_row("AAA", close=30, stack="full_bull",
                                  k=25, d=22, signal="bull_cross_oversold"),
        ("BBB", "1wk"): make_row("BBB", close=40, stack="full_bull",
                                  k=55, d=50, signal="bull_continuation"),
    }

    # Stub the universe resolver so this test is hermetic
    import free_range.universe as fru
    monkeypatch.setattr(fru, "free_range_universe",
                        lambda *args, **kwargs:
                        ("AAA",) if kwargs.get("universe") == "nasdaq_100"
                        else ("BBB",))

    result = scan_weekly_watchlist(
        benchmark="SPY",
        scan_fn=make_scan_fn(rows),
        universe=["nasdaq_100", "sp500_top_50"],
    )

    by_ticker = {s.ticker: s for s in result.setups}
    assert by_ticker["AAA"].source_universe == "nasdaq_100"
    assert by_ticker["BBB"].source_universe == "sp500_top_50"


def test_scanner_explicit_tickers_leaves_source_universe_none():
    """Per-ticker scan path keeps source_universe=None."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAPL", "1wk"): make_row("AAPL", close=30, stack="full_bull",
                                   k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["AAPL"], benchmark="SPY", scan_fn=make_scan_fn(rows),
    )
    assert result.setups[0].source_universe is None


def test_scanner_no_tickers_and_no_universe_raises():
    rows = {("SPY", "1d"): make_row("SPY", regime="bull")}
    with pytest.raises(ValueError, match="tickers.*or.*universe"):
        scan_weekly_watchlist(benchmark="SPY", scan_fn=make_scan_fn(rows))


def test_scanner_explicit_tickers_wins_over_universe():
    """When both are passed, explicit tickers takes priority."""
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAPL", "1wk"): make_row("AAPL", close=30, stack="full_bull",
                                   k=25, d=22, signal="bull_cross_oversold"),
    }
    result = scan_weekly_watchlist(
        ["AAPL"], benchmark="SPY", scan_fn=make_scan_fn(rows),
        universe=["nasdaq_100"],
    )
    assert [s.ticker for s in result.setups] == ["AAPL"]
    assert result.setups[0].source_universe is None


# ─────────────────────────────────────────────────────────────────────────
# 10 WMA trailing-stop alert
# ─────────────────────────────────────────────────────────────────────────


def _weekly_position(direction: str = "long", account: str = "weekly") -> Position:
    return Position(
        id="test_weekly_aapl",
        ticker="AAPL", direction=direction, instrument="call",
        account_key=account,
        entry_date="2026-04-01",
        contracts=1, strike=200, expiry="2026-09-15",
        premium_paid_per_contract=10.0,
        total_cost_usd=1000, max_loss_usd=1000,
        target_price=220, invalidation_price=190,
        status="open",
    )


def test_weekly_trail_alert_long_break_fires():
    pos = _weekly_position(direction="long")
    weekly_row = {"close": 95.0, "ma_ribbon": {"ma_10": 100.0, "stack_state": "full_bull"}}
    alerts = _weekly_trail_alerts(pos, weekly_row)
    assert len(alerts) == 1
    assert alerts[0].rule == "weekly_10wma_trail_break"
    assert alerts[0].severity == "action"
    assert "below 10 WMA" in alerts[0].message


def test_weekly_trail_alert_short_break_fires():
    pos = _weekly_position(direction="short")
    weekly_row = {"close": 105.0, "ma_ribbon": {"ma_10": 100.0, "stack_state": "full_bear"}}
    alerts = _weekly_trail_alerts(pos, weekly_row)
    assert len(alerts) == 1
    assert "above 10 WMA" in alerts[0].message


def test_weekly_trail_alert_no_break_silent():
    pos = _weekly_position(direction="long")
    weekly_row = {"close": 110.0, "ma_ribbon": {"ma_10": 100.0, "stack_state": "full_bull"}}
    assert _weekly_trail_alerts(pos, weekly_row) == []


def test_weekly_trail_alert_skips_non_weekly_account():
    """Apex/main account positions must NOT fire the weekly trail alert."""
    pos = _weekly_position(direction="long", account="main")
    weekly_row = {"close": 95.0, "ma_ribbon": {"ma_10": 100.0, "stack_state": "full_bull"}}
    assert _weekly_trail_alerts(pos, weekly_row) == []


def test_weekly_trail_alert_skips_when_no_weekly_row():
    """Missing weekly scan → silently skip rather than fail."""
    pos = _weekly_position(direction="long")
    assert _weekly_trail_alerts(pos, None) == []


def test_weekly_trail_alert_skips_when_data_missing():
    pos = _weekly_position(direction="long")
    assert _weekly_trail_alerts(pos, {"close": None, "ma_ribbon": {}}) == []


def test_evaluate_alerts_includes_weekly_trail_when_provided():
    """Integration: evaluate_alerts forwards weekly_scan_row to the trail check."""
    pos = _weekly_position(direction="long")
    daily_row = {
        "close": 100.0, "ma_ribbon": {"stack_state": "full_bull"},
        "stochastic": {"signal": None}, "sqn": {"regime": "bull"},
    }
    weekly_row = {"close": 90.0, "ma_ribbon": {"ma_10": 100.0}}
    alerts = evaluate_alerts(pos, daily_row, weekly_scan_row=weekly_row)
    rules = {a.rule for a in alerts}
    assert "weekly_10wma_trail_break" in rules


# ─────────────────────────────────────────────────────────────────────────
# API integration
# ─────────────────────────────────────────────────────────────────────────


def test_api_weekly_scan_endpoint(monkeypatch):
    rows = {
        ("SPY", "1d"): make_row("SPY", regime="bull"),
        ("AAPL", "1wk"): make_row("AAPL", regime="bull"),
    }

    def fake_scan(ticker, period=None, timeframe="1d"):
        return rows[(ticker.upper(), timeframe)]

    monkeypatch.setattr("scan.scan_ticker", fake_scan)

    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/weekly/scan", json={
        "tickers": ["AAPL"], "benchmark": "SPY",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["benchmark"] == "SPY"
    assert body["benchmark_regime"] == "bull"
    assert len(body["setups"]) == 1
    assert body["setups"][0]["ticker"] == "AAPL"


def test_api_weekly_scan_rejects_empty_tickers():
    """Empty tickers AND no universe → 400."""
    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/weekly/scan", json={"tickers": []})
    assert resp.status_code == 400


def test_api_weekly_scan_accepts_universe(monkeypatch):
    """Universe-only request resolves tickers from the named index and
    each result includes a source_universe tag."""
    def fake_scan(ticker, period=None, timeframe="1d"):
        if timeframe == "1d":  # benchmark
            return {
                "ticker": ticker, "close": 580.0, "bar_date": "2026-05-09",
                "ma_ribbon": {"stack_state": "full_bull"},
                "stochastic": {"k": 50, "d": 50, "zone": "mid", "signal": None},
                "sqn": {"sqn_value": 1.0, "regime": "bull",
                        "sqn_20_value": 0.5, "regime_20": "bull"},
            }
        return {
            "ticker": ticker, "close": 30.0, "bar_date": "2026-05-09",
            "ma_ribbon": {"stack_state": "full_bull",
                          "ma_10": 29, "ma_20": 28, "ma_50": 25, "ma_200": 20},
            "stochastic": {"k": 25, "d": 22, "zone": "oversold",
                           "signal": "bull_cross_oversold"},
            "sqn": {"sqn_value": 1.0, "regime": "bull",
                    "sqn_20_value": 0.5, "regime_20": "bull"},
        }
    monkeypatch.setattr("scan.scan_ticker", fake_scan)
    import free_range.universe as fru
    monkeypatch.setattr(fru, "free_range_universe",
                        lambda *args, **kwargs: ("AAA", "BBB"))

    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/weekly/scan",
        json={"universe": ["nasdaq_100"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    tickers = {s["ticker"] for s in body["setups"]}
    assert tickers == {"AAA", "BBB"}
    for s in body["setups"]:
        assert s["source_universe"] == "nasdaq_100"


def test_api_weekly_scan_handles_scan_error(monkeypatch):
    """Per-ticker failures surface in the errors map, not as 502."""
    def fake_scan(ticker, period=None, timeframe="1d"):
        raise RuntimeError("yfinance dead")
    monkeypatch.setattr("scan.scan_ticker", fake_scan)

    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/weekly/scan", json={"tickers": ["AAPL"]})
    assert resp.status_code == 200
    body = resp.json()
    # Both benchmark + ticker fail → captured per-key in errors
    assert "AAPL" in body["errors"] or "SPY" in body["errors"]
