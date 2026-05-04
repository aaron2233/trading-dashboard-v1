"""Tests for the trade devil's Correlation Trap category once it has access
to a real position list.
"""
from pathlib import Path

import pytest

from config import load_config
from kill_sheet.builder import build_standard
from positions.model import Position
from trade_devil import Verdict
from trade_devil.categories import check_correlation_trap
from trade_devil.runner import run_devil


def _row(ticker="SPY"):
    return {
        "ticker": ticker, "timeframe": "1d", "bar_date": "2026-04-22", "close": 580.0,
        "ma_ribbon": {"ma_10": 580, "ma_20": 575, "ma_50": 565, "ma_200": 548,
                      "stack_state": "full_bull"},
        "stochastic": {"k": 25, "d": 23, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.0, "regime": "bull"},
    }


def _sheet(direction="long", ticker="SPY"):
    cfg = load_config(Path("/nonexistent.yaml"))
    return build_standard(_row(ticker), direction, cfg.account("main"))


def _existing(ticker="SPY", direction="long"):
    return Position.open_options_position(
        ticker=ticker, direction=direction, contract_type="call",
        account_key="main", strike=580, expiry="2026-06-19",
        premium=5.50, contracts=1,
    )


def test_correlation_no_positions_passes_with_clear_note():
    r = check_correlation_trap(_sheet(), open_positions=[])
    assert r.verdict is Verdict.PASS
    assert "no overlap" in r.reason.lower() or "no open positions" in r.reason.lower()


def test_correlation_no_store_returns_skip_pass():
    r = check_correlation_trap(_sheet())  # open_positions=None
    assert r.verdict is Verdict.PASS
    assert "skipped" in r.reason.lower()


def test_correlation_kills_double_long():
    existing = [_existing(ticker="SPY", direction="long")]
    r = check_correlation_trap(_sheet(direction="long"), open_positions=existing)
    assert r.verdict is Verdict.KILL
    assert "double down" in r.reason.lower()


def test_correlation_kills_double_short():
    existing = [_existing(ticker="SPY", direction="short")]
    r = check_correlation_trap(_sheet(direction="short"), open_positions=existing)
    assert r.verdict is Verdict.KILL


def test_correlation_flags_opposite_direction_hedge():
    existing = [_existing(ticker="SPY", direction="long")]
    r = check_correlation_trap(_sheet(direction="short"), open_positions=existing)
    assert r.verdict is Verdict.FLAG
    assert "hedge" in r.reason.lower()


def test_correlation_passes_with_different_ticker_open():
    existing = [_existing(ticker="QQQ", direction="long")]
    r = check_correlation_trap(_sheet(ticker="SPY", direction="long"),
                               open_positions=existing)
    assert r.verdict is Verdict.PASS


def test_run_devil_threads_open_positions_through_correlation():
    sheet = _sheet(direction="long", ticker="SPY")
    # Set exit fields so Exit Clarity passes
    sheet.target_price = 600.0
    sheet.invalidation_price = 570.0
    existing = [_existing(ticker="SPY", direction="long")]
    report = run_devil(sheet, open_positions=existing)
    correlation = next(r for r in report.results if r.category == "Correlation Trap")
    assert correlation.verdict is Verdict.KILL
