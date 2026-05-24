"""Aggregator that builds the full RecoveryStatus payload — config +
milestones + today's R2 state + any active rule warnings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from recovery_plan.config import RecoveryConfig
from recovery_plan.milestones import (
    Milestone, compute_milestones, current_milestone_status,
)
from recovery_plan.rules import (
    R1_LOTTO_CAP_USD, R1_MAIN_CAP_USD, R2_MAX_DAILY_ENTRIES,
    _entries_today,
)


@dataclass
class RecoveryStatus:
    # Config snapshot
    year_start_balance: float
    current_balance: float
    ytd_realized_pnl: float
    deposits_total: float
    year_breakeven_target: float
    plan_committed_at: str

    # Computed
    pnl_from_today_needed: float       # $ needed from now to hit breakeven
    pct_to_breakeven: float             # 0-100, current / breakeven_target
    milestones: list[dict]
    milestone_status: dict

    # R1/R2/R3 reference state
    r1_lotto_cap_usd: float
    r1_main_cap_usd: float
    r2_max_daily_entries: int
    r2_entries_today: int
    r2_remaining_today: int

    def to_dict(self) -> dict:
        return {
            "year_start_balance": self.year_start_balance,
            "current_balance": self.current_balance,
            "ytd_realized_pnl": self.ytd_realized_pnl,
            "deposits_total": self.deposits_total,
            "year_breakeven_target": self.year_breakeven_target,
            "plan_committed_at": self.plan_committed_at,
            "pnl_from_today_needed": round(self.pnl_from_today_needed, 2),
            "pct_to_breakeven": round(self.pct_to_breakeven, 1),
            "milestones": self.milestones,
            "milestone_status": self.milestone_status,
            "r1_lotto_cap_usd": self.r1_lotto_cap_usd,
            "r1_main_cap_usd": self.r1_main_cap_usd,
            "r2_max_daily_entries": self.r2_max_daily_entries,
            "r2_entries_today": self.r2_entries_today,
            "r2_remaining_today": self.r2_remaining_today,
        }


def build_status(
    config: RecoveryConfig,
    positions: list,
    *,
    now: datetime | None = None,
) -> RecoveryStatus:
    milestones = compute_milestones(
        current_balance=config.current_balance,
        ytd_realized_pnl=config.ytd_realized_pnl,
        year_breakeven_target=config.year_breakeven_target,
    )
    ms_status = current_milestone_status(milestones)
    entries_today = _entries_today(positions, now=now)
    breakeven_target = config.year_breakeven_target
    pnl_needed = max(breakeven_target - config.current_balance, 0)
    if breakeven_target > 0:
        pct = (config.current_balance / breakeven_target) * 100
    else:
        pct = 100.0
    return RecoveryStatus(
        year_start_balance=config.year_start_balance,
        current_balance=config.current_balance,
        ytd_realized_pnl=config.ytd_realized_pnl,
        deposits_total=config.deposits_total,
        year_breakeven_target=breakeven_target,
        plan_committed_at=config.plan_committed_at,
        pnl_from_today_needed=pnl_needed,
        pct_to_breakeven=pct,
        milestones=[m.to_dict() for m in milestones],
        milestone_status=ms_status,
        r1_lotto_cap_usd=R1_LOTTO_CAP_USD,
        r1_main_cap_usd=R1_MAIN_CAP_USD,
        r2_max_daily_entries=R2_MAX_DAILY_ENTRIES,
        r2_entries_today=entries_today,
        r2_remaining_today=max(R2_MAX_DAILY_ENTRIES - entries_today, 0),
    )
