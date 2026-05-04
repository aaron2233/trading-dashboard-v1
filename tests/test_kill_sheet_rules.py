"""Tests for kill_sheet CLI integration with account rules engine."""
from pathlib import Path
from unittest.mock import patch

import pytest

from config import load_config
from positions import PositionStore
from positions.model import Position


def _row():
    return {
        "ticker": "SPY", "timeframe": "1d", "bar_date": "2026-04-22", "close": 580.0,
        "ma_ribbon": {"ma_10": 578, "ma_20": 575, "ma_50": 565, "ma_200": 548,
                      "stack_state": "full_bull"},
        "stochastic": {"k": 25, "d": 23, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.0, "regime": "bull"},
    }


def _existing_position(account="main", ticker="QQQ"):
    return Position.open_options_position(
        ticker=ticker, direction="long", contract_type="call",
        account_key=account, strike=350, expiry="2026-06-19",
        premium=5.00, contracts=1,
    )


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_rules_block_when_max_positions_reached(
    mock_multi, mock_scan, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    mock_scan.return_value = _row()
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr(
        "kill_sheet.cli.PositionStore",
        lambda: PositionStore(path=positions_file),
    )
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")
    # Override main account's max_open_positions so the test is fast and tight.
    from config.loader import AccountConfig
    real_load = load_config
    def _patched_load():
        cfg = real_load(Path("/nonexistent.yaml"))
        cfg.accounts["main"].raw["max_open_positions"] = 1
        return cfg
    monkeypatch.setattr("kill_sheet.cli.load_config", _patched_load)

    # Pre-populate one open position in main
    store = PositionStore(path=positions_file)
    store.add(_existing_position())

    from kill_sheet.cli import main
    code = main([
        "SPY", "--direction", "long", "--no-multi-tf", "--no-persist",
        "--skip-devil",
    ])
    assert code == 4  # rules-blocked
    err = capsys.readouterr().err
    assert "max_open_positions" in err


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_bypass_rules_lets_kill_sheet_render(
    mock_multi, mock_scan, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
):
    mock_scan.return_value = _row()
    mock_multi.return_value = {"1wk": {"error": "skip"}, "4h": {"error": "skip"}}
    positions_file = tmp_path / "positions.json"
    monkeypatch.setattr(
        "kill_sheet.cli.PositionStore",
        lambda: PositionStore(path=positions_file),
    )
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "ks")

    real_load = load_config
    def _patched_load():
        cfg = real_load(Path("/nonexistent.yaml"))
        cfg.accounts["main"].raw["max_open_positions"] = 1
        return cfg
    monkeypatch.setattr("kill_sheet.cli.load_config", _patched_load)

    store = PositionStore(path=positions_file)
    store.add(_existing_position())

    from kill_sheet.cli import main
    code = main([
        "SPY", "--direction", "long", "--no-multi-tf", "--no-persist",
        "--skip-devil", "--bypass-rules",
    ])
    assert code == 0
    err = capsys.readouterr().err
    assert "proceeding anyway" in err
    # Sheet was rendered to stdout
    out = capsys.readouterr  # captured by pytest


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_clean_state_passes_rules(
    mock_multi, mock_scan, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    mock_scan.return_value = _row()
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
    code = main([
        "SPY", "--direction", "long", "--no-multi-tf", "--no-persist",
        "--skip-devil",
    ])
    assert code == 0
