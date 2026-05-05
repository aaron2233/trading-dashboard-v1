"""Tests for /api/v1/query/* and /api/v1/agent/snapshot.

These verify the L0 read-only agent surface — the endpoints chat-Claude
will hit to read live dashboard state. They must:
- Return well-shaped JSON
- Honor filter parameters
- Survive empty cache (return empty arrays, not 500)
- Never mutate data (read-only contract)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from storage.cache import Cache


@pytest.fixture
def cache_path(tmp_path: Path) -> Path:
    return tmp_path / "cache.sqlite"


@pytest.fixture
def cache(cache_path: Path) -> Cache:
    c = Cache(path=cache_path)
    yield c
    c.close()


@pytest.fixture
def client(cache_path: Path):
    """A TestClient bound to a Cache rooted at cache_path."""
    from positions import PositionStore

    def _factory():
        return Cache(path=cache_path)

    app = create_app(
        store_factory=PositionStore,
        cache_factory=_factory,
    )
    return TestClient(app)


def _seed_position(cache: Cache, **overrides) -> dict:
    payload = {
        "id": "pos_1",
        "ticker": "SPY",
        "direction": "long",
        "instrument": "call",
        "account_key": "main",
        "status": "open",
        "skill": None,
        "tier": None,
        "entry_date": "2026-04-01T10:00:00+00:00",
        "closed_date": None,
        "contracts": 1,
        "shares": None,
        "strike": 580,
        "expiry": "2026-06-19",
        "premium_paid_per_contract": 5.50,
        "total_cost_usd": 550.0,
        "max_loss_usd": 550.0,
        "target_price": 600,
        "invalidation_price": 560,
        "pnl_usd": None,
        "notes": None,
    }
    payload.update(overrides)
    cache.upsert_position(payload)
    return payload


def _seed_score(cache: Cache, **overrides) -> dict:
    payload = {
        "position_id": "pos_1",
        "kill_sheet_id": None,
        "closed_at": "2026-04-15T14:00:00+00:00",
        "ticker": "SPY",
        "direction": "long",
        "instrument": "call",
        "entry_at": "2026-04-01T10:00:00+00:00",
        "pnl_usd": 220.0,
        "score_numerator": 14,
        "score_denominator": 15,
        "score": 0.9333,
        "profitable_violation": False,
        "counterfactual_loss_usd": None,
        "full_adherence": False,
        "profitable_violation_resolution": None,
        "notes": "",
        "scored_at": "2026-04-15T14:30:00+00:00",
        "rules": [],
    }
    payload.update(overrides)
    cache.upsert_discipline_score(payload)
    return payload


# ── Query — positions ──────────────────────────────────────────────────────


def test_query_positions_empty(client: TestClient):
    r = client.get("/api/v1/query/positions")
    assert r.status_code == 200
    assert r.json() == []


def test_query_positions_returns_seeded(client: TestClient, cache: Cache):
    _seed_position(cache, id="p1")
    r = client.get("/api/v1/query/positions")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["id"] == "p1"


def test_query_positions_filter_by_account(client: TestClient, cache: Cache):
    _seed_position(cache, id="p_main", account_key="main")
    _seed_position(cache, id="p_lotto", account_key="lotto")
    r = client.get("/api/v1/query/positions", params={"account": "lotto"})
    body = r.json()
    assert {p["id"] for p in body} == {"p_lotto"}


def test_query_positions_filter_by_status_validates(client: TestClient):
    """Status must be 'open' or 'closed' — 422 on invalid."""
    r = client.get("/api/v1/query/positions", params={"status": "neither"})
    assert r.status_code == 422


def test_query_positions_filter_by_close_window(client: TestClient, cache: Cache):
    _seed_position(cache, id="p_old", status="closed",
                   closed_date="2026-03-01T10:00:00+00:00", pnl_usd=10)
    _seed_position(cache, id="p_new", status="closed",
                   closed_date="2026-04-15T10:00:00+00:00", pnl_usd=20)
    r = client.get(
        "/api/v1/query/positions",
        params={"status": "closed", "closed_after": "2026-04-01"},
    )
    body = r.json()
    assert {p["id"] for p in body} == {"p_new"}


# ── Query — discipline ─────────────────────────────────────────────────────


def test_query_discipline_empty(client: TestClient):
    r = client.get("/api/v1/query/discipline")
    assert r.status_code == 200
    assert r.json() == []


def test_query_discipline_filter_full_adherence(client: TestClient, cache: Cache):
    _seed_score(cache, position_id="p1", full_adherence=True)
    _seed_score(cache, position_id="p2", full_adherence=False)
    r = client.get(
        "/api/v1/query/discipline",
        params={"full_adherence": "true"},
    )
    body = r.json()
    assert {s["position_id"] for s in body} == {"p1"}


def test_query_discipline_filter_profitable_violation(client: TestClient, cache: Cache):
    _seed_score(cache, position_id="p1", profitable_violation=True)
    _seed_score(cache, position_id="p2", profitable_violation=False)
    r = client.get(
        "/api/v1/query/discipline",
        params={"profitable_violation": "true"},
    )
    body = r.json()
    assert {s["position_id"] for s in body} == {"p1"}


def test_query_discipline_limit_clamped(client: TestClient):
    """Limit > 500 should 422."""
    r = client.get("/api/v1/query/discipline", params={"limit": 5000})
    assert r.status_code == 422


# ── Query — weekly / aggregates ────────────────────────────────────────────


def test_query_weekly_reviews_empty(client: TestClient):
    r = client.get("/api/v1/query/weekly-reviews")
    assert r.status_code == 200
    assert r.json() == []


def test_query_realized_pnl_empty(client: TestClient):
    r = client.get("/api/v1/query/realized-pnl")
    assert r.status_code == 200
    assert r.json() == {"realized_pnl_usd": 0.0}


def test_query_realized_pnl_sums_closed(client: TestClient, cache: Cache):
    _seed_position(cache, id="p1", status="closed",
                   closed_date="2026-04-10T10:00:00+00:00", pnl_usd=100)
    _seed_position(cache, id="p2", status="closed",
                   closed_date="2026-04-20T10:00:00+00:00", pnl_usd=-30)
    _seed_position(cache, id="p3", status="open")  # excluded
    r = client.get("/api/v1/query/realized-pnl")
    assert r.json()["realized_pnl_usd"] == 70.0


def test_query_realized_pnl_by_account(client: TestClient, cache: Cache):
    _seed_position(cache, id="p_main", status="closed",
                   account_key="main",
                   closed_date="2026-04-10T10:00:00+00:00", pnl_usd=100)
    _seed_position(cache, id="p_lotto", status="closed",
                   account_key="lotto",
                   closed_date="2026-04-15T10:00:00+00:00", pnl_usd=50)
    r = client.get(
        "/api/v1/query/realized-pnl",
        params={"account": "lotto"},
    )
    assert r.json()["realized_pnl_usd"] == 50.0


def test_query_discipline_summary_empty(client: TestClient):
    r = client.get("/api/v1/query/discipline-summary")
    body = r.json()
    assert body == {
        "scored": 0,
        "avg_score": 0.0,
        "full_adherence_count": 0,
        "profitable_violation_count": 0,
    }


# ── L0 agent snapshot ──────────────────────────────────────────────────────


def test_agent_snapshot_empty_cache(client: TestClient):
    """Empty cache must return well-shaped empty arrays, not 500."""
    r = client.get("/api/v1/agent/snapshot")
    assert r.status_code == 200
    body = r.json()
    assert body["open_positions"] == []
    assert body["recent_discipline_scores"] == []
    assert body["weekly_reviews"] == []
    assert body["recent_sunday_scans"] == []
    # regime_health is null when no snapshot has been cached yet
    assert body["regime_health"] is None
    assert body["summary"]["discipline"]["scored"] == 0
    assert body["summary"]["realized_pnl_total"] == 0.0


def test_agent_snapshot_includes_seeded_state(client: TestClient, cache: Cache):
    _seed_position(cache, id="p_open")
    _seed_position(
        cache, id="p_closed", status="closed",
        closed_date="2026-04-30T16:00:00+00:00", pnl_usd=240,
    )
    _seed_score(cache, position_id="p_closed")

    r = client.get("/api/v1/agent/snapshot")
    body = r.json()

    open_ids = [p["id"] for p in body["open_positions"]]
    assert "p_open" in open_ids
    assert "p_closed" not in open_ids

    score_ids = [s["position_id"] for s in body["recent_discipline_scores"]]
    assert "p_closed" in score_ids

    assert body["summary"]["realized_pnl_total"] == 240.0


def test_agent_snapshot_is_read_only(client: TestClient, cache: Cache):
    """Hitting the snapshot endpoint must not mutate the cache."""
    _seed_position(cache, id="p_x")
    before = client.get("/api/v1/query/positions").json()
    client.get("/api/v1/agent/snapshot")
    client.get("/api/v1/agent/snapshot")
    after = client.get("/api/v1/query/positions").json()
    assert before == after


# ── Cache rebuild ──────────────────────────────────────────────────────────


def test_cache_rebuild_endpoint_returns_counts(client: TestClient):
    """Rebuild on an empty home returns zero counts but doesn't error."""
    r = client.post("/api/v1/cache/rebuild")
    assert r.status_code == 200
    body = r.json()
    assert body["rebuilt"] is True
    assert "counts" in body
    assert all(isinstance(v, int) for v in body["counts"].values())
