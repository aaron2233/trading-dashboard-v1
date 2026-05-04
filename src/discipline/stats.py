"""Discipline statistics — aggregate metrics across scored trades.

Parallel to `journal/stats.py` but on a separate axis (do NOT blend with P&L
stats per stage-1 rule that discipline > P&L).
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from typing import Iterable, Literal

from discipline.model import RULE_TEXT, DisciplineScore


DriftTrend = Literal["improving", "flat", "drifting"]


@dataclass
class DisciplineStats:
    label: str
    trades_scored: int
    avg_discipline_score: float       # 0.0 - 1.0
    full_adherence_count: int
    any_violation_count: int
    profitable_violation_count: int   # the headline metric
    most_violated_rule: str | None
    most_violated_rule_text: str | None
    drift_trend: DriftTrend

    def to_dict(self) -> dict:
        return asdict(self)


def _drift_trend(current_avg: float, prior_avg: float | None) -> DriftTrend:
    if prior_avg is None:
        return "flat"
    delta = current_avg - prior_avg
    if delta > 0.05:
        return "improving"
    if delta < -0.05:
        return "drifting"
    return "flat"


def compute_discipline_stats(
    scores: Iterable[DisciplineScore],
    *,
    label: str = "all",
    prior_avg_for_drift: float | None = None,
) -> DisciplineStats:
    """Compute aggregate discipline metrics from an iterable of scored trades.

    `prior_avg_for_drift` optionally supplies the average score from a prior
    period (e.g. previous 4 weeks) to compute drift_trend.
    """
    score_list = list(scores)
    n = len(score_list)
    if n == 0:
        return DisciplineStats(
            label=label,
            trades_scored=0,
            avg_discipline_score=0.0,
            full_adherence_count=0,
            any_violation_count=0,
            profitable_violation_count=0,
            most_violated_rule=None,
            most_violated_rule_text=None,
            drift_trend="flat",
        )

    avg_score = sum(s.score for s in score_list) / n
    full_adherence = sum(1 for s in score_list if s.full_adherence)
    any_violation = sum(1 for s in score_list if s.score_denominator > 0 and not s.full_adherence)
    profitable_violation = sum(1 for s in score_list if s.profitable_violation)

    # Most-violated rule
    violation_counter: Counter[str] = Counter()
    for s in score_list:
        for rid in s.violated_rule_ids:
            violation_counter[rid] += 1
    most_violated_id: str | None = None
    most_violated_text: str | None = None
    if violation_counter:
        most_violated_id, _ = violation_counter.most_common(1)[0]
        most_violated_text = RULE_TEXT.get(most_violated_id)

    return DisciplineStats(
        label=label,
        trades_scored=n,
        avg_discipline_score=avg_score,
        full_adherence_count=full_adherence,
        any_violation_count=any_violation,
        profitable_violation_count=profitable_violation,
        most_violated_rule=most_violated_id,
        most_violated_rule_text=most_violated_text,
        drift_trend=_drift_trend(avg_score, prior_avg_for_drift),
    )
