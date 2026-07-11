"""Live-snapshot reconcile: broker open positions vs journal opens.

Snapshot row shapes mirror real robinhood-trading MCP responses
(get_option_positions / get_option_instruments / get_equity_positions,
captured 2026-07-11) with synthetic values.
"""
from __future__ import annotations

import json

import pytest

from positions.model import Position
from reconcile.live import (
    LiveSnapshotError,
    format_live_report,
    live_reconcile,
)


def _opt_position(symbol="TTD", option_id="oid-1", qty="3.0000",
                  expiry="2026-07-17", type_="long") -> dict:
    return {
        "option_id": option_id,
        "chain_id": "cid-1",
        "chain_symbol": symbol,
        "type": type_,
        "quantity": qty,
        "average_price": "33.0000",
        "expiration_date": expiry,
        "trade_value_multiplier": "100.0000",
        "pending_buy_quantity": "0.0000",
        "opened_at": "2026-07-10T15:38:28.917958Z",
    }


def _instrument(option_id="oid-1", symbol="TTD", strike="19.0000",
                type_="put", expiry="2026-07-17") -> dict:
    return {
        "id": option_id,
        "chain_id": "cid-1",
        "chain_symbol": symbol,
        "expiration_date": expiry,
        "strike_price": strike,
        "type": type_,
        "state": "active",
        "tradability": "tradable",
    }


def _equity(symbol="MRLN", qty="70.0000") -> dict:
    return {"symbol": symbol, "quantity": qty, "average_buy_price": "7.14"}


def _snapshot(option_positions=(), option_instruments=(),
              equity_positions=()) -> dict:
    return {
        "source": "robinhood-mcp",
        "fetched_at": "2026-07-11T20:15:00Z",
        "account": "…4907",
        "option_positions": list(option_positions),
        "option_instruments": list(option_instruments),
        "equity_positions": list(equity_positions),
    }


def _journal_option(ticker="TTD", kind="put", strike=19.0,
                    expiry="2026-07-17", contracts=3,
                    account="lotto") -> Position:
    return Position.open_options_position(
        ticker=ticker, direction="long", contract_type=kind,
        account_key=account, strike=strike, expiry=expiry,
        premium=0.33, contracts=contracts,
    )


def _journal_shares(ticker="MRLN", shares=70,
                    account="portfolio") -> Position:
    return Position.open_shares_position(
        ticker=ticker, direction="long", account_key=account,
        shares=shares, entry_price=7.14, invalidation_price=5.0,
    )


# ─── clean states ────────────────────────────────────────────────────────────


def test_matching_option_and_shares_is_clean():
    snap = _snapshot(
        option_positions=[_opt_position()],
        option_instruments=[_instrument()],
        equity_positions=[_equity()],
    )
    report = live_reconcile(snap, [_journal_option(), _journal_shares()])
    assert report.findings == []
    assert report.fills_count == 2
    assert not report.has_high_severity
    assert "✓" in format_live_report(report)


def test_closed_journal_positions_are_ignored():
    closed = _journal_option(ticker="PYPL", strike=42.0, expiry="2026-06-26")
    closed.close(pnl_usd=100.0, notes="closed")
    snap = _snapshot(
        option_positions=[_opt_position()],
        option_instruments=[_instrument()],
    )
    report = live_reconcile(snap, [_journal_option(), closed])
    assert report.findings == []


def test_zero_quantity_broker_rows_are_ignored():
    snap = _snapshot(
        option_positions=[_opt_position(qty="0.0000")],
        option_instruments=[_instrument()],
        equity_positions=[_equity(qty="0")],
    )
    report = live_reconcile(snap, [])
    assert report.fills_count == 0
    assert report.findings == []


# ─── discrepancies ───────────────────────────────────────────────────────────


def test_unlogged_broker_open_is_high():
    snap = _snapshot(
        option_positions=[_opt_position()],
        option_instruments=[_instrument()],
    )
    report = live_reconcile(snap, [])
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.category == "unlogged_open"
    assert f.severity == "high"
    assert "TTD" in f.contract
    assert report.has_high_severity


def test_journal_open_missing_at_broker_is_high():
    report = live_reconcile(_snapshot(), [_journal_option()])
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.category == "journal_stale_open"
    assert f.severity == "high"
    assert f.position_ids


def test_quantity_mismatch_is_medium():
    snap = _snapshot(
        option_positions=[_opt_position(qty="2.0000")],
        option_instruments=[_instrument()],
    )
    report = live_reconcile(snap, [_journal_option(contracts=3)])
    assert len(report.findings) == 1
    f = report.findings[0]
    assert f.category == "qty_mismatch"
    assert f.severity == "medium"
    assert not report.has_high_severity


def test_partial_exit_journal_qty_uses_remaining_contracts():
    p = _journal_option(contracts=3)
    p.partial_close(contracts_closed=1, pnl_usd=10.0, notes="scale out")
    snap = _snapshot(
        option_positions=[_opt_position(qty="2.0000")],
        option_instruments=[_instrument()],
    )
    report = live_reconcile(snap, [p])
    assert report.findings == []


def test_same_contract_split_across_sleeves_sums_journal_qty():
    snap = _snapshot(
        option_positions=[_opt_position(qty="3.0000")],
        option_instruments=[_instrument()],
    )
    positions = [
        _journal_option(contracts=2, account="main"),
        _journal_option(contracts=1, account="lotto"),
    ]
    report = live_reconcile(snap, positions)
    assert report.findings == []


# ─── snapshot quality ────────────────────────────────────────────────────────


def test_missing_instrument_row_warns_and_skips():
    snap = _snapshot(option_positions=[_opt_position()])
    report = live_reconcile(snap, [])
    assert report.findings == []          # position skipped, not misfiled
    assert any("no matching instrument" in w for w in report.warnings)


def test_short_option_type_warns():
    snap = _snapshot(
        option_positions=[_opt_position(type_="short")],
        option_instruments=[_instrument()],
    )
    report = live_reconcile(snap, [_journal_option()])
    assert any("SHORT option" in w for w in report.warnings)


def test_malformed_snapshot_raises():
    with pytest.raises(LiveSnapshotError):
        live_reconcile({"option_positions": []}, [])


# ─── CLI dispatch ────────────────────────────────────────────────────────────


def test_cli_json_dispatch_exit_codes(tmp_path, monkeypatch):
    from reconcile.cli import main

    snap_file = tmp_path / "snapshot.json"
    snap_file.write_text(json.dumps(_snapshot(
        option_positions=[_opt_position()],
        option_instruments=[_instrument()],
    )))

    class _FakeStore:
        def list_all(self):
            return []

    monkeypatch.setattr("reconcile.cli.PositionStore", _FakeStore)
    # unlogged open → exit 1
    assert main([str(snap_file)]) == 1

    out = tmp_path / "report.json"
    assert main([str(snap_file), "--json", str(out)]) == 1
    payload = json.loads(out.read_text())
    assert payload["findings"][0]["category"] == "unlogged_open"

    # malformed snapshot → exit 2
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert main([str(bad)]) == 2
