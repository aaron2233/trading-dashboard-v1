"""Tests for fixtures_draft CLI — verifies CSV format and that categorical
truth fields are blanked so the human cross-check stays meaningful.
"""
from __future__ import annotations

import csv
import io
from unittest.mock import patch

import pandas as pd
import pytest

from fixtures_draft import draft_ma_ribbon, draft_stochastic, main


def _fake_bars(rows: int = 250) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=rows)
    closes = pd.Series([100.0 + i * 0.1 for i in range(rows)], index=dates)
    return pd.DataFrame({
        "open": closes,
        "high": closes + 0.5,
        "low": closes - 0.5,
        "close": closes,
        "volume": 1_000_000,
    })


@patch("fixtures_draft.load_bars")
def test_draft_ma_ribbon_emits_correct_header_and_blanks_stack_state(mock_load):
    mock_load.return_value = _fake_bars(250)
    output = draft_ma_ribbon("SPY", days=10)
    rows = list(csv.reader(io.StringIO(output)))

    assert rows[0] == ["date", "ma_10", "ma_20", "ma_50", "ma_200", "stack_state"]
    assert len(rows) == 11

    for row in rows[1:]:
        assert row[-1] == "", "stack_state must be blank for human verification"
        assert row[1] != "" and float(row[1]) > 0, "ma_10 should be filled"
        assert row[2] != "" and float(row[2]) > 0, "ma_20 should be filled"


@patch("fixtures_draft.load_bars")
def test_draft_stochastic_emits_correct_header_and_blanks_signal(mock_load):
    mock_load.return_value = _fake_bars(60)
    output = draft_stochastic("SPY", days=10)
    rows = list(csv.reader(io.StringIO(output)))

    assert rows[0] == ["date", "k", "d", "zone", "signal"]
    assert len(rows) == 11

    for row in rows[1:]:
        assert row[-1] == "", "signal must be blank for human verification"
        assert row[3] in ("oversold", "mid", "overbought", ""), \
            "zone should be deterministic value or blank during warmup"


@patch("fixtures_draft.load_bars")
def test_draft_ma_ribbon_respects_days_parameter(mock_load):
    mock_load.return_value = _fake_bars(250)
    output = draft_ma_ribbon("SPY", days=5)
    rows = list(csv.reader(io.StringIO(output)))
    assert len(rows) == 6  # header + 5


@patch("fixtures_draft.load_bars")
def test_draft_stochastic_respects_days_parameter(mock_load):
    mock_load.return_value = _fake_bars(60)
    output = draft_stochastic("SPY", days=15)
    rows = list(csv.reader(io.StringIO(output)))
    assert len(rows) == 16


@patch("fixtures_draft.load_bars")
def test_dates_are_iso_format(mock_load):
    mock_load.return_value = _fake_bars(250)
    output = draft_ma_ribbon("SPY", days=3)
    rows = list(csv.reader(io.StringIO(output)))
    for row in rows[1:]:
        pd.to_datetime(row[0], format="%Y-%m-%d")  # raises if malformed


@patch("fixtures_draft.load_bars")
def test_main_write_mode_creates_fixture_files(mock_load, tmp_path, monkeypatch, capsys):
    mock_load.return_value = _fake_bars(250)
    fixture_dir = tmp_path / "fixtures" / "truth"
    fixture_dir.mkdir(parents=True)
    monkeypatch.setattr("fixtures_draft.FIXTURE_DIR", fixture_dir)

    rc = main(["SPY", "--days", "5", "--write"])

    assert rc == 0
    assert (fixture_dir / "SPY_ma_ribbon.csv").exists()
    assert (fixture_dir / "SPY_stochastic.csv").exists()


@patch("fixtures_draft.load_bars")
def test_main_default_mode_prints_to_stdout(mock_load, capsys):
    mock_load.return_value = _fake_bars(250)
    rc = main(["QQQ", "--days", "3", "--indicator", "ma_ribbon"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "QQQ_ma_ribbon.csv" in captured.out
    assert "ma_10" in captured.out


@patch("fixtures_draft.load_bars")
def test_main_indicator_flag_filters_output(mock_load, capsys):
    mock_load.return_value = _fake_bars(250)
    main(["AAPL", "--days", "3", "--indicator", "stochastic"])
    captured = capsys.readouterr()
    assert "AAPL_stochastic.csv" in captured.out
    assert "AAPL_ma_ribbon.csv" not in captured.out


@patch("fixtures_draft.load_bars")
def test_ticker_is_uppercased(mock_load, capsys):
    mock_load.return_value = _fake_bars(250)
    main(["spy", "--days", "3", "--indicator", "ma_ribbon"])
    captured = capsys.readouterr()
    assert "SPY_ma_ribbon.csv" in captured.out
