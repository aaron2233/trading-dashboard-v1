"""Journal routes — stats, breakdown, recent positions, per-exit events."""
from __future__ import annotations

from fastapi import APIRouter, Query

from api.models import (
    JournalExitResponse,
    JournalReportResponse,
    JournalStatsResponse,
    PositionResponse,
)
from api.routes._helpers import position_to_response, stats_to_response
from journal import (
    by_account as journal_by_account,
    by_direction as journal_by_direction,
    by_instrument as journal_by_instrument,
    compute_stats,
)


def make_journal_router(store_factory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/journal/stats", response_model=JournalStatsResponse)
    def journal_stats(account: str | None = None):
        store = store_factory()
        positions = store.list_all()
        if account:
            positions = [p for p in positions if p.account_key == account]
        return stats_to_response(compute_stats(positions, label=account or "all"))

    @router.get("/api/v1/journal/breakdown", response_model=JournalReportResponse)
    def journal_breakdown():
        store = store_factory()
        positions = store.list_all()
        return JournalReportResponse(
            overall=stats_to_response(compute_stats(positions, label="all")),
            by_account={k: stats_to_response(s) for k, s in journal_by_account(positions).items()},
            by_instrument={k: stats_to_response(s) for k, s in journal_by_instrument(positions).items()},
            by_direction={k: stats_to_response(s) for k, s in journal_by_direction(positions).items()},
        )

    @router.get("/api/v1/journal/recent", response_model=list[PositionResponse])
    def journal_recent(limit: int = Query(10, ge=1, le=200)):
        store = store_factory()
        closed = [p for p in store.list_all() if p.status == "closed"]
        closed.sort(key=lambda p: p.closed_date or "", reverse=True)
        return [position_to_response(p) for p in closed[:limit]]

    @router.get("/api/v1/journal/exits", response_model=list[JournalExitResponse])
    def journal_exits(limit: int = Query(20, ge=1, le=500)):
        """Each exit decision as its own event — partial-close legs surface
        here alongside fully-closed positions. A position closed via the
        partial path produces N rows (one per leg); a position closed via
        the legacy single-shot path produces 1 row.
        """
        store = store_factory()
        events: list[JournalExitResponse] = []
        for p in store.list_all():
            legs = p.partial_exits or []
            if legs:
                # One event per partial leg. Covers both still-open positions
                # and positions closed via the partial path (their final leg
                # is the last entry in partial_exits).
                for leg in legs:
                    events.append(JournalExitResponse(
                        position_id=p.id,
                        date=leg.get("date") or p.closed_date or p.entry_date,
                        ticker=p.ticker,
                        account_key=p.account_key,
                        instrument=p.instrument,
                        direction=p.direction,
                        contracts_closed=leg.get("contracts_closed"),
                        pnl_usd=leg.get("pnl_usd"),
                        notes=leg.get("notes"),
                        is_partial=True,
                    ))
            elif p.status == "closed":
                # Legacy single-shot close — no per-leg history. Render the
                # position itself as the exit event.
                events.append(JournalExitResponse(
                    position_id=p.id,
                    date=p.closed_date or p.entry_date,
                    ticker=p.ticker,
                    account_key=p.account_key,
                    instrument=p.instrument,
                    direction=p.direction,
                    contracts_closed=p.contracts,
                    pnl_usd=p.pnl_usd,
                    notes=p.notes,
                    is_partial=False,
                ))
        events.sort(key=lambda e: e.date, reverse=True)
        return events[:limit]

    return router
