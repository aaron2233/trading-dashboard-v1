"""Kill sheet builder + render tests with multi-TF context populated."""
from pathlib import Path

import pytest

from config import load_config
from kill_sheet.builder import build_standard


def _daily_row() -> dict:
    return {
        "ticker": "SPY",
        "timeframe": "1d",
        "bar_date": "2026-04-22",
        "close": 580.45,
        "ma_ribbon": {"ma_10": 578.9, "ma_20": 573.2, "ma_50": 565.4,
                      "ma_200": 548.1, "stack_state": "full_bull"},
        "stochastic": {"k": 25.3, "d": 23.1, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.20, "regime": "bull"},
    }


def _multi_tf(weekly_stack: str = "full_bull",
              tf_4h_stack: str = "bull_developing",
              tf_4h_close: float = 580.0,
              tf_4h_ma_20: float = 575.0) -> dict:
    return {
        "1wk": {
            "ticker": "SPY", "timeframe": "1wk",
            "bar_date": "2026-04-19", "close": 580.0,
            "ma_ribbon": {"ma_10": 1, "ma_20": 1, "ma_50": 1, "ma_200": 1,
                          "stack_state": weekly_stack},
            "stochastic": {"k": 50, "d": 50, "zone": "mid", "signal": "neutral"},
            "sqn": {"sqn_value": None, "regime": "n/a"},
        },
        "4h": {
            "ticker": "SPY", "timeframe": "4h",
            "bar_date": "2026-04-22", "close": tf_4h_close,
            "ma_ribbon": {"ma_10": 580, "ma_20": tf_4h_ma_20, "ma_50": 570,
                          "ma_200": 555, "stack_state": tf_4h_stack},
            "stochastic": {"k": 60, "d": 55, "zone": "mid", "signal": "neutral"},
            "sqn": {"sqn_value": None, "regime": "n/a"},
        },
    }


def test_builder_populates_weekly_and_4h_when_multi_tf_provided():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(),
        direction="long",
        account=cfg.account("main"),
        multi_tf=_multi_tf(),
    )
    assert sheet.weekly_stack == "full_bull"
    assert sheet.weekly_alignment == "With trade"
    assert sheet.tf_4h_stack == "bull_developing"
    assert sheet.tf_4h_pullback == "Price above 20 MA"


def test_builder_leaves_multi_tf_fields_none_when_not_provided():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(),
        direction="long",
        account=cfg.account("main"),
    )
    assert sheet.weekly_stack is None
    assert sheet.weekly_alignment is None
    assert sheet.tf_4h_stack is None
    assert sheet.tf_4h_pullback is None


def test_builder_handles_partial_multi_tf_failure():
    cfg = load_config(Path("/nonexistent.yaml"))
    multi = _multi_tf()
    multi["4h"] = {"ticker": "SPY", "timeframe": "4h", "error": "intraday refused"}
    sheet = build_standard(
        scan_row=_daily_row(),
        direction="long",
        account=cfg.account("main"),
        multi_tf=multi,
    )
    assert sheet.weekly_stack == "full_bull"
    assert sheet.tf_4h_stack is None  # failed TF degrades to None


def test_long_against_weekly_bear_marks_counter_trend():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(),
        direction="long",
        account=cfg.account("main"),
        multi_tf=_multi_tf(weekly_stack="full_bear"),
    )
    assert sheet.weekly_alignment == "Counter-trend"


def test_lotto_counter_weekly_rejected_without_thesis():
    # Decision 2026-06: counter-Weekly lotto is REJECTED at the kill-sheet layer
    # (SKILL.md instant disqualifier) unless a counter-weekly/divergence thesis
    # is documented. Uses the multi_tf weekly stack already computed.
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(), direction="long",
        account=cfg.account("lotto"), account_key="lotto",
        multi_tf=_multi_tf(weekly_stack="full_bear"),
    )
    assert sheet.status == "REJECTED"
    assert "counter-Weekly" in (sheet.rejection_reason or "")


def test_lotto_counter_weekly_allowed_with_thesis():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(), direction="long",
        account=cfg.account("lotto"), account_key="lotto",
        multi_tf=_multi_tf(weekly_stack="full_bear"),
        counter_weekly_thesis="post-earnings reversal — documented divergence",
    )
    assert sheet.status == "AUTHORIZED"


def test_lotto_with_weekly_trend_not_rejected():
    # Weekly aligned with the lotto direction → no counter-Weekly rejection.
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(), direction="long",
        account=cfg.account("lotto"), account_key="lotto",
        multi_tf=_multi_tf(weekly_stack="full_bull"),
    )
    assert sheet.status == "AUTHORIZED"


def test_lotto_4h_opposing_rejected_without_thesis():
    # 4H stack opposing the lotto direction (weekly aligned) is also a reject.
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(), direction="long",
        account=cfg.account("lotto"), account_key="lotto",
        multi_tf=_multi_tf(weekly_stack="full_bull", tf_4h_stack="full_bear"),
    )
    assert sheet.status == "REJECTED"
    assert "4H" in (sheet.rejection_reason or "")


def test_text_renders_populated_multi_tf_sections():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(),
        direction="long",
        account=cfg.account("main"),
        multi_tf=_multi_tf(),
    )
    text = sheet.to_text()
    # Weekly section shows real values, not [TBD]
    assert "Stack:     full_bull" in text
    assert "Alignment: With trade" in text
    # 4H section also populated
    assert "Stack:     bull_developing" in text
    assert "Pullback:  Price above 20 MA" in text
    assert "[TBD — weekly bars unavailable]" not in text
    assert "[TBD — 4H bars unavailable]" not in text


def test_text_renders_tbd_when_no_multi_tf():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_daily_row(),
        direction="long",
        account=cfg.account("main"),
    )
    text = sheet.to_text()
    assert "[TBD — weekly bars unavailable]" in text
    assert "[TBD — 4H bars unavailable]" in text
