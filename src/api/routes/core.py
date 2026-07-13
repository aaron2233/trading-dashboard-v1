"""QQQM-core strategy route — monitor signal + core position + sleeve sizing."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from core_monitor import (
    core_sleeve,
    load_monitor_state,
    monitor_is_stale,
    summarize_core_positions,
)


def make_core_router(store_factory, config_loader) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/core/state")
    def core_state() -> dict[str, Any]:
        """State for the Core view. The signal itself comes from the daily
        monitor job's JSON (single computation source) — this endpoint only
        merges it with journal positions and config sizing."""
        doc, err = load_monitor_state()
        store = store_factory()
        open_positions = [p for p in store.list_all() if p.status == "open"]
        return {
            "monitor": doc,
            "monitor_error": err,
            "monitor_stale": monitor_is_stale(doc) if doc else True,
            "positions": summarize_core_positions(open_positions),
            "sleeve": core_sleeve(config_loader()),
        }

    return router
