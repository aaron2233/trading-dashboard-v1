"""Snapshot assembler + freshness check."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from regime_health.model import RegimeHealthSnapshot
from regime_health.snapshot import (
    assemble_snapshot,
    is_snapshot_fresh,
)


def _scan_row(
    *,
    sqn_regime: str = "bull",
    sqn20_diagnostic: str = "regime aligned",
    stack_state: str = "full_bull",
) -> dict:
    return {
        "ticker": "SPY", "timeframe": "1d", "bar_date": "2026-05-04",
        "close": 580.0,
        "ma_ribbon": {
            "ma_10": 580, "ma_20": 575, "ma_50": 560, "ma_200": 540,
            "stack_state": stack_state,
        },
        "stochastic": {"k": 50, "d": 48, "zone": "mid", "signal": "neutral"},
        "sqn": {
            "sqn_value": 1.1, "regime": sqn_regime,
            "sqn_20_value": 0.8, "regime_20": "bull",
            "diagnostic": sqn20_diagnostic,
        },
    }


def _bars(close: float, n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": [close] * n, "high": [close] * n, "low": [close] * n,
        "close": [close] * n, "volume": [1_000_000] * n,
    }, index=idx)


def _fred_payload(rows: list[dict]) -> dict:
    return {"observations": rows}


def _all_green_fred_fetch(url: str) -> dict:
    """Return green-band FRED data for any series_id."""
    if "DTWEXBGS" in url:
        # Stable dollar — 90 obs around 120 with tiny drift = ~+0.7% over 3mo
        return _fred_payload([
            {"date": "2026-04-30", "value": str(120.0 - i * 0.01)}
            for i in range(90)
        ])
    if "BAMLH0A0HYM2" in url:
        return _fred_payload([{"date": "2026-04-30", "value": "3.20"}])  # 320 bps
    if "T10Y2Y" in url or "T10Y3M" in url:
        return _fred_payload([{"date": "2026-04-30", "value": "0.80"}])
    if "T5YIE" in url:
        return _fred_payload([{"date": "2026-04-30", "value": "2.40"}])
    return _fred_payload([])


# ── Assembly ─────────────────────────────────────────────────────────────────


def test_assemble_returns_all_four_tiers():
    snap = assemble_snapshot(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(15.0),
        fetch=_all_green_fred_fetch,
        api_key="k",
    )
    assert isinstance(snap, RegimeHealthSnapshot)
    tiers = {t.tier for t in snap.tiers}
    assert tiers == {1, 2, 3, 4}
    tier3 = next(t for t in snap.tiers if t.tier == 3)
    tier4 = next(t for t in snap.tiers if t.tier == 4)
    assert tier3.label == "Breadth"
    assert tier4.label == "AI Capex Calendar"
    # Sprint 3 wires both — readings are present (status varies per the
    # provided load_fn / config, but the bundle is no longer a stub).
    assert len(tier3.readings) >= 1
    assert len(tier4.readings) >= 1


def test_assemble_all_green_overall_is_green():
    snap = assemble_snapshot(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(15.0),  # VIX=15 → green
        fetch=_all_green_fred_fetch,
        api_key="k",
    )
    assert snap.overall_status == "green"
    assert snap.overall_drivers == []


def test_assemble_one_amber_flips_overall_amber():
    snap = assemble_snapshot(
        # Neutral SQN regime → amber
        scan_fn=lambda t, **kw: _scan_row(sqn_regime="neutral"),
        load_fn=lambda *a, **k: _bars(15.0),
        fetch=_all_green_fred_fetch,
        api_key="k",
    )
    assert snap.overall_status == "amber"
    # Drivers are the indicator labels at the worst tier-1/2 status
    assert any("SQN(100)" in d for d in snap.overall_drivers)


def test_assemble_one_red_flips_overall_red():
    snap = assemble_snapshot(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(30.0),  # VIX=30 → red
        fetch=_all_green_fred_fetch,
        api_key="k",
    )
    assert snap.overall_status == "red"
    assert "VIX" in snap.overall_drivers


def test_assemble_unknown_does_not_drag_overall():
    """Failing readings (unknown/error) must not flip overall to red. They
    fail-open — overall reflects the best observable state."""

    def boom_scan(_t, _tf):
        raise RuntimeError("scan_ticker offline")

    snap = assemble_snapshot(
        scan_fn=boom_scan,
        load_fn=lambda *a, **k: _bars(15.0),
        fetch=_all_green_fred_fetch,
        api_key="k",
    )
    # All scan-based Tier 1 readings are error; VIX/VVIX + Tier 2 are green.
    assert snap.overall_status == "green"


def test_assemble_no_fred_key_does_not_break_snapshot(monkeypatch):
    """Without a FRED key, Tier 2 yields all-unknown — overall status
    falls back to whatever Tier 1 reports."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    snap = assemble_snapshot(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(15.0),
    )
    # Tier 1 is fully green → overall green even with no Tier 2 data.
    assert snap.overall_status == "green"
    tier2 = next(t for t in snap.tiers if t.tier == 2)
    assert all(r.status == "unknown" for r in tier2.readings)
    assert "FRED API key not configured" in (tier2.error or "")


def test_assemble_passes_through_snapshot_date():
    snap = assemble_snapshot(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(15.0),
        fetch=_all_green_fred_fetch,
        api_key="k",
        snapshot_date="2026-05-05",
    )
    assert snap.snapshot_date == "2026-05-05"


def test_to_dict_round_trippable():
    snap = assemble_snapshot(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(15.0),
        fetch=_all_green_fred_fetch,
        api_key="k",
    )
    d = snap.to_dict()
    assert d["overall_status"] == "green"
    assert "tiers" in d
    assert len(d["tiers"]) == 4
    assert all("readings" in t for t in d["tiers"])
    # pending_capex_updates is always present (empty list when no config)
    assert "pending_capex_updates" in d
    assert isinstance(d["pending_capex_updates"], list)


# ── Freshness ────────────────────────────────────────────────────────────────


def _snap_with_fetched_at(iso_ts: str) -> RegimeHealthSnapshot:
    return RegimeHealthSnapshot(
        snapshot_date="2026-05-05",
        fetched_at=iso_ts,
        overall_status="green",
        tiers=[],
        overall_drivers=[],
    )


def test_freshness_recent_is_fresh():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    assert is_snapshot_fresh(_snap_with_fetched_at(now)) is True


def test_freshness_stale_is_not_fresh():
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00",
    )
    assert is_snapshot_fresh(_snap_with_fetched_at(long_ago)) is False


def test_freshness_custom_max_age():
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1, minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00",
    )
    assert is_snapshot_fresh(_snap_with_fetched_at(one_hour_ago), max_age_hours=2) is True
    assert is_snapshot_fresh(_snap_with_fetched_at(one_hour_ago), max_age_hours=0.5) is False


def test_freshness_unparseable_timestamp_is_not_fresh():
    assert is_snapshot_fresh(_snap_with_fetched_at("garbage")) is False
    assert is_snapshot_fresh(_snap_with_fetched_at("")) is False
