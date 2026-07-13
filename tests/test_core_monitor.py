"""QQQM-core monitor state — file loading, staleness, position summary."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config import load_config
from config.loader import AccountConfig, Config
from core_monitor import (
    core_sleeve,
    load_monitor_state,
    monitor_is_stale,
    summarize_core_positions,
)
from positions.model import Position

TODAY = date(2026, 7, 12)


def test_missing_monitor_file_degrades():
    doc, err = load_monitor_state(Path("/nonexistent/latest.json"))
    assert doc is None
    assert "missing" in err


def test_monitor_roundtrip_and_staleness(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps({"generated": "2026-07-10", "headline": "X"}))
    doc, err = load_monitor_state(path)
    assert err is None
    assert doc["headline"] == "X"
    assert monitor_is_stale(doc, today=TODAY) is False
    assert monitor_is_stale(doc, today=date(2026, 7, 20)) is True
    assert monitor_is_stale({"generated": "garbage"}, today=TODAY) is True


def _core_position(expiry: str | None) -> Position:
    return Position(
        ticker="QQQM", account_key="beatmarket", instrument="call",
        strike=240.0, expiry=expiry, total_cost_usd=5000.0, status="open",
    )


def test_summarize_core_positions_dte_and_roll():
    fresh = _core_position("2027-09-17")
    warn = _core_position("2026-09-18")   # 68 days out — roll window
    now = _core_position("2026-08-21")    # 40 days out — at/below floor
    other = Position(ticker="QQQ", account_key="main", status="open")
    rows = summarize_core_positions([fresh, warn, now, other], today=TODAY)
    assert [r["roll_status"] for r in rows] == [None, "roll_window", "roll_now"]
    assert rows[0]["dte"] == (date(2027, 9, 17) - TODAY).days
    assert all(r["ticker"] == "QQQM" for r in rows)


def test_core_sleeve_from_config():
    cfg = Config(
        accounts={
            "beatmarket": AccountConfig(
                name="Beat-Market Sleeve", type="cash", balance_usd=10_000.0,
                raw={"risk_per_trade": {"high": 0.50}},
            ),
        },
        skills={}, raw={},
    )
    sleeve = core_sleeve(cfg)
    assert sleeve["premium_target_usd"] == pytest.approx(5_000.0)
    assert core_sleeve(Config(accounts={}, skills={}, raw={})) is None


def test_core_state_endpoint_defaults():
    def config_loader():
        return load_config(Path("/nonexistent.yaml"))

    client = TestClient(create_app(config_loader=config_loader))
    res = client.get("/api/v1/core/state")
    assert res.status_code == 200
    body = res.json()
    # Default install: no monitor file, no beatmarket sleeve, no positions.
    assert body["monitor_error"] is not None
    assert body["monitor_stale"] is True
    assert body["positions"] == []
    assert body["sleeve"] is None
