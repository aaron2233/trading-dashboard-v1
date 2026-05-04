"""Lotto-account-specific dashboard module.

Per ~/.claude/skills/user/lotto-options/SKILL.md — surfaces anti-greed
cooldowns (24h post-300%-win, 48h post-3-loss-streak), size-lock,
growth-ladder stage, and cash-reserve status for the $1K lotto account.
Wires into the kill-sheet rules engine to BLOCK lotto kill sheets while
cooldowns are active.
"""
from lotto.rules import LottoCooldownViolation, check_lotto_cooldown
from lotto.state import (
    BIG_WIN_COOLDOWN_HOURS,
    BIG_WIN_RETURN_PCT,
    CASH_FLOOR_USD,
    GROWTH_LADDER,
    LOSS_STREAK_COOLDOWN_HOURS,
    LOSS_STREAK_TRIGGER,
    LOTTO_ACCOUNT_KEY,
    LottoCooldown,
    LottoState,
    LottoTradeSummary,
    compute_lotto_state,
)

__all__ = [
    "BIG_WIN_COOLDOWN_HOURS",
    "BIG_WIN_RETURN_PCT",
    "CASH_FLOOR_USD",
    "GROWTH_LADDER",
    "LOSS_STREAK_COOLDOWN_HOURS",
    "LOSS_STREAK_TRIGGER",
    "LOTTO_ACCOUNT_KEY",
    "LottoCooldown",
    "LottoCooldownViolation",
    "LottoState",
    "LottoTradeSummary",
    "check_lotto_cooldown",
    "compute_lotto_state",
]
