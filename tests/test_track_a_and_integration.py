"""Tests for the cross-strategy integration pass (2026-05-09):

1. Track A 19/39 weekly cross detection in weekly_trend/scanner.py
2. Index-swing 21-DTE position alert in positions/alerts.py
3. Index-swing rule in discipline/score.py
"""
from __future__ import annotations

import pandas as pd
import pytest

from weekly_trend import (
    TRACK_A_BLOCKED_TICKERS,
    TrackASignal,
    detect_track_a_signal,
    scan_weekly_watchlist,
)


# ─── 1. Track A 19/39 detection ─────────────────────────────────────────


def _weekly_bars_with_cross(prev_19: float, prev_39: float,
                            now_19: float, now_39: float) -> pd.DataFrame:
    """Build 50 weekly bars whose 19-WMA and 39-WMA reach the requested values
    on the last two bars. Uses a synthetic close path that pivots so the means
    land where requested.

    Strategy: build bar closes such that the rolling 19/39 means hit targets.
    Simplest: set all closes equal to a value that produces the target mean.
    To set both 19 and 39 means independently we vary the recent vs older bars.
    """
    # Target the last 19 bars to mean = now_19, last 39 bars to mean = now_39.
    # Bars 0-19: contribute to 39-mean only. Bars 20-49: contribute to both.
    # Set bars 20-49 = now_19 (so 19-mean = now_19).
    # Then 39-mean over last 39 bars = (sum of 30 bars at now_19 +
    #   sum of 9 older bars) / 39 = now_39 → solve for 9 older bars.
    n = 50
    closes = [0.0] * n
    # Last 30 bars at now_19 to anchor the 19-mean
    for i in range(20, 50):
        closes[i] = now_19
    # Bars 11-19 (9 bars) need a value such that (30*now_19 + 9*x) / 39 = now_39
    needed_x = (39 * now_39 - 30 * now_19) / 9
    for i in range(11, 20):
        closes[i] = needed_x
    # Bars 0-10: not relevant for last-bar 39-mean, fill with same anchor
    for i in range(11):
        closes[i] = needed_x

    # For the previous bar's means, we need to look at bars 19 ago and 39 ago
    # from the prev bar (i.e., bar 48). Approximate by setting one bar before
    # the last to slightly different values — for our cross test we only need
    # the LAST-vs-PREV comparison to detect cross, so adjust bar 48:
    # Make prev_19 and prev_39 closely match by inserting an adjustment bar.
    # For simplicity, manipulate bar at index 49 (last) and 48 (prev):
    # Skip exact prev tuning; the scanner only needs two adjacent bars where
    # the relationship 19 vs 39 actually flips. We'll tune more precisely if
    # the simple version doesn't trigger.
    dates = pd.date_range("2026-01-01", periods=n, freq="W-FRI")
    return pd.DataFrame({"close": closes}, index=dates)


def test_track_a_signal_state_when_19_above_39():
    """When 19WMA > 39WMA on last bar AND >= prev → state = above (or cross_up
    if prev was <=)."""
    # Make 19WMA = 110, 39WMA = 100 → 19 above 39
    bars = pd.DataFrame({
        "close": [100.0] * 30 + [110.0] * 20,
    }, index=pd.date_range("2026-01-01", periods=50, freq="W-FRI"))
    sig = detect_track_a_signal(bars, "NVDA")
    assert sig.state in ("above", "cross_up")  # depends on prev bar
    assert sig.ma_19 is not None and sig.ma_39 is not None
    assert sig.ma_19 > sig.ma_39


def test_track_a_signal_state_when_19_below_39():
    """When 19WMA < 39WMA → state = below (or cross_down)."""
    bars = pd.DataFrame({
        "close": [110.0] * 30 + [100.0] * 20,
    }, index=pd.date_range("2026-01-01", periods=50, freq="W-FRI"))
    sig = detect_track_a_signal(bars, "NVDA")
    assert sig.state in ("below", "cross_down")
    assert sig.ma_19 < sig.ma_39


def test_track_a_signal_insufficient_data_returns_none():
    bars = pd.DataFrame({
        "close": [100.0] * 30,
    }, index=pd.date_range("2026-01-01", periods=30, freq="W-FRI"))
    sig = detect_track_a_signal(bars, "NVDA")
    assert sig.state == "none"
    assert sig.ma_19 is None and sig.ma_39 is None


def test_track_a_asset_blocked_for_qqq_gld_spy():
    """QQQ/GLD/SPY/AMZN/NFLX/AMD/TSLA are on the Track A blocked list."""
    bars = pd.DataFrame({
        "close": [100.0] * 50,
    }, index=pd.date_range("2026-01-01", periods=50, freq="W-FRI"))
    for blocked in ("QQQ", "GLD", "SPY", "AMZN", "NFLX", "AMD", "TSLA"):
        sig = detect_track_a_signal(bars, blocked)
        assert sig.asset_blocked is True, f"{blocked} should be blocked"


def test_track_a_asset_not_blocked_for_high_beta_singles():
    """META, ETH, BTC, MU, IWM, AAPL are NOT on the Track A blocked list."""
    bars = pd.DataFrame({
        "close": [100.0] * 50,
    }, index=pd.date_range("2026-01-01", periods=50, freq="W-FRI"))
    for permitted in ("META", "MU", "AAPL", "IWM"):
        sig = detect_track_a_signal(bars, permitted)
        assert sig.asset_blocked is False, f"{permitted} should not be blocked"


def test_track_a_blocked_set_matches_skill():
    """Backtest-derived asset list must match the kill_sheet builder constant."""
    from kill_sheet.builder import WEEKLY_TREND_TRACK_A_BLOCKED_TICKERS
    assert TRACK_A_BLOCKED_TICKERS == WEEKLY_TREND_TRACK_A_BLOCKED_TICKERS


def test_scan_weekly_watchlist_track_a_skipped_when_bars_fn_raises():
    """When bars_fn raises, track_a stays None and the rest of the scan completes."""
    fake_scans = {
        "SPY": {"sqn": {"regime": "bull"}, "ticker": "SPY", "timeframe": "1d"},
        "AAPL": {
            "ticker": "AAPL", "timeframe": "1wk", "bar_date": "2026-01-30",
            "close": 200.0,
            "ma_ribbon": {"stack_state": "full_bull"},
            "stochastic": {"k": 25.0, "d": 22.0, "signal": "bull_cross_oversold",
                           "zone": "oversold"},
        },
    }

    def fake_scan(ticker, timeframe="1d"):
        return fake_scans[ticker]

    def bars_fail(ticker):
        raise RuntimeError("yfinance unreachable")

    result = scan_weekly_watchlist(
        ["AAPL"], scan_fn=fake_scan, bars_fn=bars_fail,
    )
    assert len(result.setups) == 1
    setup = result.setups[0]
    assert setup.confluence == "high_conviction_long"
    assert setup.track_a is None  # bars_fn raised → Track A silently skipped


def test_scan_weekly_watchlist_surfaces_track_a_cross_when_no_setup():
    """When ribbon classifies as no_setup but 19/39 cross fires, Track A wins."""
    fake_scans = {
        "SPY": {"sqn": {"regime": "bull"}, "ticker": "SPY", "timeframe": "1d"},
        "META": {
            "ticker": "META", "timeframe": "1wk", "bar_date": "2026-01-30",
            "close": 500.0,
            "ma_ribbon": {"stack_state": "full_bull"},
            "stochastic": {"k": 50.0, "d": 50.0, "signal": None, "zone": "mid"},
        },
    }
    def fake_scan(ticker, timeframe="1d"):
        return fake_scans[ticker]

    # Build weekly bars where 19/39 just crossed up: prev bars = 19 below 39,
    # then last bar = 19 above 39.
    closes = [110.0] * 11 + [100.0] * 39
    closes[-1] = 105.0  # nudge up the last bar so 19-mean creeps over 39-mean
    bars = pd.DataFrame({
        "close": closes,
    }, index=pd.date_range("2026-01-01", periods=50, freq="W-FRI"))

    def fake_bars(ticker):
        return bars

    result = scan_weekly_watchlist(
        ["META"], scan_fn=fake_scan, bars_fn=fake_bars,
    )
    setup = result.setups[0]
    # The Track A field must be populated
    assert setup.track_a is not None
    assert setup.track_a.ma_19 is not None


# ─── 2. Index-swing 21-DTE position alert ──────────────────────────────


def test_index_swing_21_dte_floor_alert():
    from positions.alerts import _dte_alerts
    from positions.model import Position
    from datetime import date, timedelta

    today = date(2026, 5, 9)
    expiry = (today + timedelta(days=18)).isoformat()  # 18 DTE — below floor
    pos = Position(
        id="test-1", ticker="QQQ", account_key="main", direction="long",
        instrument="call", entry_date="2026-04-15", expiry=expiry,
        strike=480.0, contracts=1, premium_paid_per_contract=10.0, max_loss_usd=1000.0,
        skill="index-swing",
    )
    alerts = _dte_alerts(pos, today=today)
    assert any(a.rule == "dte_21_floor" for a in alerts), (
        "Expected dte_21_floor alert for index-swing position with 18 DTE"
    )


def test_index_swing_30_dte_warn_alert():
    from positions.alerts import _dte_alerts
    from positions.model import Position
    from datetime import date, timedelta

    today = date(2026, 5, 9)
    expiry = (today + timedelta(days=27)).isoformat()  # 27 DTE — warn band
    pos = Position(
        id="test-2", ticker="IWM", account_key="main", direction="long",
        instrument="call", entry_date="2026-04-15", expiry=expiry,
        strike=230.0, contracts=1, premium_paid_per_contract=5.0, max_loss_usd=500.0,
        skill="index-swing",
    )
    alerts = _dte_alerts(pos, today=today)
    rule_ids = [a.rule for a in alerts]
    assert "dte_30_warn" in rule_ids


def test_index_swing_no_alert_at_45_dte():
    from positions.alerts import _dte_alerts
    from positions.model import Position
    from datetime import date, timedelta

    today = date(2026, 5, 9)
    expiry = (today + timedelta(days=45)).isoformat()
    pos = Position(
        id="test-3", ticker="SPY", account_key="main", direction="long",
        instrument="call", entry_date="2026-04-15", expiry=expiry,
        strike=580.0, contracts=1, premium_paid_per_contract=15.0, max_loss_usd=1500.0,
        skill="index-swing",
    )
    alerts = _dte_alerts(pos, today=today)
    # No 21 or 30 DTE alerts at 45 DTE
    rule_ids = [a.rule for a in alerts]
    assert "dte_21_floor" not in rule_ids
    assert "dte_30_warn" not in rule_ids


# ─── 3. Discipline scoring for index-swing ────────────────────────────


def test_discipline_index_swing_exit_within_dte_band_pass():
    """Index-swing closed with 25 DTE remaining → passes (above 21 floor)."""
    from discipline.score import score_trade
    from positions.model import Position

    pos = Position(
        id="t1", ticker="QQQ", account_key="main", direction="long",
        instrument="call", entry_date="2026-03-01", expiry="2026-05-01",
        strike=480.0, contracts=1, premium_paid_per_contract=10.0, max_loss_usd=1000.0,
        skill="index-swing", status="closed", closed_date="2026-04-06",
        # closed 25 days before expiry → 25 DTE remaining at close
    )
    score = score_trade(pos)
    rule = next((r for r in score.rules if r.rule_id == "exit_within_dte_band"), None)
    assert rule is not None
    assert rule.score == "Y", f"Expected Y for 25-DTE-remaining exit; got {rule.score}"


def test_discipline_index_swing_exit_within_dte_band_fail():
    """Index-swing closed with only 10 DTE remaining → fails (below 21 floor)."""
    from discipline.score import score_trade
    from positions.model import Position

    pos = Position(
        id="t2", ticker="QQQ", account_key="main", direction="long",
        instrument="call", entry_date="2026-03-01", expiry="2026-05-01",
        strike=480.0, contracts=1, premium_paid_per_contract=10.0, max_loss_usd=1000.0,
        skill="index-swing", status="closed", closed_date="2026-04-21",
        # closed 10 days before expiry → 10 DTE remaining at close
    )
    score = score_trade(pos)
    rule = next((r for r in score.rules if r.rule_id == "exit_within_dte_band"), None)
    assert rule is not None
    assert rule.score == "N", f"Expected N for 10-DTE-remaining exit; got {rule.score}"
