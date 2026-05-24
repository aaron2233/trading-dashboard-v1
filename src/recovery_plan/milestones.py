"""Recovery milestones (from `~/Documents/Trading Recovery Plan 2026.md`).

Computed dynamically from the user's year_start_balance and ytd_realized_pnl
so the milestones recompute correctly if those numbers update.

Milestones from $10,140 starting line, year-end target = breakeven:
  R-floor: $11,140  (+$1K toward recovery)
  R-half:  $11,855  (~half the YTD damage recovered)
  R-full:  $13,570  (YTD breakeven — primary target)
  Stretch: $15,000  (+48% from today, realistic-with-discipline ceiling)
  Aspirational: $20,000 (motivation, NOT a sizing driver)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Milestone:
    name: str        # "floor" / "half" / "breakeven" / "stretch" / "aspirational"
    label: str       # Human-readable
    threshold: float
    hit: bool        # current_balance >= threshold

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "threshold": round(self.threshold, 2),
            "hit": self.hit,
        }


def compute_milestones(
    current_balance: float,
    ytd_realized_pnl: float,
    year_breakeven_target: float,
    *,
    stretch_target: float = 15000.0,
    aspirational_target: float = 20000.0,
) -> list[Milestone]:
    """Compute the 5-milestone ladder. Floor and half are derived from the
    current balance + breakeven gap so the ladder stays meaningful as the
    user makes (or loses) money.
    """
    gap_to_breakeven = max(year_breakeven_target - current_balance, 0)
    floor = round(current_balance + gap_to_breakeven * 0.30, 0)
    half = round(current_balance + gap_to_breakeven * 0.50, 0)
    return [
        Milestone("floor", "R-floor (30% of recovery)", float(floor),
                  hit=current_balance >= floor),
        Milestone("half", "R-half (50% of recovery)", float(half),
                  hit=current_balance >= half),
        Milestone("breakeven", "R-full · YTD breakeven", year_breakeven_target,
                  hit=current_balance >= year_breakeven_target),
        Milestone("stretch", "Stretch (realistic ceiling)", stretch_target,
                  hit=current_balance >= stretch_target),
        Milestone("aspirational", "Aspirational (motivation only)",
                  aspirational_target,
                  hit=current_balance >= aspirational_target),
    ]


def current_milestone_status(milestones: list[Milestone]) -> dict:
    """Pick the next un-hit milestone for the UI to show as "working toward"."""
    next_milestone: Milestone | None = None
    for m in milestones:
        if not m.hit:
            next_milestone = m
            break
    last_hit = None
    for m in milestones:
        if m.hit:
            last_hit = m
    return {
        "last_hit": last_hit.to_dict() if last_hit else None,
        "next": next_milestone.to_dict() if next_milestone else None,
        "all_hit": all(m.hit for m in milestones),
    }
