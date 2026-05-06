"""Tier 1 (Structural & Volatility) readers — every reader hits scan_ticker
or yfinance; tests inject mocks via the scan_fn / load_fn parameters so
nothing touches the network."""
from __future__ import annotations

import pandas as pd

from regime_health.tier1_market import (
    assemble_tier1,
    read_sqn_for_ticker,
    read_sqn20_diagnostic,
    read_vix,
    read_vvix,
    read_weekly_ma_for_ticker,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _scan_row(
    *,
    ticker: str = "SPY",
    timeframe: str = "1d",
    sqn_regime: str = "bull",
    sqn_value: float = 1.1,
    sqn20_regime: str = "bull",
    sqn20_value: float = 0.8,
    diagnostic: str = "regime aligned, healthy trend",
    stack_state: str = "full_bull",
) -> dict:
    return {
        "ticker": ticker,
        "timeframe": timeframe,
        "bar_date": "2026-05-04",
        "close": 580.0,
        "ma_ribbon": {
            "ma_10": 580.0, "ma_20": 575.0, "ma_50": 560.0, "ma_200": 540.0,
            "stack_state": stack_state,
        },
        "stochastic": {"k": 50, "d": 48, "zone": "mid", "signal": "neutral"},
        "sqn": {
            "sqn_value": sqn_value, "regime": sqn_regime,
            "sqn_20_value": sqn20_value, "regime_20": sqn20_regime,
            "diagnostic": diagnostic,
        },
    }


def _bars(close: float, n: int = 5) -> pd.DataFrame:
    idx = pd.date_range("2026-05-01", periods=n, freq="D")
    return pd.DataFrame({
        "open": [close] * n, "high": [close] * n, "low": [close] * n,
        "close": [close] * n, "volume": [1_000_000] * n,
    }, index=idx)


# ── SQN(100) reader ─────────────────────────────────────────────────────────


def test_read_sqn_bull_is_green():
    r = read_sqn_for_ticker("SPY", scan_fn=lambda t, **kw: _scan_row(sqn_regime="bull"))
    assert r.status == "green"
    assert r.tier == 1
    assert r.indicator_id == "spy_sqn_100"
    assert r.value == 1.1


def test_read_sqn_neutral_is_amber():
    r = read_sqn_for_ticker("QQQ", scan_fn=lambda t, **kw: _scan_row(sqn_regime="neutral"))
    assert r.status == "amber"
    assert r.indicator_id == "qqq_sqn_100"


def test_read_sqn_bear_is_red():
    r = read_sqn_for_ticker("SPY", scan_fn=lambda t, **kw: _scan_row(sqn_regime="strong_bear"))
    assert r.status == "red"


def test_read_sqn_failure_yields_error_status():
    def boom(*_args, **_kw):
        raise RuntimeError("yfinance dead")
    r = read_sqn_for_ticker("SPY", scan_fn=boom)
    assert r.status == "error"
    assert "yfinance dead" in (r.error or "")


# ── Weekly MA reader ─────────────────────────────────────────────────────────


def test_read_weekly_ma_full_bull_is_green():
    r = read_weekly_ma_for_ticker("SPY", scan_fn=lambda t, **kw: _scan_row(stack_state="full_bull"))
    assert r.status == "green"
    assert r.value == "full_bull"
    assert r.indicator_id == "spy_weekly_ma"


def test_read_weekly_ma_compression_is_amber():
    r = read_weekly_ma_for_ticker("QQQ", scan_fn=lambda t, **kw: _scan_row(stack_state="compression"))
    assert r.status == "amber"


def test_read_weekly_ma_full_bear_is_red():
    r = read_weekly_ma_for_ticker("SPY", scan_fn=lambda t, **kw: _scan_row(stack_state="full_bear"))
    assert r.status == "red"


def test_read_weekly_ma_unknown_state_is_unknown():
    r = read_weekly_ma_for_ticker("SPY", scan_fn=lambda t, **kw: _scan_row(stack_state="something_new"))
    assert r.status == "unknown"


# ── SQN(20) diagnostic reader ────────────────────────────────────────────────


def test_sqn20_diagnostic_aligned_is_green():
    r = read_sqn20_diagnostic(
        "SPY",
        scan_fn=lambda t, **kw: _scan_row(diagnostic="regime aligned, healthy trend"),
    )
    assert r.status == "green"


def test_sqn20_diagnostic_divergence_is_amber():
    r = read_sqn20_diagnostic(
        "SPY",
        scan_fn=lambda t, **kw: _scan_row(diagnostic="diverging — early shift signal"),
    )
    assert r.status == "amber"


def test_sqn20_diagnostic_extreme_is_amber():
    r = read_sqn20_diagnostic(
        "QQQ",
        scan_fn=lambda t, **kw: _scan_row(diagnostic="extreme reading — chase risk"),
    )
    assert r.status == "amber"


def test_sqn20_diagnostic_capitulation_is_amber():
    r = read_sqn20_diagnostic(
        "SPY",
        scan_fn=lambda t, **kw: _scan_row(diagnostic="capitulation reset inside Bull"),
    )
    assert r.status == "amber"


def test_sqn20_diagnostic_missing_is_unknown():
    r = read_sqn20_diagnostic(
        "SPY",
        scan_fn=lambda t, **kw: _scan_row(diagnostic=""),
    )
    assert r.status == "unknown"


# ── VIX / VVIX readers ───────────────────────────────────────────────────────


def test_vix_below_amber_is_green():
    r = read_vix(load_fn=lambda *a, **k: _bars(15.0))
    assert r.status == "green"
    assert r.value == 15.0
    assert r.indicator_id == "vix"


def test_vix_in_amber_band():
    r = read_vix(load_fn=lambda *a, **k: _bars(20.0))
    assert r.status == "amber"


def test_vix_above_red_is_red():
    r = read_vix(load_fn=lambda *a, **k: _bars(30.0))
    assert r.status == "red"


def test_vvix_below_amber_is_green():
    r = read_vvix(load_fn=lambda *a, **k: _bars(85.0))
    assert r.status == "green"


def test_vvix_in_amber_band():
    r = read_vvix(load_fn=lambda *a, **k: _bars(105.0))
    assert r.status == "amber"


def test_vvix_above_red_is_red():
    r = read_vvix(load_fn=lambda *a, **k: _bars(120.0))
    assert r.status == "red"


def test_vix_empty_bars_yields_unknown():
    r = read_vix(load_fn=lambda *a, **k: pd.DataFrame())
    assert r.status == "unknown"
    assert "no bars" in (r.error or "")


def test_vvix_load_failure_yields_error():
    def boom(*_a, **_kw):
        raise RuntimeError("network dead")
    r = read_vvix(load_fn=boom)
    assert r.status == "error"
    assert "network dead" in (r.error or "")


# ── Tier assembly ────────────────────────────────────────────────────────────


def test_assemble_tier1_returns_eight_readings():
    bundle = assemble_tier1(
        scan_fn=lambda t, **kw: _scan_row(),
        load_fn=lambda *a, **k: _bars(16.0),
    )
    assert bundle.tier == 1
    assert bundle.label == "Structural & Volatility"
    assert len(bundle.readings) == 8
    ids = {r.indicator_id for r in bundle.readings}
    assert ids == {
        "spy_sqn_100", "qqq_sqn_100",
        "spy_weekly_ma", "qqq_weekly_ma",
        "spy_sqn20_diagnostic", "qqq_sqn20_diagnostic",
        "vix", "vvix",
    }
    assert bundle.error is None


def test_assemble_tier1_partial_failure_does_not_block_bundle():
    """One reader failing must not abort the bundle."""
    def scan(_t, _tf):
        raise RuntimeError("scan_ticker offline")
    bundle = assemble_tier1(
        scan_fn=scan,
        load_fn=lambda *a, **k: _bars(15.0),
    )
    # Tier 1 has 6 scan-based + 2 yfinance-based readings.
    assert len(bundle.readings) == 8
    error_count = sum(1 for r in bundle.readings if r.status == "error")
    green_count = sum(1 for r in bundle.readings if r.status == "green")
    # All 6 scan-based fail; both yfinance-based succeed (VIX 15 = green).
    assert error_count == 6
    assert green_count == 2
    # Bundle-level error only fires when *every* reader fails.
    assert bundle.error is None


def test_assemble_tier1_total_failure_sets_bundle_error():
    def scan(_t, _tf):
        raise RuntimeError("scan_ticker offline")

    def load(_s, **_kw):
        raise RuntimeError("yfinance offline")

    bundle = assemble_tier1(scan_fn=scan, load_fn=load)
    assert bundle.error is not None
    assert "All Tier 1 readers failed" in bundle.error
