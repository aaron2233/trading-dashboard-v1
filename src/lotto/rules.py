"""Kill-sheet rules-engine integration for lotto-account anti-greed.

When KillSheetRequest.account == "lotto", the API rules layer calls
`check_lotto_cooldown(positions)` to fire RuleViolation entries for active
cooldowns. The kill sheet builder treats these as 'block' severity by
default — to override, the user passes `bypass_rules=true` (same escape
hatch as other discipline gates), at which point the violation is logged
but the kill sheet still generates.

Three checks:
- lotto_cooldown_24h: 300%+ winner closed within last 24h
- lotto_cooldown_48h: 3 consecutive losses, most recent within last 48h
- lotto_size_lock: most recent lotto trade was a loss (warn, not block)
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from lotto.state import LottoCooldown, compute_lotto_state
from positions.model import Position
from positions.rules import RuleViolation


# Type alias for clarity at the call site
LottoCooldownViolation = RuleViolation


def check_lotto_cooldown(
    open_positions: Iterable[Position],
    closed_positions: Iterable[Position],
    *,
    base_balance_usd: float = 1_000.0,
    now: datetime | None = None,
) -> list[RuleViolation]:
    """Return RuleViolation list for active lotto cooldowns + size-lock.

    Caller is responsible for invoking this only when the proposed trade is
    on the lotto account. Returns empty list when no cooldowns active.
    """
    state = compute_lotto_state(
        open_positions=open_positions,
        closed_positions=closed_positions,
        base_balance_usd=base_balance_usd,
        now=now,
    )
    violations: list[RuleViolation] = []

    cd: LottoCooldown = state.cooldown
    if cd.active:
        if cd.reason == "post_big_win":
            violations.append(RuleViolation(
                rule="lotto_cooldown_24h",
                severity="block",
                message=(
                    "Anti-greed protocol: 24h cooldown active after a 300%+ "
                    f"lotto winner. {cd.hours_remaining:.1f}h remaining "
                    f"(expires {cd.expires_at}). "
                    "Bank the win, reset the head, then trade."
                ),
                current_value=float(cd.hours_remaining or 0.0),
                limit=0.0,
            ))
        elif cd.reason == "post_loss_streak":
            violations.append(RuleViolation(
                rule="lotto_cooldown_48h",
                severity="block",
                message=(
                    "Anti-greed protocol: 48h cooldown active after 3 "
                    f"consecutive lotto losses. {cd.hours_remaining:.1f}h "
                    f"remaining (expires {cd.expires_at}). "
                    "Review the 3 kill sheets — variance or process problem?"
                ),
                current_value=float(cd.hours_remaining or 0.0),
                limit=0.0,
            ))

    if state.size_lock_active:
        violations.append(RuleViolation(
            rule="lotto_size_lock",
            severity="warn",
            message=(
                f"Cardinal sin reminder: {state.size_lock_reason} "
                "Sizing this trade larger than your previous lotto requires "
                "explicit acknowledgement."
            ),
            current_value=1.0,
            limit=0.0,
        ))

    return violations
