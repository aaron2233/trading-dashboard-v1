"""Tier 3 (Breadth) + Tier 4 (AI capex calendar) readers."""
from __future__ import annotations

from datetime import date

import pandas as pd

from regime_health.tier3_breadth import (
    assemble_tier3,
    read_rsp_spy_5d_slope,
)
from regime_health.tier4_capex import (
    assemble_tier4,
    find_pending_capex_updates,
    read_capex_aggregate,
    read_capex_calendar,
)


def _bars(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    idx = pd.date_range("2026-04-25", periods=n, freq="D")
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1_000_000] * n,
    }, index=idx)


# ── Tier 3 — RSP/SPY 5d slope ────────────────────────────────────────────────


def test_rsp_spy_flat_breadth_is_green():
    """Both indices climb in lockstep → ratio constant → 0% slope → green."""
    rsp_closes = [200.0 + i * 0.5 for i in range(7)]   # 200..203
    spy_closes = [700.0 + i * 1.75 for i in range(7)]  # ratio constant

    def fn(symbol, **_kw):
        return _bars(rsp_closes if symbol == "RSP" else spy_closes)

    r = read_rsp_spy_5d_slope(load_fn=fn)
    assert r.status == "green"
    assert r.indicator_id == "rsp_spy_5d_slope"
    assert abs(r.value or 0) < 0.01


def test_rsp_spy_modest_negative_is_amber():
    """RSP flat, SPY ~+0.7% over 5d → ratio falls ~0.7% → amber (slope < -0.5%)."""
    rsp_closes = [200.0] * 7
    # 5 days ago index = -6 = spy_closes[1] = 701; latest = 706 → +0.71%
    spy_closes = [700.0, 701.0, 702.0, 703.0, 704.0, 705.0, 706.0]

    def fn(symbol, **_kw):
        return _bars(rsp_closes if symbol == "RSP" else spy_closes)

    r = read_rsp_spy_5d_slope(load_fn=fn)
    assert r.status == "amber"
    assert r.value is not None and r.value < -0.5


def test_rsp_spy_steep_negative_is_red():
    """RSP -3%, SPY +1% → ratio drops ~4% → red."""
    rsp_closes = [206.0, 205.5, 205.0, 204.0, 203.0, 202.0, 200.0]
    spy_closes = [700.0, 701.0, 702.0, 703.0, 704.0, 705.0, 707.0]

    def fn(symbol, **_kw):
        return _bars(rsp_closes if symbol == "RSP" else spy_closes)

    r = read_rsp_spy_5d_slope(load_fn=fn)
    assert r.status == "red"


def test_rsp_spy_load_failure_is_error():
    def boom(*_a, **_kw):
        raise RuntimeError("yfinance dead")
    r = read_rsp_spy_5d_slope(load_fn=boom)
    assert r.status == "error"
    assert "yfinance dead" in (r.error or "")


def test_rsp_spy_too_few_bars_is_unknown():
    def fn(*_a, **_kw):
        return _bars([100.0, 101.0])  # only 2 bars
    r = read_rsp_spy_5d_slope(load_fn=fn)
    assert r.status == "unknown"


def test_assemble_tier3_returns_one_reading():
    rsp_closes = [200.0] * 7
    spy_closes = [700.0] * 7

    def fn(symbol, **_kw):
        return _bars(rsp_closes if symbol == "RSP" else spy_closes)

    bundle = assemble_tier3(load_fn=fn)
    assert bundle.tier == 3
    assert bundle.label == "Breadth"
    assert len(bundle.readings) == 1
    assert bundle.error is None


def test_assemble_tier3_total_failure_sets_bundle_error():
    def boom(*_a, **_kw):
        raise RuntimeError("yfinance offline")
    bundle = assemble_tier3(load_fn=boom)
    assert bundle.error is not None
    assert "yfinance unavailable" in bundle.error


# ── Tier 4 — capex aggregate ─────────────────────────────────────────────────


def test_capex_aggregate_no_config_is_unknown():
    r = read_capex_aggregate(config=None)
    assert r.status == "unknown"
    assert "No regime_health.capex" in (r.error or "")


def test_capex_aggregate_zero_cuts_is_green():
    cfg = {
        "tickers": ["MSFT", "GOOGL", "META", "AMZN", "NVDA"],
        "directions": {
            "MSFT": "raised", "GOOGL": "raised", "META": "held",
            "AMZN": "raised", "NVDA": "raised",
        },
    }
    r = read_capex_aggregate(config=cfg)
    assert r.status == "green"
    assert r.value == 0
    assert "0 cut" in r.formatted_value


def test_capex_aggregate_one_cut_is_amber():
    cfg = {
        "directions": {
            "MSFT": "raised", "GOOGL": "raised", "META": "cut",
            "AMZN": "held", "NVDA": "raised",
        },
    }
    r = read_capex_aggregate(config=cfg)
    assert r.status == "amber"
    assert r.value == 1


def test_capex_aggregate_three_cuts_is_red():
    cfg = {
        "directions": {
            "MSFT": "cut", "GOOGL": "cut", "META": "cut",
            "AMZN": "held", "NVDA": "raised",
        },
    }
    r = read_capex_aggregate(config=cfg)
    assert r.status == "red"
    assert r.value == 3


def test_capex_aggregate_handles_missing_tickers():
    """Tickers without an explicit direction count as unknown (not toward
    cuts). 0 cuts across 5 → green."""
    cfg = {
        "directions": {"MSFT": "raised"},  # only 1 of 5 logged
    }
    r = read_capex_aggregate(config=cfg)
    assert r.status == "green"
    assert "4 pending" in r.formatted_value


def test_capex_aggregate_invalid_direction_treated_as_unknown():
    cfg = {
        "directions": {"MSFT": "garbage"},
    }
    r = read_capex_aggregate(config=cfg)
    # Unknown directions don't count as cuts
    assert r.status == "green"


# ── Tier 4 — calendar ────────────────────────────────────────────────────────


def test_capex_calendar_no_config_is_unknown():
    r = read_capex_calendar(config=None)
    assert r.status == "unknown"


def test_capex_calendar_lists_upcoming_dates():
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {
            "NVDA": "2026-05-21",
            "MSFT": "2026-04-29",  # past — excluded
            "GOOGL": "2026-07-30",
            "META": "2026-09-30",  # >90d out — excluded
        },
    }
    r = read_capex_calendar(config=cfg, today=today)
    assert r.status == "green"
    # 2 upcoming: NVDA 5-21, GOOGL 7-30
    assert "NVDA 2026-05-21" in r.formatted_value
    assert "GOOGL 2026-07-30" in r.formatted_value
    # MSFT past, META beyond 90d
    assert "MSFT 2026-04-29" not in r.formatted_value
    assert "META 2026-09-30" not in r.formatted_value


def test_capex_calendar_no_upcoming_is_unknown():
    today = date(2026, 5, 5)
    cfg = {"next_prints": {"MSFT": "2025-12-01"}}
    r = read_capex_calendar(config=cfg, today=today)
    assert r.status == "unknown"


def test_capex_calendar_skips_invalid_dates():
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {
            "NVDA": "2026-05-21",
            "GOOGL": "not-a-date",  # skipped silently
        },
    }
    r = read_capex_calendar(config=cfg, today=today)
    assert "NVDA" in r.formatted_value
    assert "GOOGL" not in r.formatted_value


# ── Tier 4 assembly ──────────────────────────────────────────────────────────


def test_assemble_tier4_returns_two_readings():
    cfg = {
        "directions": {"MSFT": "raised"},
        "next_prints": {"NVDA": "2026-05-21"},
    }
    today = date(2026, 5, 5)
    bundle = assemble_tier4(config=cfg, today=today)
    assert bundle.tier == 4
    assert len(bundle.readings) == 2
    assert bundle.error is None


def test_assemble_tier4_no_config_sets_bundle_error():
    bundle = assemble_tier4(config=None)
    assert bundle.error is not None
    assert "Tier 4 capex calendar not configured" in bundle.error


# ── Pending capex updates ────────────────────────────────────────────────────


def test_pending_updates_no_config_is_empty():
    assert find_pending_capex_updates(config=None) == []


def test_pending_updates_no_next_prints_is_empty():
    cfg = {"directions": {"NVDA": "unknown"}}
    assert find_pending_capex_updates(config=cfg, today=date(2026, 5, 5)) == []


def test_pending_updates_flags_past_print_with_unknown_direction():
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {"NVDA": "2026-04-29"},  # past
        "directions": {"NVDA": "unknown"},
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert pending == [{"ticker": "NVDA", "print_date": "2026-04-29"}]


def test_pending_updates_skips_when_direction_logged():
    """If user has flipped directions[X] to raised/held/cut, no reminder."""
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {"NVDA": "2026-04-29"},
        "directions": {"NVDA": "raised"},
    }
    assert find_pending_capex_updates(config=cfg, today=today) == []


def test_pending_updates_skips_future_dates():
    """Dates in the future aren't pending — they're upcoming (calendar reading)."""
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {"NVDA": "2026-05-21"},
        "directions": {"NVDA": "unknown"},
    }
    assert find_pending_capex_updates(config=cfg, today=today) == []


def test_pending_updates_includes_today():
    """Boundary: a print dated today is treated as past (it has happened
    by the time the user is opening the dashboard later in the day)."""
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {"NVDA": "2026-05-05"},
        "directions": {"NVDA": "unknown"},
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "NVDA"


def test_pending_updates_sorted_oldest_first():
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {
            "NVDA": "2026-04-30",
            "MSFT": "2026-03-15",
            "META":  "2026-04-15",
        },
        "directions": {
            "NVDA": "unknown", "MSFT": "unknown", "META": "unknown",
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    tickers = [p["ticker"] for p in pending]
    assert tickers == ["MSFT", "META", "NVDA"]


def test_pending_updates_skips_invalid_date():
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {"NVDA": "garbage", "MSFT": "2026-04-15"},
        "directions": {"NVDA": "unknown", "MSFT": "unknown"},
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert [p["ticker"] for p in pending] == ["MSFT"]


def test_pending_updates_treats_missing_direction_as_unknown():
    """Ticker in next_prints but absent from directions → unknown by default."""
    today = date(2026, 5, 5)
    cfg = {
        "next_prints": {"NVDA": "2026-04-29"},
        "directions": {},
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "NVDA"
