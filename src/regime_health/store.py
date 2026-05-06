"""Storage layer for Regime Health snapshots.

JSON canonical at ~/.trading-dashboard/regime_health/<YYYY-MM-DD>.json.
Optional SQLite cache write-through (mirrors the V1.5 pattern in
PositionStore / DisciplineStore / sunday_scan persist).

Daily key — one snapshot per day. A force-refresh during the day
overwrites the same file. History queries (Sprint 3) read across files
or from the SQLite snapshots table.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from regime_health.model import RegimeHealthSnapshot
from storage.atomic import load_json_safe, write_json_atomic

if TYPE_CHECKING:
    from storage.cache import Cache


logger = logging.getLogger(__name__)


def _default_regime_health_dir() -> Path:
    """Resolve the default storage directory at call time, not module
    load time, so per-test Path.home() patches are honored.

    Other stores in this project freeze their DEFAULT_* constants at
    import; that works for them because their tests always pass an
    explicit path. The regime_health API endpoint constructs the store
    without args, so we need lazy resolution to keep tests isolated.
    """
    return Path.home() / ".trading-dashboard" / "regime_health"


class RegimeHealthStore:
    """Daily snapshot persistence.

    JSON file = source of truth (human-readable, grep-able).
    Optional SQLite cache = queryable history via the V1.5 cache layer.
    Cache failures are logged, never raised — JSON stays canonical.
    """

    def __init__(
        self,
        base_dir: Path | None = None,
        cache: "Cache | None" = None,
    ) -> None:
        self.base_dir = base_dir if base_dir is not None else _default_regime_health_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.cache = cache

    def _path(self, snapshot_date: str) -> Path:
        return self.base_dir / f"{snapshot_date}.json"

    def save(self, snapshot: RegimeHealthSnapshot) -> Path:
        path = self._path(snapshot.snapshot_date)
        payload = snapshot.to_dict()
        write_json_atomic(path, payload)
        if self.cache is not None:
            try:
                self.cache.upsert_regime_health_snapshot(payload)
            except Exception:
                logger.exception(
                    "cache upsert failed for regime_health snapshot %s",
                    snapshot.snapshot_date,
                )
        return path

    def load_for_date(self, snapshot_date: str) -> RegimeHealthSnapshot | None:
        """Load the snapshot for a specific date. Returns None if not found
        or unparseable."""
        path = self._path(snapshot_date)
        if not path.exists():
            return None
        data = load_json_safe(path)
        if data is None:
            logger.warning("regime_health snapshot at %s is corrupt or empty", path)
            return None
        return _from_dict(data)

    def load_today(self) -> RegimeHealthSnapshot | None:
        """Load today's snapshot if present."""
        from datetime import date
        return self.load_for_date(date.today().isoformat())

    def list_recent(self, limit: int = 30) -> list[RegimeHealthSnapshot]:
        """List the most recent N snapshots (newest first), filesystem-ordered.

        Used by Sprint 3's /history endpoint. Skips corrupt files silently
        (logged) so one bad snapshot doesn't break the list.
        """
        if not self.base_dir.exists():
            return []
        out: list[RegimeHealthSnapshot] = []
        # JSON filenames are YYYY-MM-DD.json — lexicographic = chronological.
        for path in sorted(self.base_dir.glob("*.json"), reverse=True)[:limit]:
            data = load_json_safe(path)
            if data is None:
                logger.warning("skipping corrupt regime_health snapshot %s", path)
                continue
            try:
                out.append(_from_dict(data))
            except Exception:
                logger.exception("skipping malformed regime_health snapshot %s", path)
        return out


def _from_dict(data: dict) -> RegimeHealthSnapshot:
    """Reconstruct a snapshot from its to_dict() payload.

    Tolerant rebuild — missing optional fields fall back to safe defaults,
    unknown extra fields are ignored. Per the V1.5 hardening pattern in
    PositionStore.from_dict.
    """
    from regime_health.model import IndicatorReading, TierBundle

    tiers_raw = data.get("tiers") or []
    tiers: list[TierBundle] = []
    for t in tiers_raw:
        if not isinstance(t, dict):
            continue
        readings_raw = t.get("readings") or []
        readings: list[IndicatorReading] = []
        for r in readings_raw:
            if not isinstance(r, dict):
                continue
            readings.append(IndicatorReading(
                indicator_id=r.get("indicator_id", ""),
                label=r.get("label", ""),
                tier=int(r.get("tier", 0) or 0),
                status=r.get("status", "unknown"),
                value=r.get("value"),
                formatted_value=r.get("formatted_value", "—"),
                threshold_note=r.get("threshold_note", ""),
                source=r.get("source", ""),
                error=r.get("error"),
                fetched_at=r.get("fetched_at", ""),
            ))
        tiers.append(TierBundle(
            tier=int(t.get("tier", 0) or 0),
            label=t.get("label", ""),
            readings=readings,
            error=t.get("error"),
        ))

    return RegimeHealthSnapshot(
        snapshot_date=data.get("snapshot_date", ""),
        fetched_at=data.get("fetched_at", ""),
        overall_status=data.get("overall_status", "unknown"),
        tiers=tiers,
        overall_drivers=list(data.get("overall_drivers") or []),
        pending_capex_updates=list(data.get("pending_capex_updates") or []),
    )
