"""Tests for the --balance-json broker balance audit.

Snapshot mirrors the real get_portfolio payload shape (synthetic values
only — no real account data in fixtures, per the local-only PII rule).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from config.loader import AccountConfig, Config
from kill_sheet.balance_audit import (
    DRIFT_WARN_PCT,
    STALE_AFTER_MINUTES,
    audit_balance,
    load_balance_snapshot,
)
from positions.model import Position


def _config(balance: float = 10_000.0, raw: dict | None = None) -> Config:
    return Config(
        accounts={"main": AccountConfig(
            name="Main", type="cash", balance_usd=balance, raw={})},
        skills={},
        raw=raw or {},
    )


def _closed(pnl: float, closed_date: str = "2026-07-01") -> Position:
    return Position(
        id=f"t_{pnl}_{closed_date}", ticker="TST1", direction="long",
        instrument="call", account_key="main", entry_date=closed_date,
        contracts=1, strike=100, expiry="2026-08-21",
        premium_paid_per_contract=1.0, total_cost_usd=100, max_loss_usd=100,
        target_price=110, invalidation_price=95,
        status="closed", closed_date=closed_date, pnl_usd=pnl,
    )


def _snapshot(total_value: str = "10000.00",
              fetched_at: str = "2026-07-04T17:00:00Z",
              **overrides) -> dict:
    snap = {
        "source": "robinhood-mcp",
        "fetched_at": fetched_at,
        "account": "…4907",
        "portfolio": {"total_value": total_value, "cash": "9000.00"},
    }
    snap.update(overrides)
    return snap


_NOW = datetime(2026, 7, 4, 17, 10, tzinfo=timezone.utc)  # 10 min after fetch


# ─── audit_balance ───────────────────────────────────────────────────────


def test_matching_totals_pass_clean():
    audit = audit_balance(_snapshot("10150.00"), _config(),
                          [_closed(150.0)], now=_NOW)
    assert audit.broker_total_usd == 10_150.0
    assert audit.model_total_usd == 10_150.0
    assert audit.drift_pct == pytest.approx(0.0)
    assert audit.warnings == []
    assert "✓" in audit.line()


def test_drift_at_or_above_cutoff_warns():
    # broker 3% above a 10,000 book model
    audit = audit_balance(_snapshot("10300.00"), _config(), [], now=_NOW)
    assert audit.drift_pct == pytest.approx(3.0)
    assert len(audit.warnings) == 1
    assert "reconcile" in audit.warnings[0]
    assert "⚠" in audit.line()


def test_drift_below_cutoff_is_clean():
    audit = audit_balance(_snapshot("10100.00"), _config(), [], now=_NOW)
    assert audit.drift_pct == pytest.approx(1.0)
    assert abs(audit.drift_pct) < DRIFT_WARN_PCT
    assert audit.warnings == []


def test_anchor_config_drives_model_total():
    """balance.anchor is authoritative: base=anchor, realized counts only
    post-anchor closes (same semantics as the dashboard stage banner)."""
    cfg = _config(raw={"balance": {"anchor_usd": 10_500.0,
                                   "anchor_date": "2026-07-01"}})
    closed = [
        _closed(999.0, closed_date="2026-06-30"),  # pre-anchor → excluded
        _closed(50.0, closed_date="2026-07-02"),   # post-anchor → counted
    ]
    audit = audit_balance(_snapshot("10550.00"), cfg, closed, now=_NOW)
    assert audit.model_total_usd == 10_550.0
    assert audit.drift_pct == pytest.approx(0.0)


def test_missing_portfolio_raises():
    snap = _snapshot()
    del snap["portfolio"]
    with pytest.raises(ValueError, match="portfolio"):
        audit_balance(snap, _config(), [], now=_NOW)


def test_non_numeric_total_raises():
    with pytest.raises(ValueError, match="total_value"):
        audit_balance(_snapshot("n/a"), _config(), [], now=_NOW)


def test_account_is_masked_even_when_written_raw():
    audit = audit_balance(_snapshot(account="5QS44907"), _config(),
                          [], now=_NOW)
    assert audit.account_masked == "…4907"
    assert "5QS4" not in audit.line()


# ─── load_balance_snapshot ───────────────────────────────────────────────


def test_load_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_balance_snapshot(tmp_path / "nope.json")


def test_load_bad_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_balance_snapshot(p)


# ─── CLI staleness gate ──────────────────────────────────────────────────


def _write_snapshot(tmp_path, **kwargs):
    p = tmp_path / "balance.json"
    p.write_text(json.dumps(_snapshot(**kwargs)))
    return p


def test_cli_refuses_stale_snapshot(tmp_path):
    """A snapshot past the cutoff dies at parse time (before any scan)."""
    from kill_sheet.cli import main
    p = _write_snapshot(tmp_path, fetched_at="2020-01-01T00:00:00Z")
    with pytest.raises(SystemExit) as exc:
        main(["TST1", "--direction", "long", "--balance-json", str(p)])
    assert exc.value.code == 2


def test_cli_refuses_snapshot_without_fetched_at(tmp_path):
    from kill_sheet.cli import main
    snap = _snapshot()
    del snap["fetched_at"]
    p = tmp_path / "balance.json"
    p.write_text(json.dumps(snap))
    with pytest.raises(SystemExit) as exc:
        main(["TST1", "--direction", "long", "--balance-json", str(p)])
    assert exc.value.code == 2


def test_stale_cutoff_is_wider_than_options_cutoff():
    """Balance is an anchor, not a fill price — cutoff is deliberately wider
    than the 30-min options-quote window, but still same-day."""
    from options_input.robinhood import STALE_AFTER_MINUTES as OPTIONS_CUTOFF
    assert STALE_AFTER_MINUTES > OPTIONS_CUTOFF
    assert STALE_AFTER_MINUTES <= 24 * 60


# ─── sheet render ────────────────────────────────────────────────────────


def test_sheet_renders_balance_audit_line():
    from kill_sheet.model import KillSheet

    audit = audit_balance(_snapshot("10000.00"), _config(), [], now=_NOW)
    sheet = KillSheet(
        ticker="TST1", direction="long", intent="SWING", trigger_tf="Daily",
        bias="bullish", confidence="high", confidence_reason="test",
        account_key="main", account_name="Main",
        account_balance_usd=10_000, risk_conviction="high", risk_pct=0.025,
        max_risk_usd=250, bar_date="2026-07-02", close_at_generation=100.0,
        sqn_value=1.2, regime="bull",
        ma_10=99, ma_20=98, ma_50=95, ma_200=90, ma_stack="full_bull",
        stoch_k=42.0, stoch_d=38.0, stoch_signal="rising",
        stoch_zone="neutral",
        balance_audit=audit.line(),
    )
    rendered = sheet.to_text()
    assert "Balance audit:" in rendered
    assert "book model" in rendered
