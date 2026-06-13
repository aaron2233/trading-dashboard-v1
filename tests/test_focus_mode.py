"""Tests for qqq-gld-focus mode: focus rules engine + scan/kill_sheet wiring."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from config import load_config
from positions import (
    COOLOFF_TRADING_DAYS,
    FOCUS_DTE_BANDS,
    FOCUS_MAX_RISK_USD,
    FOCUS_TICKERS,
    PositionStore,
    check_focus_options_structure,
    check_focus_trade,
)
from positions.focus_rules import _weekdays_elapsed
from positions.model import Position


# ─────────────────────────────────────────────────────────────────────────
# focus_rules unit tests
# ─────────────────────────────────────────────────────────────────────────

def _open_position(ticker: str, direction: str = "long",
                   instrument: str = "call") -> Position:
    return Position.open_options_position(
        ticker=ticker, direction=direction, contract_type=instrument,
        account_key="main", strike=100, expiry="2026-06-19",
        premium=2.00, contracts=1,
    )


def _stopped_position(ticker: str, days_ago_weekday: int) -> Position:
    """Build a closed losing position with closed_date `days_ago_weekday` weekdays before today."""
    pos = _open_position(ticker)
    # Walk back `days_ago_weekday` weekdays from today
    cursor = datetime.now(timezone.utc)
    moved = 0
    while moved < days_ago_weekday:
        cursor = cursor - timedelta(days=1)
        if cursor.weekday() < 5:
            moved += 1
    pos.close(pnl_usd=-150.0, notes="hit -60% stop")
    pos.closed_date = cursor.isoformat()
    return pos


def test_focus_rejects_non_qqq_gld_ticker():
    violations = check_focus_trade("SPY", "long", [], [])
    assert len(violations) == 1
    assert violations[0].rule == "focus_ticker"


def test_focus_accepts_qqq_with_no_open_positions():
    violations = check_focus_trade("QQQ", "long", [], [])
    assert violations == []


def test_focus_accepts_gld_with_no_open_positions():
    violations = check_focus_trade("GLD", "short", [], [])
    assert violations == []


def test_focus_blocks_second_position_in_same_asset():
    open_pos = [_open_position("QQQ", "long")]
    violations = check_focus_trade("QQQ", "short", open_pos, [])
    rules = {v.rule for v in violations}
    assert "focus_one_per_asset" in rules


def test_focus_blocks_same_direction_pair():
    open_pos = [_open_position("QQQ", "long")]
    violations = check_focus_trade("GLD", "long", open_pos, [])
    rules = {v.rule for v in violations}
    assert "focus_no_same_direction_pair" in rules


def test_focus_allows_opposite_direction_pair():
    open_pos = [_open_position("QQQ", "long")]
    violations = check_focus_trade("GLD", "short", open_pos, [])
    assert violations == []


def test_focus_blocks_same_thesis_pair_with_long_put():
    # An open bearish LONG PUT stores direction='long' (thesis bearish). A
    # proposed bearish trade on the other asset is a same-THESIS correlated pair
    # and must block — pre-fix the raw 'long' direction missed it.
    open_pos = [_open_position("QQQ", "long", instrument="put")]  # bearish
    violations = check_focus_trade("GLD", "short", open_pos, [])  # bearish thesis
    rules = {v.rule for v in violations}
    assert "focus_no_same_direction_pair" in rules


def test_focus_allows_opposite_thesis_hedge_with_long_put():
    # Open bearish long put on QQQ (thesis bearish). A proposed BULLISH GLD trade
    # is an opposite-thesis hedge → must NOT be blocked as a same-direction pair.
    open_pos = [_open_position("QQQ", "long", instrument="put")]  # bearish
    violations = check_focus_trade("GLD", "long", open_pos, [])   # bullish thesis
    rules = {v.rule for v in violations}
    assert "focus_no_same_direction_pair" not in rules


def test_focus_blocks_during_cooloff():
    closed = [_stopped_position("QQQ", days_ago_weekday=1)]
    violations = check_focus_trade("QQQ", "long", [], closed)
    rules = {v.rule for v in violations}
    assert "focus_cooloff" in rules


def test_focus_clears_cooloff_after_three_weekdays():
    # 4 weekdays elapsed → past the 3-day floor → clear
    closed = [_stopped_position("QQQ", days_ago_weekday=4)]
    violations = check_focus_trade("QQQ", "long", [], closed)
    assert violations == []


def test_focus_cooloff_only_applies_to_same_asset():
    closed = [_stopped_position("QQQ", days_ago_weekday=1)]
    violations = check_focus_trade("GLD", "long", [], closed)
    assert violations == []


def test_focus_cooloff_ignores_winning_close():
    closed = [_stopped_position("QQQ", days_ago_weekday=1)]
    closed[0].pnl_usd = 200.0  # not a stop, was a winner
    violations = check_focus_trade("QQQ", "long", [], closed)
    assert violations == []


def test_weekdays_elapsed_same_day_returns_zero():
    now = datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc)  # Tuesday
    assert _weekdays_elapsed(now, now) == 0


def test_weekdays_elapsed_skips_weekend():
    fri = datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc)   # Friday
    mon = datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc)   # Monday
    # day after Fri = Sat (skip), Sun (skip), Mon (count) → 1 weekday
    assert _weekdays_elapsed(fri, mon) == 1


def test_weekdays_elapsed_within_workweek():
    mon = datetime(2026, 4, 27, 10, 0, tzinfo=timezone.utc)
    fri = datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)
    # Tue, Wed, Thu, Fri = 4 weekdays
    assert _weekdays_elapsed(mon, fri) == 4


def test_focus_tickers_are_qqq_gld():
    assert FOCUS_TICKERS == frozenset({"QQQ", "GLD"})


def test_cooloff_constant_is_three():
    assert COOLOFF_TRADING_DAYS == 3


# ─────────────────────────────────────────────────────────────────────────
# check_focus_options_structure
# ─────────────────────────────────────────────────────────────────────────

def test_focus_options_max_risk_blocks_above_cap():
    violations = check_focus_options_structure("QQQ", "long", max_loss_usd=250)
    rules = {v.rule for v in violations}
    assert "focus_max_risk" in rules


def test_focus_options_max_risk_passes_at_cap():
    violations = check_focus_options_structure("QQQ", "long", max_loss_usd=200)
    rules = {v.rule for v in violations}
    assert "focus_max_risk" not in rules


def test_focus_options_dte_inside_band_passes():
    # QQQ long band is 30-45
    violations = check_focus_options_structure("QQQ", "long",
                                                max_loss_usd=150, dte=37)
    assert violations == []


@pytest.mark.parametrize("ticker,direction,bad_dte", [
    ("QQQ", "long", 20),    # below 30
    ("QQQ", "long", 60),    # above 45
    ("QQQ", "short", 14),   # below 21
    ("QQQ", "short", 35),   # above 30
    ("GLD", "long", 30),    # below 45
    ("GLD", "long", 75),    # above 60
    ("GLD", "short", 21),   # below 30
    ("GLD", "short", 60),   # above 45
])
def test_focus_options_dte_outside_band_blocks(ticker, direction, bad_dte):
    violations = check_focus_options_structure(
        ticker, direction, max_loss_usd=150, dte=bad_dte,
    )
    rules = {v.rule for v in violations}
    assert "focus_dte_band" in rules


def test_focus_options_no_dte_skips_band_check():
    # No options contract → only risk-cap check runs
    violations = check_focus_options_structure(
        "QQQ", "long", max_loss_usd=150, dte=None,
    )
    assert violations == []


def test_focus_dte_bands_cover_all_four_combinations():
    expected = {("QQQ", "long"), ("QQQ", "short"),
                ("GLD", "long"), ("GLD", "short")}
    assert set(FOCUS_DTE_BANDS.keys()) == expected


def test_focus_max_risk_constant_is_200():
    assert FOCUS_MAX_RISK_USD == 200.0


# ─────────────────────────────────────────────────────────────────────────
# scan --focus CLI wiring
# ─────────────────────────────────────────────────────────────────────────

def test_scan_focus_defaults_to_spy_qqq_gld(monkeypatch, tmp_path):
    from scan import FOCUS_SCAN_TICKERS, main as scan_main

    captured: list[str] = []

    def fake_scan_ticker(ticker, period=None, timeframe="1d"):
        captured.append(ticker)
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-04-24",
            "close": 100.0,
            "ma_ribbon": {"ma_10": 100, "ma_20": 100, "ma_50": 100,
                          "ma_200": 100, "stack_state": "compression"},
            "stochastic": {"k": 50, "d": 50, "zone": "neutral", "signal": "none"},
            "sqn": {"sqn_value": 0.5, "regime": "neutral"},
        }

    monkeypatch.setattr("scan.scan_ticker", fake_scan_ticker)
    monkeypatch.setattr("scan.SCANS_DIR", tmp_path)

    code = scan_main(["--focus", "--no-persist"])
    assert code == 0
    assert captured == list(FOCUS_SCAN_TICKERS)


def test_scan_focus_rejects_foreign_ticker(monkeypatch, capsys):
    from scan import main as scan_main

    with pytest.raises(SystemExit) as exc:
        scan_main(["--focus", "AAPL"])
    assert exc.value.code != 0
    err = capsys.readouterr().err
    assert "--focus" in err and "AAPL" in err


def test_scan_focus_allows_subset_of_focus_tickers(monkeypatch, tmp_path):
    from scan import main as scan_main

    captured: list[str] = []

    def fake_scan_ticker(ticker, period=None, timeframe="1d"):
        captured.append(ticker)
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-04-24",
            "close": 100.0,
            "ma_ribbon": {"ma_10": 100, "ma_20": 100, "ma_50": 100,
                          "ma_200": 100, "stack_state": "compression"},
            "stochastic": {"k": 50, "d": 50, "zone": "neutral", "signal": "none"},
            "sqn": {"sqn_value": 0.5, "regime": "neutral"},
        }

    monkeypatch.setattr("scan.scan_ticker", fake_scan_ticker)
    monkeypatch.setattr("scan.SCANS_DIR", tmp_path)

    code = scan_main(["--focus", "QQQ", "GLD", "--no-persist"])
    assert code == 0
    assert captured == ["QQQ", "GLD"]


# ─────────────────────────────────────────────────────────────────────────
# kill_sheet --focus CLI wiring
# ─────────────────────────────────────────────────────────────────────────

def _scan_row(ticker="QQQ"):
    return {
        "ticker": ticker, "timeframe": "1d", "bar_date": "2026-04-24",
        "close": 480.0,
        "ma_ribbon": {"ma_10": 478, "ma_20": 475, "ma_50": 465, "ma_200": 440,
                      "stack_state": "full_bull"},
        "stochastic": {"k": 25, "d": 23, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.2, "regime": "bull"},
    }


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_kill_sheet_focus_rejects_non_focus_ticker(
    mock_multi, mock_scan, capsys, monkeypatch, tmp_path,
):
    mock_scan.return_value = _scan_row("SPY")
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path)
    monkeypatch.setattr(
        "kill_sheet.cli.load_config",
        lambda: load_config(Path("/nonexistent.yaml")),
    )

    from kill_sheet.cli import main
    code = main([
        "SPY", "--direction", "long", "--no-multi-tf", "--no-persist",
        "--skip-devil", "--focus",
    ])
    assert code == 2
    err = capsys.readouterr().err
    assert "--focus" in err and "SPY" in err


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_kill_sheet_focus_blocks_when_focus_pair_violated(
    mock_multi, mock_scan, capsys, monkeypatch, tmp_path,
):
    mock_scan.return_value = _scan_row("QQQ")
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr(
        "kill_sheet.cli.PositionStore",
        lambda: PositionStore(path=positions_file),
    )
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")
    monkeypatch.setattr(
        "kill_sheet.cli.load_config",
        lambda: load_config(Path("/nonexistent.yaml")),
    )

    # Pre-populate an open GLD long → blocks a same-direction QQQ long under focus
    store = PositionStore(path=positions_file)
    store.add(_open_position("GLD", "long"))

    from kill_sheet.cli import main
    code = main([
        "QQQ", "--direction", "long", "--no-multi-tf", "--no-persist",
        "--skip-devil", "--focus",
    ])
    assert code == 4
    err = capsys.readouterr().err
    assert "focus_no_same_direction_pair" in err


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_kill_sheet_focus_blocks_on_dte_out_of_band(
    mock_multi, mock_scan, capsys, monkeypatch, tmp_path,
):
    mock_scan.return_value = _scan_row("QQQ")
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr(
        "kill_sheet.cli.PositionStore",
        lambda: PositionStore(path=positions_file),
    )
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")
    monkeypatch.setattr(
        "kill_sheet.cli.load_config",
        lambda: load_config(Path("/nonexistent.yaml")),
    )

    # 14 DTE for QQQ long is well below the 30-45 band → blocks under focus
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=14)).isoformat()

    from kill_sheet.cli import main
    code = main([
        "QQQ", "--direction", "long",
        "--strike", "500", "--premium", "1.50", "--expiry", expiry,
        "--no-multi-tf", "--no-persist", "--skip-devil",
        "--focus", "--conviction", "speculative",  # speculative keeps risk under $200
    ])
    assert code == 4
    err = capsys.readouterr().err
    assert "focus_dte_band" in err


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_kill_sheet_focus_blocks_on_max_risk(
    mock_multi, mock_scan, capsys, monkeypatch, tmp_path,
):
    mock_scan.return_value = _scan_row("QQQ")
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr(
        "kill_sheet.cli.PositionStore",
        lambda: PositionStore(path=positions_file),
    )
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")
    monkeypatch.setattr(
        "kill_sheet.cli.load_config",
        lambda: load_config(Path("/nonexistent.yaml")),
    )

    # Default conviction=high on $10K account = 2.5% = $250 → over $200 cap
    from kill_sheet.cli import main
    code = main([
        "QQQ", "--direction", "long",
        "--no-multi-tf", "--no-persist", "--skip-devil", "--focus",
    ])
    assert code == 4
    err = capsys.readouterr().err
    assert "focus_max_risk" in err


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_kill_sheet_focus_clean_state_passes(
    mock_multi, mock_scan, monkeypatch, tmp_path,
):
    mock_scan.return_value = _scan_row("QQQ")
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr(
        "kill_sheet.cli.PositionStore",
        lambda: PositionStore(path=positions_file),
    )
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")
    monkeypatch.setattr(
        "kill_sheet.cli.load_config",
        lambda: load_config(Path("/nonexistent.yaml")),
    )

    from kill_sheet.cli import main
    # speculative conviction → 0.75% × $10K = $75 → under $200 focus cap
    code = main([
        "QQQ", "--direction", "long", "--no-multi-tf", "--no-persist",
        "--skip-devil", "--focus", "--conviction", "speculative",
    ])
    assert code == 0
