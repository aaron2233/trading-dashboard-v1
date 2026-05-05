"""Discipline scorecard data model.

Per DISCIPLINE-LAYER-ADDITION.md and the 15-rule scorecard template at
~/.claude/skills/user/discipline/references/scorecard-template.md.

A `DisciplineScore` records:
- 15 rule results (Y / N / N/A) with auto/manual provenance
- Numerator (Y count) and denominator (non-N/A count) for the discipline score
- P&L (separate axis — never blended with discipline score per stage-1 rule)
- Profitable-violation flag (score < 1.0 AND P&L > 0) — highest-risk pattern
- Counterfactual loss at the -60/-70% cut for the profitable-violation flag

Scoring rule IDs are stable string keys so the model survives rule re-ordering.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


# Stable rule identifiers. Order matches scorecard-template.md.
RULE_IDS: list[str] = [
    "kill_sheet_complete",
    "sqn100_authorized",
    "sqn20_respected",
    "size_within_tier",
    "trigger_dte_match",
    "iv_rank_under_70",
    "dte_min_7",
    "trade_devil_passed",
    "no_spreads_margin",
    "daily_not_chop",
    "weekly_not_opposing",
    "cut_at_60_70",
    "exit_within_dte_band",
    "no_average_down",
]

# Human-readable rule text (mirrors scorecard-template.md).
RULE_TEXT: dict[str, str] = {
    "kill_sheet_complete":  "Pre-trade kill sheet completed in full BEFORE entry",
    "sqn100_authorized":    "SQN(100) authorized the direction at entry",
    "sqn20_respected":      "SQN(20) tactical state respected (no chasing >+2.5, no chasing puts <-1.9 + ATH)",
    "size_within_tier":     "Position size within stated tier (0.5-1% / 1-2% / 2-3%)",
    "trigger_dte_match":    "Trigger TF → DTE match per orchestrator rule 6",
    "iv_rank_under_70":     "IV Rank ≤ 70% at entry (or explicit post-earnings crush thesis documented)",
    "dte_min_7":            "DTE ≥ 7 at entry (or explicit 0DTE framing)",
    "trade_devil_passed":   "Trade-devil run; verdict was PROCEED or CONDITIONAL with conditions met",
    "no_spreads_margin":    "No spreads / margin / strangles / condors",
    "daily_not_chop":       "Daily MA stack not chop at entry",
    "weekly_not_opposing":  "Weekly not opposing (or counter-Weekly downsized + thesis documented)",
    "cut_at_60_70":         "Cut at -60% to -70% if invalidation hit (or exit at target)",
    "exit_within_dte_band": "Exited before 50% DTE (apex/lotto) or held >60 DTE (weekly-trend-trader)",
    "no_average_down":      "Did not average down without a new signal",
}


RuleVerdict = Literal["Y", "N", "N/A"]


@dataclass
class RuleResult:
    rule_id: str
    score: RuleVerdict
    auto_evaluated: bool
    note: str | None = None


@dataclass
class DisciplineScore:
    position_id: str
    kill_sheet_id: str | None  # None for legacy positions or shadow trades
    closed_at: str             # ISO timestamp
    rules: list[RuleResult] = field(default_factory=list)
    pnl_usd: float | None = None

    # Optional metadata
    ticker: str = ""
    direction: str = ""
    instrument: str = ""
    entry_at: str | None = None

    # Computed at scoring time and persisted; recomputed on round-trip is
    # identical so persistence stays sourceful.
    score_numerator: int = 0    # Y count
    score_denominator: int = 0  # non-N/A count
    profitable_violation: bool = False
    counterfactual_loss_usd: float | None = None

    notes: str = ""
    profitable_violation_resolution: str | None = None

    # Stamp
    scored_at: str = ""

    # ── Computed properties ─────────────────────────────────────────────────

    @property
    def score(self) -> float:
        """Discipline score: Y count / non-N/A count. 0.0 if no rules evaluated."""
        if self.score_denominator == 0:
            return 0.0
        return self.score_numerator / self.score_denominator

    @property
    def violated_rule_ids(self) -> list[str]:
        return [r.rule_id for r in self.rules if r.score == "N"]

    @property
    def full_adherence(self) -> bool:
        return self.score_denominator > 0 and self.score_numerator == self.score_denominator

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = self.score
        d["violated_rule_ids"] = self.violated_rule_ids
        d["full_adherence"] = self.full_adherence
        return d

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DisciplineScore":
        # Computed fields are derivable; strip before reconstructing.
        data = {
            k: v for k, v in payload.items()
            if k not in ("score", "violated_rule_ids", "full_adherence")
        }
        rules_raw = data.pop("rules", [])
        rules = [RuleResult(**r) for r in rules_raw]
        return cls(rules=rules, **data)

    @classmethod
    def stamp(cls, **kwargs) -> "DisciplineScore":
        kwargs.setdefault("scored_at", datetime.now(timezone.utc).isoformat())
        return cls(**kwargs)


@dataclass
class WeeklyReview:
    """Aggregate for one Sunday-to-Saturday window of scored trades."""

    week_start: str   # ISO date (Sunday)
    week_end: str     # ISO date (Saturday)
    trades_scored: int
    avg_discipline_score: float
    full_adherence_count: int
    any_violation_count: int
    profitable_violation_count: int  # the headline metric
    most_violated_rule: str | None
    drift_trend: Literal["improving", "flat", "drifting"]
    pnl_usd: float
    lockdown_behavior: str | None = None  # user-supplied

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WeeklyReview":
        return cls(**payload)
