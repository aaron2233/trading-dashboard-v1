"""Tests for src/lotto/ — anti-greed cooldowns, growth ladder, cash reserve.

Coverage targets per ~/.claude/skills/user/lotto-options/SKILL.md:
- Big-win cooldown: 300%+ winner triggers 24h pause
- Loss-streak cooldown: 3 consecutive losses triggers 48h pause
- Loss-streak takes precedence over big-win when both fire
- Cooldowns expire correctly past the threshold
- Size lock: most-recent-loss flag (warn, not block)
- Growth ladder breakpoint resolution
- Cash reserve floor ($200)
- Kill-sheet rules wiring: lotto-account-only short-circuit
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from lotto import (
    BIG_WIN_COOLDOWN_HOURS,
    BIG_WIN_RETURN_PCT,
    CASH_FLOOR_USD,
    LOSS_STREAK_TRIGGER,
    check_lotto_cooldown,
    compute_lotto_state,
)
from positions.model import Position


# ─────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────


def _lotto_position(
    *,
    ticker: str = "SPY",
    pnl: float | None = None,
    cost: float = 100.0,
    closed_at: datetime | None = None,
    status: str = "closed",
    account: str = "lotto",
) -> Position:
    """Build a lotto-account closed (or open) position."""
    closed_iso = (closed_at or datetime(2026, 5, 10, 16, 0, tzinfo=timezone.utc)).isoformat()
    return Position(
        id=f"lotto_{ticker}_{closed_iso}",
        ticker=ticker, direction="long", instrument="call",
        account_key=account,
        entry_date=closed_iso,
        contracts=1, strike=100, expiry="2026-05-15",
        premium_paid_per_contract=cost / 100,
        total_cost_usd=cost, max_loss_usd=cost,
        target_price=120, invalidation_price=95,
        status=status,
        closed_date=closed_iso if status == "closed" else None,
        pnl_usd=pnl,
    )


def _now() -> datetime:
    return datetime(2026, 5, 13, 12, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────
# Big-win cooldown
# ─────────────────────────────────────────────────────────────────────────


def test_big_win_300pct_triggers_24h_cooldown():
    """A 300%+ win in the last 24h fires post_big_win cooldown."""
    closed = [_lotto_position(
        ticker="NVDA", pnl=350.0, cost=100.0,  # 350% return
        closed_at=_now() - timedelta(hours=10),
    )]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is True
    assert state.cooldown.reason == "post_big_win"
    assert state.cooldown.hours_remaining is not None
    assert 13 < state.cooldown.hours_remaining < 15  # 24 - 10 = 14h


def test_big_win_below_300pct_no_cooldown():
    """A 250% return is excellent but below the 300% threshold."""
    closed = [_lotto_position(
        ticker="NVDA", pnl=250.0, cost=100.0,
        closed_at=_now() - timedelta(hours=2),
    )]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is False


def test_big_win_outside_24h_window_expired():
    """A 300%+ win 30 hours ago is past the 24h cooldown."""
    closed = [_lotto_position(
        ticker="NVDA", pnl=400.0, cost=100.0,
        closed_at=_now() - timedelta(hours=30),
    )]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is False


# ─────────────────────────────────────────────────────────────────────────
# Loss-streak cooldown
# ─────────────────────────────────────────────────────────────────────────


def test_three_losses_in_a_row_triggers_48h_cooldown():
    closed = [
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=20)),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=15)),
        _lotto_position(ticker="C", pnl=-100, closed_at=_now() - timedelta(hours=5)),
    ]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is True
    assert state.cooldown.reason == "post_loss_streak"
    # Most recent loss was 5h ago, expires 48h after that = 43h from now
    assert 42 < state.cooldown.hours_remaining < 44


def test_two_losses_then_win_no_streak_cooldown():
    """Two losses then a win does NOT trigger the 3-streak."""
    closed = [
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=20)),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=15)),
        _lotto_position(ticker="C", pnl=80,   closed_at=_now() - timedelta(hours=5)),
    ]
    state = compute_lotto_state([], closed, now=_now())
    # Win was below 300% threshold, so no big-win cooldown either
    assert state.cooldown.active is False


def test_loss_streak_expires_after_48h():
    """3 losses where most recent closed >48h ago → cooldown expired."""
    closed = [
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=70)),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=60)),
        _lotto_position(ticker="C", pnl=-100, closed_at=_now() - timedelta(hours=50)),
    ]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is False


def test_loss_streak_takes_precedence_over_big_win():
    """When both could fire, loss-streak (more dangerous signal) wins."""
    closed = [
        # 3-loss streak (recent)
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=20)),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=15)),
        _lotto_position(ticker="C", pnl=-100, closed_at=_now() - timedelta(hours=5)),
        # Plus a 300% winner today (would also trigger big-win)
        _lotto_position(ticker="D", pnl=500.0, cost=100.0, closed_at=_now() - timedelta(hours=2)),
    ]
    # Note: D being a win breaks the streak for the most-recent-3 check.
    # Re-arrange so 3 losses are the most recent
    closed = [
        _lotto_position(ticker="D", pnl=500.0, cost=100.0, closed_at=_now() - timedelta(hours=20)),
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=15)),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=10)),
        _lotto_position(ticker="C", pnl=-100, closed_at=_now() - timedelta(hours=5)),
    ]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is True
    assert state.cooldown.reason == "post_loss_streak"


# ─────────────────────────────────────────────────────────────────────────
# Size lock (most-recent-loss flag)
# ─────────────────────────────────────────────────────────────────────────


def test_size_lock_after_recent_loss():
    closed = [_lotto_position(
        ticker="X", pnl=-50.0,
        closed_at=_now() - timedelta(hours=2),
    )]
    state = compute_lotto_state([], closed, now=_now())
    assert state.size_lock_active is True
    assert "Cardinal sin" in (state.size_lock_reason or "")


def test_size_lock_clears_after_win():
    closed = [
        _lotto_position(ticker="X", pnl=-50.0, closed_at=_now() - timedelta(hours=10)),
        _lotto_position(ticker="Y", pnl=80.0,  closed_at=_now() - timedelta(hours=2)),
    ]
    state = compute_lotto_state([], closed, now=_now())
    assert state.size_lock_active is False


# ─────────────────────────────────────────────────────────────────────────
# Growth ladder
# ─────────────────────────────────────────────────────────────────────────


def test_growth_ladder_at_each_breakpoint():
    """Ladder transitions at 1K / 2K / 3K / 5K."""
    pairs = [
        (500, "Sub-$1K"),
        (1_000, "$1K"),
        (1_500, "$1K"),     # still in $1K stage
        (2_000, "$2K"),
        (3_000, "$3K"),
        (5_000, "$5K+"),
        (15_000, "$5K+"),   # well past
    ]
    for balance, expected_substring in pairs:
        # We adjust base + add realized P&L to reach the balance
        delta = balance - 1_000.0
        closed = (
            [_lotto_position(pnl=delta, closed_at=_now() - timedelta(hours=1))]
            if delta != 0
            else []
        )
        state = compute_lotto_state([], closed, now=_now())
        assert expected_substring in state.growth_ladder_stage, (
            f"balance={balance}: expected '{expected_substring}' in '{state.growth_ladder_stage}'"
        )


# ─────────────────────────────────────────────────────────────────────────
# Cash reserve floor
# ─────────────────────────────────────────────────────────────────────────


def test_cash_reserve_ok_when_above_floor():
    open_pos = [_lotto_position(ticker="X", cost=100.0, status="open")]
    state = compute_lotto_state(open_pos, [], now=_now())
    # 1000 base - 100 open = 900 cash. Above $200 floor.
    assert state.cash_available_usd == 900.0
    assert state.cash_reserve_status == "ok"


def test_cash_reserve_below_floor_when_overcommitted():
    """800 in open premium + base $1K = $200 cash. Just at the floor."""
    open_pos = [
        _lotto_position(ticker="X", cost=400.0, status="open"),
        _lotto_position(ticker="Y", cost=400.0, status="open"),
    ]
    state = compute_lotto_state(open_pos, [], now=_now())
    assert state.cash_available_usd == 200.0
    # At the floor: ok. Below the floor: below_floor.
    assert state.cash_reserve_status == "ok"

    # Now over-commit by $1
    open_pos.append(_lotto_position(ticker="Z", cost=1.0, status="open"))
    state = compute_lotto_state(open_pos, [], now=_now())
    assert state.cash_available_usd == 199.0
    assert state.cash_reserve_status == "below_floor"


# ─────────────────────────────────────────────────────────────────────────
# Recent-trade summary
# ─────────────────────────────────────────────────────────────────────────


def test_recent_trades_include_return_pct_and_flags():
    closed = [
        _lotto_position(ticker="A", pnl=400.0, cost=100.0, closed_at=_now() - timedelta(hours=5)),
        _lotto_position(ticker="B", pnl=-50.0, cost=100.0, closed_at=_now() - timedelta(hours=3)),
    ]
    state = compute_lotto_state([], closed, now=_now())
    by_ticker = {t.ticker: t for t in state.recent_trades}
    assert by_ticker["A"].return_pct == 4.0  # 400/100
    assert by_ticker["A"].is_big_win is True
    assert by_ticker["B"].is_loss is True
    assert by_ticker["B"].is_big_win is False


def test_lotto_state_filters_non_lotto_positions():
    """Main-account closed trades must not affect lotto state."""
    closed = [
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=20),
                        account="main"),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=15),
                        account="main"),
        _lotto_position(ticker="C", pnl=-100, closed_at=_now() - timedelta(hours=5),
                        account="main"),
    ]
    state = compute_lotto_state([], closed, now=_now())
    assert state.cooldown.active is False
    assert state.recent_trades == []


# ─────────────────────────────────────────────────────────────────────────
# Rules-engine integration
# ─────────────────────────────────────────────────────────────────────────


def test_check_lotto_cooldown_returns_block_violation_on_big_win():
    closed = [_lotto_position(
        ticker="X", pnl=500.0, cost=100.0,
        closed_at=_now() - timedelta(hours=2),
    )]
    violations = check_lotto_cooldown([], closed, now=_now())
    rules = {v.rule for v in violations}
    assert "lotto_cooldown_24h" in rules
    big_win_v = next(v for v in violations if v.rule == "lotto_cooldown_24h")
    assert big_win_v.severity == "block"


def test_check_lotto_cooldown_returns_block_violation_on_loss_streak():
    closed = [
        _lotto_position(ticker="A", pnl=-100, closed_at=_now() - timedelta(hours=20)),
        _lotto_position(ticker="B", pnl=-100, closed_at=_now() - timedelta(hours=10)),
        _lotto_position(ticker="C", pnl=-100, closed_at=_now() - timedelta(hours=2)),
    ]
    violations = check_lotto_cooldown([], closed, now=_now())
    rules = {v.rule for v in violations}
    assert "lotto_cooldown_48h" in rules


def test_check_lotto_cooldown_size_lock_warn_not_block():
    closed = [_lotto_position(ticker="X", pnl=-50, closed_at=_now() - timedelta(hours=2))]
    violations = check_lotto_cooldown([], closed, now=_now())
    size_lock = next((v for v in violations if v.rule == "lotto_size_lock"), None)
    assert size_lock is not None
    assert size_lock.severity == "warn"


def test_check_lotto_cooldown_no_violations_when_clean():
    """No closed lotto trades → no violations."""
    assert check_lotto_cooldown([], [], now=_now()) == []


# ─────────────────────────────────────────────────────────────────────────
# API integration
# ─────────────────────────────────────────────────────────────────────────


def test_api_lotto_state_endpoint(tmp_path):
    """End-to-end: GET /api/v1/lotto/state returns the structured shape."""
    from positions.store import PositionStore

    def fake_store_factory():
        return PositionStore(path=tmp_path / "positions.json")

    app = create_app(store_factory=fake_store_factory)
    client = TestClient(app)
    resp = client.get("/api/v1/lotto/state")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["base_balance_usd"] == 1_000.0  # default config
    assert body["account_balance_usd"] == 1_000.0  # no closed trades
    assert body["cooldown"]["active"] is False
    assert body["growth_ladder_stage"].startswith("$1K")


def test_api_lotto_cooldown_blocks_lotto_kill_sheet(tmp_path, monkeypatch):
    """Kill sheet on lotto account during cooldown → blocked."""
    from positions.store import PositionStore

    store_path = tmp_path / "positions.json"

    def fake_store_factory():
        return PositionStore(path=store_path)

    # Pre-populate a 300%+ winner from 2 hours ago in the lotto account
    store = fake_store_factory()
    big_winner = _lotto_position(
        ticker="X", pnl=400.0, cost=100.0,
        closed_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    store.add(big_winner)

    # Mock scan so we don't hit yfinance
    def fake_scan(ticker, period=None, timeframe="1d"):
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-05-13",
            "close": 100.0,
            "ma_ribbon": {"ma_10": 99, "ma_20": 98, "ma_50": 95, "ma_200": 90,
                          "stack_state": "full_bull"},
            "stochastic": {"k": 50, "d": 50, "zone": "neutral", "signal": None},
            "sqn": {"sqn_value": 1.0, "regime": "bull",
                    "sqn_20_value": 0.5, "regime_20": "bull", "diagnostic": "ok"},
        }
    monkeypatch.setattr("api.app.scan_ticker", fake_scan)
    monkeypatch.setattr("api.app.compute_multi_tf",
                        lambda t, timeframes=None: {})

    app = create_app(store_factory=fake_store_factory)
    client = TestClient(app)

    # Lotto kill sheet during cooldown — should be blocked
    resp = client.post("/api/v1/kill_sheet", json={
        "ticker": "AAPL", "direction": "long",
        "account": "lotto", "intent": "SCALP",
        "trigger_tf": "2H", "conviction": "high",
    })
    assert resp.status_code == 200
    body = resp.json()
    rule_ids = {v["rule"] for v in body["rule_violations"]}
    assert "lotto_cooldown_24h" in rule_ids
    assert body["rules_blocked"] is True


def test_api_lotto_size_lock_warn_does_not_block_kill_sheet(tmp_path, monkeypatch):
    """Regression (2026-05-18): a warn-severity violation (lotto_size_lock —
    most recent lotto trade was a loss) must NOT set rules_blocked. The rule
    is advisory; the user sees the warning but can still generate the kill
    sheet and decide on sizing.
    """
    from positions.store import PositionStore

    store_path = tmp_path / "positions.json"

    def fake_store_factory():
        return PositionStore(path=store_path)

    # Single recent loss → triggers size_lock (warn), no cooldown (only 1 loss).
    store = fake_store_factory()
    loser = _lotto_position(
        ticker="RGTI", pnl=-69.0, cost=155.0,
        closed_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    store.add(loser)

    def fake_scan(ticker, period=None, timeframe="1d"):
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-05-18",
            "close": 100.0,
            "ma_ribbon": {"ma_10": 99, "ma_20": 98, "ma_50": 95, "ma_200": 90,
                          "stack_state": "full_bull"},
            "stochastic": {"k": 50, "d": 50, "zone": "neutral", "signal": None},
            "sqn": {"sqn_value": 1.0, "regime": "bull",
                    "sqn_20_value": 0.5, "regime_20": "bull", "diagnostic": "ok"},
        }
    monkeypatch.setattr("api.app.scan_ticker", fake_scan)
    monkeypatch.setattr("api.app.compute_multi_tf",
                        lambda t, timeframes=None: {})

    app = create_app(store_factory=fake_store_factory)
    client = TestClient(app)

    resp = client.post("/api/v1/kill_sheet", json={
        "ticker": "AAPL", "direction": "long",
        "account": "lotto", "intent": "SCALP",
        "trigger_tf": "2H", "conviction": "high",
    })
    assert resp.status_code == 200
    body = resp.json()
    rule_ids = {v["rule"] for v in body["rule_violations"]}
    # size_lock should still fire as an advisory
    assert "lotto_size_lock" in rule_ids
    size_lock = next(v for v in body["rule_violations"] if v["rule"] == "lotto_size_lock")
    assert size_lock["severity"] == "warn"
    # …but it must NOT gate the kill sheet
    assert body["rules_blocked"] is False


def test_api_lotto_cooldown_does_not_block_main_kill_sheet(tmp_path, monkeypatch):
    """Same lotto cooldown should NOT affect main-account kill sheets."""
    from positions.store import PositionStore

    store_path = tmp_path / "positions.json"

    def fake_store_factory():
        return PositionStore(path=store_path)

    store = fake_store_factory()
    big_winner = _lotto_position(
        ticker="X", pnl=400.0, cost=100.0,
        closed_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    store.add(big_winner)

    def fake_scan(ticker, period=None, timeframe="1d"):
        return {
            "ticker": ticker, "timeframe": timeframe, "bar_date": "2026-05-13",
            "close": 30.0,
            "ma_ribbon": {"ma_10": 29, "ma_20": 28, "ma_50": 27, "ma_200": 25,
                          "stack_state": "full_bull"},
            "stochastic": {"k": 30, "d": 28, "zone": "oversold",
                           "signal": "bull_cross_oversold"},
            "sqn": {"sqn_value": 1.0, "regime": "bull",
                    "sqn_20_value": 0.5, "regime_20": "bull", "diagnostic": "ok"},
        }
    monkeypatch.setattr("api.app.scan_ticker", fake_scan)
    monkeypatch.setattr("api.app.compute_multi_tf",
                        lambda t, timeframes=None: {})

    app = create_app(store_factory=fake_store_factory)
    client = TestClient(app)

    # MAIN-account kill sheet — lotto cooldown should NOT block
    resp = client.post("/api/v1/kill_sheet", json={
        "ticker": "AAPL", "direction": "long",
        "account": "main", "intent": "SWING",
        "trigger_tf": "Daily", "conviction": "high",
    })
    assert resp.status_code == 200
    body = resp.json()
    rule_ids = {v["rule"] for v in body["rule_violations"]}
    assert "lotto_cooldown_24h" not in rule_ids
    assert "lotto_cooldown_48h" not in rule_ids
