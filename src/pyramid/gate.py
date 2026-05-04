"""Pyramid gate evaluation — the 5 pre-conditions that must hold before T1.

Per ~/.claude/skills/user/trend-pyramid/SKILL.md:

LONG gate (all 5 must pass):
  1. SQN(100) on benchmark = Bull (>+0.7) or Strong Bull (>+1.5)
  2. SQN(20) is not in chase zone (<= +2.5)
  3. MA Ribbon: Full Bull stack (10>20>50>200, all rising, price above)
  4. Most recent pullback held the 20MA or 50MA
  5. Higher-low confirmed on Daily price structure

SHORT gate (all 5 must pass — mirrored):
  1. SQN(100) = Bear (-1.5..-0.7) or Strong Bear (<-1.5)
  2. SQN(20) is not in extreme capitulation zone (>= -2.5)
  3. MA Ribbon: Full Bear stack (10<20<50<200, all falling, price below)
  4. Most recent rally rejected at the 20MA or 50MA
  5. Lower-high confirmed on Daily price structure

The gate is a binary go/no-go — primary skill rule. Failed gates produce
specific blocker strings the UI/CLI can surface.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pyramid.structure import StructureRead


# SQN thresholds beyond which fresh tranches must not fire even if regime allows.
SQN_20_LONG_CHASE_THRESHOLD = 2.5     # > +2.5 → no fresh longs
SQN_20_SHORT_CAPITULATION_THRESHOLD = -2.5  # < -2.5 → no fresh shorts


@dataclass
class GateResult:
    permitted: bool                   # all 5 pass
    direction: str
    sqn_100_pass: bool
    sqn_20_pass: bool
    ma_stack_pass: bool
    pullback_pass: bool
    structure_pass: bool
    blockers: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)


# Allowed MA stack states per direction
LONG_OK_STACK = {"full_bull"}
LONG_DEGRADED_STACK = {"bull_developing"}  # may permit T1 with caveat — surfaced as warning
SHORT_OK_STACK = {"full_bear"}
SHORT_DEGRADED_STACK = {"bear_developing"}


def evaluate_gate(
    direction: str,
    sqn_100_regime: str | None,
    sqn_20_regime: str | None,
    sqn_20_value: float | None,
    ma_stack_state: str | None,
    structure: StructureRead,
) -> GateResult:
    if direction not in ("long", "short"):
        raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")

    blockers: list[str] = []
    reasons: dict[str, str] = {}

    # ── 1. SQN(100) regime gate ──────────────────────────────────────────────
    if direction == "long":
        sqn100_ok = sqn_100_regime in ("bull", "strong_bull")
    else:
        sqn100_ok = sqn_100_regime in ("bear", "strong_bear")
    if not sqn100_ok:
        required = "Bull/Strong Bull" if direction == "long" else "Bear/Strong Bear"
        blockers.append(
            f"SQN(100) regime '{sqn_100_regime}' fails — required {required} for {direction}"
        )
    reasons["sqn_100"] = f"regime={sqn_100_regime}"

    # ── 2. SQN(20) tactical gate ─────────────────────────────────────────────
    if direction == "long":
        if sqn_20_value is None:
            sqn20_ok = False
            blockers.append("SQN(20) value missing (warmup or insufficient bars)")
        elif sqn_20_value > SQN_20_LONG_CHASE_THRESHOLD:
            sqn20_ok = False
            blockers.append(
                f"SQN(20) at {sqn_20_value:.2f} > +{SQN_20_LONG_CHASE_THRESHOLD} (chase zone)"
            )
        else:
            sqn20_ok = True
    else:
        if sqn_20_value is None:
            sqn20_ok = False
            blockers.append("SQN(20) value missing (warmup or insufficient bars)")
        elif sqn_20_value < SQN_20_SHORT_CAPITULATION_THRESHOLD:
            sqn20_ok = False
            blockers.append(
                f"SQN(20) at {sqn_20_value:.2f} < {SQN_20_SHORT_CAPITULATION_THRESHOLD} "
                f"(capitulation extreme)"
            )
        else:
            sqn20_ok = True
    reasons["sqn_20"] = (
        f"value={sqn_20_value if sqn_20_value is not None else 'n/a'}, "
        f"regime={sqn_20_regime}"
    )

    # ── 3. MA Ribbon stack gate ──────────────────────────────────────────────
    if direction == "long":
        ma_stack_ok = ma_stack_state in LONG_OK_STACK
        if not ma_stack_ok:
            blockers.append(
                f"MA stack '{ma_stack_state}' fails — required full_bull for long pyramid"
            )
    else:
        ma_stack_ok = ma_stack_state in SHORT_OK_STACK
        if not ma_stack_ok:
            blockers.append(
                f"MA stack '{ma_stack_state}' fails — required full_bear for short pyramid"
            )
    reasons["ma_stack"] = f"state={ma_stack_state}"

    # ── 4. Pullback / rally hold gate ────────────────────────────────────────
    if direction == "long":
        pullback_ok = structure.pullback_held_20ma or structure.pullback_held_50ma
        if not pullback_ok:
            blockers.append(
                "Most recent pullback did not hold 20MA or 50MA (structure broken)"
            )
        reasons["pullback"] = (
            f"held_20ma={structure.pullback_held_20ma}, "
            f"held_50ma={structure.pullback_held_50ma}"
        )
    else:
        pullback_ok = structure.rally_rejected_at_20ma or structure.rally_rejected_at_50ma
        if not pullback_ok:
            blockers.append(
                "Most recent rally was not rejected at 20MA or 50MA (downtrend not confirmed)"
            )
        reasons["pullback"] = (
            f"rally_rejected_20ma={structure.rally_rejected_at_20ma}, "
            f"rally_rejected_50ma={structure.rally_rejected_at_50ma}"
        )

    # ── 5. Structure (HH/HL or LH/LL) gate ───────────────────────────────────
    if direction == "long":
        structure_ok = structure.higher_low_confirmed
        if not structure_ok:
            blockers.append("Higher-low not confirmed on Daily structure")
        reasons["structure"] = (
            f"higher_low={structure.higher_low_confirmed}, "
            f"recent_swing_low={structure.recent_swing_low}, "
            f"prior_swing_low={structure.prior_swing_low}"
        )
    else:
        structure_ok = structure.lower_high_confirmed
        if not structure_ok:
            blockers.append("Lower-high not confirmed on Daily structure")
        reasons["structure"] = (
            f"lower_high={structure.lower_high_confirmed}, "
            f"recent_swing_high={structure.recent_swing_high}, "
            f"prior_swing_high={structure.prior_swing_high}"
        )

    permitted = all([sqn100_ok, sqn20_ok, ma_stack_ok, pullback_ok, structure_ok])

    return GateResult(
        permitted=permitted,
        direction=direction,
        sqn_100_pass=sqn100_ok,
        sqn_20_pass=sqn20_ok,
        ma_stack_pass=ma_stack_ok,
        pullback_pass=pullback_ok,
        structure_pass=structure_ok,
        blockers=blockers,
        reasons=reasons,
    )
