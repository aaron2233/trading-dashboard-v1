"""Focus / Sunday-scan routes — current scan, history, per-date detail + outcome."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.models import (
    FocusOutcomeResponse,
    FocusRecentSummaryResponse,
    FocusSetup,
    FocusTopSetupSummary,
    MatchedPositionResponse,
    ScanResult,
    SundayScanResponse,
    SundayScanSummaryResponse,
)
from api.routes._helpers import scan_to_response
from focus import (
    build_outcome,
    list_recent_sunday_scans,
    load_sunday_scan,
    persist_sunday_scan,
    run_sunday_scan,
    summarize_recent_outcomes,
)
from scan import scan_ticker


def make_focus_router(store_factory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/focus/sunday-scan", response_model=SundayScanResponse)
    def focus_sunday_scan(
        persist: bool = Query(True, description="Write scan to ~/.trading-dashboard/sunday_scans/"),
    ):
        result = run_sunday_scan(
            scan_fn=lambda t, **kw: scan_ticker(t, **kw),
        )
        if persist:
            try:
                persist_sunday_scan(result)
            except OSError as exc:
                # Disk is full / permission denied / etc — log and return the
                # scan anyway. The user still gets the read; they just don't
                # get a saved snapshot.
                import sys as _sys
                print(f"⚠ Failed to persist Sunday scan: {exc}", file=_sys.stderr)
        return SundayScanResponse(
            scan_time_utc=result.scan_time_utc,
            spy=scan_to_response(result.spy) if result.spy else None,
            qqq=scan_to_response(result.qqq) if result.qqq else None,
            gld=scan_to_response(result.gld) if result.gld else None,
            setups=[FocusSetup(**s.to_dict()) for s in result.setups],
            recommendation=result.recommendation,
            headline=result.headline,
            errors=result.errors,
        )

    @router.get(
        "/api/v1/focus/sunday-scan/recent",
        response_model=list[SundayScanSummaryResponse],
    )
    def focus_recent_scans(
        limit: int = Query(10, ge=1, le=50),
    ):
        summaries = list_recent_sunday_scans(limit=limit)
        return [
            SundayScanSummaryResponse(
                date=s.date,
                scan_time_utc=s.scan_time_utc,
                recommendation=s.recommendation,  # type: ignore[arg-type]
                headline=s.headline,
                top_setup=FocusTopSetupSummary(**s.top_setup) if s.top_setup else None,
            )
            for s in summaries
        ]

    @router.get(
        "/api/v1/focus/summary",
        response_model=FocusRecentSummaryResponse,
    )
    def focus_summary(
        weeks: int = Query(4, ge=1, le=52),
    ):
        store = store_factory()
        summary = summarize_recent_outcomes(
            weeks=weeks, positions=store.list_all(),
        )
        return FocusRecentSummaryResponse(**summary.to_dict())

    @router.get(
        "/api/v1/focus/sunday-scan/{date}/outcome",
        response_model=FocusOutcomeResponse,
    )
    def focus_outcome(date: str):
        try:
            payload = load_sunday_scan(date)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read scan for {date}: {exc}",
            )
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"No saved scan for {date}",
            )
        store = store_factory()
        outcome = build_outcome(date, payload, store.list_all())
        top = outcome.top_setup
        return FocusOutcomeResponse(
            scan_date=outcome.scan_date,
            recommendation=outcome.recommendation,  # type: ignore[arg-type]
            top_setup=FocusTopSetupSummary(
                asset=top["asset"],
                direction=top["direction"],
                score=top["score"],
                status=top["status"],
            ) if top else None,
            window_days=outcome.window_days,
            followed=outcome.followed,
            matched=[MatchedPositionResponse(**m.to_dict())
                     for m in outcome.matched],
            realized_pnl_usd=outcome.realized_pnl_usd,
            open_count=outcome.open_count,
            closed_count=outcome.closed_count,
            aggregate_status=outcome.aggregate_status,  # type: ignore[arg-type]
        )

    @router.get(
        "/api/v1/focus/sunday-scan/{date}",
        response_model=SundayScanResponse,
    )
    def focus_sunday_scan_by_date(date: str):
        try:
            payload = load_sunday_scan(date)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read scan for {date}: {exc}",
            )
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"No saved scan for {date}",
            )

        def _scan_or_none(row: Any) -> ScanResult | None:
            return scan_to_response(row) if row else None

        return SundayScanResponse(
            scan_time_utc=payload.get("scan_time_utc", ""),
            spy=_scan_or_none(payload.get("spy")),
            qqq=_scan_or_none(payload.get("qqq")),
            gld=_scan_or_none(payload.get("gld")),
            setups=[FocusSetup(**s) for s in payload.get("setups", [])],
            recommendation=payload.get("recommendation", "cash"),
            headline=payload.get("headline", ""),
            errors=payload.get("errors", {}),
        )

    return router
