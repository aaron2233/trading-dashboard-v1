"""Hard-gate rules check for proposed trades.

Sources:
  - max_open_positions, max_premium_at_risk_pct, cash_floor_usd: AccountConfig
    defaults from ~/CLAUDE.md
  - Per PRD FR33-FR35: position count limits, premium-at-risk caps, cash floor
    enforced as hard gates that block new trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from config import AccountConfig
from positions.model import Position


@dataclass
class RuleViolation:
    rule: str
    severity: str        # "block" | "warn"
    message: str
    current_value: float
    limit: float

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "message": self.message,
            "current_value": self.current_value,
            "limit": self.limit,
        }


def check_proposed_trade(
    proposed_max_loss_usd: float,
    account: AccountConfig,
    account_key: str,
    open_positions: Iterable[Position],
) -> list[RuleViolation]:
    """Validate that opening a new trade with the given max loss won't violate
    any of the account's hard gates. Returns list of violations (empty = clean).
    """
    open_in_account = [
        p for p in open_positions
        if p.account_key == account_key and p.status == "open"
    ]
    violations: list[RuleViolation] = []

    # ─ Max open positions ─
    max_pos = account.raw.get("max_open_positions")
    if max_pos is not None:
        proposed_count = len(open_in_account) + 1
        if proposed_count > max_pos:
            violations.append(RuleViolation(
                rule="max_open_positions",
                severity="block",
                message=(
                    f"Opening this trade would be {proposed_count} positions in "
                    f"{account.name}; max is {max_pos}."
                ),
                current_value=float(proposed_count),
                limit=float(max_pos),
            ))

    # ─ Max premium-at-risk percentage ─
    current_at_risk = sum(p.max_loss_usd for p in open_in_account)
    proposed_at_risk = current_at_risk + proposed_max_loss_usd

    max_pct = account.raw.get("max_premium_at_risk_pct")
    if max_pct is not None and account.balance_usd > 0:
        proposed_pct = proposed_at_risk / account.balance_usd
        if proposed_pct > max_pct:
            violations.append(RuleViolation(
                rule="max_premium_at_risk_pct",
                severity="block",
                message=(
                    f"Opening this trade would put {proposed_pct:.1%} of account "
                    f"at risk (${proposed_at_risk:,.0f} of ${account.balance_usd:,.0f}); "
                    f"max is {max_pct:.1%}."
                ),
                current_value=proposed_pct,
                limit=max_pct,
            ))

    # ─ Cash floor (lotto) ─
    cash_floor = account.raw.get("cash_floor_usd")
    if cash_floor is not None and account.balance_usd > 0:
        cash_after = account.balance_usd - proposed_at_risk
        if cash_after < cash_floor:
            violations.append(RuleViolation(
                rule="cash_floor",
                severity="block",
                message=(
                    f"Opening this trade would leave ${cash_after:,.2f} cash; "
                    f"floor is ${cash_floor:,.2f}."
                ),
                current_value=cash_after,
                limit=float(cash_floor),
            ))

    return violations
