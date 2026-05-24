"""Tests for src/discipline/dashboard.py + GET /api/v1/dashboard/state.

Covers:
- Account balance dedupes pool members (weekly shares main's pool)
- Realized P&L sums non-legacy closed positions only
- Stage detection rolls over at $100K
- Unreviewed weeks: requires closed non-legacy trades, no saved review,
  excludes the current in-progress week
- Newest-week-first ordering
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from config.loader import AccountConfig, Config, SkillConfig
from discipline.dashboard import (
    compute_account_balance,
    compute_dashboard_state,
    find_unreviewed_weeks,
)
from discipline.model import DisciplineScore, RuleResult, WeeklyReview
from discipline.store import DisciplineStore
from positions.model import Position


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def _config_with(accounts: dict[str, AccountConfig]) -> Config:
    return Config(accounts=accounts, skills={}, raw={})


def _account(name: str, balance: float, *, pool_member_of: str | None = None) -> AccountConfig:
    return AccountConfig(
        name=name, type="cash", balance_usd=balance, raw={},
        pool_member_of=pool_member_of,
    )


def _closed_position(
    *, ticker: str = "SPY", pnl: float | None = 100.0, closed_date: str = "2026-05-10",
    instrument: str = "call",
) -> Position:
    return Position(
        id=f"test_{ticker}_{closed_date}",
        ticker=ticker,
        direction="long",
        instrument=instrument,
        account_key="main",
        entry_date=closed_date,  # entry/close same for test simplicity
        contracts=1, strike=400, expiry="2026-07-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        target_price=420, invalidation_price=395,
        status="closed",
        closed_date=closed_date,
        pnl_usd=pnl,
    )


def _empty_store(tmp_path: Path) -> DisciplineStore:
    return DisciplineStore(base_dir=tmp_path / "discipline")


# ─────────────────────────────────────────────────────────────────────────
# compute_account_balance
# ─────────────────────────────────────────────────────────────────────────


def test_balance_sums_distinct_pools_only():
    """Weekly account with pool_member_of='main' must NOT add to base."""
    cfg = _config_with({
        "main": _account("Main", 10_000),
        "lotto": _account("Lotto", 1_000),
        "weekly": _account("Weekly", 10_000, pool_member_of="main"),
    })
    base, realized, total = compute_account_balance(cfg, [])
    # 10_000 (main) + 1_000 (lotto). Weekly is pooled, excluded.
    assert base == 11_000
    assert realized == 0
    assert total == 11_000


def test_balance_includes_realized_pnl_from_closed_positions():
    cfg = _config_with({"main": _account("Main", 10_000)})
    closed = [
        _closed_position(pnl=200),
        _closed_position(ticker="QQQ", pnl=-50, closed_date="2026-05-12"),
    ]
    base, realized, total = compute_account_balance(cfg, closed)
    assert base == 10_000
    assert realized == 150
    assert total == 10_150


def test_balance_skips_legacy_closed_positions():
    """Trades closed before 2026-05-02 are exempt from stage accounting."""
    cfg = _config_with({"main": _account("Main", 10_000)})
    closed = [
        _closed_position(pnl=500, closed_date="2026-04-15"),  # legacy → skip
        _closed_position(pnl=200, closed_date="2026-05-10"),  # post-rollout → count
    ]
    base, realized, total = compute_account_balance(cfg, closed)
    assert realized == 200


def test_balance_skips_open_positions():
    """Open positions are at-risk capital, not realized P&L."""
    open_pos = _closed_position(pnl=999)
    open_pos.status = "open"
    open_pos.closed_date = None
    open_pos.pnl_usd = None
    cfg = _config_with({"main": _account("Main", 10_000)})
    _, realized, _ = compute_account_balance(cfg, [open_pos])
    assert realized == 0


# ─────────────────────────────────────────────────────────────────────────
# find_unreviewed_weeks
# ─────────────────────────────────────────────────────────────────────────


def test_unreviewed_weeks_skips_current_week(tmp_path):
    """Don't nag for review until the week ends."""
    store = _empty_store(tmp_path)
    today = date(2026, 5, 13)  # Wednesday
    # Position closed earlier this week (Sunday onwards)
    closed = [_closed_position(closed_date="2026-05-11")]  # Monday of current week
    weeks = find_unreviewed_weeks(closed, store, today=today)
    assert weeks == []


def test_unreviewed_weeks_returns_past_weeks_with_closed_trades(tmp_path):
    store = _empty_store(tmp_path)
    today = date(2026, 5, 20)  # Wednesday of week starting 2026-05-17
    closed = [
        _closed_position(closed_date="2026-05-12"),  # week 2026-05-10 (Sun) → 05-16 (Sat)
        _closed_position(ticker="QQQ", closed_date="2026-05-13"),
    ]
    weeks = find_unreviewed_weeks(closed, store, today=today)
    assert len(weeks) == 1
    assert weeks[0].week_start == "2026-05-10"
    assert weeks[0].week_end == "2026-05-16"
    assert weeks[0].closed_trade_count == 2


def test_unreviewed_weeks_excludes_already_reviewed(tmp_path):
    """If a WeeklyReview file exists for the week, don't list it."""
    store = _empty_store(tmp_path)
    today = date(2026, 5, 20)

    # Save a review for the week containing 2026-05-12
    review = WeeklyReview(
        week_start="2026-05-10",
        week_end="2026-05-16",
        trades_scored=1,
        avg_discipline_score=1.0,
        full_adherence_count=1,
        any_violation_count=0,
        profitable_violation_count=0,
        most_violated_rule=None,
        drift_trend="flat",
        pnl_usd=100.0,
    )
    store.save_weekly(review)

    closed = [_closed_position(closed_date="2026-05-12")]
    assert find_unreviewed_weeks(closed, store, today=today) == []


def test_unreviewed_weeks_skips_legacy_positions(tmp_path):
    store = _empty_store(tmp_path)
    today = date(2026, 5, 20)
    # Pre-rollout closure → exempt; should NOT trigger unreviewed
    closed = [_closed_position(closed_date="2026-04-15")]
    assert find_unreviewed_weeks(closed, store, today=today) == []


def test_unreviewed_weeks_newest_first(tmp_path):
    store = _empty_store(tmp_path)
    today = date(2026, 5, 25)  # Sunday → not in a current-week skip
    closed = [
        _closed_position(closed_date="2026-05-05"),  # week 2026-05-03
        _closed_position(ticker="QQQ", closed_date="2026-05-12"),  # week 2026-05-10
        _closed_position(ticker="GLD", closed_date="2026-05-19"),  # week 2026-05-17
    ]
    weeks = find_unreviewed_weeks(closed, store, today=today)
    week_starts = [w.week_start for w in weeks]
    assert week_starts == ["2026-05-17", "2026-05-10", "2026-05-03"]


# ─────────────────────────────────────────────────────────────────────────
# compute_dashboard_state
# ─────────────────────────────────────────────────────────────────────────


def test_dashboard_state_stage_1_below_threshold(tmp_path):
    cfg = _config_with({
        "main": _account("Main", 10_000),
        "lotto": _account("Lotto", 1_000),
    })
    store = _empty_store(tmp_path)
    state = compute_dashboard_state(
        cfg, [], discipline_store=store, today=date(2026, 5, 20),
    )
    assert state.stage == "stage_1"
    assert state.account_balance_usd == 11_000
    assert state.threshold_usd == 100_000
    assert 0.0 < state.progress_to_threshold < 1.0
    assert state.unreviewed_weeks == []


def test_dashboard_state_stage_2_at_threshold(tmp_path):
    cfg = _config_with({"main": _account("Main", 100_000)})
    store = _empty_store(tmp_path)
    state = compute_dashboard_state(
        cfg, [], discipline_store=store, today=date(2026, 5, 20),
    )
    assert state.stage == "stage_2"
    assert state.progress_to_threshold == 1.0


def test_dashboard_state_serialises_to_dict(tmp_path):
    cfg = _config_with({"main": _account("Main", 10_000)})
    store = _empty_store(tmp_path)
    state = compute_dashboard_state(
        cfg, [], discipline_store=store, today=date(2026, 5, 20),
    )
    d = state.to_dict()
    assert d["stage"] == "stage_1"
    assert "unreviewed_weeks" in d
    assert isinstance(d["unreviewed_weeks"], list)


# ─────────────────────────────────────────────────────────────────────────
# API integration
# ─────────────────────────────────────────────────────────────────────────


def test_api_dashboard_state_endpoint(tmp_path, monkeypatch):
    """End-to-end: GET /api/v1/dashboard/state returns the structured shape."""
    # Inject a fresh empty position store
    from positions.store import PositionStore

    def fake_store_factory():
        return PositionStore(path=tmp_path / "positions.json")

    discipline_dir = tmp_path / "discipline"

    # Monkeypatch DisciplineStore default to use tmp_path
    import discipline.dashboard as dash_mod
    original_store = dash_mod.DisciplineStore
    dash_mod.DisciplineStore = lambda: original_store(base_dir=discipline_dir)

    # Point the recovery-plan loader at tmp_path so the user's live
    # ~/.trading-dashboard/recovery_plan.json doesn't override the balance.
    monkeypatch.setenv("HOME", str(tmp_path))
    import recovery_plan.config as rp_config_mod
    monkeypatch.setattr(
        rp_config_mod, "DEFAULT_CONFIG_PATH",
        tmp_path / "recovery_plan.json",
    )

    try:
        app = create_app(store_factory=fake_store_factory)
        client = TestClient(app)
        resp = client.get("/api/v1/dashboard/state")
    finally:
        dash_mod.DisciplineStore = original_store

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Default config = main $10K + lotto $1K, weekly pooled with main
    assert body["stage"] == "stage_1"
    assert body["account_balance_usd"] == 11_000
    assert body["base_balance_usd"] == 11_000
    assert body["threshold_usd"] == 100_000
    assert body["realized_pnl_usd"] == 0
    assert body["unreviewed_weeks"] == []
    assert "Stage 1" in body["stage_reminder"]
