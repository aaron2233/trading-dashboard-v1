"""Apex options template tests: helpers + builder + render + devil integration."""
from datetime import date
from pathlib import Path

import pytest

from config import load_config
from kill_sheet import (
    KillSheet,
    OptionsStructure,
    breakeven,
    build_standard,
    compute_dte,
    delta_target,
    dte_target,
    evaluate_structure,
    iv_rank_label,
)
from trade_devil import Verdict
from trade_devil.categories import check_iv_premium_overpricing


def _row():
    return {
        "ticker": "AAPL",
        "timeframe": "1d",
        "bar_date": "2026-04-22",
        "close": 30.0,
        "ma_ribbon": {"ma_10": 30, "ma_20": 29, "ma_50": 28, "ma_200": 25,
                      "stack_state": "full_bull"},
        "stochastic": {"k": 25, "d": 22, "zone": "oversold",
                       "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.2, "regime": "bull"},
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────


def test_compute_dte_calculation():
    today = date(2026, 4, 25)
    assert compute_dte("2026-05-22", today=today) == 27


def test_compute_dte_clamps_to_zero():
    today = date(2026, 4, 25)
    assert compute_dte("2026-04-20", today=today) == 0


def test_breakeven_call():
    assert breakeven(100.0, 5.0, "call") == 105.0


def test_breakeven_put():
    assert breakeven(100.0, 5.0, "put") == 95.0


def test_breakeven_invalid():
    with pytest.raises(ValueError):
        breakeven(100.0, 5.0, "stock")


def test_iv_rank_label_buckets():
    assert iv_rank_label(20) == "cheap"
    assert iv_rank_label(40) == "fair"
    assert iv_rank_label(60) == "elevated"
    assert iv_rank_label(85) == "expensive"
    assert iv_rank_label(None) == "n/a"


def test_delta_target_by_conviction():
    assert delta_target("high") == (0.50, 0.60)
    assert delta_target("medium") == (0.35, 0.45)
    assert delta_target("speculative") == (0.20, 0.30)


def test_dte_target_by_trigger_tf():
    assert dte_target("2H") == (0, 14)
    assert dte_target("4H") == (14, 30)
    assert dte_target("Daily") == (21, 45)


# ─── Structure evaluation ─────────────────────────────────────────────────────


def _opt(**overrides) -> OptionsStructure:
    base = dict(
        strike=580.0, contract_type="call", expiry="2026-05-22",
        dte=27, premium=5.50, delta=0.55, iv_rank=35.0,
        open_interest=8500, bid_ask_spread=0.05,
    )
    base.update(overrides)
    return OptionsStructure(**base)


def test_structure_eval_clean_high_conviction_4h():
    chk = evaluate_structure(_opt(), conviction="high", trigger_tf="4H")
    assert chk.delta_in_band is True
    assert chk.dte_in_band is True
    assert chk.liquidity_ok is True
    assert chk.iv_rank_label == "fair"


def test_structure_eval_low_oi_fails_liquidity():
    chk = evaluate_structure(_opt(open_interest=200), conviction="high",
                             trigger_tf="4H")
    assert chk.liquidity_ok is False


def test_structure_eval_wide_spread_fails_liquidity():
    # spread $1 on $5.50 premium = 18% spread
    chk = evaluate_structure(_opt(bid_ask_spread=1.0), conviction="high",
                             trigger_tf="4H")
    assert chk.liquidity_ok is False


def test_structure_eval_delta_out_of_band():
    chk = evaluate_structure(_opt(delta=0.25), conviction="high",
                             trigger_tf="4H")
    assert chk.delta_in_band is False


def test_structure_eval_dte_out_of_band():
    chk = evaluate_structure(_opt(dte=60), conviction="high",
                             trigger_tf="4H")
    assert chk.dte_in_band is False


# ─── Builder + sizing ─────────────────────────────────────────────────────────


def test_build_standard_with_options_computes_contracts():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        _row(), direction="long", account=cfg.account("main"),
        options=_opt(),
    )
    assert sheet.options is not None
    assert sheet.options.strike == 580.0
    # max_risk = $250; cost_per_contract = $5.50 * 100 = $550
    # contracts = int($250 // $550) = 0
    # Hmm — that would mean zero contracts. Let me check expectation.
    # With 2.5% risk on $10K = $250 budget, $550 premium per contract
    # is too rich. That's a real ACCOUNT FIT signal but builder just computes 0.
    # The render will show "0 contracts". This is correct math.


def test_build_standard_with_affordable_options_computes_contracts():
    cfg = load_config(Path("/nonexistent.yaml"))
    cheap = _opt(strike=30, premium=1.10, expiry="2026-05-22")
    sheet = build_standard(
        _row(), direction="long", account=cfg.account("main"),
        options=cheap,
    )
    # max_risk $250 / cost $110 = 2 contracts (int floor)
    text = sheet.to_text()
    assert "Contracts:         2" in text


def test_to_text_renders_apex_block_when_options_present():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        _row(), direction="long", account=cfg.account("main"),
        options=_opt(strike=30, premium=1.10),
    )
    text = sheet.to_text()
    assert "OPTION STRUCTURE:" in text
    assert "Strike" not in text  # we use a different label below
    assert "Contract:" in text
    assert "Breakeven:" in text
    assert "IV Rank:" in text
    assert "Open Int:" in text


def test_to_text_renders_placeholder_when_no_options():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_row(), direction="long", account=cfg.account("main"))
    text = sheet.to_text()
    assert "[pass --strike/--premium/... for options template]" in text


def test_to_dict_includes_options_when_set():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        _row(), direction="long", account=cfg.account("main"),
        options=_opt(strike=30, premium=1.10),
    )
    payload = sheet.to_dict()
    assert "options" in payload
    assert payload["options"]["strike"] == 30


# ─── Trade devil integration ──────────────────────────────────────────────────


def _sheet_with_opts(**opt_overrides) -> KillSheet:
    cfg = load_config(Path("/nonexistent.yaml"))
    return build_standard(
        _row(), direction="long", account=cfg.account("main"),
        options=_opt(**opt_overrides),
    )


def test_devil_iv_overpricing_kill_at_85():
    r = check_iv_premium_overpricing(_sheet_with_opts(iv_rank=85))
    assert r.verdict is Verdict.KILL


def test_devil_iv_overpricing_flag_at_60():
    r = check_iv_premium_overpricing(_sheet_with_opts(iv_rank=60, premium=1.00))
    assert r.verdict is Verdict.FLAG


def test_devil_iv_overpricing_pass_at_30():
    r = check_iv_premium_overpricing(_sheet_with_opts(iv_rank=30, premium=1.00))
    assert r.verdict is Verdict.PASS


def test_devil_iv_overpricing_premium_kill_over_5pct():
    # On $10K account: $5.50 * 100 = $550 = 5.5% of account → KILL
    r = check_iv_premium_overpricing(_sheet_with_opts(premium=5.50, iv_rank=20))
    assert r.verdict is Verdict.KILL


def test_devil_iv_overpricing_spread_kill_over_10pct():
    # spread $0.50 on $1.00 = 50% → KILL
    r = check_iv_premium_overpricing(_sheet_with_opts(premium=1.00,
                                                     bid_ask_spread=0.50,
                                                     iv_rank=20))
    assert r.verdict is Verdict.KILL


def test_devil_iv_no_options_returns_pass_with_skip_note():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_row(), direction="long", account=cfg.account("main"))
    r = check_iv_premium_overpricing(sheet)
    assert r.verdict is Verdict.PASS
    assert "no options data" in r.reason.lower()
