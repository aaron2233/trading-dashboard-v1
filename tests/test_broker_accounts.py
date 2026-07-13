"""Broker-account breakout — config parsing, snapshot reads, staleness."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from broker_accounts import (
    STALE_AFTER_HOURS,
    load_broker_accounts,
    selectable_account_keys,
    unmapped_sleeves,
)
from config import load_config
from config.loader import Config, AccountConfig


NOW = datetime(2026, 7, 12, 18, 0, 0, tzinfo=timezone.utc)


def _config(raw: dict, accounts: dict[str, AccountConfig] | None = None) -> Config:
    return Config(accounts=accounts or {}, skills={}, raw=raw)


def _write_snapshot(dir_: Path, mask: str, *, total="1234.56", cash="1000.00",
                    fetched_at="2026-07-12T12:00:00Z") -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"portfolio-{mask}.json").write_text(json.dumps({
        "source": "robinhood-mcp",
        "fetched_at": fetched_at,
        "account": f"…{mask}",
        "portfolio": {"total_value": total, "cash": cash},
    }))


def test_no_broker_accounts_block_returns_empty():
    assert load_broker_accounts(_config({}), Path("/nonexistent"), now=NOW) == []


def test_snapshot_values_and_masking(tmp_path):
    _write_snapshot(tmp_path, "1111", total="8000.00", cash="7500.00")
    cfg = _config({"broker_accounts": [
        {"key": "individual", "label": "Individual", "account_mask": "1111",
         "sleeves": ["main", "lotto"]},
    ]})
    [acct] = load_broker_accounts(cfg, tmp_path, now=NOW)
    assert acct.account_masked == "…1111"
    assert acct.total_value_usd == pytest.approx(8000.0)
    assert acct.cash_usd == pytest.approx(7500.0)
    assert acct.sleeves == ["main", "lotto"]
    assert acct.stale is False
    assert acct.age_hours == pytest.approx(6.0)
    assert acct.error is None


def test_full_account_number_still_masks_to_last_4(tmp_path):
    _write_snapshot(tmp_path, "1234", total="50")
    cfg = _config({"broker_accounts": [
        {"key": "x", "label": "X", "account_mask": "999991234"},
    ]})
    [acct] = load_broker_accounts(cfg, tmp_path, now=NOW)
    assert acct.account_masked == "…1234"
    assert acct.total_value_usd == pytest.approx(50.0)


def test_missing_snapshot_degrades_to_stale_with_error(tmp_path):
    cfg = _config({"broker_accounts": [
        {"key": "roth", "label": "Roth", "account_mask": "2222"},
    ]})
    [acct] = load_broker_accounts(cfg, tmp_path, now=NOW)
    assert acct.total_value_usd is None
    assert acct.stale is True
    assert "no snapshot" in acct.error


def test_old_snapshot_flagged_stale(tmp_path):
    _write_snapshot(tmp_path, "3333", fetched_at="2026-07-01T12:00:00Z")
    cfg = _config({"broker_accounts": [
        {"key": "agentic", "label": "Agentic", "account_mask": "3333"},
    ]})
    [acct] = load_broker_accounts(cfg, tmp_path, now=NOW)
    assert acct.age_hours > STALE_AFTER_HOURS
    assert acct.stale is True


def _accounts_fixture() -> dict[str, AccountConfig]:
    return {
        "main": AccountConfig(name="Main", type="cash", balance_usd=10_000.0),
        "lotto": AccountConfig(name="Lotto", type="cash", balance_usd=1_000.0),
        "weekly": AccountConfig(name="Weekly", type="cash", balance_usd=10_000.0,
                                pool_member_of="main"),
        "beatmarket": AccountConfig(name="Beat-Market Sleeve", type="cash",
                                    balance_usd=10_000.0),
    }


def test_unmapped_sleeves_excludes_mapped_and_pool_members(tmp_path):
    _write_snapshot(tmp_path, "1111")
    cfg = _config(
        {"broker_accounts": [
            {"key": "individual", "label": "Individual", "account_mask": "1111",
             "sleeves": ["main", "lotto"]},
        ]},
        accounts=_accounts_fixture(),
    )
    accounts = load_broker_accounts(cfg, tmp_path, now=NOW)
    sleeves = unmapped_sleeves(cfg, accounts)
    assert [s.key for s in sleeves] == ["beatmarket"]
    assert sleeves[0].balance_usd == pytest.approx(10_000.0)


def test_unmapped_sleeves_empty_without_broker_mapping():
    cfg = _config({}, accounts=_accounts_fixture())
    assert unmapped_sleeves(cfg, []) == []


def test_selectable_account_keys_excludes_pool_members():
    cfg = _config({}, accounts=_accounts_fixture())
    assert selectable_account_keys(cfg) == ["main", "lotto", "beatmarket"]


# ─── API integration ─────────────────────────────────────────────────────────


def test_accounts_endpoints(tmp_path):
    def config_loader():
        return load_config(Path("/nonexistent.yaml"))

    app = create_app(config_loader=config_loader)
    client = TestClient(app)

    res = client.get("/api/v1/accounts/broker")
    assert res.status_code == 200
    body = res.json()
    # Default install has no broker_accounts block — panel hides itself.
    assert body["accounts"] == []
    assert body["unmapped_sleeves"] == []

    res = client.get("/api/v1/accounts/keys")
    assert res.status_code == 200
    # Defaults minus the pool-member 'weekly' key.
    assert res.json()["keys"] == ["main", "lotto"]
