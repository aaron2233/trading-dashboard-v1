"""Discipline enforcement layer for the trading dashboard.

Implements the discipline skill (`~/.claude/skills/user/discipline/`):
- 15-rule per-trade scoring with profitable-violation flagging
- Weekly review aggregating discipline drift
- Stage detection (stage 1 = discipline > P&L)

Wires into the existing dashboard via:
- `KillSheet.status` regime gate + `DisciplineAttestation` block
- Per-position scoring via `score_trade(position, kill_sheet=..., ...)`
- JSON persistence at `~/.trading-dashboard/discipline/`
- `/api/v1/discipline` endpoints + `DisciplineView` / `WeeklyReviewView`

Source of truth: `~/Documents/Product Specs/Trading Dashboard/DISCIPLINE-LAYER-ADDITION.md`
"""
from discipline.dashboard import (
    DashboardState,
    UnreviewedWeek,
    compute_account_balance,
    compute_dashboard_state,
    find_unreviewed_weeks,
)
from discipline.model import (
    RULE_IDS,
    RULE_TEXT,
    DisciplineScore,
    RuleResult,
    WeeklyReview,
)
from discipline.score import ScoringContext, score_trade
from discipline.stage import STAGE_1_THRESHOLD_USD, Stage, current_stage, stage_reminder
from discipline.stats import DisciplineStats, compute_discipline_stats
from discipline.store import (
    DEFAULT_DISCIPLINE_DIR,
    LEGACY_CUTOFF,
    DisciplineStore,
    is_legacy_position,
)
from discipline.weekly_review import (
    compute_weekly_review,
    get_or_compute_weekly,
    week_bounds,
)


__all__ = [
    "DEFAULT_DISCIPLINE_DIR",
    "DashboardState",
    "DisciplineScore",
    "DisciplineStats",
    "DisciplineStore",
    "LEGACY_CUTOFF",
    "RULE_IDS",
    "RULE_TEXT",
    "RuleResult",
    "STAGE_1_THRESHOLD_USD",
    "ScoringContext",
    "Stage",
    "UnreviewedWeek",
    "WeeklyReview",
    "compute_account_balance",
    "compute_dashboard_state",
    "compute_discipline_stats",
    "compute_weekly_review",
    "current_stage",
    "find_unreviewed_weeks",
    "get_or_compute_weekly",
    "is_legacy_position",
    "score_trade",
    "stage_reminder",
    "week_bounds",
]
