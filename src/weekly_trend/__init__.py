"""Weekly-trend module — Sunday-scan workflow for the weekly TF.

Per ~/.claude/skills/user/weekly-trend-trader/SKILL.md. Surfaces per-ticker
weekly MA + Stoch + SQN reads with confluence ratings, ranks them per the
skill's priority order (regime alignment > Stoch position > MA clarity), and
flags penny stocks for shares-vs-options vehicle selection.
"""
from weekly_trend.scanner import (
    PENNY_STOCK_THRESHOLD,
    Confluence,
    Direction,
    Vehicle,
    WeeklyScanResult,
    WeeklySetup,
    classify_confluence,
    scan_weekly_watchlist,
)

__all__ = [
    "Confluence",
    "Direction",
    "PENNY_STOCK_THRESHOLD",
    "Vehicle",
    "WeeklyScanResult",
    "WeeklySetup",
    "classify_confluence",
    "scan_weekly_watchlist",
]
