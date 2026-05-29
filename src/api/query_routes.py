"""Query API + L0 read-only agent endpoints.

`/api/v1/query/...` — fast SQL-backed cross-cutting queries that the
JSON file-scan can't deliver well. Read from the SQLite cache.

`/api/v1/agent/snapshot` — comprehensive read-only state bundle for the
L0 read-only agent. Lets chat-Claude (in another conversation) see the
live dashboard state — regime, open positions, recent discipline scores,
weekly stats, latest Sunday scan — in one round-trip.

The L0 agent never mutates state. Per V2 decision: "Agent autonomy
capped at L0 (read-only) and L1 (propose-and-prepare); L2/L3 explicitly
out of V2."
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from storage.cache import Cache, get_cache


def make_query_router(cache_factory=get_cache) -> APIRouter:
    """Build the query + L0 router. cache_factory is injectable for tests."""
    router = APIRouter()

    def _cache() -> Cache:
        try:
            return cache_factory()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"cache unavailable: {exc}",
            )

    # ── Query endpoints ────────────────────────────────────────────────────

    @router.get("/api/v1/query/positions")
    def query_positions(
        account: str | None = Query(None),
        status: str | None = Query(None, pattern="^(open|closed)$"),
        ticker: str | None = Query(None),
        closed_after: str | None = Query(None, description="ISO date or datetime"),
        closed_before: str | None = Query(None, description="ISO date or datetime"),
    ) -> list[dict[str, Any]]:
        return _cache().query_positions(
            account=account,
            status=status,
            ticker=ticker,
            closed_after=closed_after,
            closed_before=closed_before,
        )

    @router.get("/api/v1/query/discipline")
    def query_discipline(
        full_adherence: bool | None = Query(None),
        profitable_violation: bool | None = Query(None),
        closed_after: str | None = Query(None),
        closed_before: str | None = Query(None),
        limit: int | None = Query(None, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return _cache().query_discipline_scores(
            full_adherence=full_adherence,
            profitable_violation=profitable_violation,
            closed_after=closed_after,
            closed_before=closed_before,
            limit=limit,
        )

    @router.get("/api/v1/query/weekly-reviews")
    def query_weekly(
        limit: int | None = Query(None, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return _cache().query_weekly_reviews(limit=limit)

    @router.get("/api/v1/query/realized-pnl")
    def query_realized_pnl(
        account: str | None = Query(None),
        closed_after: str | None = Query(None),
        closed_before: str | None = Query(None),
    ) -> dict[str, float]:
        total = _cache().realized_pnl(
            account=account,
            closed_after=closed_after,
            closed_before=closed_before,
        )
        return {"realized_pnl_usd": total}

    @router.get("/api/v1/query/discipline-summary")
    def query_discipline_summary() -> dict[str, Any]:
        return _cache().discipline_summary()

    # ── Cache control ──────────────────────────────────────────────────────

    @router.post("/api/v1/cache/rebuild")
    def rebuild_cache() -> dict[str, Any]:
        """Rebuild the SQLite cache from the JSON canonical store. Use after
        manual JSON edits, recovery from backups, or schema changes."""
        from discipline.store import DisciplineStore
        from positions.store import PositionStore

        cache = _cache()
        positions = [p.to_dict() for p in PositionStore().list_all()]
        d_store = DisciplineStore()
        scores = [s.to_dict() for s in d_store.list_scores()]
        weekly = []
        weekly_dir = d_store.base_dir / "weekly"
        if weekly_dir.exists():
            from storage.atomic import load_json_safe
            for path in sorted(weekly_dir.glob("*.json")):
                payload = load_json_safe(path)
                if payload is not None:
                    weekly.append(payload)
        from focus.sunday_scan import SUNDAY_SCANS_DIR
        from storage.atomic import load_json_safe
        scan_payloads: list[dict] = []
        if SUNDAY_SCANS_DIR.exists():
            for path in sorted(SUNDAY_SCANS_DIR.glob("*.json")):
                payload = load_json_safe(path)
                if payload is not None:
                    scan_payloads.append(payload)

        counts = cache.rebuild_from_json(
            positions=positions,
            discipline_scores=scores,
            weekly_reviews=weekly,
            sunday_scans=scan_payloads,
        )
        return {"rebuilt": True, "counts": counts}

    # ── L0 agent snapshot ──────────────────────────────────────────────────

    @router.get("/api/v1/agent/snapshot")
    def agent_snapshot() -> dict[str, Any]:
        """One-shot read-only state bundle for chat-Claude. Read-only
        access to: open positions, recent discipline scores, latest
        weekly review, last 5 Sunday scans, latest regime_health
        snapshot, summary aggregates. Regime health is the cached
        snapshot — agent doesn't trigger a fresh fetch (that's the
        /regime-health endpoint's job)."""
        cache = _cache()
        regime_recent = cache.query_regime_health_recent(limit=1)
        regime_health = regime_recent[0] if regime_recent else None
        return {
            "open_positions": cache.query_positions(status="open"),
            "recent_discipline_scores": cache.query_discipline_scores(limit=10),
            "weekly_reviews": cache.query_weekly_reviews(limit=4),
            "recent_sunday_scans": cache.query_recent_sunday_scans(limit=5),
            "regime_health": regime_health,
            "summary": {
                "discipline": cache.discipline_summary(),
                "realized_pnl_total": cache.realized_pnl(),
                "realized_pnl_main": cache.realized_pnl(account="main"),
                "realized_pnl_lotto": cache.realized_pnl(account="lotto"),
                "realized_pnl_weekly": cache.realized_pnl(account="weekly"),
            },
        }

    return router
