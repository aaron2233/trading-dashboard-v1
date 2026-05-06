"""Top-level snapshot assembler — runs every tier and aggregates the
overall regime-health status.

Sprint 1 ships Tier 1 + Tier 2 fully wired. Tier 3 (breadth) and Tier 4
(AI capex calendar) ship as empty TierBundle stubs; Sprint 3 fills them
in. The snapshot shape is final from this commit forward — frontend can
build against the v1 schema.

Status precedence:
  Overall = worst of any Tier 1 OR Tier 2 reading
  unknown / error readings are fail-open (don't drag overall to red)
  Tier 3 + Tier 4 are informational only (don't gate overall)
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Callable

from regime_health.model import (
    IndicatorReading,
    RegimeHealthSnapshot,
    TierBundle,
    worst_status,
)
from regime_health.thresholds import DEFAULT_THRESHOLDS, ThresholdConfig
from regime_health.tier1_market import assemble_tier1
from regime_health.tier2_macro import assemble_tier2
from regime_health.tier3_breadth import assemble_tier3
from regime_health.tier4_capex import assemble_tier4, find_pending_capex_updates


logger = logging.getLogger(__name__)


def _compute_overall(
    tier1: TierBundle, tier2: TierBundle,
) -> tuple[str, list[str]]:
    """Return (overall_status, drivers).

    Drivers are the indicator labels whose status equals the overall
    status — useful for the panel header copy ("Overall: AMBER · driven
    by HY OAS, 2s10s curve").
    """
    statuses: list[str] = []
    for r in (*tier1.readings, *tier2.readings):
        statuses.append(r.status)
    overall = worst_status(*statuses)

    if overall in ("amber", "red"):
        drivers = [
            r.label
            for r in (*tier1.readings, *tier2.readings)
            if r.status == overall
        ]
    else:
        drivers = []
    return overall, drivers


def assemble_snapshot(
    *,
    thresholds: ThresholdConfig | None = None,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
    load_fn: Callable[..., Any] | None = None,
    fetch: Callable[[str], dict[str, Any]] | None = None,
    api_key: str | None = None,
    snapshot_date: str | None = None,
) -> RegimeHealthSnapshot:
    """Assemble a full Regime Health snapshot.

    Tier 1 and Tier 2 run sequentially (network IO is the bottleneck;
    moving to asyncio is a v2 perf optimization, not a v1 correctness
    issue). Each tier handles its own per-reader failures internally.

    Injectable params let tests run the assembler without network IO.
    """
    cfg = thresholds or DEFAULT_THRESHOLDS
    today = snapshot_date or date.today().isoformat()

    tier1 = assemble_tier1(thresholds=cfg, scan_fn=scan_fn, load_fn=load_fn)
    tier2 = assemble_tier2(thresholds=cfg, fetch=fetch, api_key=api_key)
    tier3 = assemble_tier3(thresholds=cfg, load_fn=load_fn)
    tier4 = assemble_tier4()

    overall, drivers = _compute_overall(tier1, tier2)

    # Pending updates are informational — separate from overall_status.
    # Reads the same YAML config tier4 uses; failures degrade to empty
    # list (no reminder shown).
    try:
        pending = find_pending_capex_updates()
    except Exception:
        logger.exception("find_pending_capex_updates failed")
        pending = []

    snapshot = RegimeHealthSnapshot(
        snapshot_date=today,
        fetched_at=_now_iso(),
        overall_status=overall,
        tiers=[tier1, tier2, tier3, tier4],
        overall_drivers=drivers,
        pending_capex_updates=pending,
    )
    return snapshot


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def is_snapshot_fresh(
    snapshot: RegimeHealthSnapshot,
    *,
    max_age_hours: float = 12.0,
) -> bool:
    """Decide whether a cached snapshot is still fresh enough to serve.

    Default 12h means morning-fetched snapshots stay fresh for the
    trading day. Forced refresh (POST /refresh) bypasses this check.
    """
    from datetime import datetime, timezone
    try:
        fetched = datetime.fromisoformat(snapshot.fetched_at)
    except (TypeError, ValueError):
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - fetched).total_seconds() / 3600.0
    return age < max_age_hours
