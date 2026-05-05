"""Tests for src/storage/cache.py — SQLite cache layer.

JSON remains canonical; the cache is a derived index. These tests guard:
- DDL applies idempotently
- Upserts round-trip through the schema
- Queries match the filter semantics they advertise
- Rebuild from JSON wipes and reloads cleanly
- Aggregates compute correctly across mixed states
"""
from __future__ import annotations

from pathlib import Path

import pytest

from storage.cache import (
    SCHEMA_VERSION,
    Cache,
    _to_ts,
)


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(path=tmp_path / "cache.sqlite")
    yield c
    c.close()


# ── Schema ─────────────────────────────────────────────────────────────────


def test_schema_version_recorded(cache: Cache) -> None:
    assert cache.schema_version() == SCHEMA_VERSION


def test_ddl_idempotent(tmp_path: Path) -> None:
    """Opening twice should not error or wipe data."""
    p = tmp_path / "cache.sqlite"
    c1 = Cache(path=p)
    c1.upsert_position(_pos(id="abc", ticker="SPY"))
    c1.close()

    c2 = Cache(path=p)
    rows = c2.query_positions()
    assert len(rows) == 1
    assert rows[0]["id"] == "abc"
    c2.close()


def test_clear_all_wipes_every_table(cache: Cache) -> None:
    cache.upsert_position(_pos(id="p1"))
    cache.upsert_weekly_review(_weekly(week_start="2026-04-26"))
    cache.clear_all()
    assert cache.query_positions() == []
    assert cache.query_weekly_reviews() == []


# ── Helpers to build payloads ──────────────────────────────────────────────


def _pos(**overrides) -> dict:
    base = {
        "id": "pos_1",
        "ticker": "SPY",
        "direction": "long",
        "instrument": "call",
        "account_key": "main",
        "status": "open",
        "skill": None,
        "tier": None,
        "entry_date": "2026-04-01T10:00:00+00:00",
        "closed_date": None,
        "contracts": 1,
        "shares": None,
        "strike": 580,
        "expiry": "2026-06-19",
        "premium_paid_per_contract": 5.50,
        "total_cost_usd": 550.0,
        "max_loss_usd": 550.0,
        "target_price": 600,
        "invalidation_price": 560,
        "pnl_usd": None,
        "notes": None,
    }
    base.update(overrides)
    return base


def _score(**overrides) -> dict:
    base = {
        "position_id": "pos_1",
        "kill_sheet_id": None,
        "closed_at": "2026-04-15T14:00:00+00:00",
        "ticker": "SPY",
        "direction": "long",
        "instrument": "call",
        "entry_at": "2026-04-01T10:00:00+00:00",
        "pnl_usd": 220.0,
        "score_numerator": 14,
        "score_denominator": 15,
        "score": 0.9333,
        "profitable_violation": False,
        "counterfactual_loss_usd": None,
        "full_adherence": False,
        "profitable_violation_resolution": None,
        "notes": "",
        "scored_at": "2026-04-15T14:30:00+00:00",
        "rules": [
            {"rule_id": "R01", "score": "Y", "auto_evaluated": True, "note": None},
            {"rule_id": "R02", "score": "N", "auto_evaluated": True, "note": "iv too high"},
        ],
    }
    base.update(overrides)
    return base


def _weekly(**overrides) -> dict:
    base = {
        "week_start": "2026-04-26",
        "week_end": "2026-05-02",
        "trades_scored": 3,
        "avg_discipline_score": 0.93,
        "full_adherence_count": 2,
        "any_violation_count": 1,
        "profitable_violation_count": 0,
        "most_violated_rule": "R02",
        "drift_trend": "flat",
        "pnl_usd": 420.0,
        "lockdown_behavior": None,
    }
    base.update(overrides)
    return base


def _sunday_scan(**overrides) -> dict:
    base = {
        "scan_time_utc": "2026-04-28T13:00:00+00:00",
        "recommendation": "trade",
        "headline": "QQQ long fires",
        "setups": [
            {"asset": "QQQ", "direction": "long", "score": 75, "status": "fires"},
            {"asset": "GLD", "direction": "long", "score": 45, "status": "watch"},
        ],
    }
    base.update(overrides)
    return base


# ── Position upserts and queries ───────────────────────────────────────────


def test_upsert_position_roundtrip(cache: Cache) -> None:
    cache.upsert_position(_pos(id="abc"))
    rows = cache.query_positions()
    assert len(rows) == 1
    assert rows[0]["id"] == "abc"
    assert rows[0]["ticker"] == "SPY"


def test_upsert_position_overwrites_same_id(cache: Cache) -> None:
    cache.upsert_position(_pos(id="abc", status="open"))
    cache.upsert_position(_pos(id="abc", status="closed", pnl_usd=300))
    rows = cache.query_positions()
    assert len(rows) == 1
    assert rows[0]["status"] == "closed"
    assert rows[0]["pnl_usd"] == 300


def test_query_positions_by_account(cache: Cache) -> None:
    cache.upsert_position(_pos(id="p_main", account_key="main"))
    cache.upsert_position(_pos(id="p_lotto", account_key="lotto"))
    main_only = cache.query_positions(account="main")
    assert [r["id"] for r in main_only] == ["p_main"]


def test_query_positions_by_status(cache: Cache) -> None:
    cache.upsert_position(_pos(id="p_open", status="open"))
    cache.upsert_position(
        _pos(id="p_closed", status="closed",
             closed_date="2026-04-30T16:00:00+00:00", pnl_usd=100)
    )
    closed = cache.query_positions(status="closed")
    assert [r["id"] for r in closed] == ["p_closed"]


def test_query_positions_by_close_date_range(cache: Cache) -> None:
    cache.upsert_position(
        _pos(id="p_old", status="closed",
             closed_date="2026-03-01T10:00:00+00:00", pnl_usd=10)
    )
    cache.upsert_position(
        _pos(id="p_new", status="closed",
             closed_date="2026-04-15T10:00:00+00:00", pnl_usd=20)
    )
    in_april = cache.query_positions(
        status="closed",
        closed_after="2026-04-01",
        closed_before="2026-05-01",
    )
    assert [r["id"] for r in in_april] == ["p_new"]


def test_delete_position(cache: Cache) -> None:
    cache.upsert_position(_pos(id="abc"))
    cache.delete_position("abc")
    assert cache.query_positions() == []


# ── Discipline scores ──────────────────────────────────────────────────────


def test_upsert_discipline_score_with_rules(cache: Cache) -> None:
    cache.upsert_discipline_score(_score(position_id="p1"))
    scores = cache.query_discipline_scores()
    assert len(scores) == 1
    rules = cache.conn.execute(
        "SELECT * FROM discipline_rules WHERE position_id = 'p1' ORDER BY rule_id"
    ).fetchall()
    assert [dict(r)["rule_id"] for r in rules] == ["R01", "R02"]


def test_upsert_discipline_replaces_rules(cache: Cache) -> None:
    """Rewriting a score must replace its rules, not accumulate."""
    cache.upsert_discipline_score(_score(position_id="p1"))
    altered = _score(
        position_id="p1",
        rules=[{"rule_id": "R03", "score": "Y", "auto_evaluated": True, "note": None}],
    )
    cache.upsert_discipline_score(altered)
    rules = cache.conn.execute(
        "SELECT rule_id FROM discipline_rules WHERE position_id = 'p1'"
    ).fetchall()
    assert [dict(r)["rule_id"] for r in rules] == ["R03"]


def test_query_discipline_by_full_adherence(cache: Cache) -> None:
    cache.upsert_discipline_score(_score(position_id="p1", full_adherence=True))
    cache.upsert_discipline_score(_score(position_id="p2", full_adherence=False))
    full = cache.query_discipline_scores(full_adherence=True)
    assert [r["position_id"] for r in full] == ["p1"]


def test_query_discipline_by_profitable_violation(cache: Cache) -> None:
    cache.upsert_discipline_score(_score(position_id="p1", profitable_violation=True))
    cache.upsert_discipline_score(_score(position_id="p2", profitable_violation=False))
    pv = cache.query_discipline_scores(profitable_violation=True)
    assert [r["position_id"] for r in pv] == ["p1"]


def test_query_discipline_limit(cache: Cache) -> None:
    for i in range(5):
        cache.upsert_discipline_score(
            _score(position_id=f"p{i}",
                   closed_at=f"2026-04-{10+i:02d}T12:00:00+00:00")
        )
    scores = cache.query_discipline_scores(limit=2)
    assert len(scores) == 2
    # Newest first
    assert scores[0]["position_id"] == "p4"


# ── Weekly reviews ─────────────────────────────────────────────────────────


def test_upsert_weekly_review_roundtrip(cache: Cache) -> None:
    cache.upsert_weekly_review(_weekly(week_start="2026-04-26"))
    rows = cache.query_weekly_reviews()
    assert len(rows) == 1
    assert rows[0]["pnl_usd"] == 420.0


def test_query_weekly_reviews_descending(cache: Cache) -> None:
    cache.upsert_weekly_review(_weekly(week_start="2026-04-19", week_end="2026-04-25"))
    cache.upsert_weekly_review(_weekly(week_start="2026-04-26", week_end="2026-05-02"))
    rows = cache.query_weekly_reviews()
    assert [r["week_start"] for r in rows] == ["2026-04-26", "2026-04-19"]


# ── Sunday scans ───────────────────────────────────────────────────────────


def test_upsert_sunday_scan(cache: Cache) -> None:
    cache.upsert_sunday_scan(_sunday_scan())
    rows = cache.query_recent_sunday_scans()
    assert len(rows) == 1
    assert rows[0]["scan_date"] == "2026-04-28"
    assert rows[0]["top_setup_asset"] == "QQQ"


def test_query_sunday_scans_descending(cache: Cache) -> None:
    cache.upsert_sunday_scan(
        _sunday_scan(scan_time_utc="2026-04-21T13:00:00+00:00")
    )
    cache.upsert_sunday_scan(
        _sunday_scan(scan_time_utc="2026-04-28T13:00:00+00:00")
    )
    rows = cache.query_recent_sunday_scans()
    assert [r["scan_date"] for r in rows] == ["2026-04-28", "2026-04-21"]


# ── Aggregates ─────────────────────────────────────────────────────────────


def test_realized_pnl_total(cache: Cache) -> None:
    cache.upsert_position(_pos(id="p1", status="closed",
                               closed_date="2026-04-10T16:00:00+00:00",
                               pnl_usd=100))
    cache.upsert_position(_pos(id="p2", status="closed",
                               closed_date="2026-04-20T16:00:00+00:00",
                               pnl_usd=-50))
    cache.upsert_position(_pos(id="p3", status="open"))  # excluded
    assert cache.realized_pnl() == 50.0


def test_realized_pnl_by_account(cache: Cache) -> None:
    cache.upsert_position(_pos(id="p_main", status="closed",
                               account_key="main",
                               closed_date="2026-04-10T16:00:00+00:00",
                               pnl_usd=200))
    cache.upsert_position(_pos(id="p_lotto", status="closed",
                               account_key="lotto",
                               closed_date="2026-04-15T16:00:00+00:00",
                               pnl_usd=80))
    assert cache.realized_pnl(account="main") == 200.0
    assert cache.realized_pnl(account="lotto") == 80.0


def test_realized_pnl_by_date_range(cache: Cache) -> None:
    cache.upsert_position(_pos(id="p_old", status="closed",
                               closed_date="2026-03-15T10:00:00+00:00",
                               pnl_usd=100))
    cache.upsert_position(_pos(id="p_new", status="closed",
                               closed_date="2026-04-15T10:00:00+00:00",
                               pnl_usd=200))
    april = cache.realized_pnl(closed_after="2026-04-01", closed_before="2026-05-01")
    assert april == 200.0


def test_discipline_summary(cache: Cache) -> None:
    cache.upsert_discipline_score(_score(position_id="p1", score=1.0,
                                         full_adherence=True))
    cache.upsert_discipline_score(_score(position_id="p2", score=0.9,
                                         full_adherence=False,
                                         profitable_violation=True))
    s = cache.discipline_summary()
    assert s["scored"] == 2
    assert s["full_adherence_count"] == 1
    assert s["profitable_violation_count"] == 1
    assert 0.94 < s["avg_score"] < 0.96  # mean of 1.0 + 0.9333


# ── Rebuild ────────────────────────────────────────────────────────────────


def test_rebuild_from_json_wipes_and_reloads(cache: Cache) -> None:
    # Pre-existing data that should be dropped
    cache.upsert_position(_pos(id="stale"))

    counts = cache.rebuild_from_json(
        positions=[_pos(id="fresh1"), _pos(id="fresh2", ticker="QQQ")],
        discipline_scores=[_score(position_id="fresh1")],
        weekly_reviews=[_weekly()],
        sunday_scans=[_sunday_scan()],
    )
    assert counts == {
        "positions": 2,
        "discipline_scores": 1,
        "weekly_reviews": 1,
        "sunday_scans": 1,
    }
    assert {p["id"] for p in cache.query_positions()} == {"fresh1", "fresh2"}


def test_rebuild_skips_bad_payloads(cache: Cache) -> None:
    """A malformed payload shouldn't abort the whole rebuild."""
    bad_position = {"id": "bad"}  # missing required fields
    counts = cache.rebuild_from_json(
        positions=[_pos(id="good1"), bad_position, _pos(id="good2")],
    )
    assert counts["positions"] == 2
    assert {p["id"] for p in cache.query_positions()} == {"good1", "good2"}


# ── Helpers ────────────────────────────────────────────────────────────────


def test_to_ts_handles_iso_z_suffix() -> None:
    assert _to_ts("2026-04-01T10:00:00Z") is not None


def test_to_ts_handles_date_only() -> None:
    assert _to_ts("2026-04-01") is not None


def test_to_ts_returns_none_on_garbage() -> None:
    assert _to_ts("not a date") is None
    assert _to_ts(None) is None
    assert _to_ts("") is None
