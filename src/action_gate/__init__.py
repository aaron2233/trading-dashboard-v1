"""Action gate — turns multi-TF indicator reads into a 5-state action
verdict (enter_now / setup_forming / chase_zone / stale / disqualified)
with per-skill rules.

Why this exists: scanners surface "candidate has confluence" but don't
tell the user *whether to enter today*. The gate layer applies the
orchestrator's tier-specific rules (~/CLAUDE.md) to translate raw
indicator state into a buy/wait/skip call.

One classifier per skill; shared ActionVerdict output type. Skill rules
diverge enough (lotto's 0-14 DTE chase guard vs weekly trend's
multi-month tolerance) that a single config-parameterized function
would obscure the differences.
"""
from action_gate.classifiers import (
    classify_focus_action,
    classify_lotto_action,
    classify_weekly_trend_action,
)
from action_gate.model import (
    ActionState,
    ActionVerdict,
)

__all__ = [
    "ActionState",
    "ActionVerdict",
    "classify_focus_action",
    "classify_lotto_action",
    "classify_weekly_trend_action",
]
