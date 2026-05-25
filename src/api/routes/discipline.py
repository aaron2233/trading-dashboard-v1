"""Discipline scorecard routes — per-trade scoring, stats, weekly review."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.models import (
    DisciplineScoreOverridesRequest,
    DisciplineScoreResponse,
    DisciplineStatsResponse,
    LockdownRequest,
    WeeklyReviewResponse,
)
from discipline import (
    DisciplineStore,
    compute_discipline_stats,
    get_or_compute_weekly,
    is_legacy_position,
    score_trade,
)
from discipline.model import RuleResult


def _to_discipline_response(score) -> DisciplineScoreResponse:
    return DisciplineScoreResponse(**score.to_dict())


def make_discipline_router(store_factory) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/v1/discipline/score/{position_id}",
        response_model=DisciplineScoreResponse,
    )
    def get_discipline_score(position_id: str, score_legacy: bool = Query(False)):
        """Fetch (or compute) a discipline score for a closed position."""
        dstore = DisciplineStore()
        if dstore.has_score(position_id):
            return _to_discipline_response(dstore.load_score(position_id))

        pstore = store_factory()
        try:
            position = pstore.get(position_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        if position.status != "closed":
            raise HTTPException(
                status_code=409,
                detail=f"Position {position_id} is not closed (status={position.status})",
            )
        if is_legacy_position(position.closed_date) and not score_legacy:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Position closed before discipline-layer rollout "
                    f"({position.closed_date}); pass score_legacy=true to override"
                ),
            )

        score = score_trade(position)
        dstore.save_score(score)
        return _to_discipline_response(score)

    @router.post(
        "/api/v1/discipline/score/{position_id}",
        response_model=DisciplineScoreResponse,
    )
    def update_discipline_score(position_id: str, req: DisciplineScoreOverridesRequest):
        """Apply user attestations / rule overrides to a previously-scored trade."""
        dstore = DisciplineStore()
        if not dstore.has_score(position_id):
            # Auto-compute first; user is supplying overrides on top of base score
            pstore = store_factory()
            try:
                position = pstore.get(position_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            if position.status != "closed":
                raise HTTPException(status_code=409, detail="position not closed")
            if is_legacy_position(position.closed_date) and not req.score_legacy:
                raise HTTPException(status_code=409, detail="legacy position; pass score_legacy=true")
            score = score_trade(position)
        else:
            score = dstore.load_score(position_id)

        if req.notes is not None:
            score.notes = req.notes
        if req.profitable_violation_resolution is not None:
            score.profitable_violation_resolution = req.profitable_violation_resolution

        if req.rule_overrides:
            updated_rules: list[RuleResult] = []
            for r in score.rules:
                ov = req.rule_overrides.get(r.rule_id)
                if ov is not None:
                    updated_rules.append(RuleResult(
                        rule_id=ov.rule_id,
                        score=ov.score,
                        auto_evaluated=False,
                        note=ov.note,
                    ))
                else:
                    updated_rules.append(r)
            score.rules = updated_rules
            # Recompute aggregate metrics
            score.score_numerator = sum(1 for r in score.rules if r.score == "Y")
            n_count = sum(1 for r in score.rules if r.score == "N")
            score.score_denominator = score.score_numerator + n_count
            pnl = score.pnl_usd or 0.0
            score.profitable_violation = (n_count > 0) and (pnl > 0)

        dstore.save_score(score)
        return _to_discipline_response(score)

    @router.get(
        "/api/v1/discipline/scores",
        response_model=list[DisciplineScoreResponse],
    )
    def list_discipline_scores(limit: int = Query(20, ge=1, le=200)):
        dstore = DisciplineStore()
        scores = dstore.list_scores()
        # Sort by closed_at descending (most recent first)
        scores.sort(key=lambda s: s.closed_at or "", reverse=True)
        return [_to_discipline_response(s) for s in scores[:limit]]

    @router.get(
        "/api/v1/discipline/stats",
        response_model=DisciplineStatsResponse,
    )
    def discipline_stats(range_: str = Query("all", alias="range",
                                              pattern="^(week|month|all)$")):
        from datetime import date, timedelta
        dstore = DisciplineStore()
        scores = list(dstore.iter_scores())
        if range_ == "week":
            cutoff = date.today() - timedelta(days=7)
            scores = [s for s in scores if s.closed_at and s.closed_at[:10] >= cutoff.isoformat()]
        elif range_ == "month":
            cutoff = date.today() - timedelta(days=30)
            scores = [s for s in scores if s.closed_at and s.closed_at[:10] >= cutoff.isoformat()]
        stats = compute_discipline_stats(scores, label=range_)
        return DisciplineStatsResponse(**stats.to_dict())

    @router.get(
        "/api/v1/discipline/weekly-review",
        response_model=WeeklyReviewResponse,
    )
    def weekly_review_endpoint(
        week_of: str | None = Query(None, description="Date inside target week, YYYY-MM-DD"),
        recompute: bool = Query(False),
    ):
        from datetime import datetime
        week_date = None
        if week_of:
            try:
                week_date = datetime.strptime(week_of, "%Y-%m-%d").date()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        review = get_or_compute_weekly(week_date, force_recompute=recompute)
        return WeeklyReviewResponse(**review.to_dict())

    @router.post(
        "/api/v1/discipline/weekly-review/{week_start}/lockdown",
        response_model=WeeklyReviewResponse,
    )
    def update_lockdown(week_start: str, req: LockdownRequest):
        dstore = DisciplineStore()
        try:
            review = dstore.update_lockdown(week_start, req.behavior)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return WeeklyReviewResponse(**review.to_dict())

    return router
