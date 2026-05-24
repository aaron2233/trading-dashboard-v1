"""2026 trading recovery plan — R1/R2/R3 rule engine + milestone tracker.

Implements the recovery plan documented at
`~/Documents/Trading Recovery Plan 2026.md` (2026-05-13). The three hard
rules are SURFACED as warnings via the API; per existing dashboard design
(see api/app.py:1043-1046 "journal entries should never be blocked"),
positions are still recorded even when a rule is violated, and the
discipline scorecard handles retrospective rule-adherence scoring.

The point of this module is to make non-compliance LOUD before/during
entry — not to prevent honest journal-keeping after the fact.
"""
from recovery_plan.config import (
    RecoveryConfig, DEFAULT_CONFIG_PATH,
    load_config, save_config,
)
from recovery_plan.rules import (
    R1_LOTTO_CAP_USD, R1_MAIN_CAP_USD, R2_MAX_DAILY_ENTRIES,
    R3_STOP_FRACTION,
    check_r1_dollar_cap, check_r2_daily_entries, check_r3_standing_stop,
    RuleViolation, RuleCheckResult,
)
from recovery_plan.milestones import (
    Milestone, compute_milestones, current_milestone_status,
)
from recovery_plan.status import (
    RecoveryStatus, build_status,
)

__all__ = [
    "RecoveryConfig", "DEFAULT_CONFIG_PATH",
    "load_config", "save_config",
    "R1_LOTTO_CAP_USD", "R1_MAIN_CAP_USD", "R2_MAX_DAILY_ENTRIES",
    "R3_STOP_FRACTION",
    "check_r1_dollar_cap", "check_r2_daily_entries", "check_r3_standing_stop",
    "RuleViolation", "RuleCheckResult",
    "Milestone", "compute_milestones", "current_milestone_status",
    "RecoveryStatus", "build_status",
]
