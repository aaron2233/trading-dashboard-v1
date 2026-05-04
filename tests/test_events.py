import json
from pathlib import Path

from events.log import (
    log_event,
    log_flag,
    log_resolved,
    log_shadow_trade,
    read_events,
)


def test_log_event_creates_parent_dir_and_appends(tmp_path: Path):
    events_path = tmp_path / "nested" / "events.jsonl"
    log_event("flag", "SPY", path=events_path)
    assert events_path.exists()
    events = read_events(events_path)
    assert len(events) == 1
    assert events[0]["type"] == "flag"
    assert events[0]["ticker"] == "SPY"
    assert "ts" in events[0]


def test_log_event_uppercases_ticker(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    log_event("flag", "spy", path=events_path)
    events = read_events(events_path)
    assert events[0]["ticker"] == "SPY"


def test_log_flag_carries_payload(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    payload = {"stoch_signal": "bull_cross_oversold", "sqn_regime": "bull"}
    log_flag("QQQ", payload=payload, path=events_path)
    events = read_events(events_path)
    assert events[0]["payload"] == payload


def test_log_shadow_trade_and_resolved(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    log_shadow_trade("IWM", path=events_path)
    log_resolved("IWM", note="closed at break-even", path=events_path)
    events = read_events(events_path)
    assert len(events) == 2
    assert events[0]["type"] == "shadow_trade"
    assert events[1]["type"] == "resolved"
    assert events[1]["payload"]["note"] == "closed at break-even"


def test_multiple_events_append(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    for t in ["SPY", "QQQ", "IWM"]:
        log_event("flag", t, path=events_path)
    events = read_events(events_path)
    assert [e["ticker"] for e in events] == ["SPY", "QQQ", "IWM"]


def test_read_events_returns_empty_for_missing_file(tmp_path: Path):
    missing = tmp_path / "does_not_exist.jsonl"
    assert read_events(missing) == []


def test_events_are_valid_jsonl(tmp_path: Path):
    events_path = tmp_path / "events.jsonl"
    log_flag("SPY", payload={"k": 75.3}, path=events_path)
    log_shadow_trade("QQQ", note="taken outside dashboard", path=events_path)
    lines = events_path.read_text().splitlines()
    for line in lines:
        json.loads(line)
