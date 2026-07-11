"""Tier 1 + Tier 2 combined portfolio rules for QQQ + GLD.

Implements orchestration rule 11 from `~/CLAUDE.md`:

    Max 2 concurrent QQQ/GLD positions across Tier 1 and Tier 2 combined
    (one per asset). Never long QQQ + long GLD same direction simultaneously
    — pick the stronger setup. Cool-off 3 trading days after a stop on
    either asset before re-entering on the same name.

Fires whenever the ticker is QQQ or GLD, regardless of skill. (The Tier 4
qqq-gld-focus workflow and its opt-in `focus_rules.py` gates were removed
2026-07-11 — QQQ/GLD coverage now rides the standard strategy scans.)

V1 limitation: Position model does not yet carry a skill/tier field. We
therefore count ALL open QQQ/GLD positions regardless of skill. This is
strictly broader than "Tier 1 + Tier 2 combined" but produces no false
negatives. When positions gain a skill/tier field, refine to filter on tier.
Marked TODO inline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo

from positions.model import Position
from positions.rules import RuleViolation


TIER_PORTFOLIO_TICKERS: frozenset[str] = frozenset({"QQQ", "GLD"})
COOLOFF_TRADING_DAYS: int = 3
TIERS_IN_SCOPE: frozenset[int] = frozenset({1, 2})

# Cool-off "trading days" are exchange-local (ET) calendar days — a stop logged
# evening-PT is ~03:00 UTC the next day, so counting in UTC shifted the window
# by a day. Convert all timestamps to ET before counting. (Fixed 2026-06.)
_EXCHANGE_TZ = ZoneInfo("America/New_York")


def _weekdays_elapsed(since: datetime, now: datetime) -> int:
    """Count FULL weekdays (Mon-Fri) elapsed between `since` and `now`.

    Counts the day AFTER `since` through the last COMPLETED day before `now`
    — the in-progress day (`now`'s own date) does NOT count, so "3 trading
    days after a stop" means re-entry on the (N+1)th session, not the Nth.
    (Decision 2026-06: "after N full trading days".) Both args are assumed to
    be in the same timezone frame (ET) so `.date()` yields ET calendar days.

    (Formerly mirrored in focus_rules._weekdays_elapsed; that module was
    removed 2026-07-11 — this is now the only copy.)
    """
    if now <= since:
        return 0
    days = 0
    cursor = since.date() + timedelta(days=1)
    end_date = now.date() - timedelta(days=1)  # exclude the in-progress day
    while cursor <= end_date:
        if cursor.weekday() < 5:
            days += 1
        cursor = cursor + timedelta(days=1)
    return days


def _is_tier_1_or_2_position(position: Position) -> bool:
    """Is this position in scope of orchestrator rule 11 (Tier 1 + Tier 2)?

    Three cases:
      - position.tier in {1, 2} → in scope (Tier 1 or Tier 2)
      - position.tier == 4      → out of scope (Tier 4 specialty)
      - position.tier is None   → in scope (legacy / pre-tag positions —
                                  conservative default; no false negatives)

    Rationale: rule 11 explicitly covers Tier 1 + Tier 2 only. Tier 4
    has its own specialty gates. But null-tier positions predate the tagging
    and could be on any skill — counting them as in-scope is the safe default.
    """
    tier = getattr(position, "tier", None)
    if tier is None:
        return True  # legacy / untagged — conservative
    return tier in TIERS_IN_SCOPE


def check_tier_portfolio_trade(
    ticker: str,
    direction: str,
    open_positions: Iterable[Position],
    closed_positions: Iterable[Position],
    now: datetime | None = None,
) -> list[RuleViolation]:
    """Validate a proposed Tier 1 / Tier 2 trade against orchestrator rule 11.

    Returns empty list when the trade is non-QQQ/GLD (rule doesn't apply) or
    when all three checks pass. Each violation is severity 'block'.

    Caller is responsible for invoking this only when the proposed trade is
    on a Tier 1 or Tier 2 skill — this function does not check the proposed
    skill itself, only the existing portfolio.
    """
    ticker_u = ticker.upper()
    direction_l = direction.lower()

    # Rule applies to QQQ + GLD only — short-circuit on other tickers.
    if ticker_u not in TIER_PORTFOLIO_TICKERS:
        return []

    # Work in exchange-local (ET): default to ET now; convert a passed-in clock
    # (aware → ET; naive → assume UTC, as positions are stored UTC-aware).
    if now is None:
        now = datetime.now(_EXCHANGE_TZ)
    elif now.tzinfo is not None:
        now = now.astimezone(_EXCHANGE_TZ)
    else:
        now = now.replace(tzinfo=timezone.utc).astimezone(_EXCHANGE_TZ)

    violations: list[RuleViolation] = []

    open_in_scope = [
        p for p in open_positions
        if p.ticker.upper() in TIER_PORTFOLIO_TICKERS
        and p.status == "open"
        and _is_tier_1_or_2_position(p)
    ]

    # ─ Rule 11.1: One open position per asset (max 2 across QQQ+GLD) ─
    if any(p.ticker.upper() == ticker_u for p in open_in_scope):
        violations.append(RuleViolation(
            rule="tier_portfolio_one_per_asset",
            severity="block",
            message=(
                f"Tier 1+2 portfolio rule (orchestrator rule 11): {ticker_u} "
                "already has an open position. Max 2 concurrent QQQ/GLD positions, "
                "one per asset, across Tier 1 and Tier 2 combined."
            ),
            current_value=1.0,
            limit=1.0,
        ))

    # ─ Rule 11.2: No same-THESIS QQQ+GLD pair simultaneously ─
    # Compare thesis direction, not contract direction: in this cash account
    # every option is direction="long" (a bearish trade is a long PUT), so a
    # raw p.direction compare both missed real correlated pairs (two bearish
    # legs both read "long") and blocked legitimate hedges. The proposed
    # `direction` is the kill-sheet thesis ("long"=bullish, "short"=bearish);
    # existing positions expose thesis via Position.thesis_direction. (Fixed 2026-06.)
    proposed_thesis = "bullish" if direction_l == "long" else "bearish"
    other_asset = [p for p in open_in_scope if p.ticker.upper() != ticker_u]
    same_dir_other = [p for p in other_asset if p.thesis_direction == proposed_thesis]
    if same_dir_other:
        existing = same_dir_other[0]
        violations.append(RuleViolation(
            rule="tier_portfolio_no_same_direction_pair",
            severity="block",
            message=(
                f"Tier 1+2 portfolio rule (orchestrator rule 11): "
                f"{existing.ticker} is already {existing.thesis_direction}. Never "
                "hold QQQ + GLD same thesis simultaneously — pick the "
                "stronger setup. Correlation in this regime is 1.0, not "
                "diversification."
            ),
            current_value=1.0,
            limit=0.0,
        ))

    # ─ Rule 11.3: 3-trading-day cool-off after a stop on the same asset ─
    for p in closed_positions:
        if p.ticker.upper() != ticker_u:
            continue
        if p.status != "closed":
            continue
        if p.pnl_usd is None or p.pnl_usd >= 0:
            continue
        if not p.closed_date:
            continue
        try:
            closed_dt = datetime.fromisoformat(p.closed_date)
        except ValueError:
            continue
        if closed_dt.tzinfo is None:
            closed_dt = closed_dt.replace(tzinfo=timezone.utc)
        closed_dt = closed_dt.astimezone(_EXCHANGE_TZ)  # count in ET, not UTC
        elapsed = _weekdays_elapsed(closed_dt, now)
        if elapsed < COOLOFF_TRADING_DAYS:
            violations.append(RuleViolation(
                rule="tier_portfolio_cooloff",
                severity="block",
                message=(
                    f"Tier 1+2 portfolio rule (orchestrator rule 11): "
                    f"{COOLOFF_TRADING_DAYS} trading days required after a stop "
                    f"on {ticker_u}; last stop closed "
                    f"{closed_dt.date().isoformat()} "
                    f"({elapsed} weekday(s) ago)."
                ),
                current_value=float(elapsed),
                limit=float(COOLOFF_TRADING_DAYS),
            ))
            break  # one violation is enough; don't list every prior loss

    return violations
