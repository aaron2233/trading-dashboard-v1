"""Hard-gate rules specific to qqq-gld-focus mode.

Layered on top of `check_proposed_trade`. Only invoked when the caller passes
--focus. Implements the asset-restricted playbook from
~/.claude/skills/user/qqq-gld-focus/SKILL.md:

  - Two assets only: QQQ and GLD.
  - Max one open position per asset (so max two concurrent across both).
  - No same-direction pair: if QQQ has an open long, a GLD long is blocked
    (and vice versa). The skill treats Fed-pivot-style correlated longs as
    correlation 1.0, not diversification.
  - 3-trading-day cool-off after a stop on the same asset. "Stop" is
    proxied by a closed position with pnl_usd < 0; we don't track exit
    reason explicitly. Counts weekdays only — does not account for market
    holidays.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable

from positions.model import Position
from positions.rules import RuleViolation


FOCUS_TICKERS: frozenset[str] = frozenset({"QQQ", "GLD"})
COOLOFF_TRADING_DAYS: int = 3

# Per-trade dollar risk cap from the focus skill SKILL.md
# (skill says "$200 risk max" for QQQ/GLD calls, "$150-200" for puts;
#  enforce the higher cap so the gate is hit only on truly oversized trades).
FOCUS_MAX_RISK_USD: float = 200.0

# (low_dte, high_dte) inclusive bands per asset/direction.
# Source: ~/.claude/skills/user/qqq-gld-focus/SKILL.md
#   QQQ Long Setup → DTE 30-45
#   QQQ Short Setup → DTE 21-30
#   GLD Long Setup → DTE 45-60
#   GLD Short Setup → DTE 30-45
FOCUS_DTE_BANDS: dict[tuple[str, str], tuple[int, int]] = {
    ("QQQ", "long"):  (30, 45),
    ("QQQ", "short"): (21, 30),
    ("GLD", "long"):  (45, 60),
    ("GLD", "short"): (30, 45),
}


def _weekdays_elapsed(since: datetime, now: datetime) -> int:
    """Count weekdays (Mon-Fri) from the day AFTER `since` through `now`.

    Same calendar day returns 0. Stopped Monday + asked Tuesday → 1.
    Stopped Monday + asked Friday → 4. Does not account for market holidays.
    """
    if now <= since:
        return 0
    days = 0
    cursor = since.date() + timedelta(days=1)
    end_date = now.date()
    while cursor <= end_date:
        if cursor.weekday() < 5:
            days += 1
        cursor = cursor + timedelta(days=1)
    return days


def check_focus_trade(
    ticker: str,
    direction: str,
    open_positions: Iterable[Position],
    closed_positions: Iterable[Position],
    now: datetime | None = None,
) -> list[RuleViolation]:
    """Validate that opening a trade in focus mode respects the focus skill rules.

    Returns list of violations (empty = clean). All violations are 'block'
    severity — focus mode is opt-in, so any violation means the user explicitly
    asked for focus discipline and is breaking it.
    """
    ticker_u = ticker.upper()
    direction_l = direction.lower()
    if now is None:
        now = datetime.now(timezone.utc)

    violations: list[RuleViolation] = []

    # ─ Ticker restriction ─
    if ticker_u not in FOCUS_TICKERS:
        violations.append(RuleViolation(
            rule="focus_ticker",
            severity="block",
            message=(
                f"Focus mode trades QQQ and GLD only; got {ticker_u}. "
                "Drop --focus to trade other tickers."
            ),
            current_value=0.0,
            limit=0.0,
        ))
        # No point checking the rest if the ticker itself is out of bounds.
        return violations

    open_focus = [
        p for p in open_positions
        if p.ticker.upper() in FOCUS_TICKERS and p.status == "open"
    ]

    # ─ One position per asset (so max 2 across the pair) ─
    if any(p.ticker.upper() == ticker_u for p in open_focus):
        violations.append(RuleViolation(
            rule="focus_one_per_asset",
            severity="block",
            message=(
                f"Focus mode allows one open position per asset; "
                f"{ticker_u} already has an open position."
            ),
            current_value=1.0,
            limit=1.0,
        ))

    # ─ No same-direction pair across QQQ + GLD ─
    # Compare by THESIS, not stored contract direction: every long option stores
    # direction="long" (a long put is bearish), so a raw direction compare both
    # missed correlated bearish pairs and wrongly blocked opposite-thesis hedges.
    # The proposed `direction` is the thesis ("long"=bullish, "short"=bearish).
    proposed_thesis = "bullish" if direction_l == "long" else "bearish"
    other = [p for p in open_focus if p.ticker.upper() != ticker_u]
    same_dir_other = [p for p in other if p.thesis_direction == proposed_thesis]
    if same_dir_other:
        existing = same_dir_other[0]
        violations.append(RuleViolation(
            rule="focus_no_same_direction_pair",
            severity="block",
            message=(
                f"Focus mode rejects same-direction QQQ+GLD pairs: "
                f"{existing.ticker} is already {existing.thesis_direction}. "
                "Pick the stronger setup; correlation is 1.0, not diversification."
            ),
            current_value=1.0,
            limit=0.0,
        ))

    # ─ 3-trading-day cool-off after a stop on the same asset ─
    for p in closed_positions:
        if p.ticker.upper() != ticker_u:
            continue
        if p.status != "closed":
            continue
        if p.pnl_usd is None or p.pnl_usd >= 0:
            continue
        if not p.closed_date:
            continue
        try:
            closed_dt = datetime.fromisoformat(p.closed_date)
        except ValueError:
            continue
        if closed_dt.tzinfo is None:
            closed_dt = closed_dt.replace(tzinfo=timezone.utc)
        elapsed = _weekdays_elapsed(closed_dt, now)
        if elapsed < COOLOFF_TRADING_DAYS:
            violations.append(RuleViolation(
                rule="focus_cooloff",
                severity="block",
                message=(
                    f"Focus mode requires {COOLOFF_TRADING_DAYS} trading days "
                    f"after a stop on {ticker_u}; "
                    f"last stop closed {closed_dt.date().isoformat()} "
                    f"({elapsed} weekday(s) ago)."
                ),
                current_value=float(elapsed),
                limit=float(COOLOFF_TRADING_DAYS),
            ))
            break  # one violation is enough; don't list every prior loss

    return violations


def check_focus_options_structure(
    ticker: str,
    direction: str,
    max_loss_usd: float,
    dte: int | None = None,
) -> list[RuleViolation]:
    """Focus skill's per-trade options gates: $200 risk cap + DTE band.

    DTE check only runs when `dte` is provided (i.e. the user passed a real
    contract on the kill sheet). Risk cap always runs because every kill
    sheet computes a max_loss_usd from account.risk_pct × balance.
    """
    ticker_u = ticker.upper()
    direction_l = direction.lower()
    violations: list[RuleViolation] = []

    if max_loss_usd > FOCUS_MAX_RISK_USD:
        violations.append(RuleViolation(
            rule="focus_max_risk",
            severity="block",
            message=(
                f"Focus mode caps per-trade risk at ${FOCUS_MAX_RISK_USD:.0f}; "
                f"this trade budgets ${max_loss_usd:,.2f}. "
                "Lower conviction tier or reduce contract size."
            ),
            current_value=float(max_loss_usd),
            limit=float(FOCUS_MAX_RISK_USD),
        ))

    if dte is not None and ticker_u in FOCUS_TICKERS:
        band = FOCUS_DTE_BANDS.get((ticker_u, direction_l))
        if band is not None:
            low, high = band
            if dte < low or dte > high:
                violations.append(RuleViolation(
                    rule="focus_dte_band",
                    severity="block",
                    message=(
                        f"Focus mode requires {ticker_u} {direction_l} contracts "
                        f"in the {low}-{high} DTE band; got {dte} DTE."
                    ),
                    current_value=float(dte),
                    limit=float(low if dte < low else high),
                ))

    return violations
