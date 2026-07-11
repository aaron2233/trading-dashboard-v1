"""FastAPI application — HTTP wrappers around the existing CLI surface.

API version /api/v1/ baked in from day one (per Winston's architectural
recommendation: future agent terminal will consume a superset of these endpoints
and unversioned URLs would break notebooks written against v1).

Persistence (positions.json, events.jsonl) and config (config.yaml) load from
the same locations the CLIs use; a fresh user gets the baked-in defaults.

Route handlers live under api/routes/ split by domain (lotto, tier_scans,
regime, indicators, kill_sheet, positions, journal, discipline).
This file wires them onto the app along with the query/L0 agent router.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.models import HealthResponse
from api.query_routes import make_query_router
from api.routes.discipline import make_discipline_router
from api.routes.indicators import make_indicators_router
from api.routes.journal import make_journal_router
from api.routes.kill_sheet import make_kill_sheet_router
from api.routes.lotto import make_lotto_router
from api.routes.positions import make_positions_router
from api.routes.regime import make_regime_router
from api.routes.tier_scans import make_tier_scans_router
from config import load_config
from positions import PositionStore
from storage.cache import get_cache


VERSION = "0.1.0"


def create_app(
    store_factory=PositionStore,
    config_loader=load_config,
    cache_factory=None,
) -> FastAPI:
    """Build a FastAPI app. store_factory, config_loader, and cache_factory
    are injectable for tests."""
    if cache_factory is None:
        cache_factory = get_cache

    app = FastAPI(
        title="Trading Dashboard API",
        version=VERSION,
        description=(
            "HTTP wrappers around the discipline-engine CLI surface "
            "(scan, kill sheet, positions, alerts, journal)."
        ),
    )

    # CORS for local React dev (Vite default port 5173). Browser-only project,
    # localhost-only deploy, so wide-open is fine in V1.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health():
        return HealthResponse(status="ok", version=VERSION)

    app.include_router(make_lotto_router(store_factory, config_loader))
    app.include_router(make_tier_scans_router())
    app.include_router(make_regime_router(store_factory, config_loader, cache_factory))
    app.include_router(make_indicators_router())
    app.include_router(make_kill_sheet_router(store_factory, config_loader))
    app.include_router(make_positions_router(store_factory))
    app.include_router(make_journal_router(store_factory))
    app.include_router(make_discipline_router(store_factory))
    app.include_router(make_query_router(cache_factory=cache_factory))

    return app


# Module-level app for `uvicorn api.app:app`
app = create_app()
