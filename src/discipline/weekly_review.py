"""Weekly review aggregator.

Pulls scored trades for a given Sun→Sat week and computes the WeeklyReview
metrics (trades, avg score, adherence, violations, profitable violations,
most-violated rule, drift trend, P&L). Drift trend compares current week's
avg score against the prior 4-week moving average.

Per ~/.claude/skills/user/discipline/SKILL.md weekly review workflow + the
scorecard template's weekly section.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from discipline.model import DisciplineScore, WeeklyReview
from discipline.stats import compute_discipline_stats
from discipline.store import DisciplineStore


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None


def week_bounds(reference: date | None = None) -> tuple[date, date]:
    """Return (Sunday, Saturday) for the week containing `reference` (default today).

    Sunday = start of week per the scorecard template's weekly review section.
    """
    today = reference or date.today()
    # Python: Monday=0..Sunday=6. We want Sunday as week start.
    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday)
    saturday = sunday + timedelta(days=6)
    return sunday, saturday


def _scores_in_window(
    scores: Iterable[DisciplineScore], start: date, end: date,
) -> list[DisciplineScore]:
    out: list[DisciplineScore] = []
    for s in scores:
        closed_d = _parse_date(s.closed_at)
        if closed_d is None:
            continue
        if start <= closed_d <= end:
            out.append(s)
    return out


def compute_weekly_review(
    week_of: date | None = None,
    *,
    store: DisciplineStore | None = None,
) -> WeeklyReview:
    """Compute (do not persist) the weekly review for the week containing `week_of`."""
    store = store or DisciplineStore()
    sunday, saturday = week_bounds(week_of)

    # Portfolio sleeve runs a MONTHLY scorecard cadence, not the weekly
    # options-book cadence (~/CLAUDE.md), and is excluded from the weekly
    # unreviewed-nag (dashboard.find_unreviewed_weeks) — exclude it here too so
    # the weekly review the nag links to doesn't fold portfolio closures into
    # the options-book stats. (Legacy scores predating the account_key field
    # carry '' and are unaffected.)
    all_scores = [s for s in store.iter_scores() if s.account_key != "portfolio"]
    week_scores = _scores_in_window(all_scores, sunday, saturday)

    # Drift trend: compare against prior 4-week moving avg
    prior_start = sunday - timedelta(days=28)
    prior_end = sunday - timedelta(days=1)
    prior_scores = _scores_in_window(all_scores, prior_start, prior_end)
    prior_avg = (
        sum(s.score for s in prior_scores) / len(prior_scores)
        if prior_scores else None
    )

    stats = compute_discipline_stats(
        week_scores, label="week", prior_avg_for_drift=prior_avg,
    )
    pnl_total = sum((s.pnl_usd or 0.0) for s in week_scores)

    return WeeklyReview(
        week_start=sunday.isoformat(),
        week_end=saturday.isoformat(),
        trades_scored=stats.trades_scored,
        avg_discipline_score=stats.avg_discipline_score,
        full_adherence_count=stats.full_adherence_count,
        any_violation_count=stats.any_violation_count,
        profitable_violation_count=stats.profitable_violation_count,
        most_violated_rule=stats.most_violated_rule,
        drift_trend=stats.drift_trend,
        pnl_usd=pnl_total,
    )


def get_or_compute_weekly(
    week_of: date | None = None,
    *,
    store: DisciplineStore | None = None,
    force_recompute: bool = False,
) -> WeeklyReview:
    """Load a saved review for the week, or compute a fresh one if absent."""
    store = store or DisciplineStore()
    sunday, _ = week_bounds(week_of)
    if not force_recompute:
        existing = store.load_weekly(sunday.isoformat())
        if existing is not None:
            return existing
    review = compute_weekly_review(week_of, store=store)
    store.save_weekly(review)
    return review
