"""Threshold-evaluation logic for regime_health indicators."""
from __future__ import annotations

from regime_health.thresholds import (
    BreakevenThreshold,
    DEFAULT_THRESHOLDS,
    MA_STACK_TO_STATUS,
    NumericThreshold,
    SQN_REGIME_TO_STATUS,
)


# ── NumericThreshold (above) ─────────────────────────────────────────────────


def test_numeric_above_green_when_below_amber():
    t = NumericThreshold(amber_at=350, red_at=500, direction="above")
    assert t.evaluate(300) == "green"


def test_numeric_above_amber_at_boundary():
    t = NumericThreshold(amber_at=350, red_at=500, direction="above")
    assert t.evaluate(350) == "amber"
    assert t.evaluate(400) == "amber"


def test_numeric_above_red_at_boundary():
    t = NumericThreshold(amber_at=350, red_at=500, direction="above")
    assert t.evaluate(500) == "red"
    assert t.evaluate(900) == "red"


def test_numeric_above_unknown_when_none():
    t = NumericThreshold(amber_at=350, red_at=500, direction="above")
    assert t.evaluate(None) == "unknown"


# ── NumericThreshold (below) — for breadth slope ─────────────────────────────


def test_numeric_below_green_when_above_amber():
    t = NumericThreshold(amber_at=-0.5, red_at=-1.5, direction="below")
    # Healthy slope (positive or near-zero) is green
    assert t.evaluate(0.3) == "green"
    assert t.evaluate(-0.4) == "green"


def test_numeric_below_amber_at_boundary():
    t = NumericThreshold(amber_at=-0.5, red_at=-1.5, direction="below")
    assert t.evaluate(-0.5) == "amber"
    assert t.evaluate(-1.0) == "amber"


def test_numeric_below_red_at_boundary():
    t = NumericThreshold(amber_at=-0.5, red_at=-1.5, direction="below")
    assert t.evaluate(-1.5) == "red"
    assert t.evaluate(-3.0) == "red"


# ── BreakevenThreshold ───────────────────────────────────────────────────────


def test_breakeven_green_in_band():
    t = BreakevenThreshold(
        green_low=2.0, green_high=2.7,
        amber_low=1.8, amber_high=3.0,
        red_low=1.5, red_high=3.5,
    )
    assert t.evaluate(2.4) == "green"
    assert t.evaluate(2.0) == "green"
    assert t.evaluate(2.7) == "green"


def test_breakeven_amber_outside_green_inside_outer():
    t = BreakevenThreshold(
        green_low=2.0, green_high=2.7,
        amber_low=1.8, amber_high=3.0,
        red_low=1.5, red_high=3.5,
    )
    assert t.evaluate(1.9) == "amber"
    assert t.evaluate(2.85) == "amber"


def test_breakeven_red_beyond_outer_band():
    t = BreakevenThreshold(
        green_low=2.0, green_high=2.7,
        amber_low=1.8, amber_high=3.0,
        red_low=1.5, red_high=3.5,
    )
    assert t.evaluate(1.4) == "red"
    assert t.evaluate(3.6) == "red"


def test_breakeven_unknown_when_none():
    t = BreakevenThreshold(
        green_low=2.0, green_high=2.7,
        amber_low=1.8, amber_high=3.0,
        red_low=1.5, red_high=3.5,
    )
    assert t.evaluate(None) == "unknown"


# ── DEFAULT_THRESHOLDS ───────────────────────────────────────────────────────


def test_default_thresholds_match_spec():
    """⚠️ UNVERIFIED levels in the spec — spot-check that defaults didn't drift."""
    assert DEFAULT_THRESHOLDS.vix.amber_at == 18.0
    assert DEFAULT_THRESHOLDS.vix.red_at == 25.0
    assert DEFAULT_THRESHOLDS.vvix.amber_at == 100.0
    assert DEFAULT_THRESHOLDS.vvix.red_at == 115.0
    assert DEFAULT_THRESHOLDS.hy_oas_bps.amber_at == 350.0
    assert DEFAULT_THRESHOLDS.hy_oas_bps.red_at == 500.0


# ── Categorical mappings ─────────────────────────────────────────────────────


def test_sqn_regime_mapping_covers_all_bands():
    for regime in ("strong_bull", "bull", "neutral", "bear", "strong_bear"):
        assert regime in SQN_REGIME_TO_STATUS


def test_sqn_regime_mapping_directionality():
    assert SQN_REGIME_TO_STATUS["bull"] == "green"
    assert SQN_REGIME_TO_STATUS["strong_bull"] == "green"
    assert SQN_REGIME_TO_STATUS["neutral"] == "amber"
    assert SQN_REGIME_TO_STATUS["bear"] == "red"
    assert SQN_REGIME_TO_STATUS["strong_bear"] == "red"


def test_ma_stack_mapping_covers_all_states():
    for stack in (
        "full_bull", "bull_developing", "compression",
        "chop_tangled", "bear_developing", "full_bear",
    ):
        assert stack in MA_STACK_TO_STATUS
