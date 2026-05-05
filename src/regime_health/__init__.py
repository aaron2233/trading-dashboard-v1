"""Regime Health — leading-indicator monitor that surfaces macro cracks
3-6 months before SQN(100) flips structural regime.

Spec: ~/Documents/Product Specs/Trading Dashboard/REGIME-HEALTH-PANEL-2026-05-05.md

Tiers:
  1. Structural & Volatility — SPY/QQQ SQN(100)/(20), Weekly MA, VIX, VVIX
  2. Macro (FRED) — HY OAS, 2s10s, 3m10s, 5Y breakeven, broad dollar
  3. Breadth — RSP/SPY ratio
  4. AI capex calendar — manual entry, hand-edited per quarter

The snapshot is a passive read-only state bundle. No alerts in v1.
"""
from regime_health.model import (
    IndicatorReading,
    IndicatorStatus,
    RegimeHealthSnapshot,
    TierBundle,
)
from regime_health.thresholds import DEFAULT_THRESHOLDS, ThresholdConfig

__all__ = [
    "DEFAULT_THRESHOLDS",
    "IndicatorReading",
    "IndicatorStatus",
    "RegimeHealthSnapshot",
    "ThresholdConfig",
    "TierBundle",
]
