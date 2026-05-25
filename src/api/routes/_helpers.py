"""Shared response converters used by multiple route modules."""
from __future__ import annotations

from typing import Any

from api.models import (
    DevilCategoryResult,
    DevilReportResponse,
    JournalStatsResponse,
    PositionResponse,
    ScanResult,
)
from positions.model import Position


def scan_to_response(row: dict[str, Any]) -> ScanResult:
    return ScanResult(
        ticker=row["ticker"],
        timeframe=row.get("timeframe", "1d"),
        bar_date=row.get("bar_date"),
        close=row.get("close"),
        ma_ribbon=row.get("ma_ribbon", {}) or {},
        stochastic=row.get("stochastic", {}) or {},
        sqn=row.get("sqn", {}) or {},
    )


def devil_to_response(report) -> DevilReportResponse:
    return DevilReportResponse(
        aggregate=report.aggregate,
        kills=report.kills,
        flags=report.flags,
        passes=report.passes,
        triggered_by_risk_threshold=report.triggered_by_risk_threshold,
        results=[
            DevilCategoryResult(
                category=r.category, verdict=r.verdict.value, reason=r.reason,
            )
            for r in report.results
        ],
    )


def position_to_response(p: Position) -> PositionResponse:
    return PositionResponse(**p.to_dict())


def stats_to_response(stats) -> JournalStatsResponse:
    return JournalStatsResponse(**stats.to_dict())
