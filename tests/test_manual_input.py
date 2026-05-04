"""Tests for Story 21:
  - max_per_trade_usd cap in PositionSize
  - Account-aware DTE band recommendation
  - Manual fill CLI flags (target/invalidation/trigger_desc/notes)
  - Interactive input mode (--interactive)
  - IV check no longer has the verdict-priority inversion
"""
from pathlib import Path
from unittest.mock import patch

import pytest

from config import load_config
from kill_sheet import build_standard, calculate_position_size
from kill_sheet.builder import _dte_band_for
from trade_devil.categories import check_iv_premium_overpricing
from trade_devil.verdict import Verdict


# ─── max_per_trade_usd cap ────────────────────────────────────────────────────


def test_position_size_no_cap():
    p = calculate_position_size(10_000.0, 0.025)
    assert p.max_risk_usd == 250.0
    assert p.capped_by is None


def test_position_size_cap_kicks_in():
    # 25% of $10K = $2500, cap = $150 → cap wins
    p = calculate_position_size(10_000.0, 0.25, max_per_trade_usd=150.0)
    assert p.max_risk_usd == 150.0
    assert p.capped_by == "max_per_trade_usd"


def test_position_size_cap_not_engaged_when_pct_smaller():
    p = calculate_position_size(10_000.0, 0.01, max_per_trade_usd=150.0)
    assert p.max_risk_usd == 100.0
    assert p.capped_by is None


def test_position_size_cap_with_units():
    p = calculate_position_size(10_000.0, 0.25, max_loss_per_unit=50.0,
                                max_per_trade_usd=150.0)
    assert p.max_risk_usd == 150.0
    assert p.units == 3  # 150 / 50
    assert p.capped_by == "max_per_trade_usd"


def test_lotto_account_respects_cap():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row={
            "ticker": "FAKE", "timeframe": "1d", "bar_date": "2026-04-22",
            "close": 25.0,
            "ma_ribbon": {"ma_10": 25, "ma_20": 24, "ma_50": 22, "ma_200": 20,
                          "stack_state": "full_bull"},
            "stochastic": {"k": 25, "d": 22, "zone": "oversold",
                           "signal": "bull_cross_oversold"},
            "sqn": {"sqn_value": 1.2, "regime": "bull"},
        },
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
        risk_conviction="default",
    )
    # lotto default risk_pct = 7.5% × $1000 = $75 — below the $150 cap
    assert sheet.max_risk_usd == 75.0
    assert sheet.risk_capped_by_max_trade is False


def test_high_risk_pct_triggers_cap():
    # Synthetic config with risk_pct that would exceed cap
    from config.loader import AccountConfig
    account = AccountConfig(
        name="Test", type="cash", balance_usd=1_000.0,
        raw={"risk_per_trade": {"high": 0.30},
             "max_per_trade_usd": 150.0},
    )
    sheet = build_standard(
        scan_row={
            "ticker": "FAKE", "timeframe": "1d", "bar_date": "2026-04-22", "close": 25.0,
            "ma_ribbon": {"ma_10": 25, "ma_20": 24, "ma_50": 22, "ma_200": 20,
                          "stack_state": "full_bull"},
            "stochastic": {"k": 25, "d": 22, "zone": "oversold",
                           "signal": "bull_cross_oversold"},
            "sqn": {"sqn_value": 1.2, "regime": "bull"},
        },
        direction="long",
        account=account,
        account_key="test",
        risk_conviction="high",
    )
    # 30% × $1000 = $300, cap = $150 → cap wins
    assert sheet.max_risk_usd == 150.0
    assert sheet.risk_capped_by_max_trade is True
    assert "[capped by max_per_trade_usd]" in sheet.to_text()


# ─── Account-aware DTE band ───────────────────────────────────────────────────


def test_dte_band_lotto():
    assert "5–14" in _dte_band_for("lotto", "SWING", "Daily")


def test_dte_band_weekly_account():
    assert "120–180" in _dte_band_for("weekly", "SWING", "Daily")


def test_dte_band_position_intent():
    assert "120–180" in _dte_band_for("main", "POSITION", "Daily")


def test_dte_band_scalp():
    assert "0–14" in _dte_band_for("main", "SCALP", "2H")


def test_dte_band_trend_capture():
    assert "21–45" in _dte_band_for("main", "TREND CAPTURE", "Daily")


def test_dte_band_default_swing():
    assert "14–30" in _dte_band_for("main", "SWING", "4H")


# ─── Manual fill via build_standard ───────────────────────────────────────────


def _row():
    return {
        "ticker": "AAPL", "timeframe": "1d", "bar_date": "2026-04-22", "close": 30.0,
        "ma_ribbon": {"ma_10": 30, "ma_20": 29, "ma_50": 28, "ma_200": 25,
                      "stack_state": "full_bull"},
        "stochastic": {"k": 25, "d": 22, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.2, "regime": "bull"},
    }


def test_build_standard_carries_manual_fill():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        _row(), "long", cfg.account("main"),
        target_price=33.0,
        invalidation_price=28.5,
        trigger_description="hold above 20MA pullback",
        notes="earnings 6 weeks out, clean window",
    )
    assert sheet.target_price == 33.0
    assert sheet.invalidation_price == 28.5
    assert sheet.trigger_description == "hold above 20MA pullback"
    assert sheet.notes == "earnings 6 weeks out, clean window"

    text = sheet.to_text()
    assert "TARGET:        $33.00" in text
    assert "INVALIDATION:  $28.50" in text
    assert "hold above 20MA pullback" in text
    assert "earnings 6 weeks out, clean window" in text


# ─── Interactive prompt logic ─────────────────────────────────────────────────


def test_maybe_interactive_skips_when_flag_off():
    from kill_sheet.cli import _maybe_interactive_fill
    args = type("A", (), {"interactive": False, "target": None,
                          "invalidation": None, "trigger_desc": None,
                          "notes": None, "strike": None, "premium": None,
                          "expiry": None, "contract_type": None, "delta": None,
                          "iv_rank": None, "oi": None, "spread": None})()
    _maybe_interactive_fill(args, input_fn=lambda _: "should_not_be_called")
    assert args.target is None
    assert args.notes is None


def test_maybe_interactive_fills_target_and_invalidation():
    from kill_sheet.cli import _maybe_interactive_fill
    inputs = iter(["33.0", "28.5", "", "", "n"])  # target, invalidation, blank trigger, blank notes, no apex
    args = type("A", (), {"interactive": True, "target": None,
                          "invalidation": None, "trigger_desc": None,
                          "notes": None, "strike": None, "premium": None,
                          "expiry": None, "contract_type": None, "delta": None,
                          "iv_rank": None, "oi": None, "spread": None})()
    _maybe_interactive_fill(args, input_fn=lambda _: next(inputs))
    assert args.target == 33.0
    assert args.invalidation == 28.5
    assert args.trigger_desc is None
    assert args.notes is None


def test_maybe_interactive_skips_already_set_flags():
    from kill_sheet.cli import _maybe_interactive_fill
    inputs = iter(["28.5", "", "", "n"])  # target was preset; invalidation prompted
    args = type("A", (), {"interactive": True, "target": 33.0,
                          "invalidation": None, "trigger_desc": None,
                          "notes": None, "strike": None, "premium": None,
                          "expiry": None, "contract_type": None, "delta": None,
                          "iv_rank": None, "oi": None, "spread": None})()
    _maybe_interactive_fill(args, input_fn=lambda _: next(inputs))
    assert args.target == 33.0  # preserved
    assert args.invalidation == 28.5  # prompted


def test_maybe_interactive_apex_yes_prompts_options():
    from kill_sheet.cli import _maybe_interactive_fill
    # target/inv/trigger/notes blank, apex y, then strike/premium/expiry/...
    inputs = iter([
        "", "", "", "",     # target, invalidation, trigger, notes — all skipped
        "y",                # apex yes
        "100.0", "1.50", "2026-06-19",  # strike, premium, expiry
        "", "", "", "", "",  # contract_type, delta, iv_rank, oi, spread (all skipped)
    ])
    args = type("A", (), {"interactive": True, "target": None,
                          "invalidation": None, "trigger_desc": None,
                          "notes": None, "strike": None, "premium": None,
                          "expiry": None, "contract_type": None, "delta": None,
                          "iv_rank": None, "oi": None, "spread": None})()
    _maybe_interactive_fill(args, input_fn=lambda _: next(inputs))
    assert args.strike == 100.0
    assert args.premium == 1.50
    assert args.expiry == "2026-06-19"


# ─── IV check priority bug fix ────────────────────────────────────────────────


def _opts_sheet(iv_rank=None, premium=1.0, account_balance=10_000):
    cfg = load_config(Path("/nonexistent.yaml"))
    from kill_sheet.options import OptionsStructure
    opts = OptionsStructure(
        strike=30, contract_type="call", expiry="2026-05-22", dte=27,
        premium=premium, delta=0.4, iv_rank=iv_rank,
        open_interest=8000, bid_ask_spread=0.05,
    )
    return build_standard(_row(), "long", cfg.account("main"), options=opts)


def test_iv_high_iv_rank_AND_high_premium_pct_both_flag():
    """Regression: previous code used max() which inverted priority and
    sometimes returned PASS instead of FLAG when both signals fired.
    """
    # IV Rank 65 (>50, FLAG) + premium $4 * 100 = $400 = 4% of $10K (>3%, FLAG)
    sheet = _opts_sheet(iv_rank=65, premium=4.0)
    r = check_iv_premium_overpricing(sheet)
    assert r.verdict is Verdict.FLAG
    assert "IV Rank 65%" in r.reason
    assert "Premium 4.0% of account" in r.reason
