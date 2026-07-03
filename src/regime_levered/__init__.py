"""Regime-levered-trend strategy module.

Per ~/.claude/skills/user/regime-levered-trend/SKILL.md — concentrated
long-premium book: 1-2 deep-delta LEAPS on the strongest own-SQN(100) Bull
trends (Layer 1), rule-19 dip-buy sleeve on SPY/QQQ (Layer 2), cash in
Neutral/Bear (Layer 3). Long calls / long puts only.

Backtest evidence (synthetic-option, 2000-2026): 53 trades, WR 45%, avg
premium +39.8%, 34.7x vs SPY 8.2x, MaxDD −36% vs −55%. FORWARD-TEST cohort —
see the skill's Provenance section for the model's limits. Harness:
scripts/regime_levered_trend_backtest.py.
"""
from regime_levered.scanner import (
    BROAD_SQN_MIN,
    DEFAULT_UNIVERSE,
    DEPLOYMENT_GATE_NOTE,
    MAX_CORE_POSITIONS,
    OWN_SQN_MIN,
    DipBuySignal,
    RegimeLeveredScanResult,
    RegimeLeveredSetup,
    WeeklyState,
    classify_layer1,
    compute_weekly_state,
    scan_regime_levered,
)

__all__ = [
    "BROAD_SQN_MIN",
    "DEFAULT_UNIVERSE",
    "DEPLOYMENT_GATE_NOTE",
    "MAX_CORE_POSITIONS",
    "OWN_SQN_MIN",
    "DipBuySignal",
    "RegimeLeveredScanResult",
    "RegimeLeveredSetup",
    "WeeklyState",
    "classify_layer1",
    "compute_weekly_state",
    "scan_regime_levered",
]
