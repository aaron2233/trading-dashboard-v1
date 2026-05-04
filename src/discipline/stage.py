"""Stage detection.

Per ~/.claude/skills/user/discipline/SKILL.md and DISCIPLINE-LAYER-ADDITION.md:
- Stage 1 (account < $100K): discipline score is the primary KPI; P&L is secondary.
- Stage 2 (account ≥ $100K): P&L returns to primary metric; discipline floor remains.

Account balance source is `src/positions/` aggregated state. Single read; no
caching at this scale.
"""
from __future__ import annotations

from typing import Literal


STAGE_1_THRESHOLD_USD = 100_000

Stage = Literal["stage_1", "stage_2"]


def current_stage(account_balance_usd: float) -> Stage:
    """Return the active stage given an account balance."""
    return "stage_2" if account_balance_usd >= STAGE_1_THRESHOLD_USD else "stage_1"


def stage_reminder(stage: Stage) -> str:
    """Human-readable reminder text shown in dashboard banners and CLI output."""
    if stage == "stage_2":
        return "Stage 2 — P&L primary, discipline floor"
    return "Stage 1 — Discipline > P&L until $100K"
