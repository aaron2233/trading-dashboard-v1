"""Tests for the unified Buy / Wait / No-Go verdict mapping (scan_verdict.py)
and the new lotto setup scanner (lotto/scanner.py)."""
from __future__ import annotations

import pytest

from scan_verdict import (
    weekly_verdict, index_swing_verdict, lotto_verdict, TradeVerdict,
)
from lotto import scan_lotto_watchlist


# ─── Weekly-trend verdict mapping ────────────────────────────────────


def test_weekly_high_conviction_long_in_bull_regime():
    v = weekly_verdict("high_conviction_long", "long", "bull", [])
    assert v.verdict == "buy"


def test_weekly_high_conviction_long_in_bear_regime_downgrades():
    """Counter-trend regime downgrades BUY → WAIT."""
    v = weekly_verdict("high_conviction_long", "long", "bear", [])
    assert v.verdict == "wait"
    assert "opposes" in v.reason.lower()


def test_weekly_chop_is_no_go():
    v = weekly_verdict("chop", "none", "bull", [])
    assert v.verdict == "no_go"
    assert "chop" in v.reason.lower() or "tangled" in v.reason.lower()


def test_weekly_compression_is_wait():
    v = weekly_verdict("compression", "none", "bull", [])
    assert v.verdict == "wait"


def test_weekly_track_a_blocked_asset_downgrades():
    """Track A signal on QQQ (blocked) → WAIT, not BUY."""
    blockers = ["QQQ is on the Track A blocked list — switch to Track B"]
    v = weekly_verdict("track_a_cross_long", "long", "bull", blockers)
    assert v.verdict == "wait"
    assert "blocked" in v.reason.lower()


def test_weekly_track_a_unblocked_asset_buys():
    """Track A signal on NVDA (not blocked) + bull regime → BUY."""
    v = weekly_verdict("track_a_cross_long", "long", "bull", [])
    assert v.verdict == "buy"
    assert "track a" in v.reason.lower() or "19/39" in v.reason.lower()


# ─── Tightening (2026-05-15): fresh trigger + Track A separation ────────


def test_weekly_continuation_long_with_fresh_trigger_buys():
    """Continuation long + fresh bull cross → BUY."""
    v = weekly_verdict(
        "continuation_long", "long", "bull", [],
        stoch_signal="bull_continuation",
    )
    assert v.verdict == "buy"


def test_weekly_continuation_long_with_neutral_stoch_downgrades():
    """Continuation long with no fresh trigger (neutral stoch) → WAIT.
    Captures ABNB-style 'state-true but trigger-absent' false BUYs."""
    v = weekly_verdict(
        "continuation_long", "long", "bull", [],
        stoch_signal="neutral",
    )
    assert v.verdict == "wait"
    assert "fresh" in v.reason.lower() or "wait" in v.reason.lower()


def test_weekly_continuation_short_requires_bear_signal():
    v = weekly_verdict(
        "continuation_short", "short", "bear", [],
        stoch_signal="bull_continuation",  # wrong direction
    )
    assert v.verdict == "wait"


def test_weekly_track_a_thin_separation_downgrades():
    """Track A with sub-0.5% 19/39 separation → WAIT.
    Captures KDP-style razor-thin crosses (0.17% on 29-spot)."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=0.17,
    )
    assert v.verdict == "wait"
    assert "separation" in v.reason.lower() or "thin" in v.reason.lower() or "0.5" in v.reason


def test_weekly_track_a_strong_separation_still_buys():
    """Track A with 0.8% separation → BUY (clears the 0.5% threshold)."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=0.8,
    )
    assert v.verdict == "buy"


def test_weekly_continuation_back_compat_without_stoch_signal():
    """Back-compat: calling without stoch_signal keeps old behavior."""
    v = weekly_verdict("continuation_long", "long", "bull", [])
    assert v.verdict == "buy"


def test_weekly_track_a_back_compat_without_separation():
    """Back-compat: calling without track_a_separation_pct keeps old behavior."""
    v = weekly_verdict("track_a_cross_long", "long", "bull", [])
    assert v.verdict == "buy"


# ─── Green-candle confirmation (2026-05-15) ─────────────────────────


def test_weekly_long_red_candle_downgrades_to_wait():
    """LONG setup but bar closed red → WAIT. Captures the red-reversal
    pattern: MAs cross but the candle itself is bearish, signaling a
    reversal toward the MAs rather than a clean breakout."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=1.0,
        bar_is_bullish=False,
    )
    assert v.verdict == "wait"
    assert "red" in v.reason.lower() or "green" in v.reason.lower()


def test_weekly_long_green_candle_buys():
    """LONG setup on a green bar → BUY (all other filters passing)."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=1.0,
        bar_is_bullish=True,
    )
    assert v.verdict == "buy"


def test_weekly_short_green_candle_downgrades_to_wait():
    """SHORT setup but bar closed green → WAIT."""
    v = weekly_verdict(
        "continuation_short", "short", "bear", [],
        stoch_signal="bear_continuation",
        bar_is_bullish=True,
    )
    assert v.verdict == "wait"
    assert "green" in v.reason.lower() or "red" in v.reason.lower()


def test_weekly_short_red_candle_buys():
    """SHORT setup on a red bar → BUY (all other filters passing)."""
    v = weekly_verdict(
        "continuation_short", "short", "bear", [],
        stoch_signal="bear_continuation",
        bar_is_bullish=False,
    )
    assert v.verdict == "buy"


def test_weekly_high_conviction_long_red_candle_downgrades():
    """Even high_conviction longs require a green close."""
    v = weekly_verdict(
        "high_conviction_long", "long", "bull", [],
        bar_is_bullish=False,
    )
    assert v.verdict == "wait"


def test_weekly_bar_color_back_compat_without_param():
    """Back-compat: omitting bar_is_bullish keeps prior behavior."""
    v = weekly_verdict("track_a_cross_long", "long", "bull", [])
    assert v.verdict == "buy"


# ─── Track A stretch ceiling (2026-05-15) ───────────────────────────


def test_weekly_track_a_stretched_above_19wma_downgrades():
    """Track A long with close 44% above 19WMA (ARM-style) → WAIT.
    Stop distance becomes unworkable for LEAPS sizing."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=1.18,
        track_a_stretch_pct=44.0,
    )
    assert v.verdict == "wait"
    assert "stretched" in v.reason.lower() or "retest" in v.reason.lower()


def test_weekly_track_a_within_15pct_buys():
    """Track A long with close 10% above 19WMA → still BUY."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=0.8,
        track_a_stretch_pct=10.0,
        bar_is_bullish=True,
    )
    assert v.verdict == "buy"


def test_weekly_track_a_at_15pct_boundary_buys():
    """Exact 15% stretch should still buy (strictly greater than ceiling
    is the cutoff)."""
    v = weekly_verdict(
        "track_a_cross_long", "long", "bull", [],
        track_a_separation_pct=0.8,
        track_a_stretch_pct=15.0,
    )
    assert v.verdict == "buy"


def test_weekly_track_a_stretch_back_compat():
    """Back-compat: omitting track_a_stretch_pct keeps prior behavior."""
    v = weekly_verdict("track_a_cross_long", "long", "bull", [])
    assert v.verdict == "buy"


# ─── Index-swing verdict mapping ─────────────────────────────────────


def test_index_swing_high_conviction_breakout_buys():
    v = index_swing_verdict("breakout_high_conviction", 4)
    assert v.verdict == "buy"


def test_index_swing_standard_breakout_waits():
    """1/5 confluence → marginal; verdict = wait per skill."""
    v = index_swing_verdict("breakout_standard", 1)
    assert v.verdict == "wait"


def test_index_swing_no_breakout_waits():
    v = index_swing_verdict("no_breakout", None)
    assert v.verdict == "wait"


def test_index_swing_bear_volatile_no_go():
    v = index_swing_verdict("skip_bear_volatile", None)
    assert v.verdict == "no_go"


def test_index_swing_low_volume_no_go():
    v = index_swing_verdict("skip_low_volume", None)
    assert v.verdict == "no_go"


def test_index_swing_universe_violation_no_go():
    v = index_swing_verdict("universe_violation", None)
    assert v.verdict == "no_go"


# ─── Lotto verdict mapping ───────────────────────────────────────────


def test_lotto_chop_is_no_go():
    v = lotto_verdict("chop", "bull", 0.5, "bull_cross_oversold", "oversold", "long")
    assert v.verdict == "no_go"


def test_lotto_compression_is_no_go_both_directions():
    """The ribbon's squeezed-MAs state is "compression" (it never emits
    "tangled") — trendless, hard no-trade per the anti-patterns."""
    long_v = lotto_verdict(
        "compression", "bull", 1.6, "bull_cross_oversold", "oversold", "long",
    )
    short_v = lotto_verdict(
        "compression", "bear", -1.0, "bear_cross_overbought", "overbought", "short",
    )
    assert long_v.verdict == "no_go"
    assert short_v.verdict == "no_go"


def test_lotto_missing_sqn100_fails_closed():
    """No SQN(100) regime → regime gates can't evaluate → WAIT, never BUY."""
    v = lotto_verdict("full_bull", None, 1.6, "bull_cross_oversold", "oversold", "long")
    assert v.verdict == "wait"
    assert "unavailable" in v.reason.lower()


def test_lotto_missing_sqn20_fails_closed():
    """No SQN(20) value → chase gate / rule-18 / v2 gates can't evaluate →
    WAIT, never BUY (previously this fell through to BUY)."""
    v = lotto_verdict("full_bull", "bull", None, "bull_cross_oversold", "oversold", "long")
    assert v.verdict == "wait"
    assert "unavailable" in v.reason.lower()


def test_lotto_long_in_strong_bear_no_go():
    v = lotto_verdict("full_bull", "strong_bear", 0.0, "bull_cross_oversold", "oversold", "long")
    assert v.verdict == "no_go"


def test_lotto_long_with_chase_warning_no_go():
    """SQN(20) > +2.5 + bullish lotto → chase warning HARD SKIP."""
    v = lotto_verdict("full_bull", "bull", 3.0, "bull_continuation", "mid", "long")
    assert v.verdict == "no_go"
    assert "chase" in v.reason.lower()


def test_lotto_bear_with_capitulation_long_no_go():
    """SQN(100) Bear + SQN(20) < -1.9 → structural Bear-Volatile, hard skip."""
    v = lotto_verdict("full_bull", "bear", -2.5, "bull_cross_oversold", "oversold", "long")
    assert v.verdict == "no_go"
    assert "bear-volatile" in v.reason.lower() or "bear volatile" in v.reason.lower()


def test_lotto_long_with_2h_trigger_buys():
    # SQN(20)=1.6 → strong_bull band, productive cohort (full_bull + strong_bull,
    # avgR +1.04 in backtest). Must pass v2 gates.
    v = lotto_verdict(
        "full_bull", "bull", 1.6, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "buy"


def test_lotto_long_no_2h_trigger_waits():
    # SQN(20)=1.6 in strong_bull band (passes v2 gates) — no trigger → wait
    v = lotto_verdict("full_bull", "bull", 1.6, "neutral", "mid", "long")
    assert v.verdict == "wait"


def test_lotto_short_in_bull_no_go():
    """Bearish lotto requires Bear regime; in Bull = no go."""
    v = lotto_verdict(
        "full_bear", "bull", 0.5, "bear_cross_overbought", "overbought", "short",
    )
    assert v.verdict == "no_go"


# ─── Lotto v2 cohort-derived gates (added 2026-05-12) ──────────────────────


def test_lotto_v2_short_in_strong_bear_no_go():
    """v2: strong_bear shorts blocked (mean-reversion zone, backtest avgR -0.45)."""
    v = lotto_verdict(
        "full_bear", "strong_bear", -2.0, "bear_cross_overbought", "overbought", "short",
    )
    assert v.verdict == "no_go"
    assert "mean-reversion" in v.reason.lower() or "v2" in v.reason.lower()


def test_lotto_v2_bull_developing_with_weak_momentum_no_go():
    """v2: stack=bull_developing + SQN(20) < +0.5 blocks long (soft setup drag)."""
    v = lotto_verdict(
        "bull_developing", "neutral", 0.0, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "no_go"
    assert "soft-setup" in v.reason.lower() or "v2" in v.reason.lower()


def test_lotto_v2_bull_developing_with_strong_momentum_buys():
    """v2: stack=bull_developing IS allowed with SQN(20) >= +0.5."""
    v = lotto_verdict(
        "bull_developing", "neutral", 0.8, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "buy"


def test_lotto_v2_full_bull_mid_momentum_band_no_go():
    """v2: full_bull + SQN(20) in [+0.5, +1.4) blocks long (consolidation chop,
    largest single losing cohort in backtest: n=162, avgR -0.31)."""
    v = lotto_verdict(
        "full_bull", "bull", 1.0, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "no_go"
    assert "mid-momentum" in v.reason.lower() or "v2" in v.reason.lower()


def test_lotto_v2_full_bull_with_strong_bull_sqn20_still_buys():
    """v2: full_bull + SQN(20) >= +1.4 (strong_bull band) is the BEST cohort
    (avgR +1.04 in backtest). Must still buy."""
    v = lotto_verdict(
        "full_bull", "bull", 1.5, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "buy"


def test_lotto_v2_full_bull_with_neutral_sqn20_still_buys():
    """v2: full_bull + SQN(20) in [-1.1, +0.5) (neutral band) is allowed
    (avgR -0.00 in backtest — breakeven, kept to preserve trade tempo)."""
    v = lotto_verdict(
        "full_bull", "bull", 0.0, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "buy"


def test_lotto_v2_bull_developing_with_no_sqn20_fails_closed():
    """Missing SQN(20) now fails closed globally (WAIT) before the v2 gates
    are reached — still no entry, consistent with every other missing-SQN
    case (was a per-gate no_go fail-safe)."""
    v = lotto_verdict(
        "bull_developing", "neutral", None, "bull_cross_oversold", "oversold", "long",
    )
    assert v.verdict == "wait"
    assert "unavailable" in v.reason.lower()


# ─── Lotto scanner (uses fake scan_fn so no yfinance) ────────────────


def test_lotto_scanner_emits_two_setups_per_ticker():
    """One ticker → two setups (long + short)."""
    fake = {
        "QQQ": {
            "ticker": "QQQ", "timeframe": "1d", "bar_date": "2026-05-09",
            "close": 480.0,
            "ma_ribbon": {
                "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0,
                "stack_state": "full_bull",
            },
            "stochastic": {"k": 60.0, "d": 55.0, "zone": "mid", "signal": "neutral"},
            "sqn": {"sqn_value": 1.2, "regime": "bull",
                    "sqn_20_value": 1.6, "regime_20": "strong_bull"},
        },
    }
    fake_h2 = {
        "QQQ": {
            "ticker": "QQQ", "timeframe": "2h",
            "ma_ribbon": {"stack_state": "full_bull"},
            "stochastic": {"k": 25.0, "d": 22.0, "zone": "oversold",
                           "signal": "bull_cross_oversold"},
        },
    }
    def fake_scan(ticker, timeframe="1d"):
        return fake[ticker] if timeframe == "1d" else fake_h2[ticker]

    result = scan_lotto_watchlist(["QQQ"], scan_fn=fake_scan)
    assert len(result.setups) == 2
    longs = [s for s in result.setups if s.direction == "long"]
    shorts = [s for s in result.setups if s.direction == "short"]
    assert len(longs) == 1 and len(shorts) == 1


def test_lotto_scanner_long_signal_yields_buy_in_supportive_regime():
    fake_d = {
        "QQQ": {
            "ticker": "QQQ", "timeframe": "1d", "bar_date": "2026-05-09",
            "close": 480.0,
            "ma_ribbon": {"stack_state": "full_bull",
                          "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0},
            "stochastic": {"k": 50.0, "d": 50.0, "zone": "mid", "signal": "neutral"},
            "sqn": {"sqn_value": 1.0, "regime": "bull",
                    "sqn_20_value": 1.6, "regime_20": "strong_bull"},
        },
    }
    fake_h2 = {
        "QQQ": {
            "ma_ribbon": {"stack_state": "full_bull"},
            "stochastic": {"k": 25.0, "d": 22.0, "zone": "oversold",
                           "signal": "bull_cross_oversold"},
        },
    }
    def fake_scan(ticker, timeframe="1d"):
        return fake_d[ticker] if timeframe == "1d" else fake_h2[ticker]

    result = scan_lotto_watchlist(["QQQ"], scan_fn=fake_scan)
    long_setup = next(s for s in result.setups if s.direction == "long")
    assert long_setup.verdict == "buy"
    assert long_setup.entry_price is not None
    assert long_setup.stop_price is not None
    # Stop is below entry for longs
    assert long_setup.stop_price < long_setup.entry_price
    # Target is above entry for longs
    assert long_setup.target_price > long_setup.entry_price


def test_lotto_scanner_chase_warning_blocks_buy():
    fake_d = {
        "QQQ": {
            "ticker": "QQQ", "timeframe": "1d", "bar_date": "2026-05-09",
            "close": 480.0,
            "ma_ribbon": {"stack_state": "full_bull",
                          "ma_10": 478.0, "ma_20": 475.0, "ma_50": 470.0, "ma_200": 450.0},
            "stochastic": {"k": 90.0, "d": 88.0, "zone": "overbought",
                           "signal": "neutral"},
            "sqn": {"sqn_value": 1.5, "regime": "bull",
                    "sqn_20_value": 3.5, "regime_20": "strong_bull"},
        },
    }
    fake_h2 = {
        "QQQ": {
            "ma_ribbon": {"stack_state": "full_bull"},
            "stochastic": {"k": 92.0, "d": 89.0, "zone": "overbought",
                           "signal": "bearish_divergence"},
        },
    }
    def fake_scan(ticker, timeframe="1d"):
        return fake_d[ticker] if timeframe == "1d" else fake_h2[ticker]

    result = scan_lotto_watchlist(["QQQ"], scan_fn=fake_scan)
    long_setup = next(s for s in result.setups if s.direction == "long")
    assert long_setup.verdict == "no_go"
    assert long_setup.entry_price is None  # NO_GO suppresses entry/stop
    assert "chase" in long_setup.verdict_reason.lower()


# ─── Lotto scanner price-band gate (regression — 2026-05-12) ──────────────


def _otherwise_supportive_scan(ticker: str, close: float):
    """Build a fake scan that would otherwise produce BUY for both directions
    — clean stack, supportive regime, fired 2H trigger. Used to isolate the
    price-band gate from every other gate."""
    daily = {
        "ticker": ticker, "timeframe": "1d", "bar_date": "2026-05-12",
        "close": close,
        "ma_ribbon": {"stack_state": "full_bull",
                      "ma_10": close * 0.99, "ma_20": close * 0.97,
                      "ma_50": close * 0.94, "ma_200": close * 0.88},
        "stochastic": {"k": 50.0, "d": 50.0, "zone": "mid", "signal": "neutral"},
        "sqn": {"sqn_value": 1.0, "regime": "bull",
                "sqn_20_value": 1.6, "regime_20": "strong_bull"},
    }
    h2 = {
        "ma_ribbon": {"stack_state": "full_bull"},
        "stochastic": {"k": 25.0, "d": 22.0, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
    }
    return daily, h2


def test_lotto_scanner_blocks_single_stock_above_price_band():
    """Regression: SANM at $233, DY at $415 surfaced as BUY before the
    price-band gate was wired in. Single stocks above $50 must be no_go
    regardless of how strong the rest of the setup is."""
    daily, h2 = _otherwise_supportive_scan("SANM", close=233.26)
    def fake_scan(ticker, timeframe="1d"):
        return daily if timeframe == "1d" else h2

    result = scan_lotto_watchlist(["SANM"], scan_fn=fake_scan)
    assert len(result.setups) == 2
    for s in result.setups:
        assert s.verdict == "no_go"
        assert "price band" in s.verdict_reason.lower()
        assert s.entry_price is None


def test_lotto_scanner_blocks_single_stock_below_price_band():
    """Sub-$10 single stock out of band (floor lowered from $15 → $10 on 2026-05-14)."""
    daily, h2 = _otherwise_supportive_scan("LAC", close=5.48)
    def fake_scan(ticker, timeframe="1d"):
        return daily if timeframe == "1d" else h2

    result = scan_lotto_watchlist(["LAC"], scan_fn=fake_scan)
    for s in result.setups:
        assert s.verdict == "no_go"
        assert "price band" in s.verdict_reason.lower()


def test_lotto_scanner_passes_single_stock_at_12_dollars():
    """$12 single stock passes — above the new $10 floor (was $15 pre-2026-05-14)."""
    daily, h2 = _otherwise_supportive_scan("RDW", close=12.50)
    def fake_scan(ticker, timeframe="1d"):
        return daily if timeframe == "1d" else h2

    result = scan_lotto_watchlist(["RDW"], scan_fn=fake_scan)
    long_setup = next(s for s in result.setups if s.direction == "long")
    assert long_setup.verdict == "buy"


def test_lotto_scanner_blocks_just_under_new_floor():
    """$9.99 single stock blocked — just under the new $10 floor."""
    daily, h2 = _otherwise_supportive_scan("BBAI", close=9.99)
    def fake_scan(ticker, timeframe="1d"):
        return daily if timeframe == "1d" else h2

    result = scan_lotto_watchlist(["BBAI"], scan_fn=fake_scan)
    for s in result.setups:
        assert s.verdict == "no_go"
        assert "price band" in s.verdict_reason.lower()


def test_lotto_scanner_etf_at_any_price_passes_band_gate():
    """ETFs are exempt from the $15-50 band. SPY at $580 must still buy."""
    daily, h2 = _otherwise_supportive_scan("SPY", close=580.0)
    def fake_scan(ticker, timeframe="1d"):
        return daily if timeframe == "1d" else h2

    result = scan_lotto_watchlist(["SPY"], scan_fn=fake_scan)
    long_setup = next(s for s in result.setups if s.direction == "long")
    assert long_setup.verdict == "buy"


def test_lotto_scanner_in_band_single_stock_passes():
    """Single stock at $30 (mid of $15-50 band) hits BUY when the rest is clean."""
    daily, h2 = _otherwise_supportive_scan("PLTR", close=30.0)
    def fake_scan(ticker, timeframe="1d"):
        return daily if timeframe == "1d" else h2

    result = scan_lotto_watchlist(["PLTR"], scan_fn=fake_scan)
    long_setup = next(s for s in result.setups if s.direction == "long")
    assert long_setup.verdict == "buy"


# ─── Mag 7 exemption from lotto price band (2026-05-12) ────────────────────


def test_lotto_scanner_mag7_above_50_passes_band_gate():
    """Mag 7 (AAPL, MSFT, GOOGL/GOOG, AMZN, META, NVDA, TSLA) are exempt
    from the lotto price-band even though spot >$50. Each must reach BUY
    when the rest of the setup is clean."""
    for ticker, price in [
        ("AAPL", 292.79),
        ("MSFT", 412.58),
        ("GOOGL", 185.0),
        ("GOOG", 187.0),
        ("AMZN", 220.0),
        ("META", 600.0),
        ("NVDA", 219.46),
        ("TSLA", 445.07),
    ]:
        daily, h2 = _otherwise_supportive_scan(ticker, close=price)
        def fake_scan(t, timeframe="1d", _d=daily, _h=h2):
            return _d if timeframe == "1d" else _h

        result = scan_lotto_watchlist([ticker], scan_fn=fake_scan)
        long_setup = next(s for s in result.setups if s.direction == "long")
        assert long_setup.verdict == "buy", (
            f"{ticker} at ${price} should pass Mag 7 exemption, "
            f"got verdict={long_setup.verdict} reason={long_setup.verdict_reason}"
        )


def test_lotto_scanner_non_mag7_above_50_still_blocked():
    """Mag 7 exemption is a whitelist, not a blanket. Other big caps
    (AVGO, BRK-B, SANM, DY, etc.) still get price-band gated."""
    for ticker, price in [
        ("AVGO", 1800.0),
        ("SANM", 233.26),
        ("DY", 415.01),
    ]:
        daily, h2 = _otherwise_supportive_scan(ticker, close=price)
        def fake_scan(t, timeframe="1d", _d=daily, _h=h2):
            return _d if timeframe == "1d" else _h

        result = scan_lotto_watchlist([ticker], scan_fn=fake_scan)
        for s in result.setups:
            assert s.verdict == "no_go", (
                f"{ticker} at ${price} should still be band-gated, "
                f"got verdict={s.verdict}"
            )
            assert "price band" in s.verdict_reason.lower()
