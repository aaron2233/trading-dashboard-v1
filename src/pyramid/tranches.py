"""Per-tranche trigger evaluation for the pyramid module.

Each tranche has its own trigger conditions on top of the gate (which must
already pass).

T1 — Initial Entry:
  long:  Stoch %K in 20-50 AND %K crossing %D upward; price within ~3% of 20MA
  short: Stoch %K in 50-80 AND %K crossing %D downward; price within ~3% of 20MA

T2 — Retest Confirmation:
  Requires T1 filled. Pullback/rally retest of 20MA held; Stoch reset and turn;
  SQN(100) still in regime; SQN(20) coming off neutral or capitulation.

T3 — Continuation Breakout:
  Requires T1 and T2 filled. New swing high (long) / low (short); MA Ribbon
  expanding; SQN(20) confirming; Stoch in continuation zone (not exhausted).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pyramid.model import Pyramid
from pyramid.structure import StructureRead


# Distance-from-20MA tolerance for T1 entry (as a fraction of price).
T1_PRICE_NEAR_20MA_TOLERANCE = 0.03  # 3%


@dataclass
class TrancheTriggerResult:
    tranche_id: int
    should_fire: bool
    blockers: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


def _stoch_crossed_up_in_range(
    k: float | None, d: float | None, k_min: float, k_max: float
) -> bool:
    """%K is in [k_min, k_max] AND %K is above %D (proxy for 'crossed up')."""
    if k is None or d is None:
        return False
    return (k_min <= k <= k_max) and (k > d)


def _stoch_crossed_down_in_range(
    k: float | None, d: float | None, k_min: float, k_max: float
) -> bool:
    if k is None or d is None:
        return False
    return (k_min <= k <= k_max) and (k < d)


def _price_near_ma(price: float | None, ma: float | None, tolerance: float) -> bool:
    if price is None or ma is None:
        return False
    if ma == 0:
        return False
    return abs(price - ma) / ma <= tolerance


def evaluate_t1(
    direction: str,
    *,
    stoch_k: float | None,
    stoch_d: float | None,
    close: float | None,
    ma_20: float | None,
) -> TrancheTriggerResult:
    blockers: list[str] = []
    reasons: dict[str, str] = {}

    if direction == "long":
        stoch_ok = _stoch_crossed_up_in_range(stoch_k, stoch_d, 20.0, 50.0)
        if not stoch_ok:
            blockers.append(
                f"Stoch %K={stoch_k}/%D={stoch_d} fails T1 (need %K in 20-50 AND %K>%D)"
            )
    else:
        stoch_ok = _stoch_crossed_down_in_range(stoch_k, stoch_d, 50.0, 80.0)
        if not stoch_ok:
            blockers.append(
                f"Stoch %K={stoch_k}/%D={stoch_d} fails T1 (need %K in 50-80 AND %K<%D)"
            )
    reasons["stoch"] = f"k={stoch_k}, d={stoch_d}"

    near_20ma = _price_near_ma(close, ma_20, T1_PRICE_NEAR_20MA_TOLERANCE)
    if not near_20ma:
        blockers.append(
            f"Close {close} not within {T1_PRICE_NEAR_20MA_TOLERANCE * 100:.0f}% of 20MA {ma_20}"
        )
    reasons["price_vs_20ma"] = f"close={close}, ma_20={ma_20}"

    return TrancheTriggerResult(
        tranche_id=1,
        should_fire=stoch_ok and near_20ma,
        blockers=blockers,
        reasons=reasons,
    )


def evaluate_t2(
    direction: str,
    pyramid: Pyramid,
    *,
    stoch_k: float | None,
    stoch_d: float | None,
    sqn_100_regime: str | None,
    sqn_20_regime: str | None,
    structure: StructureRead,
) -> TrancheTriggerResult:
    blockers: list[str] = []
    reasons: dict[str, str] = {}

    t1 = pyramid.get_tranche(1)
    if t1.status != "filled":
        blockers.append("T2 requires T1 filled")
        return TrancheTriggerResult(tranche_id=2, should_fire=False, blockers=blockers, reasons=reasons)

    # Retest of 20MA held (long) / rejected (short)
    if direction == "long":
        retest_ok = structure.pullback_held_20ma
        if not retest_ok:
            blockers.append("20MA retest did not hold (T2 requires pullback hold)")
        reasons["retest"] = f"pullback_held_20ma={structure.pullback_held_20ma}"
    else:
        retest_ok = structure.rally_rejected_at_20ma
        if not retest_ok:
            blockers.append("20MA rally retest not rejected (T2 requires rally rejection)")
        reasons["retest"] = f"rally_rejected_20ma={structure.rally_rejected_at_20ma}"

    # Stoch reset and turn
    if direction == "long":
        stoch_reset_ok = _stoch_crossed_up_in_range(stoch_k, stoch_d, 20.0, 40.0)
        if not stoch_reset_ok:
            blockers.append("Stoch did not reset to 20-40 and turn up for T2")
    else:
        stoch_reset_ok = _stoch_crossed_down_in_range(stoch_k, stoch_d, 60.0, 80.0)
        if not stoch_reset_ok:
            blockers.append("Stoch did not reset to 60-80 and turn down for T2")
    reasons["stoch_reset"] = f"k={stoch_k}, d={stoch_d}"

    # SQN(100) still in regime
    if direction == "long":
        sqn100_ok = sqn_100_regime in ("bull", "strong_bull")
    else:
        sqn100_ok = sqn_100_regime in ("bear", "strong_bear")
    if not sqn100_ok:
        blockers.append(f"SQN(100) regime {sqn_100_regime} no longer supports {direction}")
    reasons["sqn_100"] = f"regime={sqn_100_regime}"

    # Higher-low (long) / lower-high (short) at the retest
    if direction == "long":
        struct_ok = structure.higher_low_confirmed
        if not struct_ok:
            blockers.append("Higher-low not confirmed at retest")
    else:
        struct_ok = structure.lower_high_confirmed
        if not struct_ok:
            blockers.append("Lower-high not confirmed at retest")
    reasons["structure"] = (
        f"higher_low={structure.higher_low_confirmed}, "
        f"lower_high={structure.lower_high_confirmed}"
    )

    return TrancheTriggerResult(
        tranche_id=2,
        should_fire=retest_ok and stoch_reset_ok and sqn100_ok and struct_ok,
        blockers=blockers,
        reasons=reasons,
    )


def evaluate_t3(
    direction: str,
    pyramid: Pyramid,
    *,
    stoch_k: float | None,
    stoch_d: float | None,
    sqn_20_value: float | None,
    sqn_100_regime: str | None,
    structure: StructureRead,
    ma_10: float | None,
    ma_20: float | None,
) -> TrancheTriggerResult:
    from pyramid.gate import (
        SQN_20_LONG_CHASE_THRESHOLD,
        SQN_20_SHORT_CAPITULATION_THRESHOLD,
    )

    blockers: list[str] = []
    reasons: dict[str, str] = {}

    t1 = pyramid.get_tranche(1)
    t2 = pyramid.get_tranche(2)
    if t1.status != "filled" or t2.status != "filled":
        blockers.append("T3 requires both T1 and T2 filled")
        return TrancheTriggerResult(tranche_id=3, should_fire=False, blockers=blockers, reasons=reasons)

    # New swing extreme above T1's stored reference. Per skill spec the T3
    # gate is "new swing high above the breakout that triggered T1" — this is
    # an exact reference that gets captured at T1 fill time on the Tranche
    # (swing_high_at_fill / swing_low_at_fill). When the reference is absent
    # (legacy fills, or caller didn't pass structure on fill), we fall back to
    # the looser recent vs. prior swing comparison.
    t1_ref_high = t1.swing_high_at_fill
    t1_ref_low = t1.swing_low_at_fill
    if direction == "long":
        if t1_ref_high is not None and structure.recent_swing_high is not None:
            new_extreme_ok = structure.recent_swing_high > t1_ref_high
            reasons["new_extreme"] = (
                f"recent_high={structure.recent_swing_high}, T1_swing_high={t1_ref_high} "
                f"(exact T1 reference)"
            )
            if not new_extreme_ok:
                blockers.append(
                    f"Recent swing high {structure.recent_swing_high} not above "
                    f"T1's swing high reference {t1_ref_high} (continuation not confirmed)"
                )
        else:
            new_extreme_ok = (
                structure.recent_swing_high is not None
                and structure.prior_swing_high is not None
                and structure.recent_swing_high > structure.prior_swing_high
            )
            reasons["new_extreme"] = (
                f"recent_high={structure.recent_swing_high}, prior_high={structure.prior_swing_high} "
                f"(fallback — T1 reference not captured)"
            )
            if not new_extreme_ok:
                blockers.append(
                    "No new swing high above prior swing high (continuation not confirmed)"
                )
    else:
        if t1_ref_low is not None and structure.recent_swing_low is not None:
            new_extreme_ok = structure.recent_swing_low < t1_ref_low
            reasons["new_extreme"] = (
                f"recent_low={structure.recent_swing_low}, T1_swing_low={t1_ref_low} "
                f"(exact T1 reference)"
            )
            if not new_extreme_ok:
                blockers.append(
                    f"Recent swing low {structure.recent_swing_low} not below "
                    f"T1's swing low reference {t1_ref_low} (breakdown continuation not confirmed)"
                )
        else:
            new_extreme_ok = (
                structure.recent_swing_low is not None
                and structure.prior_swing_low is not None
                and structure.recent_swing_low < structure.prior_swing_low
            )
            reasons["new_extreme"] = (
                f"recent_low={structure.recent_swing_low}, prior_low={structure.prior_swing_low} "
                f"(fallback — T1 reference not captured)"
            )
            if not new_extreme_ok:
                blockers.append(
                    "No new swing low below prior swing low (breakdown continuation not confirmed)"
                )

    # MA Ribbon expanding: 10 vs 20 distance growing — proxy by 10/20 spread > X% of price
    # Direction-aware: long wants 10 > 20 with widening gap; short wants opposite.
    if ma_10 is not None and ma_20 is not None and ma_10 != 0:
        spread_pct = (ma_10 - ma_20) / ma_20
        if direction == "long":
            ribbon_expanding = spread_pct > 0.005  # 10MA at least 0.5% above 20MA
        else:
            ribbon_expanding = spread_pct < -0.005
        if not ribbon_expanding:
            blockers.append(
                f"MA Ribbon not expanding ({spread_pct*100:.2f}% 10/20 spread)"
            )
    else:
        ribbon_expanding = False
        blockers.append("MA Ribbon expansion check unavailable (MA values missing)")
    reasons["ribbon_expansion"] = f"ma_10={ma_10}, ma_20={ma_20}"

    # SQN(20) confirming, not extreme
    if direction == "long":
        if sqn_20_value is None:
            sqn20_ok = False
            blockers.append("SQN(20) missing")
        elif sqn_20_value > SQN_20_LONG_CHASE_THRESHOLD:
            sqn20_ok = False
            blockers.append(f"SQN(20) {sqn_20_value:.2f} in chase zone — do not add T3")
        else:
            sqn20_ok = True
    else:
        if sqn_20_value is None:
            sqn20_ok = False
            blockers.append("SQN(20) missing")
        elif sqn_20_value < SQN_20_SHORT_CAPITULATION_THRESHOLD:
            sqn20_ok = False
            blockers.append(f"SQN(20) {sqn_20_value:.2f} in capitulation extreme")
        else:
            sqn20_ok = True
    reasons["sqn_20"] = f"value={sqn_20_value}"

    # Stoch in continuation zone (not exhausted)
    if direction == "long":
        stoch_ok = stoch_k is not None and 50.0 <= stoch_k <= 70.0 and (stoch_d is None or stoch_k > stoch_d)
        if not stoch_ok:
            blockers.append(
                f"Stoch %K={stoch_k} not in continuation zone 50-70 (or %K<%D)"
            )
    else:
        stoch_ok = stoch_k is not None and 30.0 <= stoch_k <= 50.0 and (stoch_d is None or stoch_k < stoch_d)
        if not stoch_ok:
            blockers.append(
                f"Stoch %K={stoch_k} not in short-continuation zone 30-50 (or %K>%D)"
            )
    reasons["stoch"] = f"k={stoch_k}, d={stoch_d}"

    # SQN(100) still in regime
    if direction == "long":
        sqn100_ok = sqn_100_regime in ("bull", "strong_bull")
    else:
        sqn100_ok = sqn_100_regime in ("bear", "strong_bear")
    if not sqn100_ok:
        blockers.append(f"SQN(100) regime {sqn_100_regime} no longer supports {direction}")
    reasons["sqn_100"] = f"regime={sqn_100_regime}"

    return TrancheTriggerResult(
        tranche_id=3,
        should_fire=new_extreme_ok and ribbon_expanding and sqn20_ok and stoch_ok and sqn100_ok,
        blockers=blockers,
        reasons=reasons,
    )
