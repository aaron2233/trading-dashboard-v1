"""R1/R2/R3 rule checkers.

Each `check_*` returns a `RuleCheckResult` with `.compliant` + a `.violation`
that explains the failure (None when compliant). The API surfaces these on
the recovery status endpoint and as warnings on POST /api/v1/positions.

Per dashboard design (api/app.py:1043), violations DO NOT block trade
entry — they're surfaced loudly so the user sees them at journal time
and they feed into the discipline scorecard retrospectively.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


R1_LOTTO_CAP_USD: float = 150.0
R1_MAIN_CAP_USD: float = 300.0
R2_MAX_DAILY_ENTRIES: int = 2
R3_STOP_FRACTION: float = 0.40  # premium stop at 40% of entry = -60% premium loss


@dataclass
class RuleViolation:
    rule: str            # "R1" / "R2" / "R3"
    severity: str        # "warn" (recorded but not blocking)
    message: str
    details: dict


@dataclass
class RuleCheckResult:
    compliant: bool
    violation: RuleViolation | None = None

    def to_dict(self) -> dict:
        return {
            "compliant": self.compliant,
            "violation": (
                {
                    "rule": self.violation.rule,
                    "severity": self.violation.severity,
                    "message": self.violation.message,
                    "details": dict(self.violation.details),
                }
                if self.violation
                else None
            ),
        }


# ─── R1 — dollar size cap ────────────────────────────────────────────────


def check_r1_dollar_cap(account: str, max_loss_usd: float) -> RuleCheckResult:
    """R1: max_loss_usd ≤ $150 (lotto) or $300 (main). The cap is on RISK
    (premium paid for long options = max loss), not notional."""
    cap = R1_LOTTO_CAP_USD if account == "lotto" else R1_MAIN_CAP_USD
    if max_loss_usd <= cap + 0.005:  # tolerate sub-cent rounding
        return RuleCheckResult(compliant=True)
    return RuleCheckResult(
        compliant=False,
        violation=RuleViolation(
            rule="R1",
            severity="warn",
            message=(
                f"R1 violation — {account} position max-loss "
                f"${max_loss_usd:.2f} exceeds ${cap:.0f} cap by "
                f"${max_loss_usd - cap:.2f}"
            ),
            details={
                "account": account,
                "max_loss_usd": round(max_loss_usd, 2),
                "cap_usd": cap,
                "excess_usd": round(max_loss_usd - cap, 2),
            },
        ),
    )


# ─── R2 — daily entry count ──────────────────────────────────────────────


def _entries_today(positions: list, now: datetime | None = None) -> int:
    """Count today's NEW entries across all accounts. Date is UTC since
    positions store ISO timestamps in UTC."""
    today = (now or datetime.now(timezone.utc)).date()
    count = 0
    for p in positions:
        entry_date = getattr(p, "entry_date", None)
        if not entry_date:
            continue
        try:
            dt = datetime.fromisoformat(entry_date)
            if dt.date() == today:
                count += 1
        except (ValueError, TypeError):
            continue
    return count


def check_r2_daily_entries(positions: list, now: datetime | None = None) -> RuleCheckResult:
    """R2: max 2 new entries per day across all accounts. Call BEFORE
    appending a new position — returns compliant=False if today's count
    is already at or above 2."""
    count = _entries_today(positions, now=now)
    if count < R2_MAX_DAILY_ENTRIES:
        return RuleCheckResult(compliant=True)
    return RuleCheckResult(
        compliant=False,
        violation=RuleViolation(
            rule="R2",
            severity="warn",
            message=(
                f"R2 violation — {count} entries already opened today; "
                f"max is {R2_MAX_DAILY_ENTRIES}. Adding another would be the "
                f"{count + 1}th."
            ),
            details={
                "entries_today": count,
                "max_per_day": R2_MAX_DAILY_ENTRIES,
            },
        ),
    )


# ─── R3 — standing −60% stop set at order placement ──────────────────────


def check_r3_standing_stop(
    premium_paid_per_contract: float | None,
    premium_stop: float | None,
) -> RuleCheckResult:
    """R3: premium_stop must be set at order placement and represent a
    −60% premium loss (stop at 40% of entry premium).

    For shares positions (premium_paid_per_contract=None), this rule does
    not apply — we return compliant=True.
    """
    if premium_paid_per_contract is None or premium_paid_per_contract <= 0:
        return RuleCheckResult(compliant=True)
    if premium_stop is None or premium_stop <= 0:
        return RuleCheckResult(
            compliant=False,
            violation=RuleViolation(
                rule="R3",
                severity="warn",
                message=(
                    "R3 violation — premium_stop not set at order placement. "
                    f"For entry ${premium_paid_per_contract:.2f}, place a "
                    f"standing stop at ${premium_paid_per_contract * R3_STOP_FRACTION:.2f} "
                    f"(−60% premium loss)."
                ),
                details={
                    "premium_paid": round(premium_paid_per_contract, 2),
                    "expected_stop": round(premium_paid_per_contract * R3_STOP_FRACTION, 2),
                    "actual_stop": premium_stop,
                },
            ),
        )
    expected = premium_paid_per_contract * R3_STOP_FRACTION
    # Allow tolerance — broker stops can't always land at exact decimals
    tol = max(0.05, premium_paid_per_contract * 0.05)
    if premium_stop > expected + tol:
        # Stop is TOO LOOSE (will cut later than −60%)
        return RuleCheckResult(
            compliant=False,
            violation=RuleViolation(
                rule="R3",
                severity="warn",
                message=(
                    f"R3 violation — premium_stop ${premium_stop:.2f} is "
                    f"looser than the −60% cut ($ {expected:.2f}). Tighten "
                    f"to ${expected:.2f} to enforce the standing cut rule."
                ),
                details={
                    "premium_paid": round(premium_paid_per_contract, 2),
                    "expected_stop": round(expected, 2),
                    "actual_stop": round(premium_stop, 2),
                    "looseness_usd": round(premium_stop - expected, 2),
                },
            ),
        )
    return RuleCheckResult(compliant=True)
