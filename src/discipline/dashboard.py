"""Dashboard-level state for the discipline UX layer.

Composes:
- current_stage based on live account balance
- account_balance from config baseline + realized P&L on closed positions
- list of unreviewed weeks (closed trades present, no saved WeeklyReview)

Used by the new GET /api/v1/dashboard/state endpoint to drive the dynamic
stage banner and the HomeView "Run weekly review" CTA.

Honest balance accounting per anti-fabrication rule:
- Account base balances summed ONCE per pool (pool_member_of dedupes)
- Realized P&L only — open positions are at-risk capital, not realized loss
- No mark-to-market guess on open options
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

from config.loader import Config
from discipline.stage import STAGE_1_THRESHOLD_USD, Stage, current_stage, stage_reminder
from discipline.store import DisciplineStore, is_legacy_position
from discipline.weekly_review import week_bounds
from positions.model import Position


@dataclass
class UnreviewedWeek:
    week_start: str          # ISO date (Sunday)
    week_end: str            # ISO date (Saturday)
    closed_trade_count: int  # how many closed positions fell in this week


@dataclass
class DashboardState:
    stage: Stage
    stage_reminder: str
    account_balance_usd: float
    threshold_usd: int
    progress_to_threshold: float    # 0.0–1.0; >1.0 once stage 2 reached
    realized_pnl_usd: float
    base_balance_usd: float         # sum of distinct-pool balances from config
    unreviewed_weeks: list[UnreviewedWeek] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["unreviewed_weeks"] = [asdict(w) for w in self.unreviewed_weeks]
        return d


def compute_account_balance(
    config: Config,
    closed_positions: Iterable[Position],
) -> tuple[float, float, float]:
    """Return (base_balance, realized_pnl, total).

    `base_balance` sums config account balances, deduplicating by pool
    (accounts with pool_member_of set don't contribute their own balance).
    `realized_pnl` is the sum of pnl_usd across non-legacy closed positions.
    """
    base = 0.0
    for acct in config.accounts.values():
        if acct.pool_member_of is not None:
            continue
        base += acct.balance_usd

    realized = 0.0
    for p in closed_positions:
        if p.status != "closed":
            continue
        if p.pnl_usd is None:
            continue
        # Skip legacy positions for stage accounting consistency with the
        # discipline scoring rules.
        if is_legacy_position(p.closed_date):
            continue
        realized += float(p.pnl_usd)

    return base, realized, base + realized


def find_unreviewed_weeks(
    closed_positions: Iterable[Position],
    store: DisciplineStore,
    *,
    today: date | None = None,
) -> list[UnreviewedWeek]:
    """Return unreviewed weeks (excluding the current/in-progress week).

    A week is "unreviewed" when it contains at least one closed non-legacy
    position AND no saved WeeklyReview file exists for its Sunday start. The
    current week (containing `today`) is always excluded — we don't nag for a
    review until the week ends.
    """
    today = today or date.today()
    current_sunday, _ = week_bounds(today)

    # Bucket closed positions by week_start
    week_buckets: dict[str, dict[str, object]] = {}
    for p in closed_positions:
        if p.status != "closed" or not p.closed_date:
            continue
        if is_legacy_position(p.closed_date):
            continue
        try:
            closed_d = datetime.fromisoformat(
                p.closed_date.replace("Z", "+00:00")
            ).date()
        except ValueError:
            try:
                closed_d = datetime.strptime(p.closed_date, "%Y-%m-%d").date()
            except ValueError:
                continue
        sunday, saturday = week_bounds(closed_d)
        # Skip the current week — review only after the week closes
        if sunday >= current_sunday:
            continue
        bucket = week_buckets.setdefault(
            sunday.isoformat(),
            {"week_start": sunday.isoformat(), "week_end": saturday.isoformat(), "count": 0},
        )
        bucket["count"] = int(bucket["count"]) + 1  # type: ignore[operator]

    unreviewed: list[UnreviewedWeek] = []
    for week_start, info in week_buckets.items():
        if store.load_weekly(week_start) is not None:
            continue
        unreviewed.append(UnreviewedWeek(
            week_start=week_start,
            week_end=str(info["week_end"]),
            closed_trade_count=int(info["count"]),  # type: ignore[arg-type]
        ))

    # Newest first — most actionable
    unreviewed.sort(key=lambda w: w.week_start, reverse=True)
    return unreviewed


def compute_dashboard_state(
    config: Config,
    closed_positions: Iterable[Position],
    *,
    discipline_store: DisciplineStore | None = None,
    today: date | None = None,
) -> DashboardState:
    """Top-level state for the dynamic stage banner + HomeView CTA."""
    closed_list = list(closed_positions)
    base, realized, total = compute_account_balance(config, closed_list)
    stage = current_stage(total)
    progress = total / STAGE_1_THRESHOLD_USD if STAGE_1_THRESHOLD_USD > 0 else 0.0

    store = discipline_store or DisciplineStore()
    unreviewed = find_unreviewed_weeks(closed_list, store, today=today)

    return DashboardState(
        stage=stage,
        stage_reminder=stage_reminder(stage),
        account_balance_usd=total,
        threshold_usd=STAGE_1_THRESHOLD_USD,
        progress_to_threshold=progress,
        realized_pnl_usd=realized,
        base_balance_usd=base,
        unreviewed_weeks=unreviewed,
    )
