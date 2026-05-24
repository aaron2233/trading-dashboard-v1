"""Tests for the Phase B kill-sheet authorization gate.

Coverage:
- KillSheetStore round-trips authorized kill sheets
- POST /api/v1/positions rejects without kill_sheet_id
- POST /api/v1/positions rejects with unknown / mismatched / REJECTED kill sheets
- POST /api/v1/positions rejects with mismatched ticker or direction
- POST /api/v1/positions accepts AUTHORIZED + matching kill sheet, records id
- bypass_kill_sheet=true requires non-empty notes (audit trail)
- GET /api/v1/kill_sheet/{id} returns the persisted sheet
- Cache.upsert_kill_sheet round-trips
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from kill_sheet.model import DisciplineAttestation, KillSheet
from kill_sheet.store import KillSheetStore
from positions import PositionStore
from storage.cache import Cache


# ── KillSheetStore ─────────────────────────────────────────────────────────


def _sample_authorized_kill_sheet(**overrides) -> KillSheet:
    base = dict(
        ticker="SPY",
        direction="long",
        intent="SWING",
        trigger_tf="Daily",
        bias="bullish",
        confidence="high",
        confidence_reason="full_bull stack + Stoch rising",
        account_key="main",
        account_name="Main",
        account_balance_usd=10000,
        risk_conviction="high",
        risk_pct=0.025,
        max_risk_usd=250,
        bar_date="2026-04-30",
        close_at_generation=580.0,
        sqn_value=1.2,
        regime="bull",
        ma_10=575, ma_20=570, ma_50=560, ma_200=540,
        ma_stack="full_bull",
        stoch_k=42.0, stoch_d=38.0,
        stoch_signal="rising", stoch_zone="neutral",
        status="AUTHORIZED",
        discipline_attestation=DisciplineAttestation(entry_authorized=True),
    )
    base.update(overrides)
    return KillSheet(**base)


def test_kill_sheet_store_save_load_roundtrip(tmp_path: Path):
    store = KillSheetStore(base_dir=tmp_path)
    ks = _sample_authorized_kill_sheet()
    store.save(ks)

    loaded = store.load(ks.id)
    assert loaded is not None
    assert loaded.id == ks.id
    assert loaded.ticker == "SPY"
    assert loaded.status == "AUTHORIZED"
    assert loaded.discipline_attestation is not None
    assert loaded.discipline_attestation.entry_authorized is True


def test_kill_sheet_store_load_missing_returns_none(tmp_path: Path):
    store = KillSheetStore(base_dir=tmp_path)
    assert store.load("does-not-exist") is None


def test_kill_sheet_store_load_corrupt_returns_none(tmp_path: Path):
    store = KillSheetStore(base_dir=tmp_path)
    (tmp_path / "abc.json").write_text("{ broken json")
    assert store.load("abc") is None


def test_kill_sheet_store_writes_are_atomic(tmp_path: Path):
    store = KillSheetStore(base_dir=tmp_path)
    ks = _sample_authorized_kill_sheet()
    store.save(ks)
    store.save(ks)
    siblings = list(tmp_path.iterdir())
    assert len(siblings) == 1
    assert siblings[0].name == f"{ks.id}.json"


def test_kill_sheet_store_writes_through_to_cache(tmp_path: Path):
    cache = Cache(path=tmp_path / "cache.sqlite")
    store = KillSheetStore(base_dir=tmp_path / "kill_sheets", cache=cache)
    ks = _sample_authorized_kill_sheet()
    store.save(ks)

    rows = cache.conn.execute("SELECT * FROM kill_sheets").fetchall()
    assert len(rows) == 1
    assert dict(rows[0])["id"] == ks.id
    cache.close()


# ── API authorization gate ─────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    """Test client with an isolated home directory so KillSheetStore() in
    the API uses a tempdir.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    # Force re-import of the path constants that bake in Path.home()
    import importlib
    import positions.store as ps_store
    import kill_sheet.store as ks_store
    importlib.reload(ps_store)
    importlib.reload(ks_store)

    pos_path = tmp_path / "positions.json"

    def _store_factory():
        return ps_store.PositionStore(path=pos_path)

    app = create_app(store_factory=_store_factory)
    yield TestClient(app)


def _persist_authorized(tmp_path: Path, **overrides) -> str:
    """Drop a fully-formed authorized kill sheet on disk and return its id."""
    ks = _sample_authorized_kill_sheet(**overrides)
    store = KillSheetStore(base_dir=tmp_path / ".trading-dashboard" / "kill_sheets")
    store.save(ks)
    return ks.id


def _persist_rejected(tmp_path: Path, **overrides) -> str:
    ks = _sample_authorized_kill_sheet(
        status="REJECTED", rejection_reason="counter-regime without thesis",
        discipline_attestation=DisciplineAttestation(entry_authorized=False),
        **overrides,
    )
    store = KillSheetStore(base_dir=tmp_path / ".trading-dashboard" / "kill_sheets")
    store.save(ks)
    return ks.id


def _open_payload(**overrides) -> dict:
    base = dict(
        ticker="SPY", direction="long", instrument="call", account="main",
        strike=580, expiry="2026-06-19", premium=5.50, contracts=1,
    )
    base.update(overrides)
    return base


def test_open_position_rejected_without_kill_sheet(client: TestClient):
    r = client.post("/api/v1/positions", json=_open_payload())
    assert r.status_code == 422
    assert "kill_sheet_id is required" in r.json()["detail"]


def test_open_position_rejected_with_unknown_kill_sheet_id(client, tmp_path):
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(kill_sheet_id="does-not-exist"),
    )
    assert r.status_code == 422
    assert "not found" in r.json()["detail"]


def test_open_position_accepts_rejected_kill_sheet_for_retrospective_review(client, tmp_path):
    """Per user intent (2026-05-10): a REJECTED kill sheet does NOT block
    position creation. The position is recorded with the kill_sheet_id
    attached so the per-trade discipline scorecard can flag the violation
    retrospectively. The journal must never be hard-blocked."""
    ks_id = _persist_rejected(tmp_path)
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(kill_sheet_id=ks_id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kill_sheet_id"] == ks_id


def test_open_position_rejected_when_ticker_mismatches(client, tmp_path):
    ks_id = _persist_authorized(tmp_path, ticker="SPY")
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(ticker="QQQ", kill_sheet_id=ks_id),
    )
    assert r.status_code == 422
    assert "ticker" in r.json()["detail"]


def test_open_position_rejected_when_direction_mismatches(client, tmp_path):
    ks_id = _persist_authorized(tmp_path, direction="long")
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(direction="short", kill_sheet_id=ks_id),
    )
    assert r.status_code == 422
    assert "direction" in r.json()["detail"]


def test_open_position_accepts_failed_attestation_for_retrospective_review(client, tmp_path):
    """Per user intent (2026-05-10): a kill sheet with §8 attestation
    failures (entry_authorized=False) does NOT block position creation.
    The kill sheet is attached to the position so the discipline
    scorecard can flag the violation later. The journal must never be
    hard-blocked."""
    ks_id = _persist_authorized(
        tmp_path,
        discipline_attestation=DisciplineAttestation(entry_authorized=False),
    )
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(kill_sheet_id=ks_id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kill_sheet_id"] == ks_id


def test_open_position_accepts_authorized_kill_sheet(client, tmp_path):
    ks_id = _persist_authorized(tmp_path)
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(kill_sheet_id=ks_id),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kill_sheet_id"] == ks_id


def test_bypass_requires_notes(client: TestClient):
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(bypass_kill_sheet=True),  # no notes
    )
    assert r.status_code == 422
    assert "notes" in r.json()["detail"]


def test_bypass_with_notes_succeeds(client: TestClient):
    r = client.post(
        "/api/v1/positions",
        json=_open_payload(
            bypass_kill_sheet=True,
            notes="emergency log of pre-existing trade",
        ),
    )
    assert r.status_code == 201
    assert r.json()["kill_sheet_id"] is None


def test_get_kill_sheet_by_id(client, tmp_path):
    ks_id = _persist_authorized(tmp_path)
    r = client.get(f"/api/v1/kill_sheet/{ks_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == ks_id
    assert body["status"] == "AUTHORIZED"


def test_get_kill_sheet_404_when_missing(client: TestClient):
    r = client.get("/api/v1/kill_sheet/does-not-exist")
    assert r.status_code == 404
