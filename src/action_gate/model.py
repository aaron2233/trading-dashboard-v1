"""Shared dataclasses for action gate verdicts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# State precedence (worst-to-best for sorting candidate cards):
#   disqualified > stale > chase_zone > setup_forming > enter_now
# UI displays ENTER NOW first, then FORMING, then SKIPs.
ActionState = Literal[
    "enter_now",
    "setup_forming",
    "chase_zone",
    "stale",
    "disqualified",
]


_STATE_SORT_ORDER: dict[str, int] = {
    "enter_now": 0,
    "setup_forming": 1,
    "chase_zone": 2,
    "stale": 3,
    "disqualified": 4,
}


def state_sort_key(state: ActionState) -> int:
    """Lower number = surface higher in the candidate list."""
    return _STATE_SORT_ORDER.get(state, 99)


@dataclass
class ActionVerdict:
    """The action call for a single candidate at a single moment.

    `headline` is the one-liner the panel banner displays.
    `suggested_entry_price` is populated only for `enter_now` states.
    `blockers` populates for `disqualified` (hard skip reasons).
    `advance_conditions` populates for `setup_forming` (what needs to
        happen for the verdict to advance to enter_now).
    `rule_citations` is an audit trail of which orchestrator rules
        drove the verdict — kept so the user can sanity-check the call.
    """

    state: ActionState
    direction: Literal["long", "short", "none"]
    skill: str                          # "lotto-options" / "weekly-trend-trader"
    headline: str
    suggested_entry_price: float | None = None
    blockers: list[str] = field(default_factory=list)
    advance_conditions: list[str] = field(default_factory=list)
    rule_citations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── Constants used by classifiers ────────────────────────────────────────────


# Stack states that disqualify any directional entry. Per orchestrator
# anti-patterns: "Never trade tangled/chop MAs — no trend = no trade."
CHOP_STACKS = frozenset({"chop", "compression"})

BULL_STACKS = frozenset({"full_bull", "bull_developing"})
BEAR_STACKS = frozenset({"full_bear", "bear_developing"})


# Diagnostic substrings that flip a verdict to STALE — move is exhausted.
STALE_DIAG_TOKENS = ("weakening", "exhausted", "capitul")


# Diagnostic substrings that flip a verdict to CHASE_ZONE — premium is
# being chased; orchestrator rule 13 forbids entry.
CHASE_DIAG_TOKENS = ("chase",)


def stack_supports(stack: str | None, direction: str) -> bool:
    """True if the MA stack state is aligned with the requested direction.

    `chop` and `compression` always return False — chop is never aligned
    with anything per the anti-pattern rule.
    """
    if not stack:
        return False
    if direction == "long":
        return stack in BULL_STACKS
    if direction == "short":
        return stack in BEAR_STACKS
    return False


def stack_opposes(stack: str | None, direction: str) -> bool:
    """True if the MA stack actively opposes the requested direction
    (long candidate but stack is bear, or vice versa)."""
    if not stack:
        return False
    if direction == "long":
        return stack in BEAR_STACKS
    if direction == "short":
        return stack in BULL_STACKS
    return False
