import json
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from scan import build_parser, format_table, persist_scan, scan_ticker


def _fake_bars(ticker_name: str = "FAKE") -> pd.DataFrame:
    dates = pd.bdate_range(start="2024-01-02", periods=300)
    closes = [100.0 + i * 0.3 for i in range(300)]
    return pd.DataFrame(
        {
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": 1_000_000,
        },
        index=dates,
    )


def test_parser_accepts_tickers():
    args = build_parser().parse_args(["SPY", "QQQ", "IWM"])
    assert args.tickers == ["SPY", "QQQ", "IWM"]
    assert args.period == "2y"
    assert args.no_persist is False


def test_parser_no_persist_flag():
    args = build_parser().parse_args(["SPY", "--no-persist"])
    assert args.no_persist is True


def test_parser_help_does_not_raise():
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--help"])
    assert exc.value.code == 0


@patch("scan.load_bars")
def test_scan_ticker_produces_expected_shape(mock_load):
    mock_load.return_value = _fake_bars()
    result = scan_ticker("FAKE")

    assert result["ticker"] == "FAKE"
    assert "bar_date" in result
    assert "close" in result

    for section in ("ma_ribbon", "stochastic", "sqn"):
        assert section in result

    assert set(result["ma_ribbon"].keys()) == {"ma_10", "ma_20", "ma_50", "ma_200", "stack_state"}
    assert set(result["stochastic"].keys()) == {"k", "d", "zone", "signal"}
    assert set(result["sqn"].keys()) == {
        "sqn_value", "regime",
        "sqn_20_value", "regime_20", "diagnostic",
    }

    # Steady uptrend -> should classify as full_bull or bull_developing
    assert result["ma_ribbon"]["stack_state"] in {"full_bull", "bull_developing"}


def test_format_table_renders_rows():
    rows = [{
        "ticker": "FAKE",
        "bar_date": "2026-04-22",
        "close": 180.5,
        "ma_ribbon": {"ma_10": 179.0, "ma_20": 175.0, "ma_50": 165.0, "ma_200": 140.0, "stack_state": "full_bull"},
        "stochastic": {"k": 78.3, "d": 74.1, "zone": "mid", "signal": "neutral"},
        "sqn": {"sqn_value": 1.68, "regime": "strong_bull"},
    }]
    out = format_table(rows)
    assert "FAKE" in out
    assert "full_bull" in out
    assert "78.3/74.1" in out
    assert "strong_bull" in out
    assert "Ticker" in out


def test_format_table_handles_error_row():
    rows = [{"ticker": "BADSYM", "error": "No data returned"}]
    out = format_table(rows)
    assert "BADSYM" in out
    assert "ERROR" in out


def test_persist_scan_writes_json(tmp_path: Path):
    rows = [{
        "ticker": "FAKE",
        "bar_date": "2026-04-22",
        "close": 180.5,
        "ma_ribbon": {"ma_10": 179.0, "ma_20": 175.0, "ma_50": 165.0, "ma_200": 140.0, "stack_state": "full_bull"},
        "stochastic": {"k": 78.3, "d": 74.1, "zone": "mid", "signal": "neutral"},
        "sqn": {"sqn_value": 1.68, "regime": "strong_bull"},
    }]
    scans_dir = tmp_path / "scans"
    path = persist_scan(rows, scans_dir=scans_dir)

    assert path.exists()
    payload = json.loads(path.read_text())
    assert "scan_time_utc" in payload
    assert "FAKE" in payload["tickers"]
    assert payload["tickers"]["FAKE"]["ma_ribbon"]["stack_state"] == "full_bull"
    assert payload["errors"] == {}


def test_persist_scan_includes_errors(tmp_path: Path):
    rows = [{"ticker": "BADSYM", "error": "No data"}]
    path = persist_scan(rows, scans_dir=tmp_path / "scans")
    payload = json.loads(path.read_text())
    assert payload["errors"] == {"BADSYM": "No data"}
    assert payload["tickers"] == {}


@patch("scan.load_bars")
def test_main_success_exits_zero(mock_load, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mock_load.return_value = _fake_bars()
    monkeypatch.setattr("scan.SCANS_DIR", tmp_path / "scans")
    # Redirect any flag events to a tmp path so tests never touch real events.jsonl
    events_path = tmp_path / "events.jsonl"
    import events as events_pkg
    monkeypatch.setattr("scan.log_flag",
                        lambda t, payload=None: events_pkg.log.log_flag(t, payload=payload, path=events_path))

    from scan import main
    exit_code = main(["SPY"])
    assert exit_code == 0


@patch("scan.load_bars")
def test_main_data_failure_exits_nonzero(mock_load, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mock_load.side_effect = ValueError("No data")
    monkeypatch.setattr("scan.SCANS_DIR", tmp_path / "scans")
    events_path = tmp_path / "events.jsonl"
    import events as events_pkg
    monkeypatch.setattr("scan.log_flag",
                        lambda t, payload=None: events_pkg.log.log_flag(t, payload=payload, path=events_path))

    from scan import main
    exit_code = main(["BADSYM"])
    assert exit_code == 1
