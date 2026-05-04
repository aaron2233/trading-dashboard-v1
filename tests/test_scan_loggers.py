"""Integration tests for the --shadow-trade / --mark-resolved scan flags
and for scan-emitted flag events.
"""
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from events.log import read_events


def _row_with_signal(signal: str) -> dict:
    return {
        "ticker": "FAKE",
        "bar_date": "2026-04-22",
        "close": 100.0,
        "ma_ribbon": {"ma_10": 101, "ma_20": 99, "ma_50": 95,
                      "ma_200": 90, "stack_state": "full_bull"},
        "stochastic": {"k": 25.0, "d": 23.0, "zone": "oversold", "signal": signal},
        "sqn": {"sqn_value": 1.2, "regime": "bull"},
    }


def test_shadow_trade_flag_logs_and_exits(tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch):
    events_path = tmp_path / "events.jsonl"
    monkeypatch.setattr("events.log.EVENTS_PATH", events_path)
    # Re-import path inside scan too
    import scan
    import events
    monkeypatch.setattr(scan, "log_shadow_trade",
                        lambda t, note=None: events.log.log_shadow_trade(t, note=note, path=events_path))

    exit_code = scan.main(["--shadow-trade", "SPY", "--note", "took it in RH"])
    assert exit_code == 0
    ev = read_events(events_path)
    assert len(ev) == 1
    assert ev[0]["type"] == "shadow_trade"
    assert ev[0]["ticker"] == "SPY"
    assert ev[0]["payload"]["note"] == "took it in RH"


def test_mark_resolved_flag_logs_and_exits(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch):
    events_path = tmp_path / "events.jsonl"
    import scan
    import events
    monkeypatch.setattr(scan, "log_resolved",
                        lambda t, note=None: events.log.log_resolved(t, note=note, path=events_path))

    exit_code = scan.main(["--mark-resolved", "QQQ"])
    assert exit_code == 0
    ev = read_events(events_path)
    assert ev[0]["type"] == "resolved"
    assert ev[0]["ticker"] == "QQQ"


@patch("scan.scan_ticker")
def test_scan_emits_flag_for_actionable_signal(mock_scan, tmp_path: Path,
                                               monkeypatch: pytest.MonkeyPatch):
    events_path = tmp_path / "events.jsonl"
    import events as events_pkg
    monkeypatch.setattr("scan.log_flag",
                        lambda t, payload=None: events_pkg.log.log_flag(t, payload=payload, path=events_path))
    monkeypatch.setattr("scan.SCANS_DIR", tmp_path / "scans")
    mock_scan.return_value = _row_with_signal("bull_cross_oversold")

    import scan
    exit_code = scan.main(["FAKE"])
    assert exit_code == 0
    ev = read_events(events_path)
    assert len(ev) == 1
    assert ev[0]["type"] == "flag"
    assert ev[0]["ticker"] == "FAKE"
    assert ev[0]["payload"]["stoch_signal"] == "bull_cross_oversold"
    assert ev[0]["payload"]["sqn_regime"] == "bull"


@patch("scan.scan_ticker")
def test_scan_does_not_flag_neutral_signal(mock_scan, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch):
    events_path = tmp_path / "events.jsonl"
    import events as events_pkg
    monkeypatch.setattr("scan.log_flag",
                        lambda t, payload=None: events_pkg.log.log_flag(t, payload=payload, path=events_path))
    monkeypatch.setattr("scan.SCANS_DIR", tmp_path / "scans")
    mock_scan.return_value = _row_with_signal("neutral")

    import scan
    exit_code = scan.main(["FAKE"])
    assert exit_code == 0
    ev = read_events(events_path)
    assert ev == []
