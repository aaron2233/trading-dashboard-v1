"""RegimeHealthStore — JSON canonical + optional SQLite cache write-through."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from regime_health.model import (
    IndicatorReading,
    RegimeHealthSnapshot,
    TierBundle,
)
from regime_health.store import RegimeHealthStore


def _make_snapshot(snapshot_date: str = "2026-05-05") -> RegimeHealthSnapshot:
    tier1 = TierBundle(
        tier=1, label="Structural & Volatility",
        readings=[
            IndicatorReading(
                indicator_id="vix", label="VIX", tier=1,
                status="green", value=15.5, formatted_value="15.50",
                threshold_note="amber>=18 / red>=25 pts", source="yfinance",
            ),
            IndicatorReading(
                indicator_id="spy_sqn_100", label="SPY SQN(100)", tier=1,
                status="amber", value="neutral", formatted_value="neutral",
                threshold_note="green=Bull/Strong Bull, amber=Neutral, red=Bear",
                source="scan_ticker",
            ),
        ],
    )
    tier2 = TierBundle(tier=2, label="Macro (FRED)", readings=[])
    tier3 = TierBundle(tier=3, label="Breadth", readings=[])
    tier4 = TierBundle(tier=4, label="AI Capex Calendar", readings=[])
    return RegimeHealthSnapshot(
        snapshot_date=snapshot_date,
        fetched_at="2026-05-05T09:30:00+00:00",
        overall_status="amber",
        tiers=[tier1, tier2, tier3, tier4],
        overall_drivers=["SPY SQN(100)"],
    )


# ── Save / load roundtrip ────────────────────────────────────────────────────


def test_save_writes_json_file(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    snap = _make_snapshot("2026-05-05")
    path = store.save(snap)

    assert path.exists()
    data = json.loads(path.read_text())
    assert data["snapshot_date"] == "2026-05-05"
    assert data["overall_status"] == "amber"
    assert data["overall_drivers"] == ["SPY SQN(100)"]
    assert len(data["tiers"]) == 4


def test_load_for_date_roundtrip(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    snap = _make_snapshot("2026-05-05")
    store.save(snap)

    loaded = store.load_for_date("2026-05-05")
    assert loaded is not None
    assert loaded.snapshot_date == "2026-05-05"
    assert loaded.overall_status == "amber"
    assert len(loaded.tiers) == 4
    # Tier 1 readings preserved
    tier1 = loaded.tiers[0]
    assert tier1.tier == 1
    assert len(tier1.readings) == 2
    assert tier1.readings[0].indicator_id == "vix"
    assert tier1.readings[0].value == 15.5


def test_load_for_date_returns_none_when_missing(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    assert store.load_for_date("2026-04-15") is None


def test_load_for_date_returns_none_for_corrupt_file(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    p = store.base_dir / "2026-05-05.json"
    p.write_text("{not valid json")
    assert store.load_for_date("2026-05-05") is None


def test_save_overwrites_same_day(tmp_path: Path):
    """Force-refresh during the day should overwrite, not append."""
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    snap1 = _make_snapshot("2026-05-05")
    snap1.overall_status = "green"
    store.save(snap1)

    snap2 = _make_snapshot("2026-05-05")
    snap2.overall_status = "red"
    store.save(snap2)

    files = list((tmp_path / "rh").glob("*.json"))
    assert len(files) == 1  # one file, not two
    loaded = store.load_for_date("2026-05-05")
    assert loaded.overall_status == "red"


# ── load_today / list_recent ─────────────────────────────────────────────────


def test_load_today_uses_today_date(tmp_path: Path, monkeypatch):
    """load_today() uses date.today() — verify by saving with today's date."""
    from datetime import date
    today = date.today().isoformat()
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    store.save(_make_snapshot(today))
    loaded = store.load_today()
    assert loaded is not None
    assert loaded.snapshot_date == today


def test_load_today_returns_none_when_no_today(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    # Save a snapshot for an arbitrary historical date
    store.save(_make_snapshot("2024-01-15"))
    assert store.load_today() is None


def test_list_recent_orders_newest_first(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    store.save(_make_snapshot("2026-05-01"))
    store.save(_make_snapshot("2026-05-05"))
    store.save(_make_snapshot("2026-05-03"))

    recent = store.list_recent(limit=10)
    dates = [s.snapshot_date for s in recent]
    assert dates == ["2026-05-05", "2026-05-03", "2026-05-01"]


def test_list_recent_respects_limit(tmp_path: Path):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    for d in ["2026-05-01", "2026-05-02", "2026-05-03"]:
        store.save(_make_snapshot(d))
    recent = store.list_recent(limit=2)
    assert len(recent) == 2
    assert recent[0].snapshot_date == "2026-05-03"


def test_list_recent_skips_corrupt_files(tmp_path: Path, caplog):
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    store.save(_make_snapshot("2026-05-01"))
    store.save(_make_snapshot("2026-05-03"))
    # Drop a corrupt file in the middle
    (tmp_path / "rh" / "2026-05-02.json").write_text("not json at all")

    recent = store.list_recent(limit=10)
    dates = {s.snapshot_date for s in recent}
    assert dates == {"2026-05-01", "2026-05-03"}


def test_list_recent_empty_dir(tmp_path: Path):
    """Brand new install — directory exists but nothing to list."""
    store = RegimeHealthStore(base_dir=tmp_path / "rh")
    assert store.list_recent(limit=30) == []


# ── Cache write-through ──────────────────────────────────────────────────────


def test_save_with_cache_writes_through(tmp_path: Path):
    """When a cache is supplied, save() should upsert via the cache too."""
    captured = {"calls": 0, "payloads": []}

    class FakeCache:
        def upsert_regime_health_snapshot(self, payload):
            captured["calls"] += 1
            captured["payloads"].append(payload)

    store = RegimeHealthStore(base_dir=tmp_path / "rh", cache=FakeCache())
    snap = _make_snapshot("2026-05-05")
    store.save(snap)

    assert captured["calls"] == 1
    assert captured["payloads"][0]["snapshot_date"] == "2026-05-05"


def test_save_succeeds_even_when_cache_fails(tmp_path: Path, caplog):
    """Broken cache must not block JSON write — durability invariant."""

    class BrokenCache:
        def upsert_regime_health_snapshot(self, _payload):
            raise RuntimeError("simulated cache fault")

    store = RegimeHealthStore(base_dir=tmp_path / "rh", cache=BrokenCache())
    import logging
    with caplog.at_level(logging.ERROR, logger="regime_health.store"):
        store.save(_make_snapshot("2026-05-05"))

    # JSON file still wrote
    fresh = RegimeHealthStore(base_dir=tmp_path / "rh")
    assert fresh.load_for_date("2026-05-05") is not None
    # The fault was logged
    assert any("cache upsert failed" in r.message for r in caplog.records)
