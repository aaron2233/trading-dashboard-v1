from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Verdict(str, Enum):
    KILL = "KILL"
    FLAG = "FLAG"
    PASS = "PASS"


# Aggregate verdict labels (per trade-devil/SKILL.md:53-57)
AGGREGATE_KILL = "KILL"
AGGREGATE_CONDITIONAL = "CONDITIONAL PROCEED"
AGGREGATE_PROCEED = "PROCEED"


@dataclass
class CategoryResult:
    category: str
    verdict: Verdict
    reason: str

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "verdict": self.verdict.value,
            "reason": self.reason,
        }
