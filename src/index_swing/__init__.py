"""Index-swing strategy module.

Per ~/.claude/skills/user/index-swing/SKILL.md — long-only price-action swing
on QQQ/IWM/SPY. Daily-close breakouts above the prior 5-bar swing high, with
2% structural stop and 2R take-profit. SQN(20) Bear Volatile = hard skip.

Backtest evidence: 370 trades 1999-2022, WR 52.4%, expectancy +0.88R, PF 2.09,
SQN 6.15. See ~/.claude/skills/user/index-swing/references/backtest-evidence.md.
"""
from index_swing.scanner import (
    INDEX_SWING_ALLOWED_TICKERS,
    INDEX_SWING_TIER_PRIMARY,
    INDEX_SWING_TIER_SECONDARY,
    IndexSwingScanResult,
    IndexSwingSetup,
    SwingHighBreakout,
    detect_swing_high_breakout,
    scan_index_swing_watchlist,
)

__all__ = [
    "INDEX_SWING_ALLOWED_TICKERS",
    "INDEX_SWING_TIER_PRIMARY",
    "INDEX_SWING_TIER_SECONDARY",
    "IndexSwingScanResult",
    "IndexSwingSetup",
    "SwingHighBreakout",
    "detect_swing_high_breakout",
    "scan_index_swing_watchlist",
]
