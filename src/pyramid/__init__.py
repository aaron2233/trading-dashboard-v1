"""Pyramid module — multi-tranche scaled entry on confirmed Daily-TF trends.

Implements the trend-pyramid skill. See:
- ~/.claude/skills/user/trend-pyramid/SKILL.md (source of truth)
- src/pyramid/model.py     — Pyramid + Tranche dataclasses
- src/pyramid/structure.py — price-structure analysis (HH/HL, pullback hold)
- src/pyramid/gate.py      — 5-condition pre-entry gate
- src/pyramid/tranches.py  — T1/T2/T3 trigger evaluation
- src/pyramid/exits.py     — exit cascade
- src/pyramid/evaluator.py — top-level orchestrator
- src/pyramid/store.py     — JSON persistence
- src/pyramid/cli.py       — `python -m pyramid` CLI
"""
from pyramid.evaluator import PyramidEvaluation, evaluate_pyramid
from pyramid.exits import ExitDirective, evaluate_exits
from pyramid.gate import GateResult, evaluate_gate
from pyramid.model import Pyramid, Tranche
from pyramid.store import DEFAULT_PYRAMIDS_DIR, PyramidStore
from pyramid.structure import StructureRead, analyze_structure
from pyramid.tranches import (
    TrancheTriggerResult,
    evaluate_t1,
    evaluate_t2,
    evaluate_t3,
)


__all__ = [
    "DEFAULT_PYRAMIDS_DIR",
    "ExitDirective",
    "GateResult",
    "Pyramid",
    "PyramidEvaluation",
    "PyramidStore",
    "StructureRead",
    "Tranche",
    "TrancheTriggerResult",
    "analyze_structure",
    "evaluate_exits",
    "evaluate_gate",
    "evaluate_pyramid",
    "evaluate_t1",
    "evaluate_t2",
    "evaluate_t3",
]
