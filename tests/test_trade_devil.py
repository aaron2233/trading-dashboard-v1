from pathlib import Path

import pytest

from config import load_config
from kill_sheet.builder import build_standard
from trade_devil import (
    AGGREGATE_CONDITIONAL,
    AGGREGATE_KILL,
    AGGREGATE_PROCEED,
    Verdict,
    run_devil,
)
from trade_devil.categories import (
    check_account_fit,
    check_exit_clarity,
    check_regime_mismatch,
    check_technical_invalidation,
)


def _row(stack="full_bull", signal="bull_cross_oversold", regime="bull",
         close=30.0, ticker="AAPL"):
    return {
        "ticker": ticker, "timeframe": "1d", "bar_date": "2026-04-22", "close": close,
        "ma_ribbon": {"ma_10": 30, "ma_20": 29, "ma_50": 28, "ma_200": 25, "stack_state": stack},
        "stochastic": {"k": 25, "d": 22, "zone": "oversold", "signal": signal},
        "sqn": {"sqn_value": 1.2, "regime": regime},
    }


def _sheet(direction="long", **row_overrides):
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_row(**row_overrides), direction, cfg.account("main"))
    return sheet


# ─── Regime Mismatch ──────────────────────────────────────────────────────────


def test_regime_long_into_strong_bear_kills():
    r = check_regime_mismatch(_sheet(regime="strong_bear"))
    assert r.verdict is Verdict.KILL


def test_regime_short_into_strong_bull_kills():
    r = check_regime_mismatch(_sheet(direction="short", regime="strong_bull",
                                     stack="full_bear", signal="bear_cross_overbought"))
    assert r.verdict is Verdict.KILL


def test_regime_long_into_neutral_flags():
    r = check_regime_mismatch(_sheet(regime="neutral"))
    assert r.verdict is Verdict.FLAG


def test_regime_long_into_bull_passes():
    r = check_regime_mismatch(_sheet(regime="bull"))
    assert r.verdict is Verdict.PASS


# ─── Technical Invalidation ───────────────────────────────────────────────────


def test_technical_chop_kills():
    r = check_technical_invalidation(_sheet(stack="chop"))
    assert r.verdict is Verdict.KILL


def test_technical_long_into_full_bear_kills():
    r = check_technical_invalidation(_sheet(stack="full_bear",
                                            signal="bear_cross_overbought"))
    assert r.verdict is Verdict.KILL


def test_technical_compression_flags():
    r = check_technical_invalidation(_sheet(stack="compression"))
    assert r.verdict is Verdict.FLAG


def test_technical_developing_flags():
    r = check_technical_invalidation(_sheet(stack="bull_developing"))
    assert r.verdict is Verdict.FLAG


def test_technical_full_bull_with_aligned_signal_passes():
    r = check_technical_invalidation(_sheet(stack="full_bull",
                                            signal="bull_cross_oversold"))
    assert r.verdict is Verdict.PASS


def test_technical_long_with_counter_signal_flags():
    r = check_technical_invalidation(_sheet(stack="full_bull",
                                            signal="bear_cross_overbought"))
    assert r.verdict is Verdict.FLAG


# ─── Account Fit ──────────────────────────────────────────────────────────────


def test_account_fit_etf_at_high_price_passes():
    r = check_account_fit(_sheet(ticker="SPY", close=580.0))
    assert r.verdict is Verdict.PASS


def test_account_fit_single_stock_outside_band_kills():
    r = check_account_fit(_sheet(ticker="AAPL", close=200.0))
    assert r.verdict is Verdict.KILL


def test_account_fit_single_stock_in_band_passes():
    r = check_account_fit(_sheet(ticker="AAPL", close=30.0))
    assert r.verdict is Verdict.PASS


# ─── Exit Clarity ─────────────────────────────────────────────────────────────


def test_exit_clarity_no_target_no_invalidation_kills():
    r = check_exit_clarity(_sheet())
    assert r.verdict is Verdict.KILL


def test_exit_clarity_no_invalidation_kills():
    sheet = _sheet()
    sheet.target_price = 32.0
    r = check_exit_clarity(sheet)
    assert r.verdict is Verdict.KILL


def test_exit_clarity_no_target_flags():
    sheet = _sheet()
    sheet.invalidation_price = 28.0
    r = check_exit_clarity(sheet)
    assert r.verdict is Verdict.FLAG


def test_exit_clarity_target_wrong_side_kills():
    sheet = _sheet()
    sheet.target_price = 25.0  # below entry on a long
    sheet.invalidation_price = 28.0
    r = check_exit_clarity(sheet)
    assert r.verdict is Verdict.KILL


def test_exit_clarity_low_rr_flags():
    sheet = _sheet()
    sheet.target_price = 30.6   # +2%
    sheet.invalidation_price = 29.4  # -2%, R:R=1
    r = check_exit_clarity(sheet)
    assert r.verdict is Verdict.FLAG


def test_exit_clarity_good_rr_passes():
    sheet = _sheet()
    sheet.target_price = 33.0   # +10%
    sheet.invalidation_price = 28.5  # -5%, R:R=2
    r = check_exit_clarity(sheet)
    assert r.verdict is Verdict.PASS


# ─── Aggregation ──────────────────────────────────────────────────────────────


def test_run_devil_runs_in_stage1_even_below_threshold():
    # Rule 5: stage 1 (account < $100K) → devil is mandatory for EVERY trade,
    # even a $75 lotto sheet under the $150 stage-2 threshold. (Was: returned
    # None below $150 regardless of stage — the rule-5 gap. Fixed 2026-06.)
    cfg = load_config(Path("/nonexistent.yaml"))
    lotto_sheet = build_standard(_row(), "long", cfg.account("lotto"),
                                 account_key="lotto", risk_conviction="default")
    assert lotto_sheet.max_risk_usd == 75.0
    report = run_devil(lotto_sheet)
    assert report is not None  # stage 1 ($1K lotto balance) → mandatory
    assert report.triggered_by_risk_threshold is False  # risk $75 < $150


def test_run_devil_skipped_below_threshold_in_stage2():
    # Stage 2 (account >= $100K): the $150 threshold gates the run again.
    cfg = load_config(Path("/nonexistent.yaml"))
    lotto_sheet = build_standard(_row(), "long", cfg.account("lotto"),
                                 account_key="lotto", risk_conviction="default")
    lotto_sheet.account_balance_usd = 150_000.0  # promote to stage 2
    assert run_devil(lotto_sheet) is None


def test_run_devil_force_runs_below_threshold():
    # force=True overrides the threshold even in stage 2.
    cfg = load_config(Path("/nonexistent.yaml"))
    lotto_sheet = build_standard(_row(), "long", cfg.account("lotto"),
                                 account_key="lotto", risk_conviction="default")
    lotto_sheet.account_balance_usd = 150_000.0  # stage 2, so only force triggers
    report = run_devil(lotto_sheet, force=True)
    assert report is not None
    assert report.triggered_by_risk_threshold is False


def test_aggregate_kill_when_any_category_kills():
    # Long into strong_bear gives a Regime KILL
    sheet = _sheet(regime="strong_bear")
    report = run_devil(sheet)
    assert report.aggregate == AGGREGATE_KILL
    assert report.kills >= 1


def test_aggregate_proceed_for_clean_setup():
    sheet = _sheet()
    sheet.target_price = 33.0
    sheet.invalidation_price = 28.5
    report = run_devil(sheet)
    # 4 stub PASSes + 4 derived: regime/technical/account/exit all PASS for this setup
    # Should net to PROCEED or CONDITIONAL
    assert report.aggregate in (AGGREGATE_PROCEED, AGGREGATE_CONDITIONAL)
    # No KILLs
    assert report.kills == 0


def test_three_or_more_flags_yields_kill():
    # Compression stack flags Technical + neutral regime flags Regime + missing target flags Exit
    # = 3 flags total → death by cuts
    sheet = _sheet(stack="compression", regime="neutral")
    sheet.invalidation_price = 28.0  # missing target only → Exit FLAG
    report = run_devil(sheet)
    # Compression = Technical FLAG, neutral regime = Regime FLAG, missing target = Exit FLAG
    # That's 3 → death by cuts
    assert report.flags >= 3
    assert report.aggregate == AGGREGATE_KILL


def test_to_text_includes_all_categories():
    sheet = _sheet()
    sheet.target_price = 33.0
    sheet.invalidation_price = 28.5
    report = run_devil(sheet)
    text = report.to_text()
    for cat in ("Regime Mismatch", "Technical Invalidation", "IV/Premium Overpricing",
                "Catalyst Timing", "Account Fit", "Consensus/Crowding",
                "Correlation Trap", "Exit Clarity"):
        assert cat in text
    assert "VERDICT:" in text


def test_to_dict_round_trip():
    sheet = _sheet()
    sheet.target_price = 33.0
    sheet.invalidation_price = 28.5
    report = run_devil(sheet)
    payload = report.to_dict()
    assert payload["ticker"] == "AAPL"
    assert payload["aggregate"] in (AGGREGATE_KILL, AGGREGATE_CONDITIONAL, AGGREGATE_PROCEED)
    assert len(payload["results"]) == 8
