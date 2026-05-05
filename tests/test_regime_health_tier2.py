"""Tier 2 (FRED macro) readers."""
from __future__ import annotations

from regime_health.tier2_macro import (
    SERIES_DTWEXBGS,
    SERIES_HY_OAS,
    SERIES_T5YIE,
    SERIES_T10Y2Y,
    SERIES_T10Y3M,
    assemble_tier2,
    read_2s10s,
    read_3m10s,
    read_5y_breakeven,
    read_broad_dollar,
    read_hy_oas,
)


def _payload(rows: list[dict]) -> dict:
    return {
        "observations": rows,
        "count": len(rows),
        "limit": len(rows),
    }


def _fetch_for(series_to_rows: dict[str, list[dict]]):
    """Build a fake fetch that routes by series_id substring in URL."""

    def fake_fetch(url: str) -> dict:
        for series_id, rows in series_to_rows.items():
            if f"series_id={series_id}" in url:
                return _payload(rows)
        return _payload([])

    return fake_fetch


# ── HY OAS ───────────────────────────────────────────────────────────────────


def test_hy_oas_compressed_is_green():
    """4.50% pct → 450 bps → ⚠️ between amber (350) and red (500) → amber.
    Use a comfortably-low value to confirm green band."""
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "3.20"}])
    r = read_hy_oas(fetch=fetch, api_key="k")
    assert r.value == 320.0  # 3.20% → 320 bps
    assert r.status == "green"
    assert r.indicator_id == "hy_oas"


def test_hy_oas_amber_band():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "4.00"}])
    r = read_hy_oas(fetch=fetch, api_key="k")
    assert r.value == 400.0
    assert r.status == "amber"


def test_hy_oas_red_above_500bps():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "5.50"}])
    r = read_hy_oas(fetch=fetch, api_key="k")
    assert r.value == 550.0
    assert r.status == "red"


def test_hy_oas_key_not_configured(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    r = read_hy_oas()
    assert r.status == "unknown"
    assert "not configured" in (r.error or "")


def test_hy_oas_fred_error_yields_error_status():
    def boom(url: str) -> dict:
        return {"error_code": 400, "error_message": "Bad request"}
    r = read_hy_oas(fetch=boom, api_key="k")
    assert r.status == "error"
    assert "Bad request" in (r.error or "")


# ── 2s10s curve ──────────────────────────────────────────────────────────────


def test_2s10s_positive_is_green():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "0.45"}])
    r = read_2s10s(fetch=fetch, api_key="k")
    assert r.status == "green"
    assert r.value == 0.45


def test_2s10s_inverted_is_amber():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "-0.12"}])
    r = read_2s10s(fetch=fetch, api_key="k")
    assert r.status == "amber"


def test_2s10s_zero_is_amber():
    """Boundary: exactly zero is treated as inverted (no positive carry)."""
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "0.00"}])
    r = read_2s10s(fetch=fetch, api_key="k")
    assert r.status == "amber"


# ── 3m10s curve ──────────────────────────────────────────────────────────────


def test_3m10s_positive_is_green():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "1.20"}])
    r = read_3m10s(fetch=fetch, api_key="k")
    assert r.status == "green"
    assert r.indicator_id == "t10y3m_curve"


def test_3m10s_inverted_is_amber():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "-0.40"}])
    r = read_3m10s(fetch=fetch, api_key="k")
    assert r.status == "amber"


# ── 5Y breakeven ─────────────────────────────────────────────────────────────


def test_breakeven_in_green_band():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "2.40"}])
    r = read_5y_breakeven(fetch=fetch, api_key="k")
    assert r.status == "green"


def test_breakeven_amber_below_band():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "1.85"}])
    r = read_5y_breakeven(fetch=fetch, api_key="k")
    assert r.status == "amber"


def test_breakeven_amber_above_band():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "2.85"}])
    r = read_5y_breakeven(fetch=fetch, api_key="k")
    assert r.status == "amber"


def test_breakeven_red_for_reflation():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "3.80"}])
    r = read_5y_breakeven(fetch=fetch, api_key="k")
    assert r.status == "red"


def test_breakeven_red_for_deflation():
    fetch = lambda url: _payload([{"date": "2026-04-30", "value": "1.30"}])
    r = read_5y_breakeven(fetch=fetch, api_key="k")
    assert r.status == "red"


# ── Broad dollar (3mo % change) ──────────────────────────────────────────────


def test_broad_dollar_stable_is_green():
    """Newest = 120, oldest (3mo back) = 119 → +0.84% → green."""
    rows = [{"date": "2026-04-30", "value": str(120.0 - i * 0.0125)} for i in range(90)]
    r = read_broad_dollar(fetch=lambda url: _payload(rows), api_key="k")
    assert r.status == "green"
    assert abs(r.value - 120.0) < 0.001


def test_broad_dollar_modest_surge_is_amber():
    """Newest 120, oldest 113 → +6.2% → amber."""
    new = 120.0
    old = 113.0
    rows = []
    for i in range(90):
        v = new - (new - old) * (i / 89.0)
        rows.append({"date": "2026-04-30", "value": str(v)})
    r = read_broad_dollar(fetch=lambda url: _payload(rows), api_key="k")
    assert r.status == "amber"


def test_broad_dollar_big_surge_is_red():
    """Newest 130, oldest 115 → +13% → red (>+10%)."""
    new = 130.0
    old = 115.0
    rows = []
    for i in range(90):
        v = new - (new - old) * (i / 89.0)
        rows.append({"date": "2026-04-30", "value": str(v)})
    r = read_broad_dollar(fetch=lambda url: _payload(rows), api_key="k")
    assert r.status == "red"


def test_broad_dollar_no_observations_is_unknown():
    r = read_broad_dollar(fetch=lambda url: _payload([]), api_key="k")
    assert r.status == "unknown"


# ── Tier assembly ────────────────────────────────────────────────────────────


def test_assemble_tier2_returns_five_readings():
    rows_hy = [{"date": "2026-04-30", "value": "3.20"}]                  # green
    rows_2s10s = [{"date": "2026-04-30", "value": "0.45"}]               # green
    rows_3m10s = [{"date": "2026-04-30", "value": "1.20"}]               # green
    rows_breakeven = [{"date": "2026-04-30", "value": "2.40"}]           # green
    # Stable dollar — generate 90 obs around 120 with tiny drift.
    rows_dollar = [{"date": "2026-04-30", "value": str(120.0 - i * 0.01)} for i in range(90)]

    fetch = _fetch_for({
        SERIES_HY_OAS: rows_hy,
        SERIES_T10Y2Y: rows_2s10s,
        SERIES_T10Y3M: rows_3m10s,
        SERIES_T5YIE: rows_breakeven,
        SERIES_DTWEXBGS: rows_dollar,
    })

    bundle = assemble_tier2(fetch=fetch, api_key="k")
    assert bundle.tier == 2
    assert bundle.label == "Macro (FRED)"
    assert len(bundle.readings) == 5
    ids = {r.indicator_id for r in bundle.readings}
    assert ids == {
        "hy_oas", "t10y2y_curve", "t10y3m_curve",
        "t5yie_breakeven", "broad_dollar",
    }
    assert bundle.error is None
    # All happy-path readings should be green
    assert all(r.status == "green" for r in bundle.readings)


def test_assemble_tier2_no_key_sets_bundle_error(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    bundle = assemble_tier2()
    # All 5 readings emit "key not configured" unknowns
    assert all(r.status == "unknown" for r in bundle.readings)
    assert bundle.error is not None
    assert "FRED API key not configured" in bundle.error


def test_assemble_tier2_partial_failure_does_not_set_bundle_error():
    """One reader's FRED error must not flip the whole bundle to error."""
    rows_hy = [{"date": "2026-04-30", "value": "3.20"}]
    rows_2s10s = [{"date": "2026-04-30", "value": "0.45"}]
    rows_breakeven = [{"date": "2026-04-30", "value": "2.40"}]
    rows_dollar = [{"date": "2026-04-30", "value": str(120.0 - i * 0.01)} for i in range(90)]

    def fetch(url: str) -> dict:
        if f"series_id={SERIES_T10Y3M}" in url:
            return {"error_code": 503, "error_message": "service down"}
        if f"series_id={SERIES_HY_OAS}" in url:
            return _payload(rows_hy)
        if f"series_id={SERIES_T10Y2Y}" in url:
            return _payload(rows_2s10s)
        if f"series_id={SERIES_T5YIE}" in url:
            return _payload(rows_breakeven)
        if f"series_id={SERIES_DTWEXBGS}" in url:
            return _payload(rows_dollar)
        return _payload([])

    bundle = assemble_tier2(fetch=fetch, api_key="k")
    statuses = [r.status for r in bundle.readings]
    assert "error" in statuses
    assert "green" in statuses
    assert bundle.error is None  # not all readers failed
