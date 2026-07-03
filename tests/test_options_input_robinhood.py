"""Tests for the robinhood-mcp option-quote snapshot parser + CLI merge.

Sample quote mirrors the real get_option_quotes payload shape (generic test
contract; realistic market-data values only — no account fields).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from kill_sheet.cli import apply_options_snapshot
from options_input.robinhood import (
    STALE_AFTER_MINUTES,
    parse_robinhood_snapshot,
    snapshot_age_minutes,
)

_QUOTE = {
    "instrument_id": "11111111-2222-3333-4444-555555555555",
    "ask_price": "2.390000",
    "bid_price": "2.190000",
    "mark_price": "2.290000",
    "implied_volatility": "0.496581",
    "delta": "0.205823",
    "open_interest": 9272,
    "volume": 749,
    "updated_at": "2026-07-02T19:59:59.984415183Z",
}


def _snapshot(fetched_at: str = "2026-07-03T17:00:00Z", **overrides) -> dict:
    snap = {
        "source": "robinhood-mcp",
        "fetched_at": fetched_at,
        "ticker": "TST1",
        "strike": 100.0,
        "expiry": "2026-12-18",
        "contract_type": "call",
        "quote": dict(_QUOTE),
    }
    snap.update(overrides)
    return snap


_NOW = datetime(2026, 7, 3, 17, 5, tzinfo=timezone.utc)  # 5 min after fetch


def test_full_mapping_from_fresh_snapshot():
    parsed = parse_robinhood_snapshot(_snapshot(), now=_NOW)
    assert parsed.strike == 100.0
    assert parsed.expiry == "2026-12-18"
    assert parsed.contract_type == "call"
    assert parsed.premium == 2.29
    assert parsed.bid == 2.19
    assert parsed.ask == 2.39
    assert parsed.bid_ask_spread == 0.20
    assert parsed.delta == 0.205823
    assert parsed.open_interest == 9272
    # spot IV never becomes IV Rank
    assert parsed.iv_rank is None
    assert any("spot IV" in w for w in parsed.warnings)
    # fresh snapshot: no staleness warning
    assert not any("stale" in w for w in parsed.warnings)


def test_stale_snapshot_warns():
    old = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    parsed = parse_robinhood_snapshot(
        _snapshot(fetched_at="2026-07-03T10:00:00Z"), now=old
    )
    assert any("stale" in w for w in parsed.warnings)
    assert snapshot_age_minutes(_snapshot(fetched_at="2026-07-03T10:00:00Z"), now=old) == 120.0


def test_missing_quote_leaves_fields_none():
    snap = _snapshot()
    del snap["quote"]
    parsed = parse_robinhood_snapshot(snap, now=_NOW)
    assert parsed.premium is None
    assert parsed.open_interest is None
    assert any("no quote" in w for w in parsed.warnings)
    # contract meta still parsed
    assert parsed.strike == 100.0


def test_bad_fetched_at_reports_none_age():
    assert snapshot_age_minutes(_snapshot(fetched_at="yesterday-ish")) is None
    assert snapshot_age_minutes({"quote": {}}) is None


def test_apply_options_snapshot_explicit_flags_win():
    parsed = parse_robinhood_snapshot(_snapshot(), now=_NOW)
    args = argparse.Namespace(
        strike=105.0,          # explicit — must survive
        premium=None,
        expiry=None,
        contract_type=None,
        delta=None,
        oi=None,
        spread=None,
    )
    filled = apply_options_snapshot(args, parsed)
    assert args.strike == 105.0
    assert args.premium == 2.29
    assert args.expiry == "2026-12-18"
    assert args.contract_type == "call"
    assert args.oi == 9272
    assert args.spread == 0.20
    assert "strike" not in filled
    assert set(filled) == {"premium", "expiry", "contract_type", "delta", "oi", "spread"}


def test_stale_cutoff_constant_sane():
    assert 5 <= STALE_AFTER_MINUTES <= 120
