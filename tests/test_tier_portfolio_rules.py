"""Tests for orchestrator rule 11 — Tier 1+2 portfolio rules on QQQ/GLD."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from positions.model import Position
from positions.tier_portfolio_rules import (
    COOLOFF_TRADING_DAYS,
    TIER_PORTFOLIO_TICKERS,
    check_tier_portfolio_trade,
)


def _open_position(
    ticker: str = "QQQ",
    direction: str = "long",
    instrument: str = "call",
    account_key: str = "main",
) -> Position:
    return Position(
        id="test_" + ticker.lower() + "_" + direction,
        ticker=ticker, direction=direction, instrument=instrument,
        account_key=account_key,
        entry_date=datetime.now(timezone.utc).isoformat(),
        contracts=1, strike=400, expiry="2026-07-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        target_price=420, invalidation_price=395,
        status="open",
    )


def _closed_loser(
    ticker: str = "QQQ",
    direction: str = "long",
    days_ago: int = 0,
    pnl_usd: float = -300.0,
) -> Position:
    closed = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return Position(
        id="closed_" + ticker.lower() + "_" + str(days_ago),
        ticker=ticker, direction=direction, instrument="call",
        account_key="main",
        entry_date=(closed - timedelta(days=10)).isoformat(),
        contracts=1, strike=400, expiry="2026-06-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        status="closed",
        closed_date=closed.isoformat(),
        pnl_usd=pnl_usd,
    )


# ── Ticker scope ────────────────────────────────────────────────────────────


def test_passes_for_non_qqq_gld_ticker():
    """Rule 11 doesn't apply to other tickers — must short-circuit clean."""
    violations = check_tier_portfolio_trade(
        ticker="AAPL", direction="long",
        open_positions=[_open_position(ticker="QQQ")],
        closed_positions=[],
    )
    assert violations == []


def test_qqq_and_gld_are_only_tickers_in_scope():
    assert TIER_PORTFOLIO_TICKERS == frozenset({"QQQ", "GLD"})


# ── 1-per-asset cap (rule 11.1) ─────────────────────────────────────────────


def test_blocks_second_qqq_position():
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_position(ticker="QQQ", direction="long")],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" in rules


def test_blocks_qqq_short_when_qqq_long_open():
    """Same asset → blocked even with opposite direction."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="short",
        open_positions=[_open_position(ticker="QQQ", direction="long")],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" in rules


def test_qqq_open_doesnt_block_gld_opposite_direction():
    """Different asset, opposite direction → fine (rule 11 cap allows 2 across pair)."""
    violations = check_tier_portfolio_trade(
        ticker="GLD", direction="short",
        open_positions=[_open_position(ticker="QQQ", direction="long")],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" not in rules
    assert "tier_portfolio_no_same_direction_pair" not in rules


# ── No same-direction pair (rule 11.2) ──────────────────────────────────────


def test_blocks_gld_long_when_qqq_long_open():
    violations = check_tier_portfolio_trade(
        ticker="GLD", direction="long",
        open_positions=[_open_position(ticker="QQQ", direction="long")],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_no_same_direction_pair" in rules


def test_blocks_qqq_short_when_gld_short_open():
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="short",
        open_positions=[_open_position(ticker="GLD", direction="short")],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_no_same_direction_pair" in rules


def test_pair_rule_compares_thesis_not_contract_direction_for_puts():
    # Regression (fixed 2026-06): rule 11.2 must compare THESIS, not contract
    # direction. A long PUT is bearish even though its contract direction is
    # "long". An existing GLD long put (bearish) + a proposed bearish QQQ
    # ("short") is the correlated same-thesis pair the rule forbids; the old
    # contract-direction compare missed it (false negative) and instead blocked
    # the opposite-thesis hedge (false positive).
    gld_put = _open_position(ticker="GLD", direction="long", instrument="put")
    assert gld_put.thesis_direction == "bearish"
    # Proposed bearish QQQ → same thesis → BLOCK
    blocked = check_tier_portfolio_trade(
        ticker="QQQ", direction="short",
        open_positions=[gld_put], closed_positions=[],
    )
    assert "tier_portfolio_no_same_direction_pair" in {v.rule for v in blocked}
    # Proposed bullish QQQ → opposite thesis (a hedge) → allowed
    allowed = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[gld_put], closed_positions=[],
    )
    assert "tier_portfolio_no_same_direction_pair" not in {v.rule for v in allowed}


# ── 3-day cool-off (rule 11.3) ──────────────────────────────────────────────


def test_blocks_qqq_within_cooloff_window():
    """Closed loser yesterday → still in cool-off."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[],
        closed_positions=[_closed_loser(ticker="QQQ", days_ago=1)],
        # Pin `now` to a known weekday so the test isn't weekend-flaky
        now=datetime(2026, 5, 6, tzinfo=timezone.utc),
    )
    # The closed_loser fixture uses `days_ago` against actual `now` though —
    # adjust to manual position creation for date precision:
    closed = datetime(2026, 5, 5, 16, 0, tzinfo=timezone.utc)  # Tuesday close
    p = Position(
        id="manual_close", ticker="QQQ", direction="long", instrument="call",
        account_key="main",
        entry_date=(closed - timedelta(days=5)).isoformat(),
        contracts=1, strike=400, expiry="2026-06-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        status="closed", closed_date=closed.isoformat(), pnl_usd=-300,
    )
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[], closed_positions=[p],
        now=datetime(2026, 5, 6, 16, 0, tzinfo=timezone.utc),  # Wednesday
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_cooloff" in rules


def test_passes_after_cooloff_elapsed():
    closed = datetime(2026, 4, 27, 16, 0, tzinfo=timezone.utc)  # Monday
    p = Position(
        id="old_close", ticker="QQQ", direction="long", instrument="call",
        account_key="main",
        entry_date=(closed - timedelta(days=5)).isoformat(),
        contracts=1, strike=400, expiry="2026-06-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        status="closed", closed_date=closed.isoformat(), pnl_usd=-300,
    )
    # Test from a Friday a week later — well past 3 weekdays
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[], closed_positions=[p],
        now=datetime(2026, 5, 1, 16, 0, tzinfo=timezone.utc),  # following Friday
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_cooloff" not in rules


def test_weekdays_elapsed_excludes_in_progress_day():
    # Decision 2026-06 "after N full trading days": today (now's own date) does
    # NOT count, so a Monday stop clears on Friday, not Thursday.
    from positions.tier_portfolio_rules import _weekdays_elapsed
    mon = datetime(2026, 5, 4, 12, 0)  # Monday
    assert _weekdays_elapsed(mon, datetime(2026, 5, 7, 12, 0)) == 2  # Tue,Wed (Thu in-progress)
    assert _weekdays_elapsed(mon, datetime(2026, 5, 8, 12, 0)) == 3  # Tue,Wed,Thu complete


def test_cooloff_requires_full_trading_days_after_stop():
    closed = datetime(2026, 5, 4, 16, 0, tzinfo=timezone.utc)  # Monday, 12:00 ET
    p = Position(
        id="stop", ticker="QQQ", direction="long", instrument="call",
        account_key="main", entry_date=(closed - timedelta(days=5)).isoformat(),
        contracts=1, strike=400, expiry="2026-06-01",
        premium_paid_per_contract=5.0, total_cost_usd=500, max_loss_usd=500,
        status="closed", closed_date=closed.isoformat(), pnl_usd=-300,
    )
    # Thursday: only Tue + Wed are full elapsed sessions (2 < 3) → still blocked.
    thu = check_tier_portfolio_trade(
        "QQQ", "long", [], [p],
        now=datetime(2026, 5, 7, 16, 0, tzinfo=timezone.utc),
    )
    assert "tier_portfolio_cooloff" in {v.rule for v in thu}
    # Friday: Tue + Wed + Thu = 3 full sessions → clear.
    fri = check_tier_portfolio_trade(
        "QQQ", "long", [], [p],
        now=datetime(2026, 5, 8, 16, 0, tzinfo=timezone.utc),
    )
    assert "tier_portfolio_cooloff" not in {v.rule for v in fri}


def test_winner_does_not_trigger_cooloff():
    """Cool-off only fires on stops (pnl < 0)."""
    closed = datetime(2026, 5, 5, 16, 0, tzinfo=timezone.utc)
    winner = Position(
        id="winner", ticker="QQQ", direction="long", instrument="call",
        account_key="main",
        entry_date=(closed - timedelta(days=5)).isoformat(),
        contracts=1, strike=400, expiry="2026-06-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        status="closed", closed_date=closed.isoformat(), pnl_usd=200,
    )
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[], closed_positions=[winner],
        now=datetime(2026, 5, 6, 16, 0, tzinfo=timezone.utc),
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_cooloff" not in rules


def test_cooloff_only_for_same_ticker():
    """A QQQ stop should not block a GLD trade."""
    closed = datetime(2026, 5, 5, 16, 0, tzinfo=timezone.utc)
    qqq_stop = Position(
        id="qqq_stop", ticker="QQQ", direction="long", instrument="call",
        account_key="main",
        entry_date=(closed - timedelta(days=5)).isoformat(),
        contracts=1, strike=400, expiry="2026-06-01",
        premium_paid_per_contract=5.0,
        total_cost_usd=500, max_loss_usd=500,
        status="closed", closed_date=closed.isoformat(), pnl_usd=-300,
    )
    violations = check_tier_portfolio_trade(
        ticker="GLD", direction="long",
        open_positions=[], closed_positions=[qqq_stop],
        now=datetime(2026, 5, 6, 16, 0, tzinfo=timezone.utc),
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_cooloff" not in rules


# ── Clean cases ─────────────────────────────────────────────────────────────


def test_clean_when_no_open_no_history():
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[], closed_positions=[],
    )
    assert violations == []


def test_qqq_long_with_gld_short_open_is_clean():
    """Different asset, different direction → no rule fires."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_position(ticker="GLD", direction="short")],
        closed_positions=[],
    )
    assert violations == []


def test_severity_is_block_for_all_violations():
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_position(ticker="QQQ", direction="long")],
        closed_positions=[],
    )
    assert all(v.severity == "block" for v in violations)


# ── Polish: tier filtering on Position ──────────────────────────────────────


def _open_with_tier(ticker: str, direction: str, tier: int | None) -> Position:
    p = _open_position(ticker=ticker, direction=direction)
    p.tier = tier
    return p


def test_tier_3_position_does_not_count_in_cap():
    """Tier 3 position on QQQ → not counted in Tier 1+2 cap."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_with_tier("QQQ", "long", tier=3)],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" not in rules


def test_tier_4_position_does_not_count_in_cap():
    """qqq-gld-focus (Tier 4) workflow position → not counted in rule 11."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_with_tier("QQQ", "long", tier=4)],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" not in rules


def test_tier_1_position_counts_in_cap():
    """weekly-trend-trader (Tier 1) QQQ position blocks a second Tier 1+2 entry."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_with_tier("QQQ", "long", tier=1)],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" in rules


def test_tier_2_position_counts_in_cap():
    """lotto-options (Tier 2) QQQ position blocks another QQQ entry under rule 11."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_with_tier("QQQ", "long", tier=2)],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" in rules


def test_null_tier_position_counts_in_cap_for_back_compat():
    """Legacy (null-tier) position is conservatively in scope — no false negatives."""
    violations = check_tier_portfolio_trade(
        ticker="QQQ", direction="long",
        open_positions=[_open_with_tier("QQQ", "long", tier=None)],
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    assert "tier_portfolio_one_per_asset" in rules


def test_mixed_tiers_only_tier_1_2_count():
    """Three open QQQ positions (Tier 1 + Tier 3 + Tier 4) count as one in scope."""
    positions = [
        _open_with_tier("QQQ", "long", tier=1),
        _open_with_tier("QQQ", "long", tier=3),  # out of scope
        _open_with_tier("QQQ", "long", tier=4),  # focus — out of scope
    ]
    # Try opening a Tier 2 GLD long while QQQ Tier 1 is open:
    # Should fire same-direction-pair on the Tier 1 QQQ, not on Tier 3 / 4
    violations = check_tier_portfolio_trade(
        ticker="GLD", direction="long",
        open_positions=positions,
        closed_positions=[],
    )
    rules = {v.rule for v in violations}
    # The Tier 1 QQQ long blocks; Tier 3/4 ignored
    assert "tier_portfolio_no_same_direction_pair" in rules
