"""Tests for the trend-pyramid module."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from pyramid import (
    ExitDirective,
    GateResult,
    Pyramid,
    PyramidStore,
    StructureRead,
    Tranche,
    analyze_structure,
    evaluate_exits,
    evaluate_gate,
    evaluate_pyramid,
    evaluate_t1,
    evaluate_t2,
    evaluate_t3,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _bars_from_closes(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(closes))
    series = pd.Series(closes, index=dates, name="close")
    return pd.DataFrame({
        "open": series,
        "high": series + 0.5,
        "low": series - 0.5,
        "close": series,
        "volume": 1_000_000,
    })


def _ma(closes: pd.Series, n: int) -> pd.Series:
    return closes.rolling(n).mean()


# ── Pyramid + Tranche model ─────────────────────────────────────────────────


def test_pyramid_create_initializes_three_pending_tranches():
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    assert p.ticker == "SPY"
    assert p.direction == "long"
    assert p.status == "pending"
    assert len(p.tranches) == 3
    assert all(t.status == "pending" for t in p.tranches)
    assert [t.id for t in p.tranches] == [1, 2, 3]


def test_pyramid_create_validates_direction():
    with pytest.raises(ValueError):
        Pyramid.create(ticker="SPY", direction="sideways", total_allocation_usd=5000)  # type: ignore[arg-type]


def test_pyramid_create_validates_allocation():
    with pytest.raises(ValueError):
        Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=-1)


def test_tranche_fill_records_correctly():
    t = Tranche(id=1)
    t.fill(vehicle="leaps_call", cost_basis_per_unit=12.50, quantity=3,
           strike=580, expiry="2027-01-15")
    assert t.status == "filled"
    assert t.cost_basis_per_unit == 12.50
    assert t.quantity == 3
    assert t.vehicle == "leaps_call"
    assert t.strike == 580
    assert t.expiry == "2027-01-15"
    # Options: per-share premium × contracts × 100
    assert t.total_cost_usd() == 12.50 * 3 * 100


def test_tranche_fill_shares_total_cost():
    t = Tranche(id=1)
    t.fill(vehicle="shares", cost_basis_per_unit=720.65, quantity=10)
    assert t.total_cost_usd() == 720.65 * 10


def test_tranche_double_fill_raises():
    t = Tranche(id=1)
    t.fill(vehicle="shares", cost_basis_per_unit=100.0, quantity=10)
    with pytest.raises(ValueError):
        t.fill(vehicle="shares", cost_basis_per_unit=110.0, quantity=10)


def test_pyramid_roundtrip_dict():
    p = Pyramid.create(ticker="QQQ", direction="long", total_allocation_usd=3000)
    p.tranches[0].fill(vehicle="shares", cost_basis_per_unit=500.0, quantity=2)
    d = p.to_dict()
    p2 = Pyramid.from_dict(d)
    assert p2.id == p.id
    assert p2.tranches[0].status == "filled"
    assert p2.tranches[0].quantity == 2


# ── Structure analysis ─────────────────────────────────────────────────────


def test_analyze_structure_steady_uptrend_higher_low():
    # Series with clearly identifiable pivots in the last-30-bar window.
    # Pattern: low → high → higher_low → higher_high → low → high
    closes = [
        # 30+ bars so the lookback window has multiple swings
        100, 102, 104, 106, 108, 110,    # rise
        108, 105, 102, 100, 103, 106,    # dip to 100 (pivot low #1)
        109, 112, 115, 118, 120, 122,    # rise to 122 (pivot high #1)
        120, 117, 113, 110, 108, 112,    # dip to 108 (pivot low #2 — HIGHER than #1)
        116, 120, 124, 128, 130, 132,    # rise to 132 (pivot high #2 — HIGHER than #1)
        130, 127, 123, 120, 117,         # most recent dip
    ]
    bars = _bars_from_closes(closes)
    closes_s = bars["close"]
    structure = analyze_structure(bars, _ma(closes_s, 20), _ma(closes_s, 50))
    # Verify pivots identified and HL pattern detected
    assert structure.recent_swing_low is not None
    assert structure.recent_swing_high is not None
    if structure.prior_swing_low is not None:
        assert structure.higher_low_confirmed == (
            structure.recent_swing_low > structure.prior_swing_low
        )


def test_analyze_structure_handles_short_series():
    # Series too short to identify pivots — should return empty-but-valid read.
    bars = _bars_from_closes([100.0, 101.0, 102.0])
    closes = bars["close"]
    structure = analyze_structure(bars, _ma(closes, 20), _ma(closes, 50))
    assert structure.higher_low_confirmed is False
    assert structure.lower_high_confirmed is False


# ── Gate ────────────────────────────────────────────────────────────────────


def _ok_long_structure() -> StructureRead:
    return StructureRead(
        recent_swing_high=110.0, recent_swing_high_date="2024-03-01",
        recent_swing_low=105.0, recent_swing_low_date="2024-03-15",
        prior_swing_high=108.0, prior_swing_low=100.0,
        pullback_held_20ma=True, pullback_held_50ma=True,
        rally_rejected_at_20ma=False, rally_rejected_at_50ma=False,
        higher_low_confirmed=True, lower_high_confirmed=False,
    )


def _ok_short_structure() -> StructureRead:
    return StructureRead(
        recent_swing_high=100.0, recent_swing_high_date="2024-03-01",
        recent_swing_low=90.0, recent_swing_low_date="2024-03-15",
        prior_swing_high=110.0, prior_swing_low=95.0,
        pullback_held_20ma=False, pullback_held_50ma=False,
        rally_rejected_at_20ma=True, rally_rejected_at_50ma=True,
        higher_low_confirmed=False, lower_high_confirmed=True,
    )


def test_gate_long_all_pass():
    g = evaluate_gate(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_regime="bull",
        sqn_20_value=1.0,
        ma_stack_state="full_bull",
        structure=_ok_long_structure(),
    )
    assert g.permitted is True
    assert g.blockers == []


def test_gate_long_chase_zone_blocks():
    g = evaluate_gate(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_regime="strong_bull",
        sqn_20_value=2.6,  # > +2.5 chase
        ma_stack_state="full_bull",
        structure=_ok_long_structure(),
    )
    assert g.permitted is False
    assert g.sqn_20_pass is False
    assert any("chase" in b.lower() for b in g.blockers)


def test_gate_long_neutral_sqn100_blocks():
    g = evaluate_gate(
        direction="long",
        sqn_100_regime="neutral",
        sqn_20_regime="bull",
        sqn_20_value=1.0,
        ma_stack_state="full_bull",
        structure=_ok_long_structure(),
    )
    assert g.permitted is False
    assert g.sqn_100_pass is False


def test_gate_long_wrong_stack_blocks():
    g = evaluate_gate(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_regime="bull",
        sqn_20_value=1.0,
        ma_stack_state="bull_developing",  # not full_bull
        structure=_ok_long_structure(),
    )
    assert g.permitted is False
    assert g.ma_stack_pass is False


def test_gate_short_mirrors_long():
    g = evaluate_gate(
        direction="short",
        sqn_100_regime="bear",
        sqn_20_regime="bear",
        sqn_20_value=-1.0,
        ma_stack_state="full_bear",
        structure=_ok_short_structure(),
    )
    assert g.permitted is True


def test_gate_short_capitulation_extreme_blocks():
    g = evaluate_gate(
        direction="short",
        sqn_100_regime="bear",
        sqn_20_regime="strong_bear",
        sqn_20_value=-2.6,  # < -2.5 extreme
        ma_stack_state="full_bear",
        structure=_ok_short_structure(),
    )
    assert g.permitted is False
    assert g.sqn_20_pass is False
    assert any("capitulation" in b.lower() for b in g.blockers)


def test_gate_unknown_direction_raises():
    with pytest.raises(ValueError):
        evaluate_gate(
            direction="sideways",
            sqn_100_regime="bull",
            sqn_20_regime="bull",
            sqn_20_value=1.0,
            ma_stack_state="full_bull",
            structure=_ok_long_structure(),
        )


# ── Tranche triggers ────────────────────────────────────────────────────────


def test_t1_long_fires_when_stoch_crosses_up_in_zone_and_near_20ma():
    r = evaluate_t1(
        direction="long",
        stoch_k=35.0, stoch_d=30.0,  # in 20-50, k > d
        close=100.0, ma_20=99.5,     # within 3%
    )
    assert r.should_fire is True


def test_t1_long_fails_overbought():
    r = evaluate_t1(
        direction="long",
        stoch_k=85.0, stoch_d=82.0,  # too high
        close=100.0, ma_20=99.5,
    )
    assert r.should_fire is False
    assert any("Stoch" in b for b in r.blockers)


def test_t1_long_fails_far_from_20ma():
    r = evaluate_t1(
        direction="long",
        stoch_k=35.0, stoch_d=30.0,
        close=120.0, ma_20=100.0,  # 20% above 20MA
    )
    assert r.should_fire is False
    assert any("20MA" in b for b in r.blockers)


def test_t1_short_fires_when_stoch_crosses_down_in_zone():
    r = evaluate_t1(
        direction="short",
        stoch_k=65.0, stoch_d=70.0,  # in 50-80, k < d
        close=100.0, ma_20=100.5,
    )
    assert r.should_fire is True


def test_t2_blocks_until_t1_filled():
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    r = evaluate_t2(
        direction="long",
        pyramid=p,
        stoch_k=30.0, stoch_d=28.0,
        sqn_100_regime="bull", sqn_20_regime="neutral",
        structure=_ok_long_structure(),
    )
    assert r.should_fire is False
    assert any("T1" in b for b in r.blockers)


def test_t2_long_fires_with_t1_filled_and_retest_held():
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    p.tranches[0].fill(vehicle="shares", cost_basis_per_unit=100, quantity=10)
    r = evaluate_t2(
        direction="long",
        pyramid=p,
        stoch_k=32.0, stoch_d=28.0,  # in 20-40, k > d
        sqn_100_regime="bull", sqn_20_regime="neutral",
        structure=_ok_long_structure(),  # pullback held & higher-low
    )
    assert r.should_fire is True


def test_t3_blocks_until_t1_and_t2_filled():
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    p.tranches[0].fill(vehicle="shares", cost_basis_per_unit=100, quantity=10)
    # T2 not filled
    r = evaluate_t3(
        direction="long",
        pyramid=p,
        stoch_k=60.0, stoch_d=55.0,
        sqn_20_value=1.0,
        sqn_100_regime="bull",
        structure=_ok_long_structure(),
        ma_10=110.0, ma_20=105.0,
    )
    assert r.should_fire is False
    assert any("T1 and T2" in b for b in r.blockers)


# ── T3 swing-reference behavior (v2 — exact T1 reference) ──────────────────


def _filled_pyramid_long(swing_high_at_t1: float | None = None) -> Pyramid:
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    p.tranches[0].fill(
        vehicle="shares", cost_basis_per_unit=100, quantity=10,
        swing_high_at_fill=swing_high_at_t1,
    )
    p.tranches[1].fill(vehicle="shares", cost_basis_per_unit=105, quantity=10)
    return p


def test_t3_uses_t1_reference_when_present():
    """Recent swing high must clear T1's stored swing_high_at_fill."""
    p = _filled_pyramid_long(swing_high_at_t1=109.0)
    # Recent swing_high (110) > T1 reference (109) → should pass new-extreme
    r = evaluate_t3(
        direction="long", pyramid=p,
        stoch_k=60.0, stoch_d=55.0,
        sqn_20_value=1.0, sqn_100_regime="bull",
        structure=_ok_long_structure(),  # recent_swing_high=110
        ma_10=110.0, ma_20=105.0,
    )
    # No new-extreme blocker should fire (other blockers still possible)
    assert not any("not above" in b.lower() for b in r.blockers)
    assert "exact T1 reference" in r.reasons["new_extreme"]


def test_t3_blocks_when_recent_swing_high_below_t1_reference():
    """T1 captured a swing high that current price hasn't cleared → block."""
    p = _filled_pyramid_long(swing_high_at_t1=115.0)
    r = evaluate_t3(
        direction="long", pyramid=p,
        stoch_k=60.0, stoch_d=55.0,
        sqn_20_value=1.0, sqn_100_regime="bull",
        structure=_ok_long_structure(),  # recent_swing_high=110, < 115 reference
        ma_10=110.0, ma_20=105.0,
    )
    assert any("not above" in b for b in r.blockers)
    assert "115" in r.reasons["new_extreme"]


def test_t3_falls_back_to_prior_swing_when_no_t1_reference():
    """Legacy T1 fills (no swing_high_at_fill) use the recent vs. prior comparison."""
    p = _filled_pyramid_long(swing_high_at_t1=None)  # legacy fill
    r = evaluate_t3(
        direction="long", pyramid=p,
        stoch_k=60.0, stoch_d=55.0,
        sqn_20_value=1.0, sqn_100_regime="bull",
        structure=_ok_long_structure(),  # recent=110, prior=108 → passes fallback
        ma_10=110.0, ma_20=105.0,
    )
    assert "fallback" in r.reasons["new_extreme"]
    assert not any("not above" in b.lower() for b in r.blockers)


def test_t3_short_uses_t1_low_reference_when_present():
    p = Pyramid.create(ticker="SPY", direction="short", total_allocation_usd=5000)
    p.tranches[0].fill(
        vehicle="shares", cost_basis_per_unit=100, quantity=10,
        swing_low_at_fill=92.0,  # T1 captured this swing low
    )
    p.tranches[1].fill(vehicle="shares", cost_basis_per_unit=95, quantity=10)
    # _ok_short_structure has recent_swing_low=90 < 92 → passes
    r = evaluate_t3(
        direction="short", pyramid=p,
        stoch_k=40.0, stoch_d=45.0,
        sqn_20_value=-1.0, sqn_100_regime="bear",
        structure=_ok_short_structure(),
        ma_10=95.0, ma_20=100.0,
    )
    assert "exact T1 reference" in r.reasons["new_extreme"]
    assert not any("not below" in b for b in r.blockers)


def test_tranche_fill_captures_swing_values():
    """Tranche.fill() accepts and persists swing_high/low_at_fill."""
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    t1 = p.get_tranche(1)
    t1.fill(
        vehicle="shares", cost_basis_per_unit=100, quantity=10,
        swing_high_at_fill=109.5, swing_low_at_fill=98.0,
    )
    assert t1.swing_high_at_fill == 109.5
    assert t1.swing_low_at_fill == 98.0


def test_tranche_fill_swing_values_default_none():
    """Backward compat: existing fills without swing kwargs leave fields None."""
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    t1 = p.get_tranche(1)
    t1.fill(vehicle="shares", cost_basis_per_unit=100, quantity=10)
    assert t1.swing_high_at_fill is None
    assert t1.swing_low_at_fill is None


# ── Divergence detection (v2) ──────────────────────────────────────────────


def test_detect_bearish_divergence_positive_case():
    """Higher price-pivot-high + lower stoch-pivot-high = confirmed bearish div."""
    import pandas as pd
    from pyramid.divergence import detect_bearish_divergence

    # Fabricate a 30-bar series with two clear pivot highs:
    # Bar 9 (price 110, stoch 85), Bar 19 (price 115, stoch 70)
    closes = pd.Series([100.0] * 30)
    closes.iloc[9] = 110.0
    closes.iloc[19] = 115.0   # higher price high
    stoch = pd.Series([50.0] * 30)
    stoch.iloc[9] = 85.0
    stoch.iloc[19] = 70.0     # lower stoch high → bearish divergence

    result = detect_bearish_divergence(closes, stoch)
    assert result.confirmed is True
    assert result.price_pivot_recent == 115.0
    assert result.price_pivot_prior == 110.0
    assert result.stoch_pivot_recent == 70.0
    assert result.stoch_pivot_prior == 85.0


def test_detect_bearish_divergence_negative_when_stoch_also_higher():
    """Both price AND stoch make higher highs → momentum confirms, no divergence."""
    import pandas as pd
    from pyramid.divergence import detect_bearish_divergence

    closes = pd.Series([100.0] * 30)
    closes.iloc[9] = 110.0
    closes.iloc[19] = 115.0
    stoch = pd.Series([50.0] * 30)
    stoch.iloc[9] = 70.0
    stoch.iloc[19] = 85.0  # higher → confirms, not divergent

    result = detect_bearish_divergence(closes, stoch)
    assert result.confirmed is False
    assert "didn't make a lower high" in result.note


def test_detect_bearish_divergence_insufficient_pivots():
    """Series with no clear pivots → not confirmed, no exception."""
    import pandas as pd
    from pyramid.divergence import detect_bearish_divergence

    flat = pd.Series([100.0] * 30)
    result = detect_bearish_divergence(flat, flat)
    assert result.confirmed is False


def test_detect_bullish_divergence_positive_case():
    """Lower price-pivot-low + higher stoch-pivot-low = confirmed bullish div."""
    import pandas as pd
    from pyramid.divergence import detect_bullish_divergence

    closes = pd.Series([100.0] * 30)
    closes.iloc[9] = 90.0
    closes.iloc[19] = 85.0   # lower low
    stoch = pd.Series([50.0] * 30)
    stoch.iloc[9] = 15.0
    stoch.iloc[19] = 25.0    # higher low → bullish divergence

    result = detect_bullish_divergence(closes, stoch)
    assert result.confirmed is True


def test_exits_long_stoch_overbought_without_divergence_warns_only():
    """Stoch >80 alone → warn severity, no auto-trim (skill rule)."""
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=0.5,
        stoch_k=85.0,
        close=110.0, ma_50=100.0, ma_200=95.0,
        # no divergence read passed
    )
    overbought = [d for d in exits if "overbought" in d.reason.lower()]
    assert len(overbought) == 1
    assert overbought[0].severity == "warn"
    assert "do not auto-trim" in overbought[0].reason.lower()


def test_exits_long_stoch_overbought_with_divergence_actions_trim():
    """Stoch >80 + confirmed bearish divergence → action severity trim."""
    from pyramid.divergence import DivergenceResult

    div = DivergenceResult(
        confirmed=True,
        price_pivot_recent=115.0, price_pivot_prior=110.0,
        stoch_pivot_recent=70.0, stoch_pivot_prior=85.0,
        note="price 110.00→115.00, stoch %K 85.0→70.0",
    )
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=0.5,
        stoch_k=85.0,
        close=115.0, ma_50=100.0, ma_200=95.0,
        bearish_divergence=div,
    )
    overbought = [d for d in exits if "trim 33%" in d.reason]
    assert len(overbought) == 1
    assert overbought[0].severity == "action"
    assert overbought[0].action == "trim_33"
    assert "confirmed bearish divergence" in overbought[0].reason


def test_exits_short_stoch_oversold_without_divergence_warns_only():
    exits = evaluate_exits(
        direction="short",
        sqn_100_regime="bear",
        sqn_20_value=-0.5,
        stoch_k=15.0,
        close=85.0, ma_50=100.0, ma_200=110.0,
    )
    oversold = [d for d in exits if "oversold" in d.reason.lower()]
    assert len(oversold) == 1
    assert oversold[0].severity == "warn"


def test_exits_short_stoch_oversold_with_divergence_actions_trim():
    from pyramid.divergence import DivergenceResult

    div = DivergenceResult(
        confirmed=True,
        price_pivot_recent=85.0, price_pivot_prior=90.0,
        stoch_pivot_recent=25.0, stoch_pivot_prior=15.0,
        note="price 90.00→85.00, stoch %K 15.0→25.0",
    )
    exits = evaluate_exits(
        direction="short",
        sqn_100_regime="bear",
        sqn_20_value=-0.5,
        stoch_k=15.0,
        close=85.0, ma_50=100.0, ma_200=110.0,
        bullish_divergence=div,
    )
    oversold = [d for d in exits if "trim 33%" in d.reason]
    assert len(oversold) == 1
    assert oversold[0].severity == "action"


# ── LEAPS roll directives — economic context surfacing (v2) ────────────────


def _leaps_tranche(tranche_id: int, days_until_expiry: int, strike: float = 100.0,
                   cost_basis: float = 5.00, quantity: int = 10):
    """Build a filled LEAPS tranche at a precise DTE for deterministic tests."""
    from datetime import date, timedelta
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=10_000)
    tr = p.get_tranche(tranche_id)
    expiry = (date.today() + timedelta(days=days_until_expiry)).isoformat()
    tr.fill(
        vehicle="leaps_call",
        cost_basis_per_unit=cost_basis,
        quantity=quantity,
        strike=strike,
        expiry=expiry,
    )
    return tr


def test_leaps_roll_directive_includes_economics_when_tranche_passed():
    """leaps_tranches path surfaces cost basis, qty, strike, exposure, DTE."""
    tr = _leaps_tranche(1, days_until_expiry=100, strike=480, cost_basis=15.0, quantity=5)
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull", sqn_20_value=0.5,
        stoch_k=50.0,
        close=500.0, ma_50=480.0, ma_200=460.0,
        leaps_tranches=[tr],
    )
    leaps = [d for d in exits if d.action == "roll_leaps"]
    assert len(leaps) == 1
    d = leaps[0]
    # Economic context populated
    assert d.cost_basis_per_unit == 15.0
    assert d.quantity == 5
    assert d.strike == 480
    # Exposure = 15 * 5 * 100 = $7,500
    assert d.current_exposure_usd == 7500.0
    assert d.dte == 100
    # Reason string carries the human-readable economics
    assert "$7,500" in d.reason
    assert "strike $480" in d.reason
    assert "5× contracts" in d.reason
    assert "Pull live roll quote from brokerage" in d.reason


def test_leaps_hard_close_directive_at_90_dte_includes_economics():
    tr = _leaps_tranche(2, days_until_expiry=85, strike=100, cost_basis=8.0, quantity=3)
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull", sqn_20_value=0.5,
        stoch_k=50.0,
        close=110.0, ma_50=100.0, ma_200=95.0,
        leaps_tranches=[tr],
    )
    closes = [d for d in exits if d.action == "hard_close_leaps"]
    assert len(closes) == 1
    d = closes[0]
    assert d.severity == "action"
    assert d.dte == 85
    assert d.current_exposure_usd == 2400.0  # 8 × 3 × 100
    assert "Pull live roll quote from brokerage" in d.reason


def test_leaps_legacy_expiries_path_still_works_without_economics():
    """Backward compat — passing the (id, expiry) tuple form omits economics."""
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=100)).isoformat()
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull", sqn_20_value=0.5,
        stoch_k=50.0,
        close=110.0, ma_50=100.0, ma_200=95.0,
        leaps_expiries=[(1, expiry)],
    )
    leaps = [d for d in exits if d.action == "roll_leaps"]
    assert len(leaps) == 1
    d = leaps[0]
    assert d.cost_basis_per_unit is None
    assert d.quantity is None
    assert d.current_exposure_usd is None
    # Hint not added when there's no exposure to anchor it to
    assert "Pull live roll quote from brokerage" not in d.reason
    # But DTE + expiry still populated
    assert d.dte == 100


def test_leaps_no_directive_outside_120_to_90_window():
    """200 DTE LEAPS produces no roll directive yet."""
    tr = _leaps_tranche(1, days_until_expiry=200, strike=100, cost_basis=5.0, quantity=10)
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull", sqn_20_value=0.5,
        stoch_k=50.0,
        close=110.0, ma_50=100.0, ma_200=95.0,
        leaps_tranches=[tr],
    )
    assert not any(d.action in ("roll_leaps", "hard_close_leaps") for d in exits)


# ── Exits ───────────────────────────────────────────────────────────────────


def test_exits_long_full_exit_on_bear_flip():
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bear",
        sqn_20_value=-1.0,
        stoch_k=50.0,
        close=100.0, ma_50=105.0, ma_200=110.0,
    )
    assert any(d.action == "full_exit" for d in exits)


def test_exits_long_trim_on_neutral_downgrade():
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="neutral",
        sqn_20_value=0.0,
        stoch_k=50.0,
        close=100.0, ma_50=99.0, ma_200=95.0,
    )
    actions = {d.action for d in exits}
    assert "trim_33" in actions
    assert "tighten_trail_20ma" in actions


def test_exits_long_50ma_break_trims_50():
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=0.5,
        stoch_k=40.0,
        close=98.0, ma_50=100.0, ma_200=95.0,
    )
    assert any(d.action == "trim_50" for d in exits)
    assert any(d.action == "set_hard_stop_200ma" for d in exits)


def test_exits_long_200ma_break_full_exit():
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=-1.0,
        stoch_k=20.0,
        close=80.0, ma_50=100.0, ma_200=85.0,
    )
    assert any(d.action == "full_exit" for d in exits)


def test_exits_leaps_roll_at_120_dte():
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=110)).isoformat()
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=1.0,
        stoch_k=50.0,
        close=100.0, ma_50=99.0, ma_200=95.0,
        leaps_expiries=[(1, expiry)],
    )
    assert any(d.action == "roll_leaps" for d in exits)


def test_exits_leaps_hard_close_below_90_dte():
    from datetime import date, timedelta
    expiry = (date.today() + timedelta(days=80)).isoformat()
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=1.0,
        stoch_k=50.0,
        close=100.0, ma_50=99.0, ma_200=95.0,
        leaps_expiries=[(2, expiry)],
    )
    assert any(d.action == "hard_close_leaps" for d in exits)


def test_exits_short_mirrors_long_ma_50_break():
    exits = evaluate_exits(
        direction="short",
        sqn_100_regime="bear",
        sqn_20_value=-0.5,
        stoch_k=50.0,
        close=102.0, ma_50=100.0, ma_200=105.0,
    )
    assert any(d.action == "trim_50" for d in exits)


def test_exits_default_to_hold_when_nothing_fires():
    exits = evaluate_exits(
        direction="long",
        sqn_100_regime="bull",
        sqn_20_value=1.0,
        stoch_k=50.0,  # not >80
        close=110.0, ma_50=100.0, ma_200=90.0,  # all healthy
    )
    assert exits == [d for d in exits if d.action == "hold"]
    assert exits[0].action == "hold"


# ── Store ───────────────────────────────────────────────────────────────────


def test_pyramid_store_save_load_roundtrip(tmp_path: Path):
    store = PyramidStore(base_dir=tmp_path)
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=4000)
    store.save(p)
    p2 = store.load(p.id)
    assert p2.id == p.id
    assert p2.ticker == "SPY"
    assert p2.total_allocation_usd == 4000


def test_pyramid_store_list_all_returns_all(tmp_path: Path):
    store = PyramidStore(base_dir=tmp_path)
    p1 = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=1000)
    p2 = Pyramid.create(ticker="QQQ", direction="long", total_allocation_usd=2000)
    store.save(p1)
    store.save(p2)
    found = store.list_all()
    assert {p.ticker for p in found} == {"SPY", "QQQ"}


def test_pyramid_store_list_active_excludes_closed(tmp_path: Path):
    store = PyramidStore(base_dir=tmp_path)
    p_active = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=1000)
    p_closed = Pyramid.create(ticker="QQQ", direction="long", total_allocation_usd=2000)
    p_closed.status = "completed"
    store.save(p_active)
    store.save(p_closed)
    active = store.list_active()
    assert {p.ticker for p in active} == {"SPY"}


def test_pyramid_store_load_missing_raises(tmp_path: Path):
    store = PyramidStore(base_dir=tmp_path)
    with pytest.raises(KeyError):
        store.load("does-not-exist")


def test_pyramid_store_skips_corrupt_files(tmp_path: Path):
    store = PyramidStore(base_dir=tmp_path)
    (tmp_path / "junk.json").write_text("not valid json {{{")
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=1000)
    store.save(p)
    found = store.list_all()
    assert len(found) == 1


def test_pyramid_store_writes_are_atomic(tmp_path: Path):
    """save() should leave no .tmp sibling — confirms write_json_atomic
    cleanup. Critical for the durability guarantee on a power-loss event."""
    store = PyramidStore(base_dir=tmp_path)
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=1000)
    store.save(p)
    store.save(p)  # rewrite same file
    json_files = list(tmp_path.glob("*.json"))
    tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
    assert len(json_files) == 1
    assert tmp_files == []


def test_pyramid_store_load_truncated_file_raises_keyerror(tmp_path: Path):
    """load() of a truncated file should raise KeyError (corruption), not
    JSONDecodeError or similar — keeps the API surface clean."""
    store = PyramidStore(base_dir=tmp_path)
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=1000)
    store.save(p)
    # Truncate the file mid-record
    p_path = tmp_path / f"{p.id}.json"
    p_path.write_text('{"id": "abc", "ticker": "SP')
    with pytest.raises(KeyError, match="corrupt"):
        store.load(p.id)


# ── Top-level evaluator (mocked data) ───────────────────────────────────────


def _trending_up_bars(periods: int = 250) -> pd.DataFrame:
    # Steady ~0.1%/day upward drift — produces full_bull stack and positive SQN
    closes = [100.0 * (1.001 ** i) for i in range(periods)]
    return _bars_from_closes(closes)


@patch("pyramid.evaluator.load_bars")
def test_evaluate_pyramid_planning_mode_runs_clean(mock_load):
    mock_load.return_value = _trending_up_bars()
    ev = evaluate_pyramid("SPY", "long", benchmark="SPY")
    assert ev.ticker == "SPY"
    assert ev.direction == "long"
    assert ev.gate is not None
    # T2 and T3 must be None in planning mode (no pyramid passed)
    assert ev.t2 is None
    assert ev.t3 is None
    assert ev.t1 is not None


@patch("pyramid.evaluator.load_bars")
def test_evaluate_pyramid_with_filled_t1_evaluates_t2(mock_load):
    mock_load.return_value = _trending_up_bars()
    p = Pyramid.create(ticker="SPY", direction="long", total_allocation_usd=5000)
    p.tranches[0].fill(vehicle="shares", cost_basis_per_unit=100, quantity=10)
    ev = evaluate_pyramid("SPY", "long", benchmark="SPY", pyramid=p)
    assert ev.t2 is not None
    assert ev.next_tranche == 2
