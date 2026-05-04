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
