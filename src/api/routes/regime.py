"""Regime-health + dashboard-state routes — overall market-state reads."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from api.models import (
    DashboardStateResponse,
    UnreviewedWeekResponse,
)
from discipline import compute_dashboard_state


def make_regime_router(store_factory, config_loader, cache_factory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/regime-health/snapshot")
    def regime_health_snapshot() -> dict[str, Any]:
        """Return today's Regime Health snapshot. Reads cached JSON if it
        exists and is <12h old; otherwise fetches fresh and persists.
        Per-tier failures degrade gracefully — the response always includes
        every tier with its readings (some may be 'unknown' or 'error')."""
        from regime_health import (
            RegimeHealthStore,
            assemble_snapshot,
            is_snapshot_fresh,
        )
        store = RegimeHealthStore(cache=cache_factory())
        cached = store.load_today()
        if cached is not None and is_snapshot_fresh(cached):
            return cached.to_dict()
        try:
            snapshot = assemble_snapshot()
        except Exception:
            # Total failure — return an empty snapshot rather than 500.
            # Frontend renders a "regime health unavailable" panel.
            # Log full exception server-side; return only generic text to clients.
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "regime_health snapshot assembly failed",
            )
            from regime_health.model import RegimeHealthSnapshot
            placeholder = RegimeHealthSnapshot.empty()
            placeholder.overall_drivers = ["snapshot assembly failed"]
            return placeholder.to_dict()
        try:
            store.save(snapshot)
        except Exception:
            # Persistence failure shouldn't block the response — the user
            # still gets the live read. Logged for diagnosis.
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "regime_health snapshot persistence failed",
            )
        return snapshot.to_dict()

    @router.post("/api/v1/regime-health/refresh")
    def regime_health_refresh() -> dict[str, Any]:
        """Force a fresh snapshot fetch + persist, ignoring cache freshness."""
        from regime_health import RegimeHealthStore, assemble_snapshot
        snapshot = assemble_snapshot()
        store = RegimeHealthStore(cache=cache_factory())
        try:
            store.save(snapshot)
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "regime_health snapshot persistence failed (refresh)",
            )
        return snapshot.to_dict()

    @router.get("/api/v1/regime-health/history")
    def regime_health_history(
        days: int = Query(30, ge=1, le=365),
    ) -> dict[str, Any]:
        """Return the most recent N snapshots (newest first), filesystem-backed.
        SQLite cache is queryable too but JSON is canonical and the directory
        scan is plenty fast for the sizes we're talking about (one snapshot
        per day; 30-365 entries max)."""
        from regime_health import RegimeHealthStore
        store = RegimeHealthStore(cache=cache_factory())
        snapshots = store.list_recent(limit=days)
        return {"snapshots": [s.to_dict() for s in snapshots]}

    @router.get("/api/v1/dashboard/state", response_model=DashboardStateResponse)
    def dashboard_state():
        """Aggregate stage + balance + unreviewed-weeks for the UX banner.

        Balance source: config base + realized P&L from closed positions.
        """
        config = config_loader()
        store = store_factory()
        closed_positions = [p for p in store.list_all() if p.status == "closed"]
        state = compute_dashboard_state(config, closed_positions)
        return DashboardStateResponse(
            stage=state.stage,
            stage_reminder=state.stage_reminder,
            account_balance_usd=state.account_balance_usd,
            threshold_usd=state.threshold_usd,
            progress_to_threshold=state.progress_to_threshold,
            realized_pnl_usd=state.realized_pnl_usd,
            base_balance_usd=state.base_balance_usd,
            unreviewed_weeks=[
                UnreviewedWeekResponse(
                    week_start=w.week_start,
                    week_end=w.week_end,
                    closed_trade_count=w.closed_trade_count,
                )
                for w in state.unreviewed_weeks
            ],
        )

    return router
