"""Tier 3 (Breadth) + Tier 4 (AI capex calendar) readers."""
from __future__ import annotations

from datetime import date

import pandas as pd

from regime_health.tier3_breadth import (
    assemble_tier3,
    read_iwm_spy_5d_slope,
    read_rsp_spy_5d_slope,
)
from regime_health.tier4_capex import (
    assemble_tier4,
    find_pending_capex_updates,
    read_buyer_aggregate,
    read_capex_calendar,
    read_private_flows,
    read_supplier_aggregate,
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
    """RSP -1%, SPY +0.7% over 5d → ratio drops ~1.7% → amber (≤ -1.5%, > -2.5%)."""
    rsp_closes = [202.0, 201.7, 201.3, 201.0, 200.7, 200.3, 200.0]
    spy_closes = [700.0, 701.0, 702.0, 703.0, 704.0, 705.0, 706.0]

    def fn(symbol, **_kw):
        return _bars(rsp_closes if symbol == "RSP" else spy_closes)

    r = read_rsp_spy_5d_slope(load_fn=fn)
    assert r.status == "amber"
    assert r.value is not None
    assert -2.5 < r.value <= -1.5


def test_rsp_spy_steep_negative_is_red():
    """RSP -3%, SPY +1% → ratio drops ~4% → red (≤ -2.5%)."""
    rsp_closes = [206.0, 205.5, 205.0, 204.0, 203.0, 202.0, 200.0]
    spy_closes = [700.0, 701.0, 702.0, 703.0, 704.0, 705.0, 707.0]

    def fn(symbol, **_kw):
        return _bars(rsp_closes if symbol == "RSP" else spy_closes)

    r = read_rsp_spy_5d_slope(load_fn=fn)
    assert r.status == "red"
    assert r.value is not None and r.value <= -2.5


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


def test_assemble_tier3_returns_two_readings():
    closes_by_symbol = {"RSP": [200.0] * 7, "SPY": [700.0] * 7, "IWM": [180.0] * 7}

    def fn(symbol, **_kw):
        return _bars(closes_by_symbol[symbol])

    bundle = assemble_tier3(load_fn=fn)
    assert bundle.tier == 3
    assert bundle.label == "Breadth"
    assert len(bundle.readings) == 2
    assert {r.indicator_id for r in bundle.readings} == {
        "rsp_spy_5d_slope",
        "iwm_spy_5d_slope",
    }
    assert bundle.error is None


def test_assemble_tier3_total_failure_sets_bundle_error():
    def boom(*_a, **_kw):
        raise RuntimeError("yfinance offline")
    bundle = assemble_tier3(load_fn=boom)
    assert bundle.error is not None
    assert "yfinance unavailable" in bundle.error


# ── Tier 3 — IWM/SPY 5d slope ────────────────────────────────────────────────


def test_iwm_spy_flat_is_green():
    """Both move in lockstep → ratio constant → 0% slope → green."""
    iwm_closes = [180.0 + i * 0.45 for i in range(7)]
    spy_closes = [720.0 + i * 1.80 for i in range(7)]  # ratio constant

    def fn(symbol, **_kw):
        return _bars(iwm_closes if symbol == "IWM" else spy_closes)

    r = read_iwm_spy_5d_slope(load_fn=fn)
    assert r.status == "green"
    assert r.indicator_id == "iwm_spy_5d_slope"
    assert abs(r.value or 0) < 0.01


def test_iwm_spy_modest_negative_is_amber():
    """Ratio drops ~2.3% over 5d → amber (≤ -2.0%, > -3.0%)."""
    iwm_closes = [184.0, 183.5, 182.5, 182.0, 181.5, 181.0, 180.0]
    spy_closes = [720.0, 721.0, 722.0, 723.0, 724.0, 725.0, 726.0]

    def fn(symbol, **_kw):
        return _bars(iwm_closes if symbol == "IWM" else spy_closes)

    r = read_iwm_spy_5d_slope(load_fn=fn)
    assert r.status == "amber"
    assert r.value is not None
    assert -3.0 < r.value <= -2.0


def test_iwm_spy_steep_negative_is_red():
    """IWM -4%, SPY +1% → ratio drops ~5% → red (≤ -3.0%)."""
    iwm_closes = [188.0, 187.0, 186.0, 184.0, 183.0, 181.0, 180.0]
    spy_closes = [720.0, 721.0, 722.0, 723.0, 724.0, 725.0, 727.0]

    def fn(symbol, **_kw):
        return _bars(iwm_closes if symbol == "IWM" else spy_closes)

    r = read_iwm_spy_5d_slope(load_fn=fn)
    assert r.status == "red"
    assert r.value is not None and r.value <= -3.0


def test_iwm_spy_load_failure_is_error():
    def boom(*_a, **_kw):
        raise RuntimeError("yfinance dead")
    r = read_iwm_spy_5d_slope(load_fn=boom)
    assert r.status == "error"
    assert "yfinance dead" in (r.error or "")


def test_iwm_spy_too_few_bars_is_unknown():
    def fn(*_a, **_kw):
        return _bars([100.0, 101.0])
    r = read_iwm_spy_5d_slope(load_fn=fn)
    assert r.status == "unknown"


# ── Tier 4 — buyer cohort aggregate ──────────────────────────────────────────


def test_buyer_aggregate_no_config_is_unknown():
    r = read_buyer_aggregate(config=None)
    assert r.status == "unknown"
    assert "No regime_health.capex" in (r.error or "")


def test_buyer_aggregate_zero_cuts_is_green():
    cfg = {
        "buyers": {
            "tickers": ["MSFT", "GOOGL", "META", "AMZN", "ORCL"],
            "directions": {
                "MSFT": "raised", "GOOGL": "raised", "META": "held",
                "AMZN": "raised", "ORCL": "raised",
            },
        },
    }
    r = read_buyer_aggregate(config=cfg)
    assert r.status == "green"
    assert r.value == 0
    assert "0 cut" in r.formatted_value


def test_buyer_aggregate_one_cut_is_amber():
    cfg = {
        "buyers": {
            "directions": {
                "MSFT": "raised", "GOOGL": "raised", "META": "cut",
                "AMZN": "held", "ORCL": "raised",
            },
        },
    }
    r = read_buyer_aggregate(config=cfg)
    assert r.status == "amber"
    assert r.value == 1


def test_buyer_aggregate_three_cuts_is_red():
    cfg = {
        "buyers": {
            "directions": {
                "MSFT": "cut", "GOOGL": "cut", "META": "cut",
                "AMZN": "held", "ORCL": "raised",
            },
        },
    }
    r = read_buyer_aggregate(config=cfg)
    assert r.status == "red"
    assert r.value == 3


def test_buyer_aggregate_handles_missing_tickers():
    """Tickers without explicit direction count as unknown. 0 cuts → green."""
    cfg = {"buyers": {"directions": {"MSFT": "raised"}}}
    r = read_buyer_aggregate(config=cfg)
    assert r.status == "green"
    assert "4 pending" in r.formatted_value


def test_buyer_aggregate_invalid_direction_treated_as_unknown():
    cfg = {"buyers": {"directions": {"MSFT": "garbage"}}}
    r = read_buyer_aggregate(config=cfg)
    assert r.status == "green"  # garbage → unknown → no cuts → green


def test_buyer_aggregate_missing_buyers_block_uses_defaults():
    """When cfg has no 'buyers' block, defaults apply and all are unknown."""
    cfg = {"suppliers": {}}  # buyers missing entirely
    r = read_buyer_aggregate(config=cfg)
    assert r.status == "green"  # 0 cuts (all 5 default to unknown)
    assert "5 pending" in r.formatted_value


# ── Tier 4 — supplier cohort aggregate ───────────────────────────────────────


def test_supplier_aggregate_zero_cuts_is_green():
    cfg = {
        "suppliers": {
            "directions": {
                "NVDA": "raised", "AVGO": "raised", "TSM": "held",
                "ASML": "raised", "MU": "raised",
            },
        },
    }
    r = read_supplier_aggregate(config=cfg)
    assert r.status == "green"
    assert r.value == 0


def test_supplier_aggregate_two_cuts_is_amber():
    cfg = {
        "suppliers": {
            "directions": {
                "NVDA": "cut", "AVGO": "cut", "TSM": "raised",
                "ASML": "held", "MU": "raised",
            },
        },
    }
    r = read_supplier_aggregate(config=cfg)
    assert r.status == "amber"
    assert r.value == 2


def test_supplier_aggregate_independent_of_buyer_cohort():
    """Buyer cuts shouldn't bleed into supplier aggregate (and vice versa)."""
    cfg = {
        "buyers": {"directions": {t: "cut" for t in
                                   ["MSFT", "GOOGL", "META", "AMZN", "ORCL"]}},
        "suppliers": {"directions": {t: "raised" for t in
                                      ["NVDA", "AVGO", "TSM", "ASML", "MU"]}},
    }
    buyer = read_buyer_aggregate(config=cfg)
    supplier = read_supplier_aggregate(config=cfg)
    assert buyer.status == "red"
    assert supplier.status == "green"


# ── Tier 4 — calendar (merged across cohorts) ────────────────────────────────


def test_capex_calendar_no_config_is_unknown():
    r = read_capex_calendar(config=None)
    assert r.status == "unknown"


def test_capex_calendar_merges_buyers_and_suppliers():
    today = date(2026, 5, 5)
    cfg = {
        "buyers": {
            "next_prints": {
                "MSFT": "2026-04-29",   # past — excluded
                "GOOGL": "2026-07-30",  # upcoming
            },
        },
        "suppliers": {
            "next_prints": {
                "NVDA": "2026-05-21",   # upcoming
                "META": "2026-09-30",   # >90d out — excluded
            },
        },
    }
    r = read_capex_calendar(config=cfg, today=today)
    assert r.status == "green"
    assert "NVDA 2026-05-21" in r.formatted_value
    assert "GOOGL 2026-07-30" in r.formatted_value
    assert "MSFT 2026-04-29" not in r.formatted_value
    assert "META 2026-09-30" not in r.formatted_value


def test_capex_calendar_no_upcoming_is_unknown():
    today = date(2026, 5, 5)
    cfg = {"buyers": {"next_prints": {"MSFT": "2025-12-01"}}}
    r = read_capex_calendar(config=cfg, today=today)
    assert r.status == "unknown"


def test_capex_calendar_skips_invalid_dates():
    today = date(2026, 5, 5)
    cfg = {
        "suppliers": {
            "next_prints": {
                "NVDA": "2026-05-21",
                "GOOGL": "not-a-date",
            },
        },
    }
    r = read_capex_calendar(config=cfg, today=today)
    assert "NVDA" in r.formatted_value
    assert "GOOGL" not in r.formatted_value


# ── Tier 4 — private flows ───────────────────────────────────────────────────


def test_private_flows_no_config_is_unknown():
    r = read_private_flows(config=None)
    assert r.status == "unknown"


def test_private_flows_no_entries_logged_is_unknown():
    cfg = {"private_flows": {"entries": []}}
    r = read_private_flows(config=cfg, today=date(2026, 5, 5))
    assert r.status == "unknown"
    assert "No private flow entries logged" in r.formatted_value


def test_private_flows_entries_outside_window_is_unknown():
    """Entries exist but all older than lookback_days → unknown + dated message."""
    cfg = {
        "private_flows": {
            "lookback_days": 90,
            "entries": [
                {"lab": "OpenAI", "amount_usd": 10_000_000_000,
                 "date": "2025-01-01"},
            ],
        },
    }
    r = read_private_flows(config=cfg, today=date(2026, 5, 5))
    assert r.status == "unknown"
    assert "trailing 90d" in r.formatted_value


def test_private_flows_aggregates_in_window():
    cfg = {
        "private_flows": {
            "lookback_days": 90,
            "entries": [
                {"lab": "OpenAI", "amount_usd": 40_000_000_000,
                 "date": "2026-04-01"},  # 34 days ago — in window
                {"lab": "Anthropic", "amount_usd": 8_000_000_000,
                 "date": "2026-03-15"},  # 51 days ago — in window
                {"lab": "xAI", "amount_usd": 6_000_000_000,
                 "date": "2025-12-01"},  # >90d ago — out of window
            ],
        },
    }
    r = read_private_flows(config=cfg, today=date(2026, 5, 5))
    assert r.status == "green"
    assert r.value == 48_000_000_000
    assert "$48.0B" in r.formatted_value
    assert "2 entries" in r.formatted_value


def test_private_flows_skips_malformed_entries():
    cfg = {
        "private_flows": {
            "lookback_days": 90,
            "entries": [
                {"lab": "OpenAI", "amount_usd": "garbage", "date": "2026-04-01"},
                {"lab": "Anthropic", "amount_usd": 5_000_000_000, "date": "not-a-date"},
                {"lab": "xAI", "amount_usd": 7_000_000_000, "date": "2026-04-10"},
            ],
        },
    }
    r = read_private_flows(config=cfg, today=date(2026, 5, 5))
    assert r.status == "green"
    # OpenAI: amount_usd "garbage" → float("garbage") raises → skipped.
    # Anthropic: bad date → skipped.
    # xAI alone: $7B.
    assert r.value == 7_000_000_000


def test_private_flows_default_lookback_is_90_days():
    """When lookback_days is missing, falls back to 90."""
    cfg = {
        "private_flows": {
            "entries": [
                {"lab": "OpenAI", "amount_usd": 1_000_000_000,
                 "date": "2026-02-15"},  # 79 days ago — should be in window
            ],
        },
    }
    r = read_private_flows(config=cfg, today=date(2026, 5, 5))
    assert r.status == "green"


# ── Tier 4 assembly ──────────────────────────────────────────────────────────


def test_assemble_tier4_returns_four_readings():
    cfg = {
        "buyers": {"directions": {"MSFT": "raised"}},
        "suppliers": {"directions": {"NVDA": "raised"}},
        "private_flows": {"entries": []},
    }
    today = date(2026, 5, 5)
    bundle = assemble_tier4(config=cfg, today=today)
    assert bundle.tier == 4
    assert len(bundle.readings) == 4
    assert bundle.error is None
    ids = [r.indicator_id for r in bundle.readings]
    assert ids == [
        "ai_capex_buyer_aggregate",
        "ai_capex_supplier_aggregate",
        "ai_capex_calendar",
        "ai_capex_private_flows",
    ]


def test_assemble_tier4_no_config_sets_bundle_error():
    bundle = assemble_tier4(config=None)
    assert bundle.error is not None
    assert "Tier 4 capex calendar not configured" in bundle.error


# ── Pending capex updates (cross-cohort) ─────────────────────────────────────


def test_pending_updates_no_config_is_empty():
    assert find_pending_capex_updates(config=None) == []


def test_pending_updates_no_next_prints_is_empty():
    cfg = {"buyers": {"directions": {"NVDA": "unknown"}}}
    assert find_pending_capex_updates(config=cfg, today=date(2026, 5, 5)) == []


def test_pending_updates_flags_past_print_with_unknown_direction():
    today = date(2026, 5, 5)
    cfg = {
        "suppliers": {
            "next_prints": {"NVDA": "2026-04-29"},
            "directions": {"NVDA": "unknown"},
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert pending == [
        {"ticker": "NVDA", "print_date": "2026-04-29", "cohort": "suppliers"}
    ]


def test_pending_updates_tags_each_entry_with_cohort():
    today = date(2026, 5, 5)
    cfg = {
        "buyers": {
            "next_prints": {"MSFT": "2026-04-29"},
            "directions": {"MSFT": "unknown"},
        },
        "suppliers": {
            "next_prints": {"NVDA": "2026-04-15"},
            "directions": {"NVDA": "unknown"},
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    cohorts = {p["ticker"]: p["cohort"] for p in pending}
    assert cohorts == {"MSFT": "buyers", "NVDA": "suppliers"}


def test_pending_updates_skips_when_direction_logged():
    today = date(2026, 5, 5)
    cfg = {
        "buyers": {
            "next_prints": {"MSFT": "2026-04-29"},
            "directions": {"MSFT": "raised"},
        },
    }
    assert find_pending_capex_updates(config=cfg, today=today) == []


def test_pending_updates_skips_future_dates():
    today = date(2026, 5, 5)
    cfg = {
        "suppliers": {
            "next_prints": {"NVDA": "2026-05-21"},
            "directions": {"NVDA": "unknown"},
        },
    }
    assert find_pending_capex_updates(config=cfg, today=today) == []


def test_pending_updates_includes_today():
    today = date(2026, 5, 5)
    cfg = {
        "suppliers": {
            "next_prints": {"NVDA": "2026-05-05"},
            "directions": {"NVDA": "unknown"},
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "NVDA"


def test_pending_updates_sorted_oldest_first_across_cohorts():
    today = date(2026, 5, 5)
    cfg = {
        "buyers": {
            "next_prints": {"MSFT": "2026-03-15", "META": "2026-04-15"},
            "directions": {"MSFT": "unknown", "META": "unknown"},
        },
        "suppliers": {
            "next_prints": {"NVDA": "2026-04-30"},
            "directions": {"NVDA": "unknown"},
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    tickers = [p["ticker"] for p in pending]
    assert tickers == ["MSFT", "META", "NVDA"]


def test_pending_updates_skips_invalid_date():
    today = date(2026, 5, 5)
    cfg = {
        "buyers": {
            "next_prints": {"MSFT": "2026-04-15", "GOOGL": "garbage"},
            "directions": {"MSFT": "unknown", "GOOGL": "unknown"},
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert [p["ticker"] for p in pending] == ["MSFT"]


def test_pending_updates_treats_missing_direction_as_unknown():
    today = date(2026, 5, 5)
    cfg = {
        "suppliers": {
            "next_prints": {"NVDA": "2026-04-29"},
            "directions": {},
        },
    }
    pending = find_pending_capex_updates(config=cfg, today=today)
    assert len(pending) == 1
    assert pending[0]["ticker"] == "NVDA"
