"""Tests for the Greeks/IV/premium-threshold extension to Position.

Covers:
- Position model accepts the new fields and round-trips them through to_dict / from_dict
- open_options_position factory threads them through
- from_dict tolerates unknown forward-version keys (no crash)
- API request → response carries the fields
- SQLite cache stores and retrieves them
- Schema-mismatch upgrade path drops + recreates cleanly
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from positions import Position, PositionStore
from storage.cache import Cache


# ── Position model ─────────────────────────────────────────────────────────


def test_position_open_options_accepts_greeks_and_iv():
    p = Position.open_options_position(
        ticker="WEAT",
        direction="long",
        contract_type="call",
        account_key="main",
        strike=25,
        expiry="2026-01-15",
        premium=1.80,
        contracts=1,
        underlying_price=26.0,
        delta=0.55,
        gamma=0.04,
        theta=-0.02,
        vega=0.18,
        iv=0.42,
        iv_rank=68,
        premium_stop=0.63,
        premium_target=3.60,
    )
    assert p.delta == 0.55
    assert p.gamma == 0.04
    assert p.theta == -0.02
    assert p.vega == 0.18
    assert p.iv == 0.42
    assert p.iv_rank == 68
    assert p.premium_stop == 0.63
    assert p.premium_target == 3.60


def test_position_legacy_load_without_greeks_defaults_to_none():
    """A JSON written before the Greeks extension should still load fine."""
    legacy_payload = {
        "id": "abc",
        "ticker": "SPY",
        "direction": "long",
        "instrument": "call",
        "account_key": "main",
        "entry_date": "2026-04-01T10:00:00+00:00",
        "contracts": 1,
        "strike": 580,
        "expiry": "2026-06-19",
        "premium_paid_per_contract": 5.50,
        "total_cost_usd": 550.0,
        "max_loss_usd": 550.0,
        "status": "open",
    }
    p = Position.from_dict(legacy_payload)
    assert p.delta is None
    assert p.iv is None
    assert p.premium_stop is None


def test_position_from_dict_strips_unknown_keys():
    """Forward-compat: a future version with extra keys must still load."""
    payload = {
        "id": "abc",
        "ticker": "SPY",
        "direction": "long",
        "instrument": "call",
        "account_key": "main",
        "entry_date": "2026-04-01T10:00:00+00:00",
        "total_cost_usd": 0,
        "max_loss_usd": 0,
        "status": "open",
        "future_field_not_yet_implemented": {"nested": "value"},
        "another_unknown": 42,
    }
    # Should not raise
    p = Position.from_dict(payload)
    assert p.id == "abc"


def test_position_to_dict_includes_greeks():
    p = Position.open_options_position(
        ticker="SPY", direction="long", contract_type="call", account_key="main",
        strike=580, expiry="2026-06-19", premium=5.50, contracts=1,
        delta=0.45, iv=0.30,
    )
    d = p.to_dict()
    assert d["delta"] == 0.45
    assert d["iv"] == 0.30


def test_position_roundtrip_preserves_greeks():
    p1 = Position.open_options_position(
        ticker="WEAT", direction="long", contract_type="call", account_key="main",
        strike=25, expiry="2026-01-15", premium=1.80, contracts=1,
        delta=0.55, gamma=0.04, theta=-0.02, vega=0.18,
        iv=0.42, iv_rank=68,
        premium_stop=0.63, premium_target=3.60,
    )
    p2 = Position.from_dict(p1.to_dict())
    assert p2.delta == p1.delta
    assert p2.gamma == p1.gamma
    assert p2.theta == p1.theta
    assert p2.vega == p1.vega
    assert p2.iv == p1.iv
    assert p2.iv_rank == p1.iv_rank
    assert p2.premium_stop == p1.premium_stop
    assert p2.premium_target == p1.premium_target


# ── PositionStore + Cache ──────────────────────────────────────────────────


def test_position_store_persists_greeks_through_json(tmp_path: Path):
    path = tmp_path / "positions.json"
    s1 = PositionStore(path=path)
    p = Position.open_options_position(
        ticker="WEAT", direction="long", contract_type="call", account_key="main",
        strike=25, expiry="2026-01-15", premium=1.80, contracts=1,
        delta=0.55, iv=0.42, premium_stop=0.63, premium_target=3.60,
    )
    s1.add(p)

    # Reload
    s2 = PositionStore(path=path)
    loaded = s2.get(p.id)
    assert loaded.delta == 0.55
    assert loaded.iv == 0.42
    assert loaded.premium_stop == 0.63
    assert loaded.premium_target == 3.60


def test_cache_upsert_position_stores_greeks(tmp_path: Path):
    cache = Cache(path=tmp_path / "cache.sqlite")
    cache.upsert_position({
        "id": "p1",
        "ticker": "WEAT",
        "direction": "long",
        "instrument": "call",
        "account_key": "main",
        "status": "open",
        "entry_date": "2026-04-01T10:00:00+00:00",
        "contracts": 1,
        "strike": 25,
        "expiry": "2026-01-15",
        "premium_paid_per_contract": 1.80,
        "total_cost_usd": 180.0,
        "max_loss_usd": 180.0,
        "delta": 0.55,
        "gamma": 0.04,
        "theta": -0.02,
        "vega": 0.18,
        "iv": 0.42,
        "iv_rank": 68,
        "premium_stop": 0.63,
        "premium_target": 3.60,
    })
    rows = cache.query_positions()
    assert len(rows) == 1
    r = rows[0]
    assert r["delta"] == 0.55
    assert r["gamma"] == 0.04
    assert r["theta"] == -0.02
    assert r["vega"] == 0.18
    assert r["iv"] == 0.42
    assert r["iv_rank"] == 68
    assert r["premium_stop"] == 0.63
    assert r["premium_target"] == 3.60
    cache.close()


def test_cache_schema_mismatch_drops_and_recreates(tmp_path: Path):
    """A cache from an earlier schema version must be wiped + rebuilt cleanly
    on the next open. Data is recoverable from JSON, so wiping is safe."""
    p = tmp_path / "cache.sqlite"

    # Manually create a "v1" cache with an old version stamp + stale data.
    import sqlite3
    conn = sqlite3.connect(str(p))
    conn.executescript("""
        CREATE TABLE _cache_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO _cache_meta (key, value) VALUES ('schema_version', '1');
        CREATE TABLE positions (id TEXT PRIMARY KEY, stale_col TEXT);
        INSERT INTO positions (id, stale_col) VALUES ('old', 'leftover');
    """)
    conn.commit()
    conn.close()

    # Open the cache via our class — should detect mismatch and rebuild.
    cache = Cache(path=p)
    from storage.cache import SCHEMA_VERSION
    assert cache.schema_version() == SCHEMA_VERSION
    # Old data wiped; new schema empty.
    assert cache.query_positions() == []
    cache.close()


# ── API ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path):
    """An isolated client whose store and cache live under tmp_path."""
    pos_path = tmp_path / "positions.json"
    cache_path = tmp_path / "cache.sqlite"

    def _store_factory():
        return PositionStore(path=pos_path)

    def _cache_factory():
        return Cache(path=cache_path)

    app = create_app(
        store_factory=_store_factory,
        cache_factory=_cache_factory,
    )
    return TestClient(app)


def test_api_open_position_with_greeks(client: TestClient):
    r = client.post(
        "/api/v1/positions",
        json={
            "ticker": "WEAT",
            "direction": "long",
            "instrument": "call",
            "account": "main",
            "strike": 25,
            "expiry": "2026-01-15",
            "premium": 1.80,
            "contracts": 1,
            "entry_price": 26.0,
            "delta": 0.55,
            "gamma": 0.04,
            "theta": -0.02,
            "vega": 0.18,
            "iv": 0.42,
            "iv_rank": 68,
            "premium_stop": 0.63,
            "premium_target": 3.60,
            "bypass_kill_sheet": True,
            "notes": "test — gate not under test",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["delta"] == 0.55
    assert body["iv"] == 0.42
    assert body["premium_stop"] == 0.63
    assert body["premium_target"] == 3.60


def test_api_open_position_without_greeks_still_works(client: TestClient):
    """Backwards-compat: existing clients that don't send Greeks still succeed."""
    r = client.post(
        "/api/v1/positions",
        json={
            "ticker": "SPY",
            "direction": "long",
            "instrument": "call",
            "account": "main",
            "strike": 580,
            "expiry": "2026-06-19",
            "premium": 5.50,
            "contracts": 1,
            "bypass_kill_sheet": True,
            "notes": "test — gate not under test",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["delta"] is None
    assert body["premium_stop"] is None


def test_api_get_position_surfaces_greeks(client: TestClient):
    create = client.post(
        "/api/v1/positions",
        json={
            "ticker": "WEAT", "direction": "long", "instrument": "call",
            "strike": 25, "expiry": "2026-01-15", "premium": 1.80, "contracts": 1,
            "delta": 0.55, "iv": 0.42, "iv_rank": 68,
            "bypass_kill_sheet": True,
            "notes": "test — gate not under test",
        },
    )
    pos_id = create.json()["id"]

    r = client.get(f"/api/v1/positions/{pos_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["delta"] == 0.55
    assert body["iv"] == 0.42
    assert body["iv_rank"] == 68
