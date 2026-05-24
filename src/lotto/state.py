"""Lotto-account state aggregator — anti-greed cooldowns, growth ladder, cash reserve.

Per ~/.claude/skills/user/lotto-options/SKILL.md "Anti-Greed Protocol" + "Growth
Ladder" + "Account Architecture":

    AFTER 300%+ WINNER:        24-hour cooldown before next trade
    AFTER 3 CONSECUTIVE LOSSES: 48-hour trading pause
    CARDINAL SIN:               Never increase size after a loss
    Cash floor:                 $200 always uninvested
    Growth ladder:              $1K → $2K → $3K → $5K+ (size + delta scale up)

This module reads `account_key == "lotto"` positions from PositionStore and
produces a LottoState that drives both the LottoView UI and the kill-sheet
rules engine (lotto cooldowns block lotto kill sheets).

Design:
- All thresholds are constants at the top so they can be re-tuned without
  hunting through logic.
- "Loss" means pnl_usd < 0 on a closed position. "Big win" means pnl_usd
  >= cost basis * 3 (i.e. ≥300% return on max risk).
- Cooldowns measured in trading-time-since-close UTC.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Literal

from positions.model import Position


# ─────────────────────────────────────────────────────────────────────────
# Tunable constants
# ─────────────────────────────────────────────────────────────────────────

LOTTO_ACCOUNT_KEY: str = "lotto"

# Anti-greed thresholds
BIG_WIN_RETURN_PCT: float = 3.0       # 300% on max risk = big-win threshold
BIG_WIN_COOLDOWN_HOURS: int = 24
LOSS_STREAK_TRIGGER: int = 3          # 3 consecutive losses → pause
LOSS_STREAK_COOLDOWN_HOURS: int = 48

# Cash reserve
CASH_FLOOR_USD: float = 200.0

# Growth ladder breakpoints (account total, USD)
GROWTH_LADDER: tuple[tuple[float, str], ...] = (
    (5_000, "$5K+ — Standard tier (lottos = 10% sleeve)"),
    (3_000, "$3K — 50/50 lotto/standard"),
    (2_000, "$2K — 70% lotto / 30% standard"),
    (1_000, "$1K — Full lotto"),
    (0,     "Sub-$1K — rebuild before lottos"),
)

CooldownReason = Literal[
    "post_big_win",        # 24hr after 300%+ winner
    "post_loss_streak",    # 48hr after 3 consecutive losses
]


# ─────────────────────────────────────────────────────────────────────────
# State dataclass
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class LottoTradeSummary:
    """One row in the recent-lotto-trades table."""
    position_id: str
    ticker: str
    direction: str
    closed_date: str | None
    pnl_usd: float | None
    return_pct: float | None         # pnl / max_loss; None if max_loss missing
    is_big_win: bool                 # >= 300% return
    is_loss: bool                    # pnl < 0


@dataclass
class LottoCooldown:
    """Active or recently-resolved cooldown state."""
    active: bool
    reason: CooldownReason | None
    triggered_at: str | None         # ISO timestamp
    expires_at: str | None           # ISO timestamp
    hours_remaining: float | None
    triggering_position_ids: list[str] = field(default_factory=list)


@dataclass
class LottoState:
    """Full lotto-account state for the dashboard view + rules engine."""
    account_balance_usd: float       # base + realized P&L on lotto positions
    base_balance_usd: float          # config baseline (default $1,000)
    realized_pnl_usd: float

    # Cash reserve
    open_premium_usd: float          # capital tied up in open lotto options
    cash_available_usd: float        # base_balance - open_premium (excl. reserve)
    cash_reserve_status: Literal["ok", "below_floor"]  # vs $200 floor

    # Growth ladder
    growth_ladder_stage: str         # human-readable stage label

    # Anti-greed
    cooldown: LottoCooldown
    size_lock_active: bool           # most recent close was a loss → no upsize
    size_lock_reason: str | None     # explanation when active

    # Tempo
    closed_count_last_7d: int

    # Recent trade table
    recent_trades: list[LottoTradeSummary] = field(default_factory=list)

    # Open positions snapshot
    open_position_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


# ─────────────────────────────────────────────────────────────────────────
# Computation
# ─────────────────────────────────────────────────────────────────────────


def _is_lotto(position: Position) -> bool:
    return position.account_key == LOTTO_ACCOUNT_KEY


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _return_pct(p: Position) -> float | None:
    """Return P&L as a fraction of max risk (0.50 = +50%, 3.0 = +300%).

    Lotto sizing makes max_loss == total_cost (long options). Falls back to
    total_cost_usd when max_loss_usd missing.
    """
    if p.pnl_usd is None:
        return None
    risk = p.max_loss_usd or p.total_cost_usd or 0.0
    if risk <= 0:
        return None
    return p.pnl_usd / risk


def _growth_ladder_stage(account_balance: float) -> str:
    for breakpoint, label in GROWTH_LADDER:
        if account_balance >= breakpoint:
            return label
    return GROWTH_LADDER[-1][1]


def _summarize(p: Position) -> LottoTradeSummary:
    rp = _return_pct(p)
    is_big_win = rp is not None and rp >= BIG_WIN_RETURN_PCT
    is_loss = p.pnl_usd is not None and p.pnl_usd < 0
    return LottoTradeSummary(
        position_id=p.id, ticker=p.ticker, direction=p.direction,
        closed_date=p.closed_date, pnl_usd=p.pnl_usd,
        return_pct=rp, is_big_win=is_big_win, is_loss=is_loss,
    )


def _check_big_win_cooldown(
    closed_lotto: list[Position], now: datetime,
) -> LottoCooldown | None:
    """Cooldown if any 300%+ winner closed within the past 24h."""
    cutoff = now.timestamp() - BIG_WIN_COOLDOWN_HOURS * 3600
    triggers: list[Position] = []
    most_recent_trigger: datetime | None = None
    for p in closed_lotto:
        rp = _return_pct(p)
        if rp is None or rp < BIG_WIN_RETURN_PCT:
            continue
        closed_dt = _parse_iso(p.closed_date)
        if closed_dt is None:
            continue
        if closed_dt.timestamp() < cutoff:
            continue
        triggers.append(p)
        if most_recent_trigger is None or closed_dt > most_recent_trigger:
            most_recent_trigger = closed_dt
    if not triggers or most_recent_trigger is None:
        return None
    expires = most_recent_trigger.timestamp() + BIG_WIN_COOLDOWN_HOURS * 3600
    hours_remaining = max(0.0, (expires - now.timestamp()) / 3600.0)
    return LottoCooldown(
        active=True,
        reason="post_big_win",
        triggered_at=most_recent_trigger.isoformat(),
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        hours_remaining=hours_remaining,
        triggering_position_ids=[p.id for p in triggers],
    )


def _check_loss_streak_cooldown(
    closed_lotto: list[Position], now: datetime,
) -> LottoCooldown | None:
    """48h cooldown if the 3 most recent closes were all losses."""
    sorted_closes = sorted(
        (p for p in closed_lotto if _parse_iso(p.closed_date)),
        key=lambda p: _parse_iso(p.closed_date) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if len(sorted_closes) < LOSS_STREAK_TRIGGER:
        return None
    streak = sorted_closes[:LOSS_STREAK_TRIGGER]
    if not all(p.pnl_usd is not None and p.pnl_usd < 0 for p in streak):
        return None
    triggered_dt = _parse_iso(streak[0].closed_date)  # most recent of the streak
    if triggered_dt is None:
        return None
    expires = triggered_dt.timestamp() + LOSS_STREAK_COOLDOWN_HOURS * 3600
    if expires < now.timestamp():
        return None  # already cleared
    hours_remaining = max(0.0, (expires - now.timestamp()) / 3600.0)
    return LottoCooldown(
        active=True,
        reason="post_loss_streak",
        triggered_at=triggered_dt.isoformat(),
        expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        hours_remaining=hours_remaining,
        triggering_position_ids=[p.id for p in streak],
    )


def _no_cooldown() -> LottoCooldown:
    return LottoCooldown(
        active=False, reason=None, triggered_at=None,
        expires_at=None, hours_remaining=None,
        triggering_position_ids=[],
    )


def _check_size_lock(closed_lotto: list[Position]) -> tuple[bool, str | None]:
    """Most recent closed lotto trade was a loss → size_lock active.

    Cardinal-sin rule: never increase size after a loss. The lock simply
    flags the next trade as not-eligible-to-upsize; the actual sizing
    decision lives with the user.
    """
    sorted_closes = sorted(
        (p for p in closed_lotto if _parse_iso(p.closed_date)),
        key=lambda p: _parse_iso(p.closed_date) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    if not sorted_closes:
        return False, None
    last = sorted_closes[0]
    if last.pnl_usd is None or last.pnl_usd >= 0:
        return False, None
    return True, (
        f"Most recent lotto trade ({last.ticker} {last.direction}) closed at "
        f"${last.pnl_usd:.2f}. Cardinal sin: never increase size after a loss."
    )


def compute_lotto_state(
    open_positions: Iterable[Position],
    closed_positions: Iterable[Position],
    *,
    base_balance_usd: float = 1_000.0,
    now: datetime | None = None,
    recent_trade_window_hours: int = 24 * 7,
    recent_trade_table_limit: int = 10,
) -> LottoState:
    """Compute full lotto-account state."""
    if now is None:
        now = datetime.now(timezone.utc)

    open_lotto = [p for p in open_positions if _is_lotto(p) and p.status == "open"]
    closed_lotto = [p for p in closed_positions if _is_lotto(p) and p.status == "closed"]

    realized = sum(p.pnl_usd or 0.0 for p in closed_lotto)
    account_balance = base_balance_usd + realized

    open_premium = sum(p.total_cost_usd or 0.0 for p in open_lotto)
    cash_available = max(0.0, account_balance - open_premium)
    cash_reserve_status = "ok" if cash_available >= CASH_FLOOR_USD else "below_floor"

    growth_stage = _growth_ladder_stage(account_balance)

    # Anti-greed: loss-streak takes precedence over big-win when both fire
    cooldown = _check_loss_streak_cooldown(closed_lotto, now)
    if cooldown is None:
        cooldown = _check_big_win_cooldown(closed_lotto, now)
    if cooldown is None:
        cooldown = _no_cooldown()

    size_lock_active, size_lock_reason = _check_size_lock(closed_lotto)

    # Tempo (closed in last N hours)
    cutoff = now.timestamp() - recent_trade_window_hours * 3600
    closed_recent = sum(
        1 for p in closed_lotto
        if (_parse_iso(p.closed_date) or now).timestamp() >= cutoff
    )

    sorted_recent = sorted(
        closed_lotto,
        key=lambda p: _parse_iso(p.closed_date) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )[:recent_trade_table_limit]

    return LottoState(
        account_balance_usd=account_balance,
        base_balance_usd=base_balance_usd,
        realized_pnl_usd=realized,
        open_premium_usd=open_premium,
        cash_available_usd=cash_available,
        cash_reserve_status=cash_reserve_status,
        growth_ladder_stage=growth_stage,
        cooldown=cooldown,
        size_lock_active=size_lock_active,
        size_lock_reason=size_lock_reason,
        closed_count_last_7d=closed_recent,
        recent_trades=[_summarize(p) for p in sorted_recent],
        open_position_ids=[p.id for p in open_lotto],
    )
