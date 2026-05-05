"""End-to-end tests: stores save JSON and write through to the SQLite cache.

These tests verify the full chain — when you call store.save(), the JSON
file gets written atomically AND the cache reflects the new state. They
also verify the failure-mode contract: a broken cache must not propagate
to the JSON write.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from discipline import (
    DisciplineScore,
    DisciplineStore,
    RuleResult,
    WeeklyReview,
)
from positions import Position, PositionStore
from storage.cache import Cache


@pytest.fixture
def cache(tmp_path: Path) -> Cache:
    c = Cache(path=tmp_path / "cache.sqlite")
    yield c
    c.close()


def _new_position() -> Position:
    return Position.open_options_position(
        ticker="SPY",
        direction="long",
        contract_type="call",
        account_key="main",
        strike=580,
        expiry="2026-06-19",
        premium=5.50,
        contracts=1,
    )


# ── PositionStore × Cache ──────────────────────────────────────────────────


def test_position_save_writes_through_to_cache(tmp_path: Path, cache: Cache):
    store = PositionStore(path=tmp_path / "positions.json", cache=cache)
    p = _new_position()
    store.add(p)

    rows = cache.query_positions()
    assert len(rows) == 1
    assert rows[0]["id"] == p.id
    assert rows[0]["ticker"] == "SPY"
    assert rows[0]["status"] == "open"


def test_position_close_propagates_status_to_cache(tmp_path: Path, cache: Cache):
    store = PositionStore(path=tmp_path / "positions.json", cache=cache)
    p = store.add(_new_position())
    store.close(p.id, pnl_usd=150)

    rows = cache.query_positions()
    assert rows[0]["status"] == "closed"
    assert rows[0]["pnl_usd"] == 150


def test_position_save_succeeds_when_cache_fails(tmp_path: Path, caplog):
    """Broken cache must not block the JSON write — durability invariant."""
    class BrokenCache:
        def upsert_position(self, _payload):
            raise RuntimeError("simulated cache fault")

    store = PositionStore(
        path=tmp_path / "positions.json",
        cache=BrokenCache(),
    )
    import logging
    with caplog.at_level(logging.ERROR, logger="positions.store"):
        store.add(_new_position())

    # JSON file still wrote correctly
    fresh = PositionStore(path=tmp_path / "positions.json")
    assert len(fresh.list_all()) == 1
    # The fault was logged
    assert any("cache upsert failed" in r.message for r in caplog.records)


def test_position_writethrough_no_cache_works(tmp_path: Path):
    """Stores must work standalone without a cache — backwards compat."""
    store = PositionStore(path=tmp_path / "positions.json", cache=None)
    p = _new_position()
    store.add(p)
    assert len(store.list_all()) == 1


# ── DisciplineStore × Cache ────────────────────────────────────────────────


def test_discipline_save_score_writes_through(tmp_path: Path, cache: Cache):
    store = DisciplineStore(base_dir=tmp_path / "discipline", cache=cache)
    score = DisciplineScore.stamp(
        position_id="pos_1",
        kill_sheet_id=None,
        closed_at="2026-04-15T14:00:00+00:00",
        ticker="SPY",
        direction="long",
        instrument="call",
        pnl_usd=200,
        rules=[
            RuleResult(rule_id="R01", score="Y", auto_evaluated=True),
            RuleResult(rule_id="R02", score="N", auto_evaluated=True, note="iv high"),
        ],
        score_numerator=1,
        score_denominator=2,
    )
    store.save_score(score)

    rows = cache.query_discipline_scores()
    assert len(rows) == 1
    assert rows[0]["position_id"] == "pos_1"


def test_discipline_save_weekly_writes_through(tmp_path: Path, cache: Cache):
    store = DisciplineStore(base_dir=tmp_path / "discipline", cache=cache)
    review = WeeklyReview(
        week_start="2026-04-26",
        week_end="2026-05-02",
        trades_scored=3,
        avg_discipline_score=0.93,
        full_adherence_count=2,
        any_violation_count=1,
        profitable_violation_count=0,
        most_violated_rule="R02",
        drift_trend="flat",
        pnl_usd=420.0,
    )
    store.save_weekly(review)

    rows = cache.query_weekly_reviews()
    assert len(rows) == 1
    assert rows[0]["pnl_usd"] == 420.0


def test_discipline_delete_propagates_to_cache(tmp_path: Path, cache: Cache):
    store = DisciplineStore(base_dir=tmp_path / "discipline", cache=cache)
    score = DisciplineScore.stamp(
        position_id="pos_1",
        kill_sheet_id=None,
        closed_at="2026-04-15T14:00:00+00:00",
    )
    store.save_score(score)
    assert len(cache.query_discipline_scores()) == 1

    store.delete_score("pos_1")
    assert cache.query_discipline_scores() == []

