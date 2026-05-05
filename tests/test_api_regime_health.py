"""Integration tests for /api/v1/regime-health/* endpoints.

These tests use the real FastAPI app + real RegimeHealthStore (under
tmp HOME via the conftest isolation fixture). The assemble path is
patched so we don't hit yfinance / FRED in CI.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from regime_health.model import (
    IndicatorReading,
    RegimeHealthSnapshot,
    TierBundle,
)


def _fake_snapshot(*, snapshot_date: str = "2026-05-05") -> RegimeHealthSnapshot:
    tier1 = TierBundle(
        tier=1, label="Structural & Volatility",
        readings=[
            IndicatorReading(
                indicator_id="vix", label="VIX", tier=1, status="green",
                value=15.5, formatted_value="15.50", source="yfinance",
            ),
        ],
    )
    return RegimeHealthSnapshot(
        snapshot_date=snapshot_date,
        fetched_at="2026-05-05T09:30:00+00:00",
        overall_status="green",
        tiers=[
            tier1,
            TierBundle(tier=2, label="Macro (FRED)"),
            TierBundle(tier=3, label="Breadth"),
            TierBundle(tier=4, label="AI Capex Calendar"),
        ],
        overall_drivers=[],
    )


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    app = create_app()
    with TestClient(app) as c:
        yield c


# ── /regime-health/snapshot ──────────────────────────────────────────────────


def test_snapshot_endpoint_assembles_when_no_cache(client: TestClient):
    """Cold start — no cached file, no SQLite row → endpoint runs assemble
    (which we patch), persists, returns the snapshot."""
    fake = _fake_snapshot()
    with patch("regime_health.assemble_snapshot", return_value=fake):
        r = client.get("/api/v1/regime-health/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_status"] == "green"
    assert body["snapshot_date"] == fake.snapshot_date
    assert len(body["tiers"]) == 4


def test_snapshot_endpoint_serves_fresh_cache(client: TestClient, monkeypatch, tmp_path):
    """Second hit within freshness window must NOT call assemble again."""
    from datetime import date
    fake = _fake_snapshot(snapshot_date=date.today().isoformat())
    # Populate fetched_at to "now" so freshness check returns True.
    from datetime import datetime, timezone
    fake.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    call_count = {"n": 0}

    def fake_assemble(*_a, **_kw):
        call_count["n"] += 1
        return fake

    with patch("regime_health.assemble_snapshot", fake_assemble):
        r1 = client.get("/api/v1/regime-health/snapshot")
        r2 = client.get("/api/v1/regime-health/snapshot")

    assert r1.status_code == 200
    assert r2.status_code == 200
    # First call assembles + persists; second call serves from cache.
    assert call_count["n"] == 1


def test_snapshot_endpoint_handles_assemble_failure(client: TestClient):
    """Total assemble failure → endpoint returns empty snapshot, not 500."""
    def boom(*_a, **_kw):
        raise RuntimeError("everything is on fire")

    with patch("regime_health.assemble_snapshot", boom):
        r = client.get("/api/v1/regime-health/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["overall_status"] == "unknown"
    # Drivers carry the failure context for the panel
    assert any("everything is on fire" in d for d in body["overall_drivers"])


# ── /regime-health/refresh ───────────────────────────────────────────────────


def test_refresh_endpoint_forces_assemble(client: TestClient):
    """Refresh must always call assemble — bypasses freshness."""
    from datetime import datetime, timezone, date
    fake = _fake_snapshot(snapshot_date=date.today().isoformat())
    fake.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    call_count = {"n": 0}

    def fake_assemble(*_a, **_kw):
        call_count["n"] += 1
        return fake

    with patch("regime_health.assemble_snapshot", fake_assemble):
        # Even after a snapshot endpoint hit, refresh assembles fresh.
        client.get("/api/v1/regime-health/snapshot")
        r = client.post("/api/v1/regime-health/refresh")

    assert r.status_code == 200
    assert call_count["n"] == 2  # snapshot once + refresh once


# ── Agent snapshot integration ───────────────────────────────────────────────


def test_agent_snapshot_includes_regime_health_after_assemble(client: TestClient):
    """After a snapshot has been assembled + persisted, /agent/snapshot
    returns it as `regime_health`."""
    from datetime import date
    fake = _fake_snapshot(snapshot_date=date.today().isoformat())
    from datetime import datetime, timezone
    fake.fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    with patch("regime_health.assemble_snapshot", return_value=fake):
        client.get("/api/v1/regime-health/snapshot")  # populate cache

    r = client.get("/api/v1/agent/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["regime_health"] is not None
    assert body["regime_health"]["overall_status"] == "green"
    assert body["regime_health"]["snapshot_date"] == fake.snapshot_date
