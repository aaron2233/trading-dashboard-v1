"""Weekly-trend module — Sunday-scan workflow for the weekly TF.

Per ~/.claude/skills/user/weekly-trend-trader/SKILL.md. Surfaces per-ticker
weekly MA + Stoch + SQN reads with confluence ratings, ranks them per the
skill's priority order (regime alignment > Stoch position > MA clarity), and
flags penny stocks for shares-vs-options vehicle selection.

Track A (19/39 weekly cross) detection is also surfaced as an early-entry
signal independent of the 10/20/50/200 ribbon classification.
"""
from weekly_trend.scanner import (
    PENNY_STOCK_THRESHOLD,
    TRACK_A_BLOCKED_TICKERS,
    Confluence,
    Direction,
    TrackASignal,
    Vehicle,
    WeeklyScanResult,
    WeeklySetup,
    classify_confluence,
    detect_track_a_signal,
    scan_weekly_watchlist,
)

__all__ = [
    "Confluence",
    "Direction",
    "PENNY_STOCK_THRESHOLD",
    "TRACK_A_BLOCKED_TICKERS",
    "TrackASignal",
    "Vehicle",
    "WeeklyScanResult",
    "WeeklySetup",
    "classify_confluence",
    "detect_track_a_signal",
    "scan_weekly_watchlist",
]
