"""Tests for src/index_swing/.

Covers:
- Hard universe gate (QQQ/IWM/SPY only)
- Swing-high detection (5-bar lookback/lookforward)
- Breakout confluence levels (high_conviction vs standard)
- Disqualifiers (low volume, gap >2%, Bear Volatile SQN-20)
- Stop / 2R target computation
- Kill-sheet hard blocks (universe_violation, bear_volatile_block)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd
import pytest

from index_swing import (
    INDEX_SWING_ALLOWED_TICKERS,
    INDEX_SWING_TIER_PRIMARY,
    INDEX_SWING_TIER_SECONDARY,
    detect_swing_high_breakout,
    scan_index_swing_watchlist,
)


def _make_bars(
    closes: list[float],
    *,
    volumes: list[float] | None = None,
    start_date: str = "2026-01-01",
) -> pd.DataFrame:
    """Build a daily DataFrame with synthetic high/low/open derived from close."""
    n = len(closes)
    if volumes is None:
        volumes = [1_000_000.0] * n
    dates = pd.date_range(start_date, periods=n, freq="B")
    df = pd.DataFrame({
        "open": [c * 0.999 for c in closes],
        "high": [c * 1.005 for c in closes],
        "low": [c * 0.995 for c in closes],
        "close": closes,
        "volume": volumes,
    }, index=dates)
    return df


# ─────────────────────────────────────────────────────────────────────────
# Hard universe
# ─────────────────────────────────────────────────────────────────────────


def test_universe_is_qqq_iwm_spy_only():
    assert INDEX_SWING_ALLOWED_TICKERS == frozenset({"QQQ", "IWM", "SPY"})
    assert INDEX_SWING_TIER_PRIMARY == frozenset({"QQQ", "IWM"})
    assert INDEX_SWING_TIER_SECONDARY == frozenset({"SPY"})


def test_scan_rejects_outside_universe():
    """AAPL is outside the hard universe → universe_violation."""
    result = scan_index_swing_watchlist(
        ["AAPL"],
        bars_fn=lambda t: _make_bars([100.0] * 80),
        scan_fn=lambda t, tf: {"sqn": {"regime": "bull", "regime_20": "bull"}},
    )
    assert len(result.setups) == 1
    setup = result.setups[0]
    assert setup.ticker == "AAPL"
    assert setup.in_universe is False
    assert setup.universe_tier == "outside"
    assert setup.confluence == "universe_violation"
    assert "outside" in setup.blockers[0].lower()
    assert setup not in result.actionable_setups


def test_scan_default_universe_when_no_tickers():
    """No ticker arg → scan default QQQ/IWM/SPY."""
    bars = _make_bars([100.0] * 80)
    result = scan_index_swing_watchlist(
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {"sqn": {"regime": "bull", "regime_20": "bull"}},
    )
    tickers_seen = {s.ticker for s in result.setups}
    assert tickers_seen == INDEX_SWING_ALLOWED_TICKERS


# ─────────────────────────────────────────────────────────────────────────
# Swing-high detection
# ─────────────────────────────────────────────────────────────────────────


def test_no_breakout_when_close_below_prior_swing_high():
    # Closes form a clear swing high at index 30, then trend down
    closes = [100.0] * 25 + [110.0] + [100.0] * 30
    bars = _make_bars(closes)
    confluence, breakout, blockers = detect_swing_high_breakout(bars)
    assert confluence == "no_breakout"
    assert breakout is None


def _breakout_setup_closes() -> list[float]:
    """Synthetic 78-bar daily series with a recent swing high.

    Layout: 50 flat at $100 → 5-bar climb to $110 (swing high at index 54)
    → 5-bar descent → 15 flat at $105 → 3-bar breakout to $112.
    Swing-high age from final bar: ~23 sessions (within 30-day recency window).
    """
    return (
        [100.0] * 50
        + [102, 104, 106, 108, 110]      # climb to swing high
        + [108, 106, 104, 102, 100]      # 5 bars descending
        + [105.0] * 15
        + [108.0, 109.0, 112.0]           # final close > 110
    )


def test_breakout_detected_above_prior_swing_high():
    closes = _breakout_setup_closes()
    volumes = [1_000_000.0] * (len(closes) - 1) + [1_500_000.0]
    bars = _make_bars(closes, volumes=volumes)
    confluence, breakout, _ = detect_swing_high_breakout(bars)
    assert confluence in ("breakout_high_conviction", "breakout_standard")
    assert breakout is not None
    # Swing high in synthetic data is the high (close * 1.005) of the 110-close bar
    assert breakout.swing_high_value == pytest.approx(110.0 * 1.005, rel=0.001)
    assert breakout.breakout_close == 112.0


def test_low_volume_disqualifier():
    closes = _breakout_setup_closes()
    # 30-day window covers indices 47-77; need final-bar volume < 0.7× of that mean
    n = len(closes)
    volumes = [1_000_000.0] * (n - 1) + [500_000.0]
    bars = _make_bars(closes, volumes=volumes)
    confluence, breakout, blockers = detect_swing_high_breakout(bars)
    assert confluence == "skip_low_volume"
    assert breakout is None
    assert any("volume" in b.lower() for b in blockers)


def test_insufficient_history_returns_no_breakout():
    bars = _make_bars([100.0] * 30)  # only 30 bars
    confluence, breakout, blockers = detect_swing_high_breakout(bars)
    assert confluence == "no_breakout"
    assert breakout is None
    assert any("insufficient" in b.lower() for b in blockers)


# ─────────────────────────────────────────────────────────────────────────
# Bear Volatile SQN(20) gate
# ─────────────────────────────────────────────────────────────────────────


def test_strong_bear_100_skip_at_scan_level():
    """SQN(100) = strong_bear → skip_bear_volatile (structural-bear-volatile analog)."""
    bars = _make_bars([100.0] * 80)
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {
            "sqn": {"regime": "strong_bear", "regime_20": "neutral",
                    "sqn_20_value": 0.0},
        },
    )
    assert len(result.setups) == 1
    setup = result.setups[0]
    assert setup.confluence == "skip_bear_volatile"
    assert setup.breakout is None
    assert setup.suggested_stop is None
    assert setup not in result.actionable_setups


def test_bear_100_with_capitulation_sqn20_skip():
    """SQN(100) = bear AND SQN(20) < -1.9 → skip (structural bear + capitulation)."""
    bars = _make_bars([100.0] * 80)
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {
            "sqn": {"regime": "bear", "regime_20": "strong_bear",
                    "sqn_20_value": -2.5},
        },
    )
    setup = result.setups[0]
    assert setup.confluence == "skip_bear_volatile"


def test_bear_100_without_capitulation_does_not_skip():
    """SQN(100) = bear with SQN(20) >= -1.9 → does NOT trigger the hard skip.
    The strategy may still skip via other gates but not via bear_volatile."""
    bars = _make_bars([100.0] * 80)
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {
            "sqn": {"regime": "bear", "regime_20": "neutral",
                    "sqn_20_value": -0.5},
        },
    )
    setup = result.setups[0]
    # Should fall through to no_breakout (no swing-high break in flat data)
    assert setup.confluence != "skip_bear_volatile"


def test_bull_100_with_sqn20_capitulation_does_not_skip():
    """SQN(100) = bull AND SQN(20) < -1.9 → buy-the-dip zone (rule 12), NOT a skip.
    This is the opposite of the structural Bear Volatile and is favorable."""
    bars = _make_bars([100.0] * 80)
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {
            "sqn": {"regime": "bull", "regime_20": "strong_bear",
                    "sqn_20_value": -2.5},
        },
    )
    setup = result.setups[0]
    assert setup.confluence != "skip_bear_volatile"


# ─────────────────────────────────────────────────────────────────────────
# Stop / target computation
# ─────────────────────────────────────────────────────────────────────────


def test_stop_and_target_computed_on_breakout():
    closes = _breakout_setup_closes()
    volumes = [1_000_000.0] * (len(closes) - 1) + [1_500_000.0]
    bars = _make_bars(closes, volumes=volumes)
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {"sqn": {"regime": "bull", "regime_20": "bull"}},
    )
    setup = result.setups[0]
    assert setup.confluence in ("breakout_high_conviction", "breakout_standard")
    assert setup.suggested_stop is not None
    assert setup.suggested_target_2r is not None
    # Stop must be below entry
    assert setup.suggested_stop < setup.close
    # Target must be above entry by exactly 2× the risk distance
    risk = setup.close - setup.suggested_stop
    expected_target = setup.close + 2.0 * risk
    assert setup.suggested_target_2r == pytest.approx(expected_target, rel=0.001)


def test_stop_does_not_exceed_2pct_below_entry():
    closes = _breakout_setup_closes()
    volumes = [1_000_000.0] * (len(closes) - 1) + [1_500_000.0]
    bars = _make_bars(closes, volumes=volumes)
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {"sqn": {"regime": "bull", "regime_20": "bull"}},
    )
    setup = result.setups[0]
    if setup.suggested_stop is not None:
        # Stop is at most 2% below entry (could be tighter via bar low)
        max_stop_distance_pct = (setup.close - setup.suggested_stop) / setup.close * 100
        assert max_stop_distance_pct <= 2.05  # tiny tolerance for rounding


def test_stop_caps_at_2pct_when_breakout_bar_low_is_wide():
    # Regression for the stop-inversion bug (fixed 2026-06): when the breakout
    # bar's low sits MORE than 2% below its close, the stop must still cap at
    # 2% (the tighter of the two), not widen to the bar low. The old min()
    # selected the WIDER stop here, blowing past the 2% structural premise and
    # inflating the 2R target. The default _make_bars fixture (low = close*0.995)
    # never reaches this branch, so this test forces a wide breakout bar.
    closes = _breakout_setup_closes()
    volumes = [1_000_000.0] * (len(closes) - 1) + [1_500_000.0]
    bars = _make_bars(closes, volumes=volumes)
    last = bars.index[-1]
    bars.loc[last, "low"] = float(bars.loc[last, "close"]) * 0.95  # 5% below close
    result = scan_index_swing_watchlist(
        ["QQQ"],
        bars_fn=lambda t: bars.copy(),
        scan_fn=lambda t, tf: {"sqn": {"regime": "bull", "regime_20": "bull"}},
    )
    setup = result.setups[0]
    assert setup.suggested_stop is not None
    stop_distance_pct = (setup.close - setup.suggested_stop) / setup.close * 100
    assert stop_distance_pct <= 2.05  # capped at 2%, NOT the 5%-wide bar low
    # 2R target scales off the capped (2%) risk, not the wide bar.
    risk = setup.close - setup.suggested_stop
    assert setup.suggested_target_2r == pytest.approx(setup.close + 2.0 * risk, rel=0.001)


# ─────────────────────────────────────────────────────────────────────────
# Kill-sheet hard blocks
# ─────────────────────────────────────────────────────────────────────────


def test_kill_sheet_blocks_index_swing_outside_universe():
    """Building a kill sheet with skill='index-swing' on AAPL → entry_authorized=False."""
    from kill_sheet.builder import build_standard
    from kill_sheet.options import OptionsStructure
    from config import load_config

    cfg = load_config()
    main_account = cfg.account("main")
    scan_row = {
        "ticker": "AAPL",
        "timeframe": "1d",
        "bar_date": "2026-05-09",
        "close": 200.0,
        "ma_ribbon": {
            "ma_10": 200.0, "ma_20": 198.0, "ma_50": 195.0, "ma_200": 180.0,
            "stack_state": "full_bull",
        },
        "stochastic": {"k": 50.0, "d": 48.0, "zone": "mid", "signal": None},
        "sqn": {"sqn_value": 1.2, "regime": "bull",
                "sqn_20_value": 1.0, "regime_20": "bull"},
    }
    options = OptionsStructure(
        strike=200.0, contract_type="call", expiry="2026-06-19",
        dte=45, premium=5.0, delta=0.55, iv_rank=30.0,
    )

    sheet = build_standard(
        scan_row, "long", main_account,
        account_key="main", intent="SWING", trigger_tf="Daily",
        options=options,
        skill="index-swing",
    )
    assert sheet.discipline_attestation.index_swing_universe_violation is True
    assert sheet.discipline_attestation.entry_authorized is False


def test_kill_sheet_allows_index_swing_qqq():
    """Building a kill sheet with skill='index-swing' on QQQ in Bull regime → authorized."""
    from kill_sheet.builder import build_standard
    from kill_sheet.options import OptionsStructure
    from config import load_config

    cfg = load_config()
    main_account = cfg.account("main")
    scan_row = {
        "ticker": "QQQ",
        "timeframe": "1d",
        "bar_date": "2026-05-09",
        "close": 480.0,
        "ma_ribbon": {
            "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0,
            "stack_state": "full_bull",
        },
        "stochastic": {"k": 60.0, "d": 55.0, "zone": "mid", "signal": None},
        "sqn": {"sqn_value": 1.2, "regime": "bull",
                "sqn_20_value": 0.8, "regime_20": "bull"},
    }
    options = OptionsStructure(
        strike=480.0, contract_type="call", expiry="2026-06-19",
        dte=45, premium=10.0, delta=0.55, iv_rank=30.0,
    )

    sheet = build_standard(
        scan_row, "long", main_account,
        account_key="main", intent="SWING", trigger_tf="Daily",
        options=options,
        skill="index-swing",
    )
    assert sheet.discipline_attestation.index_swing_universe_violation is False
    assert sheet.discipline_attestation.bear_volatile_block is False
    assert sheet.discipline_attestation.entry_authorized is True


def test_kill_sheet_blocks_index_swing_strong_bear_100():
    """SQN(100) strong_bear on QQQ → block, no override.

    This is the in-code analog of the backtest's "Bear Volatile" classification.
    """
    from kill_sheet.builder import build_standard
    from kill_sheet.options import OptionsStructure
    from config import load_config

    cfg = load_config()
    main_account = cfg.account("main")
    scan_row = {
        "ticker": "QQQ",
        "timeframe": "1d",
        "bar_date": "2026-05-09",
        "close": 480.0,
        "ma_ribbon": {
            "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0,
            "stack_state": "full_bull",
        },
        "stochastic": {"k": 50.0, "d": 50.0, "zone": "mid", "signal": None},
        "sqn": {"sqn_value": -1.8, "regime": "strong_bear",
                "sqn_20_value": -1.0, "regime_20": "bear"},
    }
    options = OptionsStructure(
        strike=480.0, contract_type="call", expiry="2026-06-19",
        dte=45, premium=10.0, delta=0.55, iv_rank=30.0,
    )

    sheet = build_standard(
        scan_row, "long", main_account,
        account_key="main", intent="SWING", trigger_tf="Daily",
        options=options,
        skill="index-swing",
    )
    assert sheet.discipline_attestation.bear_volatile_block is True
    assert sheet.discipline_attestation.entry_authorized is False


def test_kill_sheet_does_not_block_index_swing_bull_with_sqn20_capitulation():
    """SQN(100) bull + SQN(20) < -1.9 = buy-the-dip zone, NOT a skip.

    This test guards against the SQN-100 vs SQN-20 conflation: an SQN(20)
    extreme low INSIDE a Bull SQN(100) regime is favorable per orchestrator
    rule 12, not a hard block.
    """
    from kill_sheet.builder import build_standard
    from kill_sheet.options import OptionsStructure
    from config import load_config

    cfg = load_config()
    main_account = cfg.account("main")
    scan_row = {
        "ticker": "QQQ",
        "timeframe": "1d",
        "bar_date": "2026-05-09",
        "close": 480.0,
        "ma_ribbon": {
            "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0,
            "stack_state": "full_bull",
        },
        "stochastic": {"k": 50.0, "d": 50.0, "zone": "mid", "signal": None},
        "sqn": {"sqn_value": 1.2, "regime": "bull",
                "sqn_20_value": -2.5, "regime_20": "strong_bear"},
    }
    options = OptionsStructure(
        strike=480.0, contract_type="call", expiry="2026-06-19",
        dte=45, premium=10.0, delta=0.55, iv_rank=30.0,
    )

    sheet = build_standard(
        scan_row, "long", main_account,
        account_key="main", intent="SWING", trigger_tf="Daily",
        options=options,
        skill="index-swing",
    )
    assert sheet.discipline_attestation.bear_volatile_block is False
    assert sheet.discipline_attestation.entry_authorized is True


def test_kill_sheet_track_a_blocks_qqq_when_tagged():
    """skill='weekly-trend-trader' + weekly_trend_track_a=True on QQQ → block,
    because Track A is net-negative on QQQ in the recent backtest."""
    from kill_sheet.builder import build_standard
    from kill_sheet.options import OptionsStructure
    from config import load_config

    cfg = load_config()
    main_account = cfg.account("main")
    scan_row = {
        "ticker": "QQQ",
        "timeframe": "1wk",
        "bar_date": "2026-05-09",
        "close": 480.0,
        "ma_ribbon": {
            "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0,
            "stack_state": "full_bull",
        },
        "stochastic": {"k": 50.0, "d": 50.0, "zone": "mid", "signal": None},
        "sqn": {"sqn_value": 1.2, "regime": "bull",
                "sqn_20_value": 0.8, "regime_20": "bull"},
    }
    options = OptionsStructure(
        strike=480.0, contract_type="call", expiry="2027-01-15",
        dte=365, premium=80.0, delta=0.80, iv_rank=30.0,
    )

    sheet = build_standard(
        scan_row, "long", main_account,
        account_key="weekly", intent="POSITION", trigger_tf="Weekly",
        options=options,
        skill="weekly-trend-trader",
        attestation_user_inputs={"weekly_trend_track_a": True},
    )
    assert sheet.discipline_attestation.weekly_trend_track_a_asset_blocked is True
    # Authorization requires the Track A override attestation
    assert sheet.discipline_attestation.entry_authorized is False
