"""Tests for the free-range scanner (Sprint C, orchestrator rule 12).

Covers:
- Universe + ETF identification
- Price-band filter ($15-50 single stock, ETF exempt)
- Indicator scoring + best-direction picker
- Snapshot construction (score floor, ETF tagging, blocker propagation)
- 3-phase scanner orchestrator (baseline + user + free-range)
- Hard 5-cap with no-padding semantics
- API integration via FastAPI TestClient

NOTE: Options-liquidity gating is intentionally NOT here. yfinance options
data is stale relative to brokerage feeds; auto-gating on it would smuggle
bad data into a discipline-engine claim. Options input lives at the
kill-sheet layer (paste from brokerage / screenshot extract) — see
src/options_input/ and src/vision/options_extractor.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi.testclient import TestClient

from api.app import create_app
from free_range import (
    NASDAQ_100,
    NASDAQ_100_SNAPSHOT_DATE,
    PRICE_MAX_SINGLE_STOCK,
    PRICE_MIN_SINGLE_STOCK,
    best_direction,
    build_snapshot,
    build_why_now,
    free_range_universe,
    is_etf,
    price_band_violation,
    run_free_range_scan,
    score_direction,
)
from free_range.scanner import _tier_tag


# ─────────────────────────────────────────────────────────────────────────
# Helpers — synthetic scan rows for filter / scorer tests
# ─────────────────────────────────────────────────────────────────────────


def make_row(
    ticker: str = "AAPL",
    close: float = 30.0,
    stack: str = "full_bull",
    stoch_zone: str = "neutral",
    stoch_signal: str | None = None,
    sqn_regime: str = "bull",
    sqn_20: str = "bull",
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "timeframe": "1d",
        "bar_date": "2026-05-01",
        "close": close,
        "ma_ribbon": {"stack_state": stack},
        "stochastic": {"zone": stoch_zone, "signal": stoch_signal},
        "sqn": {"regime": sqn_regime, "regime_20": sqn_20, "sqn_value": 1.2},
    }


# ─────────────────────────────────────────────────────────────────────────
# Universe + ETF identification
# ─────────────────────────────────────────────────────────────────────────


def test_nasdaq_100_snapshot_dated():
    assert NASDAQ_100_SNAPSHOT_DATE
    # Should be ISO-shaped YYYY-MM-DD
    datetime.strptime(NASDAQ_100_SNAPSHOT_DATE, "%Y-%m-%d")


def test_nasdaq_100_contains_anchors():
    """Sanity — the most-traded names should be present."""
    expected = {"AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOG", "TSLA"}
    assert expected.issubset(set(NASDAQ_100))


def test_is_etf_recognises_known_etfs():
    assert is_etf("QQQ")
    assert is_etf("GLD")
    assert is_etf("spy")  # case-insensitive
    assert not is_etf("AAPL")
    assert not is_etf("NVDA")


def test_free_range_universe_excludes_provided():
    universe = free_range_universe(frozenset({"AAPL", "MSFT"}))
    assert "AAPL" not in universe
    assert "MSFT" not in universe
    # Other tickers preserved
    assert "NVDA" in universe


# ─────────────────────────────────────────────────────────────────────────
# Price-band filter
# ─────────────────────────────────────────────────────────────────────────


def test_price_band_accepts_single_stock_in_range():
    assert price_band_violation("AAPL", 25.0) is None
    assert price_band_violation("AAPL", PRICE_MIN_SINGLE_STOCK) is None
    assert price_band_violation("AAPL", PRICE_MAX_SINGLE_STOCK) is None


def test_price_band_rejects_below_floor():
    # Floor lowered from $15 → $10 on 2026-05-14; test value at $8 to stay
    # comfortably under the new floor without coupling to the exact constant.
    msg = price_band_violation("AAPL", 8.0)
    assert msg is not None
    assert "floor" in msg


def test_price_band_rejects_above_cap():
    msg = price_band_violation("NVDA", 500.0)
    assert msg is not None
    assert "cap" in msg


def test_price_band_exempts_etfs():
    """ETFs at any price are accepted per orchestrator account profile."""
    assert price_band_violation("SPY", 600.0) is None
    assert price_band_violation("GLD", 5.0) is None
    assert price_band_violation("QQQ", 480.0) is None


def test_price_band_rejects_missing_price():
    assert price_band_violation("AAPL", None) == "no current price"


# ─────────────────────────────────────────────────────────────────────────
# Indicator scoring
# ─────────────────────────────────────────────────────────────────────────


def test_score_long_in_strong_bull_regime():
    row = make_row(stack="full_bull", stoch_signal="bull_cross_oversold", sqn_regime="strong_bull")
    score, blockers = score_direction(row, "long")
    assert score > 0
    assert blockers == []


def test_score_short_in_strong_bear_regime():
    row = make_row(stack="full_bear", stoch_signal="bear_cross_overbought", sqn_regime="strong_bear")
    score, blockers = score_direction(row, "short")
    assert score > 0
    assert blockers == []


def test_score_blocked_when_ma_tangled():
    row = make_row(stack="tangled", sqn_regime="bull")
    score, blockers = score_direction(row, "long")
    assert any("MA tangle" in b for b in blockers)


def test_score_blocked_when_regime_opposes():
    """SQN(100) strong-bear opposes a long — blocker should fire."""
    row = make_row(stack="full_bull", sqn_regime="strong_bear")
    _, blockers = score_direction(row, "long")
    assert any("opposes long" in b for b in blockers)


def test_best_direction_picks_higher_score():
    """A strong-bull stack + bull regime should pick long over short."""
    row = make_row(stack="full_bull", sqn_regime="strong_bull",
                   stoch_signal="bull_cross_oversold")
    direction, score, _ = best_direction(row)
    assert direction == "long"
    assert score > 0


def test_best_direction_picks_short_in_bear():
    row = make_row(stack="full_bear", sqn_regime="strong_bear",
                   stoch_signal="bear_cross_overbought")
    direction, score, _ = best_direction(row)
    assert direction == "short"
    assert score > 0


# ─────────────────────────────────────────────────────────────────────────
# Why-now string
# ─────────────────────────────────────────────────────────────────────────


def test_why_now_includes_signal_when_present():
    row = make_row(stoch_signal="bull_cross_oversold")
    s = build_why_now("long", row)
    assert "LONG" in s
    assert "bull cross oversold" in s


def test_why_now_falls_back_to_zone():
    row = make_row(stoch_signal=None, stoch_zone="oversold")
    s = build_why_now("long", row)
    assert "oversold" in s


# ─────────────────────────────────────────────────────────────────────────
# Snapshot builder
# ─────────────────────────────────────────────────────────────────────────


def test_build_snapshot_marks_etf():
    row = make_row(ticker="QQQ", close=480.0, stack="full_bull",
                   stoch_signal="bull_cross_oversold", sqn_regime="bull")
    snap = build_snapshot("QQQ", "baseline", row)
    assert snap is not None
    assert snap.is_etf is True
    assert any("ETF" in n for n in snap.notes)


def test_build_snapshot_returns_none_below_floor():
    """Free-range candidate with weak score should be dropped."""
    row = make_row(stack="compression", stoch_signal=None, sqn_regime="neutral")
    snap = build_snapshot("AAPL", "free_range", row)
    assert snap is None


def test_build_snapshot_user_phase_keeps_low_score():
    """User-submitted bypasses the free-range floor — surface anyway."""
    row = make_row(stack="compression", stoch_signal=None, sqn_regime="neutral")
    snap = build_snapshot("AAPL", "user", row)
    assert snap is not None
    assert snap.phase == "user"


def test_build_snapshot_propagates_blockers():
    row = make_row(stack="tangled")
    snap = build_snapshot("AAPL", "user", row)
    assert snap is not None
    assert any("MA tangle" in n for n in snap.notes)


def test_tier_tag_strong_setup_dual_tagged():
    row = make_row(stack="full_bull", stoch_signal="bull_cross_oversold", sqn_regime="bull")
    assert _tier_tag("long", row) == "1+2"


def test_tier_tag_weak_setup_tier_2_only():
    row = make_row(stack="compression", stoch_signal=None, sqn_regime="neutral")
    assert _tier_tag("long", row) == "2"


# ─────────────────────────────────────────────────────────────────────────
# 3-phase scanner orchestrator
# ─────────────────────────────────────────────────────────────────────────


def make_scan_fn(mapping: dict[str, dict[str, Any]]):
    """Build a scan_fn that returns rows from `mapping`, raising for unknown tickers.

    Legacy single-arg signature kept for back-compat with tests that
    don't care about the action-gate verdict path. The scanner
    `_attach_lotto_verdict` helper TypeError-catches this signature
    and skips verdict computation, leaving snap.action_verdict = None.
    """
    def fn(ticker: str) -> dict[str, Any]:
        t = ticker.upper()
        if t not in mapping:
            raise ValueError(f"unknown ticker {t}")
        return mapping[t]
    return fn


def make_multi_tf_scan_fn(mapping: dict[tuple[str, str], dict[str, Any]]):
    """scan_fn that routes by (ticker, timeframe). Used for tests that
    DO want the action-gate verdict computed."""
    def fn(ticker: str, *, timeframe: str = "1d") -> dict[str, Any]:
        key = (ticker.upper(), timeframe)
        if key not in mapping:
            raise ValueError(f"unknown read for {key}")
        return mapping[key]
    return fn


def test_scanner_baseline_always_includes_qqq_gld():
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull",
                        stoch_signal="bull_cross_oversold", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull",
                        stoch_signal="bull_cross_oversold", sqn_regime="strong_bull"),
    }
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=(),  # empty universe — only baseline + user fire
    )
    tickers = [s.ticker for s in scan.baseline]
    assert "QQQ" in tickers
    assert "GLD" in tickers


def test_scanner_user_phase_includes_submitted():
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
        "AAPL": make_row("AAPL", close=200.0, stack="compression", sqn_regime="neutral"),
    }
    scan = run_free_range_scan(
        user_tickers=["AAPL"],
        scan_fn=make_scan_fn(rows),
        universe_override=(),
    )
    user_tickers = [s.ticker for s in scan.user_submitted]
    assert "AAPL" in user_tickers
    # AAPL above price cap but user-submitted — should still surface
    assert scan.user_submitted[0].current_price == 200.0


def test_scanner_user_phase_skips_baseline_duplicates():
    """User submits QQQ → already in baseline, should not re-appear in user phase."""
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
    }
    scan = run_free_range_scan(
        user_tickers=["QQQ"],
        scan_fn=make_scan_fn(rows),
        universe_override=(),
    )
    assert scan.user_submitted == []


def test_scanner_free_range_applies_price_band():
    """Universe ticker AMZN at $200 should be filtered out by price band."""
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
        "AMZN": make_row("AMZN", close=200.0, stack="full_bull",
                         stoch_signal="bull_cross_oversold", sqn_regime="bull"),
    }
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=("AMZN",),
    )
    assert scan.free_range == []
    assert "AMZN" in scan.errors


def test_scanner_free_range_top_n_hard_cap():
    """7 qualifying candidates → cap to 5, no padding required."""
    universe = ("T1", "T2", "T3", "T4", "T5", "T6", "T7")
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
    }
    # All 7 candidates pass with progressively lower scores so ordering is deterministic
    for i, t in enumerate(universe):
        rows[t] = make_row(
            t,
            close=20.0 + i,
            stack="full_bull" if i < 5 else "bull_developing",
            stoch_signal="bull_cross_oversold",
            sqn_regime="bull",
        )
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=universe,
        free_range_cap=5,
    )
    assert len(scan.free_range) == 5
    # Scores should be sorted desc
    scores = [s.score for s in scan.free_range]
    assert scores == sorted(scores, reverse=True)


def test_scanner_free_range_no_padding_when_fewer_pass():
    """Only 2 candidates pass — return 2, with explicit note saying so."""
    universe = ("T1", "T2", "T3")
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
        "T1": make_row("T1", close=20.0, stack="full_bull",
                       stoch_signal="bull_cross_oversold", sqn_regime="bull"),
        "T2": make_row("T2", close=25.0, stack="full_bull",
                       stoch_signal="bull_cross_oversold", sqn_regime="bull"),
        # T3 is in the band and qualifies but we'll fail it via stack=tangled
        "T3": make_row("T3", close=30.0, stack="tangled", sqn_regime="bull"),
    }
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=universe,
        free_range_cap=5,
    )
    assert len(scan.free_range) == 2
    # Padding-prohibited message must surface
    assert any("Padding" in n or "padding" in n for n in scan.notes)


def test_scanner_universe_size_reflects_actual_universe():
    universe = ("T1", "T2", "T3")
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
        "T1": make_row("T1", close=20.0, stack="compression", sqn_regime="neutral"),
        "T2": make_row("T2", close=25.0, stack="compression", sqn_regime="neutral"),
        "T3": make_row("T3", close=30.0, stack="compression", sqn_regime="neutral"),
    }
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=universe,
    )
    assert scan.universe_size == 3


def test_scanner_enable_free_range_false_skips_phase_3():
    """When enable_free_range=False, scanner returns baseline + user only."""
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull",
                        stoch_signal="bull_cross_oversold", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull",
                        stoch_signal="bull_cross_oversold", sqn_regime="bull"),
        "AAPL": make_row("AAPL", close=200.0, stack="full_bull",
                         stoch_signal="bull_cross_oversold", sqn_regime="bull"),
    }
    universe = ("AAPL",)  # would otherwise produce one Phase 3 result
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=universe,
        enable_free_range=False,
    )
    # Baseline still scanned (QQQ + GLD)
    assert len(scan.baseline) == 2
    # Free-range Phase 3 skipped entirely
    assert scan.free_range == []
    assert scan.universe_size == 0
    # Note explains why
    assert any("Phase 3 skipped" in n for n in scan.notes)


def test_api_free_range_scan_passes_enable_free_range_through(monkeypatch):
    """API endpoint forwards enable_free_range to the scanner."""
    captured: dict[str, Any] = {}

    def fake_run_free_range_scan(user_tickers=None, **kwargs):
        captured.update(kwargs)
        from free_range.snapshot import FreeRangeScan
        return FreeRangeScan(scan_time_utc="2026-05-13T00:00:00+00:00")

    monkeypatch.setattr("api.app.run_free_range_scan", fake_run_free_range_scan)

    from fastapi.testclient import TestClient
    from api.app import create_app
    client = TestClient(create_app())
    resp = client.post("/api/v1/free-range-scan",
                       json={"enable_free_range": False})
    assert resp.status_code == 200
    assert captured.get("enable_free_range") is False


def test_scanner_serialises_to_dict():
    """Smoke test — full scan result round-trips through to_dict()."""
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
    }
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=(),
    )
    d = scan.to_dict()
    assert "scan_time_utc" in d
    assert "baseline" in d
    assert "free_range" in d
    assert d["free_range_cap"] == 5
    # Snapshot must NOT carry liquid_options or iv_status — those were stripped
    # along with the yfinance options gate.
    if d["baseline"]:
        snap = d["baseline"][0]
        assert "liquid_options" not in snap
        assert "iv_status" not in snap


# ─────────────────────────────────────────────────────────────────────────
# API integration
# ─────────────────────────────────────────────────────────────────────────


def test_api_free_range_scan_endpoint(monkeypatch):
    """End-to-end: POST /api/v1/free-range-scan returns a structured response."""
    rows = {
        "QQQ": make_row("QQQ", close=480.0, stack="full_bull", sqn_regime="bull"),
        "GLD": make_row("GLD", close=180.0, stack="full_bull", sqn_regime="bull"),
        "AAPL": make_row("AAPL", close=200.0, stack="full_bull",
                         stoch_signal="bull_cross_oversold", sqn_regime="bull"),
    }

    def fake_run_free_range_scan(user_tickers=None, **kwargs):
        from free_range.scanner import run_free_range_scan as real
        return real(
            user_tickers=user_tickers,
            scan_fn=make_scan_fn(rows),
            universe_override=(),
            **{k: v for k, v in kwargs.items() if k in ("free_range_cap",)},
        )

    monkeypatch.setattr("api.app.run_free_range_scan", fake_run_free_range_scan)

    app = create_app()
    client = TestClient(app)
    resp = client.post(
        "/api/v1/free-range-scan",
        json={"user_tickers": ["AAPL"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "scan_time_utc" in body
    assert len(body["baseline"]) == 2
    assert len(body["user_submitted"]) == 1
    assert body["user_submitted"][0]["ticker"] == "AAPL"
    # Free-range universe is empty → empty list + a "padding forbidden" note
    assert body["free_range"] == []


def test_api_free_range_scan_handles_scanner_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("yfinance dead")
    monkeypatch.setattr("api.app.run_free_range_scan", boom)

    app = create_app()
    client = TestClient(app)
    resp = client.post("/api/v1/free-range-scan", json={})
    assert resp.status_code == 502
    assert "yfinance dead" in resp.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────
# Action gate verdict integration (Phase 2)
# ─────────────────────────────────────────────────────────────────────────


def test_legacy_single_arg_scan_fn_skips_verdict():
    """Legacy single-arg fixtures still work; verdict stays None."""
    rows = {"QQQ": make_row("QQQ", close=480.0, stack="full_bull",
                             stoch_signal="bull_cross_oversold", sqn_regime="bull"),
            "GLD": make_row("GLD", close=180.0, stack="full_bull",
                             stoch_signal="bull_cross_oversold", sqn_regime="strong_bull")}
    scan = run_free_range_scan(
        scan_fn=make_scan_fn(rows),
        universe_override=(),
    )
    for snap in scan.baseline:
        assert snap.action_verdict is None


def test_multi_tf_scan_fn_attaches_enter_now_verdict():
    """When 2H read available + trigger fires, verdict is enter_now."""
    daily = make_row("QQQ", close=480.0, stack="full_bull",
                     stoch_signal="neutral", sqn_regime="bull")
    two_h = make_row("QQQ", close=480.0, stack="full_bull",
                     stoch_zone="oversold",
                     stoch_signal="bull_cross_oversold",
                     sqn_regime="bull")
    gld_daily = make_row("GLD", close=180.0, stack="full_bull",
                          stoch_signal="neutral", sqn_regime="bull")
    gld_two_h = make_row("GLD", close=180.0, stack="full_bull",
                          stoch_zone="oversold",
                          stoch_signal="bull_cross_oversold",
                          sqn_regime="bull")
    scan = run_free_range_scan(
        scan_fn=make_multi_tf_scan_fn({
            ("QQQ", "1d"): daily, ("QQQ", "2h"): two_h,
            ("GLD", "1d"): gld_daily, ("GLD", "2h"): gld_two_h,
        }),
        universe_override=(),
    )
    qqq = next(s for s in scan.baseline if s.ticker == "QQQ")
    assert qqq.action_verdict is not None
    assert qqq.action_verdict["state"] == "enter_now"
    assert qqq.action_verdict["skill"] == "lotto-options"
    assert "BUY CALLS" in qqq.action_verdict["headline"]


def test_multi_tf_scan_fn_attaches_disqualified_verdict_for_2h_chop():
    """2H stack is chop = disqualified verdict attached to snapshot."""
    daily = make_row("QQQ", close=480.0, stack="full_bull",
                     stoch_signal="bull_cross_oversold", sqn_regime="bull")
    two_h_chop = make_row("QQQ", close=480.0, stack="chop",
                           stoch_signal="neutral", sqn_regime="bull")
    scan = run_free_range_scan(
        user_tickers=[],
        scan_fn=make_multi_tf_scan_fn({
            ("QQQ", "1d"): daily, ("QQQ", "2h"): two_h_chop,
            # GLD missing → its own scan_fn ValueError, ignored
        }),
        universe_override=(),
    )
    qqq = next((s for s in scan.baseline if s.ticker == "QQQ"), None)
    assert qqq is not None
    assert qqq.action_verdict is not None
    assert qqq.action_verdict["state"] == "disqualified"


def test_multi_tf_scan_fn_2h_failure_leaves_verdict_none():
    """2H scan failure shouldn't break the candidate; verdict stays None."""
    daily = make_row("QQQ", close=480.0, stack="full_bull",
                     stoch_signal="bull_cross_oversold", sqn_regime="bull")

    def fn(ticker: str, *, timeframe: str = "1d") -> dict[str, Any]:
        if timeframe == "2h":
            raise RuntimeError("yfinance dead on 2h")
        if ticker.upper() == "QQQ":
            return daily
        raise ValueError(f"unknown ticker {ticker}")

    scan = run_free_range_scan(
        scan_fn=fn,
        universe_override=(),
    )
    qqq = next((s for s in scan.baseline if s.ticker == "QQQ"), None)
    assert qqq is not None
    # Daily snapshot succeeded, verdict couldn't be computed
    assert qqq.action_verdict is None
