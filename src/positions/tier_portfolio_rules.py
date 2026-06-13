"""Tier 1 + Tier 2 combined portfolio rules for QQQ + GLD.

Implements orchestration rule 11 from `~/CLAUDE.md`:

    Max 2 concurrent QQQ/GLD positions across Tier 1 and Tier 2 combined
    (one per asset). Never long QQQ + long GLD same direction simultaneously
    — pick the stronger setup. Cool-off 3 trading days after a stop on
    either asset before re-entering on the same name.

This is distinct from `focus_rules.py` (Tier 4 qqq-gld-focus workflow gates):
- focus_rules fires only when `--focus` flag is set; this fires whenever the
  ticker is QQQ or GLD on a Tier 1 or Tier 2 skill.
- focus_rules also enforces the $200 per-trade cap and DTE bands; those are
  qqq-gld-focus specialty rules, NOT in orchestrator rule 11.

V1 limitation: Position model does not yet carry a skill/tier field. We
therefore count ALL open QQQ/GLD positions regardless of skill. This is
strictly broader than "Tier 1 + Tier 2 combined" but produces no false
negatives. When positions gain a skill/tier field, refine to filter on tier.
Marked TODO inline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from positions.model import Position
from positions.rules import RuleViolation


TIER_PORTFOLIO_TICKERS: frozenset[str] = frozenset({"QQQ", "GLD"})
COOLOFF_TRADING_DAYS: int = 3
TIERS_IN_SCOPE: frozenset[int] = frozenset({1, 2})


def _weekdays_elapsed(since: datetime, now: datetime) -> int:
    """Count weekdays (Mon-Fri) from the day AFTER `since` through `now`.

    Mirrors focus_rules._weekdays_elapsed (intentionally duplicated rather
    than imported to keep the two modules independent — focus_rules can be
    deleted/folded later without breaking this one).
    """
    if now <= since:
        return 0
    days = 0
    cursor = since.date() + timedelta(days=1)
    end_date = now.date()
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

    if now is None:
        now = datetime.now(timezone.utc)

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
