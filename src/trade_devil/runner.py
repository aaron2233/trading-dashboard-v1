"""Trade devil orchestrator: runs all 8 categories and aggregates verdicts."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from kill_sheet.model import KillSheet
from trade_devil.categories import (
    ALL_CHECKS,
    check_correlation_trap,
)
from trade_devil.verdict import (
    AGGREGATE_CONDITIONAL,
    AGGREGATE_KILL,
    AGGREGATE_PROCEED,
    CategoryResult,
    Verdict,
)


AUTO_TRIGGER_USD = 150.0  # per Bob's sprint plan + PRD FR27


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DevilReport:
    ticker: str
    direction: str
    results: list[CategoryResult]
    aggregate: str  # KILL / CONDITIONAL PROCEED / PROCEED
    kills: int
    flags: int
    passes: int
    triggered_by_risk_threshold: bool
    generated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "direction": self.direction,
            "results": [r.to_dict() for r in self.results],
            "aggregate": self.aggregate,
            "kills": self.kills,
            "flags": self.flags,
            "passes": self.passes,
            "triggered_by_risk_threshold": self.triggered_by_risk_threshold,
            "generated_at": self.generated_at,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_text(self) -> str:
        bar = "═" * 51
        lines = [
            bar,
            f"🔥 TRADE DEVIL: {self.ticker.upper()} ({self.direction.upper()})",
            bar,
        ]
        for r in self.results:
            lines.append("")
            lines.append(f"## {r.category}")
            lines.append(f"**{r.verdict.value}.** {r.reason}")
        lines.append("")

        if self.aggregate == AGGREGATE_KILL:
            mark = "❌"
        elif self.aggregate == AGGREGATE_CONDITIONAL:
            mark = "⚠️"
        else:
            mark = "✅"
        lines.append(f"### VERDICT: {self.aggregate} {mark}")
        lines.append(f"**Kills: {self.kills} | Flags: {self.flags} | Passes: {self.passes}**")

        if self.aggregate == AGGREGATE_KILL:
            kill_reasons = [
                f"  - [{r.category}] {r.reason}"
                for r in self.results
                if r.verdict is Verdict.KILL
            ]
            if kill_reasons:
                lines.append("")
                lines.append("Fatal flaw(s):")
                lines.extend(kill_reasons)
            elif self.flags >= 3:
                lines.append("")
                lines.append("Death by cuts: 3+ flags accumulated.")
        elif self.aggregate == AGGREGATE_CONDITIONAL:
            flag_reasons = [
                f"  - [{r.category}] {r.reason}"
                for r in self.results
                if r.verdict is Verdict.FLAG
            ]
            if flag_reasons:
                lines.append("")
                lines.append("Conditions to address before entry:")
                lines.extend(flag_reasons)
        lines.append(bar)
        return "\n".join(lines)


def _aggregate(results: list[CategoryResult]) -> tuple[str, int, int, int]:
    kills = sum(1 for r in results if r.verdict is Verdict.KILL)
    flags = sum(1 for r in results if r.verdict is Verdict.FLAG)
    passes = sum(1 for r in results if r.verdict is Verdict.PASS)
    if kills > 0:
        return AGGREGATE_KILL, kills, flags, passes
    if flags >= 3:
        return AGGREGATE_KILL, kills, flags, passes
    if flags >= 1:
        return AGGREGATE_CONDITIONAL, kills, flags, passes
    return AGGREGATE_PROCEED, kills, flags, passes


def run_devil(sheet: KillSheet, force: bool = False,
              open_positions: list | None = None) -> DevilReport | None:
    """Run all 8 kill categories on a KillSheet.

    Orchestrator rule 5: in **stage 1** (account < $100K) the devil is mandatory
    for EVERY actionable trade; the $150 ``AUTO_TRIGGER_USD`` threshold is the
    **stage 2** (account >= $100K) rule. We detect the stage from the sheet's
    own ``account_balance_usd`` and always run in stage 1 — otherwise lotto
    trades (capped at exactly $150, and `>` is strict) and any sub-$150 main
    trade would silently skip the mandatory gate. (Fixed 2026-06; previously the
    stage-2 threshold was applied universally with no stage awareness.)

    For lower-risk sheets you can still force a run with force=True. Pass
    open_positions (list of Position) to enable the Correlation Trap category
    to check against real open trades.
    """
    from discipline.stage import current_stage

    balance = getattr(sheet, "account_balance_usd", None)
    stage_1 = balance is not None and current_stage(balance) == "stage_1"
    risk_threshold_hit = sheet.max_risk_usd > AUTO_TRIGGER_USD
    triggered = stage_1 or risk_threshold_hit
    if not triggered and not force:
        return None

    # All checks except Correlation Trap take only the sheet; correlation needs
    # the position list. Run them with the right signature.
    results: list[CategoryResult] = []
    for check in ALL_CHECKS:
        if check is check_correlation_trap:
            results.append(check(sheet, open_positions=open_positions))
        else:
            results.append(check(sheet))

    aggregate, kills, flags, passes = _aggregate(results)

    return DevilReport(
        ticker=sheet.ticker,
        direction=sheet.direction,
        results=results,
        aggregate=aggregate,
        kills=kills,
        flags=flags,
        passes=passes,
        triggered_by_risk_threshold=risk_threshold_hit,
    )
