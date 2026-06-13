import json
from pathlib import Path
from unittest.mock import patch

import pytest

from config import load_config
from kill_sheet import (
    KillSheet,
    build_standard,
    calculate_position_size,
    derive_bias,
    derive_confidence,
)


# ─── Sizing ────────────────────────────────────────────────────────────────────


def test_position_size_dollar_budget_only():
    p = calculate_position_size(10_000.0, 0.025)
    assert p.max_risk_usd == 250.0
    assert p.units is None


def test_position_size_with_max_loss_per_unit():
    p = calculate_position_size(10_000.0, 0.025, max_loss_per_unit=50.0)
    assert p.max_risk_usd == 250.0
    assert p.units == 5


def test_position_size_rejects_invalid_risk_pct():
    with pytest.raises(ValueError):
        calculate_position_size(10_000.0, 1.5)


def test_position_size_rejects_zero_max_loss():
    with pytest.raises(ValueError):
        calculate_position_size(10_000.0, 0.025, max_loss_per_unit=0)


# ─── Bias / confidence ─────────────────────────────────────────────────────────


def _row(stack: str, signal: str, regime: str) -> dict:
    return {
        "ticker": "FAKE",
        "bar_date": "2026-04-22",
        "close": 100.0,
        "ma_ribbon": {"ma_10": 1, "ma_20": 1, "ma_50": 1, "ma_200": 1, "stack_state": stack},
        "stochastic": {"k": 50, "d": 50, "zone": "mid", "signal": signal},
        "sqn": {"sqn_value": 1.0, "regime": regime},
    }


def test_bias_bullish_for_full_bull():
    assert derive_bias(_row("full_bull", "neutral", "bull")) == "BULLISH"


def test_bias_bullish_for_bull_developing():
    assert derive_bias(_row("bull_developing", "neutral", "neutral")) == "BULLISH"


def test_bias_bearish_for_full_bear():
    assert derive_bias(_row("full_bear", "neutral", "bear")) == "BEARISH"


def test_bias_neutral_for_chop():
    assert derive_bias(_row("chop", "neutral", "neutral")) == "NEUTRAL"


def test_bias_neutral_for_compression():
    assert derive_bias(_row("compression", "neutral", "bull")) == "NEUTRAL"


def test_confidence_high_when_all_aligned():
    level, reason = derive_confidence(_row("full_bull", "bull_cross_oversold", "bull"))
    assert level == "HIGH"
    assert "bullish" in reason.lower() or "bull" in reason.lower()


def test_confidence_medium_when_two_of_three_aligned():
    level, _ = derive_confidence(_row("full_bull", "bull_cross_oversold", "neutral"))
    assert level == "MEDIUM"


def test_confidence_low_when_only_stack_aligned():
    level, _ = derive_confidence(_row("full_bull", "neutral", "bear"))
    assert level == "LOW"


def test_confidence_low_for_neutral_bias():
    level, _ = derive_confidence(_row("chop", "bull_cross_oversold", "bull"))
    assert level == "LOW"


# ─── Builder ───────────────────────────────────────────────────────────────────


def _full_row(ticker: str = "SPY") -> dict:
    return {
        "ticker": ticker,
        "bar_date": "2026-04-22",
        "close": 580.45,
        "ma_ribbon": {
            "ma_10": 578.90, "ma_20": 573.20, "ma_50": 565.40,
            "ma_200": 548.10, "stack_state": "full_bull",
        },
        "stochastic": {"k": 25.3, "d": 23.1, "zone": "oversold", "signal": "bull_cross_oversold"},
        "sqn": {"sqn_value": 1.20, "regime": "bull"},
    }


def test_build_standard_produces_kill_sheet():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_full_row(),
        direction="long",
        account=cfg.account("main"),
    )
    assert isinstance(sheet, KillSheet)
    assert sheet.ticker == "SPY"
    assert sheet.direction == "long"
    assert sheet.bias == "BULLISH"
    assert sheet.confidence == "HIGH"
    assert sheet.account_balance_usd == 10_000.0
    assert sheet.max_risk_usd == 250.0
    assert sheet.ma_stack == "full_bull"
    assert sheet.regime == "bull"


def test_build_standard_rejects_invalid_direction():
    cfg = load_config(Path("/nonexistent.yaml"))
    with pytest.raises(ValueError, match="direction"):
        build_standard(_full_row(), "sideways", cfg.account("main"))


def test_build_standard_rejects_error_row():
    cfg = load_config(Path("/nonexistent.yaml"))
    with pytest.raises(ValueError, match="error"):
        build_standard({"ticker": "X", "error": "no data"}, "long", cfg.account("main"))


def test_build_standard_uses_lotto_account():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_full_row(),
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
        risk_conviction="default",
    )
    assert sheet.account_balance_usd == 1_000.0
    # Lotto default risk = 7.5% of $1000 = $75
    assert sheet.max_risk_usd == 75.0


# ─── Lotto chase-warning gate ──────────────────────────────────────────────────
# Per CLAUDE.md orchestrator rule 13 + 2026-05-07 backtest calibration:
# lotto-account longs with SQN(20) > +2.5 are auto-flagged for chase-warning
# and require an explicit `lotto_chase_documented` attestation to authorize.


def _lotto_chase_row(sqn_20_value: float) -> dict:
    """Scan row for a lotto-account-long candidate with given SQN(20)."""
    row = _full_row()
    row["sqn"] = {**row["sqn"], "sqn_20_value": sqn_20_value, "regime_20": "strong_bull"}
    return row


def test_lotto_long_with_sqn20_chase_warning_blocks_entry():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_lotto_chase_row(sqn_20_value=2.7),
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
    )
    assert sheet.discipline_attestation.lotto_chase_warning is True
    assert sheet.discipline_attestation.lotto_chase_documented is False
    assert sheet.discipline_attestation.entry_authorized is False


def test_lotto_long_with_sqn20_chase_warning_unblocks_with_attestation():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_lotto_chase_row(sqn_20_value=2.7),
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
        attestation_user_inputs={"lotto_chase_documented": True},
    )
    assert sheet.discipline_attestation.lotto_chase_warning is True
    assert sheet.discipline_attestation.lotto_chase_documented is True
    assert sheet.discipline_attestation.entry_authorized is True


def test_lotto_long_below_chase_threshold_not_flagged():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_lotto_chase_row(sqn_20_value=2.5),  # boundary: > 2.5 required
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
    )
    assert sheet.discipline_attestation.lotto_chase_warning is False
    assert sheet.discipline_attestation.entry_authorized is True


def test_chop_daily_stack_blocks_entry():
    # Regression (fixed 2026-06): the daily-chop hard block compared against
    # "chop_tangled" — a token ma_ribbon never emits — so the "no trend = no
    # trade" anti-pattern silently never fired. A real "chop" stack must set
    # daily_chop and de-authorize entry.
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row("chop", "neutral", "bull"),
        direction="long",
        account=cfg.account("main"),
    )
    assert sheet.discipline_attestation.daily_chop is True
    assert sheet.discipline_attestation.entry_authorized is False


def test_neutral_regime_authorized_at_half_size():
    # Decision 2026-06: Neutral SQN(100) is a no-bias zone — authorize the entry
    # at HALF the conviction-tier size (not reject-unless-thesis), matching the
    # weekly-trend + trading-edge skills' "Neutral = half size".
    cfg = load_config(Path("/nonexistent.yaml"))
    bull = build_standard(_row("full_bull", "bull_cross_oversold", "bull"),
                          direction="long", account=cfg.account("main"))
    neutral = build_standard(_row("bull_developing", "bull_cross_oversold", "neutral"),
                             direction="long", account=cfg.account("main"))
    assert neutral.status == "AUTHORIZED"
    assert neutral.discipline_attestation.fighting_sqn_regime is False
    assert neutral.risk_pct == pytest.approx(bull.risk_pct / 2)


def test_opposing_regime_still_rejected_without_thesis():
    # Guard: half-size only applies to NEUTRAL — a regime that actively opposes
    # the direction (long into bear) is still REJECTED without a divergence thesis.
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_row("full_bear", "bear_cross_overbought", "bear"),
                           direction="long", account=cfg.account("main"))
    assert sheet.status == "REJECTED"


def test_kill_sheet_persists_rules_blocked_and_violations():
    # Decision 2026-06 (journal-first): the rule-engine outcome is persisted on
    # the sheet so a breach stays visible when the scorer loads it at close.
    from kill_sheet.store import _kill_sheet_from_dict
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_row("full_bull", "bull_cross_oversold", "bull"),
                           direction="long", account=cfg.account("main"))
    sheet.rules_blocked = True
    sheet.rule_violations = [{"rule": "max_premium_at_risk_pct", "severity": "block"}]
    restored = _kill_sheet_from_dict(sheet.to_dict())
    assert restored.rules_blocked is True
    assert restored.rule_violations == [{"rule": "max_premium_at_risk_pct", "severity": "block"}]


def test_lotto_short_with_sqn20_high_not_chase_warning():
    """Chase warning is long-only — bullish chase, not bearish."""
    cfg = load_config(Path("/nonexistent.yaml"))
    # Need a bearish setup for short to authorize; build a full_bear row.
    row = _full_row()
    row["ma_ribbon"]["stack_state"] = "full_bear"
    row["stochastic"] = {"k": 75.0, "d": 73.0, "zone": "overbought", "signal": "bear_cross_overbought"}
    row["sqn"] = {"sqn_value": -1.2, "regime": "bear", "sqn_20_value": 2.7, "regime_20": "strong_bull"}
    sheet = build_standard(
        scan_row=row,
        direction="short",
        account=cfg.account("lotto"),
        account_key="lotto",
    )
    assert sheet.discipline_attestation.lotto_chase_warning is False


def test_main_account_long_with_sqn20_high_not_chase_warning():
    """Chase warning is lotto-only — main / weekly accounts are not gated."""
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_lotto_chase_row(sqn_20_value=2.7),
        direction="long",
        account=cfg.account("main"),
        account_key="main",
    )
    assert sheet.discipline_attestation.lotto_chase_warning is False
    assert sheet.discipline_attestation.entry_authorized is True


def test_lotto_long_with_no_sqn20_data_not_flagged():
    """When SQN(20) data is missing (legacy / partial scan), do not auto-block."""
    cfg = load_config(Path("/nonexistent.yaml"))
    row = _full_row()
    # No sqn_20_value in the sqn dict — leave as base _full_row sqn block
    sheet = build_standard(
        scan_row=row,
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
    )
    assert sheet.discipline_attestation.lotto_chase_warning is False


def test_lotto_chase_warning_handles_numpy_scalar_sqn20():
    """Regression: live scan_row delivers SQN(20) as a numpy scalar; the chained
    comparison `np.float > 2.5` returns numpy.bool, which Pydantic can't
    serialize through the API layer (500). Builder must coerce to python bool.
    """
    import numpy as np
    cfg = load_config(Path("/nonexistent.yaml"))
    row = _lotto_chase_row(sqn_20_value=np.float64(2.7))
    sheet = build_standard(
        scan_row=row,
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
    )
    val = sheet.discipline_attestation.lotto_chase_warning
    assert val is True
    assert type(val) is bool, f"expected python bool, got {type(val)}"


# ─── Weekly-trend asset allowlist gate ────────────────────────────────────────
# Per 2026-05-07 backtest: IWM weekly-trend Sharpe -0.72 (33% win, all bull-
# regime trades lost) → BLOCKED until data revises. SPY Sharpe 0.80 / MaxDD
# -26% → MARGINAL warn (informational, does not gate). QQQ / GLD pass.


def _row_for(ticker: str) -> dict:
    row = _full_row(ticker=ticker)
    return row


def test_weekly_trend_iwm_blocked():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("IWM"),
        direction="long",
        account=cfg.account("main"),
        skill="weekly-trend-trader",
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is True
    assert sheet.discipline_attestation.weekly_trend_asset_marginal is False
    assert sheet.discipline_attestation.entry_authorized is False


def test_weekly_trend_iwm_unblocks_with_documented_override():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("IWM"),
        direction="long",
        account=cfg.account("main"),
        skill="weekly-trend-trader",
        attestation_user_inputs={"weekly_trend_asset_override_documented": True},
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is True
    assert sheet.discipline_attestation.weekly_trend_asset_override_documented is True
    assert sheet.discipline_attestation.entry_authorized is True


def test_weekly_trend_spy_marginal_does_not_gate():
    """SPY is a soft warn — flag fires but entry_authorized stays True."""
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("SPY"),
        direction="long",
        account=cfg.account("main"),
        skill="weekly-trend-trader",
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is False
    assert sheet.discipline_attestation.weekly_trend_asset_marginal is True
    assert sheet.discipline_attestation.entry_authorized is True


def test_weekly_trend_qqq_no_flag():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("QQQ"),
        direction="long",
        account=cfg.account("main"),
        skill="weekly-trend-trader",
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is False
    assert sheet.discipline_attestation.weekly_trend_asset_marginal is False
    assert sheet.discipline_attestation.entry_authorized is True


def test_weekly_trend_gld_no_flag():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("GLD"),
        direction="long",
        account=cfg.account("main"),
        skill="weekly-trend-trader",
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is False
    assert sheet.discipline_attestation.weekly_trend_asset_marginal is False
    assert sheet.discipline_attestation.entry_authorized is True


def test_lotto_iwm_not_subject_to_weekly_trend_gate():
    """Lotto on IWM was a backtest WINNER (Sharpe 1.74) — gate is skill-specific
    and must not fire on lotto entries."""
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("IWM"),
        direction="long",
        account=cfg.account("lotto"),
        account_key="lotto",
        skill="lotto-options",
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is False
    assert sheet.discipline_attestation.weekly_trend_asset_marginal is False


def test_weekly_trend_iwm_blocked_via_skill_config_object():
    """Skill parameter accepts SkillConfig too — gate must read .name."""
    cfg = load_config(Path("/nonexistent.yaml"))
    skill_cfg = cfg.skill("weekly-trend-trader")
    sheet = build_standard(
        scan_row=_row_for("IWM"),
        direction="long",
        account=cfg.account("main"),
        skill=skill_cfg,
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is True


def test_no_skill_no_weekly_trend_gate():
    """When skill isn't tagged, weekly-trend gate stays inert."""
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(
        scan_row=_row_for("IWM"),
        direction="long",
        account=cfg.account("main"),
    )
    assert sheet.discipline_attestation.weekly_trend_asset_blocked is False
    assert sheet.discipline_attestation.weekly_trend_asset_marginal is False


# ─── Rendering ─────────────────────────────────────────────────────────────────


def test_to_text_includes_all_sections():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_full_row(), "long", cfg.account("main"))
    text = sheet.to_text()

    expected_sections = [
        "KILL SHEET: SPY",
        "BIAS:        BULLISH",
        "CONFIDENCE:  HIGH",
        "REGIME (SQN 100d): bull",
        "WEEKLY CONTEXT:",
        "MA RIBBON (Daily)",
        "MA RIBBON (4H)",
        "STOCHASTIC (14,7,7)",
        "POSITION SIZING:",
        "TARGET:",
        "TRIGGER:",
        "INVALIDATION:",
        "OPTION STRUCTURE:",
        "EXIT PLAN:",
        "NOTES:",
    ]
    for section in expected_sections:
        assert section in text, f"missing section: {section}"


def test_to_text_marks_unfilled_fields_as_tbd():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_full_row(), "long", cfg.account("main"))
    text = sheet.to_text()
    assert "[TBD" in text  # placeholders for user-supplied fields


def test_to_json_round_trip():
    cfg = load_config(Path("/nonexistent.yaml"))
    sheet = build_standard(_full_row(), "long", cfg.account("main"))
    payload = json.loads(sheet.to_json())
    assert payload["ticker"] == "SPY"
    assert payload["bias"] == "BULLISH"
    assert payload["max_risk_usd"] == 250.0


# ─── CLI ───────────────────────────────────────────────────────────────────────


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_cli_main_success(mock_multi, mock_scan, tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch,
                          capsys: pytest.CaptureFixture):
    mock_scan.return_value = _full_row()
    mock_multi.return_value = {"1wk": {"error": "test"}, "4h": {"error": "test"}}
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "kill_sheets")
    monkeypatch.setattr("kill_sheet.cli.load_config",
                        lambda: load_config(Path("/nonexistent.yaml")))

    from kill_sheet.cli import main
    # --skip-devil isolates the kill-sheet-generation path from devil verdict
    code = main(["SPY", "--direction", "long", "--skip-devil"])
    assert code == 0
    out = capsys.readouterr().out
    assert "KILL SHEET: SPY" in out
    assert "Saved:" in out

    files = list((tmp_path / "kill_sheets").iterdir())
    assert any(f.suffix == ".json" for f in files)
    assert any(f.suffix == ".md" for f in files)


@patch("kill_sheet.cli.scan_ticker", create=True)
@patch("kill_sheet.cli.compute_multi_tf", create=True)
def test_cli_main_no_persist(mock_multi, mock_scan, tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch):
    mock_scan.return_value = _full_row()
    mock_multi.return_value = {"1wk": {"error": "test"}, "4h": {"error": "test"}}
    monkeypatch.setattr("kill_sheet.cli.KILL_SHEETS_DIR", tmp_path / "kill_sheets")
    monkeypatch.setattr("kill_sheet.cli.load_config",
                        lambda: load_config(Path("/nonexistent.yaml")))

    from kill_sheet.cli import main
    code = main(["SPY", "--direction", "long", "--no-persist", "--skip-devil"])
    assert code == 0
    assert not (tmp_path / "kill_sheets").exists()


@patch("kill_sheet.cli.scan_ticker", create=True)
def test_cli_main_unknown_account_returns_2(mock_scan, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("kill_sheet.cli.load_config",
                        lambda: load_config(Path("/nonexistent.yaml")))

    from kill_sheet.cli import main
    code = main(["SPY", "--direction", "long", "--account", "ghost"])
    assert code == 2


@patch("kill_sheet.cli.scan_ticker", create=True)
def test_cli_main_scan_failure_returns_1(mock_scan, monkeypatch: pytest.MonkeyPatch):
    mock_scan.side_effect = ValueError("no bars")
    monkeypatch.setattr("kill_sheet.cli.load_config",
                        lambda: load_config(Path("/nonexistent.yaml")))

    from kill_sheet.cli import main
    code = main(["BADSYM", "--direction", "long"])
    assert code == 1


# ── Sprint A: skill / tier / scan_phase tagging ─────────────────────────────


def test_kill_sheet_default_skill_fields_are_null():
    """No skill arg → all three fields stay None (preserves existing tests)."""
    from kill_sheet.builder import build_standard
    from config import load_config

    scan_row = _full_row()  # uses module helper
    ks = build_standard(scan_row, direction="long", account=load_config().account("main"))
    assert ks.skill is None
    assert ks.tier is None
    assert ks.scan_phase is None


def test_kill_sheet_populates_skill_and_tier_from_skill_config():
    from kill_sheet.builder import build_standard
    from config import load_config

    cfg = load_config()
    scan_row = _full_row()
    ks = build_standard(
        scan_row, direction="long", account=cfg.account("main"),
        skill=cfg.skill("weekly-trend-trader"),
        scan_phase="baseline",
    )
    assert ks.skill == "weekly-trend-trader"
    assert ks.tier == 1
    assert ks.scan_phase == "baseline"


def test_kill_sheet_skill_string_arg_leaves_tier_null():
    """String skill name without SkillConfig: name set, tier intentionally None."""
    from kill_sheet.builder import build_standard
    from config import load_config

    scan_row = _full_row()
    ks = build_standard(
        scan_row, direction="long", account=load_config().account("main"),
        skill="lotto-options",
    )
    assert ks.skill == "lotto-options"
    assert ks.tier is None  # builder won't infer without SkillConfig


def test_kill_sheet_scan_phase_independent_of_skill():
    """scan_phase can be set without a skill (e.g. free-range scan output)."""
    from kill_sheet.builder import build_standard
    from config import load_config

    scan_row = _full_row()
    ks = build_standard(
        scan_row, direction="long", account=load_config().account("main"),
        scan_phase="free_range",
    )
    assert ks.scan_phase == "free_range"
    assert ks.skill is None
    assert ks.tier is None
