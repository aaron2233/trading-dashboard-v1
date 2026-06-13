"""Discipline layer tests — model, rules, scorer, store, weekly review.

Per DISCIPLINE-LAYER-ADDITION.md acceptance criteria. Covers per-rule auto-eval,
profitable-violation flag, persistence, weekly aggregation with drift, plus the
KillSheet regime-gate / attestation extensions.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from config import load_config
from discipline import (
    DisciplineScore,
    DisciplineStore,
    LEGACY_CUTOFF,
    RULE_IDS,
    RULE_TEXT,
    compute_discipline_stats,
    compute_weekly_review,
    current_stage,
    is_legacy_position,
    score_trade,
    week_bounds,
)
from discipline.model import RuleResult, WeeklyReview
from kill_sheet.builder import build_standard
from kill_sheet.model import DisciplineAttestation, KillSheet
from kill_sheet.options import OptionsStructure
from positions.model import Position


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_scan_row(
    *,
    ticker: str = "SPY",
    regime: str = "bull",
    sqn_value: float = 1.0,
    sqn_20_value: float = 1.0,
    regime_20: str = "bull",
    ma_stack: str = "full_bull",
    weekly_alignment: str = "aligned",
) -> dict:
    return {
        "ticker": ticker,
        "timeframe": "1d",
        "bar_date": "2026-05-01",
        "close": 500.0,
        "ma_ribbon": {
            "ma_10": 510.0, "ma_20": 505.0, "ma_50": 490.0, "ma_200": 470.0,
            "stack_state": ma_stack,
        },
        "stochastic": {
            "k": 50.0, "d": 48.0, "zone": "mid", "signal": "neutral",
        },
        "sqn": {
            "sqn_value": sqn_value,
            "regime": regime,
            "sqn_20_value": sqn_20_value,
            "regime_20": regime_20,
            "diagnostic": "healthy_trend",
        },
    }


def _make_kill_sheet(**overrides) -> KillSheet:
    """Build a KillSheet via the production builder using a stub scan_row."""
    scan_row = _make_scan_row()
    cfg = load_config()
    account = cfg.account("main")
    options = OptionsStructure(
        strike=500, contract_type="call", expiry="2026-07-01",
        dte=60, premium=10.0, delta=0.55, iv_rank=30, open_interest=1000,
        bid_ask_spread=0.05,
    )
    ks = build_standard(
        scan_row, direction="long", account=account, account_key="main",
        intent="SWING", trigger_tf="Daily", options=options,
    )
    # Apply test-specific overrides
    for k, v in overrides.items():
        setattr(ks, k, v)
    return ks


def _make_position(
    *,
    ticker: str = "SPY",
    direction: str = "long",
    instrument: str = "call",
    pnl_usd: float = 100.0,
    max_loss_usd: float = 1000.0,
    closed_date: str | None = None,
    entry_date: str | None = None,
    expiry: str | None = "2026-07-01",
    account_key: str = "main",
    status: str = "closed",
) -> Position:
    if entry_date is None:
        entry_date = "2026-05-02T12:00:00+00:00"
    if closed_date is None:
        closed_date = "2026-05-15T12:00:00+00:00"
    return Position(
        id="test_" + ticker.lower(),
        ticker=ticker, direction=direction, instrument=instrument,
        account_key=account_key,
        entry_date=entry_date,
        contracts=1 if instrument != "shares" else None,
        shares=10 if instrument == "shares" else None,
        strike=500.0 if instrument != "shares" else None,
        expiry=expiry if instrument != "shares" else None,
        premium_paid_per_contract=10.0 if instrument != "shares" else None,
        total_cost_usd=1000.0,
        max_loss_usd=max_loss_usd,
        target_price=550.0, invalidation_price=480.0,
        status=status, closed_date=closed_date if status == "closed" else None,
        pnl_usd=pnl_usd if status == "closed" else None,
    )


# ── Stage detection ─────────────────────────────────────────────────────────


def test_stage_below_threshold_is_stage_1():
    assert current_stage(50_000) == "stage_1"
    assert current_stage(99_999.99) == "stage_1"
    assert current_stage(0) == "stage_1"


def test_stage_at_or_above_threshold_is_stage_2():
    assert current_stage(100_000) == "stage_2"
    assert current_stage(250_000) == "stage_2"


# ── Legacy detection ────────────────────────────────────────────────────────


def test_legacy_position_detection():
    assert is_legacy_position("2026-04-01T00:00:00+00:00") is True
    assert is_legacy_position("2026-05-01T00:00:00+00:00") is True
    assert is_legacy_position("2026-05-02T00:00:00+00:00") is False
    assert is_legacy_position("2026-05-10T00:00:00+00:00") is False
    assert is_legacy_position(None) is False


def test_legacy_cutoff_constant():
    assert LEGACY_CUTOFF == date(2026, 5, 2)


# ── Per-rule scoring ────────────────────────────────────────────────────────


def test_score_includes_all_rules():
    p = _make_position()
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    assert len(score.rules) == len(RULE_IDS)
    assert {r.rule_id for r in score.rules} == set(RULE_IDS)


def test_rule_15_trend_pyramid_stays_retired():
    # Rule 15 (trend-pyramid double-up) was retired with the trend-pyramid skill
    # (2026-05-07). Lock the engine at 14 rules and ensure no pyramid/double-up
    # rule creeps back in — the doc/skill side is kept in sync manually.
    assert len(RULE_IDS) == 14
    assert all("pyramid" not in r and "double" not in r for r in RULE_IDS)


def test_kill_sheet_complete_y_when_authorized():
    p = _make_position()
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "kill_sheet_complete")
    assert rule.score == "Y"


def test_kill_sheet_complete_n_when_rejected():
    p = _make_position()
    ks = _make_kill_sheet(status="REJECTED", rejection_reason="test")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "kill_sheet_complete")
    assert rule.score == "N"


def test_kill_sheet_complete_n_when_no_kill_sheet():
    p = _make_position()
    score = score_trade(p, kill_sheet=None)
    rule = next(r for r in score.rules if r.rule_id == "kill_sheet_complete")
    assert rule.score == "N"


def test_sqn100_authorized_y_for_long_in_bull():
    p = _make_position(direction="long")
    ks = _make_kill_sheet(regime="bull")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn100_authorized")
    assert rule.score == "Y"


def test_sqn100_authorized_n_for_long_in_bear():
    p = _make_position(direction="long")
    # Build via the builder with a Bear regime — this also produces REJECTED status
    scan_row = _make_scan_row(regime="bear", sqn_value=-1.0)
    cfg = load_config()
    account = cfg.account("main")
    ks = build_standard(scan_row, direction="long", account=account)
    assert ks.status == "REJECTED"
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn100_authorized")
    assert rule.score == "N"


def test_sqn100_authorized_y_for_long_put_in_bear():
    # Regression (PYPL 2026-05-18): a long put = bearish thesis, so a Bear
    # SQN(100) regime authorizes it. The previous code keyed off `direction`
    # alone and would score this as misaligned.
    p = _make_position(direction="long", instrument="put")
    ks = _make_kill_sheet(regime="bear")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn100_authorized")
    assert rule.score == "Y"


def test_sqn20_respected_n_long_put_in_capitulation():
    # Long put = bearish; SQN(20) < -2.5 = capitulation extreme = chase zone
    # for bearish entries, should score N.
    p = _make_position(direction="long", instrument="put")
    ks = _make_kill_sheet(sqn_20_value=-2.6, regime_20="strong_bear",
                          regime="bear")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn20_respected")
    assert rule.score == "N"


def test_no_spreads_margin_n_for_short_option():
    # A sold/short option (direction='short') is an anti-pattern in this
    # long-only cash account. The old scorer whitelisted by instrument string
    # and passed a naked short call (on-disk 7284781d) as compliant.
    for instrument in ("call", "put"):
        p = _make_position(direction="short", instrument=instrument)
        score = score_trade(p)
        rule = next(r for r in score.rules if r.rule_id == "no_spreads_margin")
        assert rule.score == "N", instrument


def test_no_spreads_margin_y_for_long_options_and_shares():
    for direction, instrument in (("long", "call"), ("long", "put"), ("long", "shares")):
        p = _make_position(direction=direction, instrument=instrument)
        score = score_trade(p)
        rule = next(r for r in score.rules if r.rule_id == "no_spreads_margin")
        assert rule.score == "Y", (direction, instrument)


def test_sqn100_authorized_y_with_divergence_thesis():
    p = _make_position(direction="long")
    scan_row = _make_scan_row(regime="bear", sqn_value=-1.0)
    cfg = load_config()
    account = cfg.account("main")
    ks = build_standard(
        scan_row, direction="long", account=account,
        divergence_thesis="Reading the bottom; Powell pivot signal",
    )
    # With thesis, status should be AUTHORIZED
    assert ks.status == "AUTHORIZED"
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn100_authorized")
    assert rule.score == "Y"


def test_sqn20_respected_n_chase_zone_long():
    p = _make_position(direction="long")
    ks = _make_kill_sheet(sqn_20_value=2.8, regime_20="strong_bull")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn20_respected")
    assert rule.score == "N"


def test_sqn20_respected_n_capitulation_extreme_short():
    p = _make_position(direction="short")
    ks = _make_kill_sheet(sqn_20_value=-2.6, regime_20="strong_bear")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn20_respected")
    assert rule.score == "N"


def test_sqn20_respected_y_in_normal_zone():
    p = _make_position(direction="long")
    ks = _make_kill_sheet(sqn_20_value=1.0, regime_20="bull")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn20_respected")
    assert rule.score == "Y"


def test_size_within_tier_y_under_3pct():
    p = _make_position(max_loss_usd=200)  # 2% of $10k main
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "size_within_tier")
    assert rule.score == "Y"


def test_size_within_tier_n_over_3pct():
    p = _make_position(max_loss_usd=500)  # 5% of $10k main
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "size_within_tier")
    assert rule.score == "N"


def test_dte_min_7_y_when_dte_geq_7():
    p = _make_position(
        entry_date="2026-05-02T00:00:00+00:00",
        expiry="2026-05-15",
    )
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "dte_min_7")
    assert rule.score == "Y"


def test_dte_min_7_n_when_under_7_no_attestation():
    p = _make_position(
        entry_date="2026-05-02T00:00:00+00:00",
        expiry="2026-05-05",
    )
    ks = _make_kill_sheet()
    # Default attestation has explicit_0dte_framing=False
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "dte_min_7")
    assert rule.score == "N"


def test_dte_min_7_lotto_override_passes_under_7():
    p = _make_position(
        account_key="lotto",
        entry_date="2026-05-02T00:00:00+00:00",
        expiry="2026-05-04",  # 2 DTE
    )
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "dte_min_7")
    assert rule.score == "Y"


def test_dte_min_7_na_for_shares():
    p = _make_position(instrument="shares", expiry=None)
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "dte_min_7")
    assert rule.score == "N/A"


def test_iv_rank_under_70_y():
    # _make_kill_sheet defaults to iv_rank=30
    p = _make_position()
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "iv_rank_under_70")
    assert rule.score == "Y"


def test_iv_rank_under_70_n_high_iv_no_thesis():
    p = _make_position()
    ks = _make_kill_sheet()
    ks.options.iv_rank = 80  # type: ignore[union-attr]
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "iv_rank_under_70")
    assert rule.score == "N"


def test_iv_rank_under_70_y_with_attestation():
    p = _make_position()
    ks = _make_kill_sheet()
    ks.options.iv_rank = 80  # type: ignore[union-attr]
    ks.discipline_attestation = DisciplineAttestation(
        iv_rank_over_70=True,
        explicit_post_earnings_crush_thesis=True,
    )
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "iv_rank_under_70")
    assert rule.score == "Y"


def test_no_spreads_margin_y_for_call():
    p = _make_position(instrument="call")
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    rule = next(r for r in score.rules if r.rule_id == "no_spreads_margin")
    assert rule.score == "Y"


def test_daily_not_chop_n_when_chop():
    p = _make_position()
    scan_row = _make_scan_row(ma_stack="chop")  # real ma_ribbon token (was "chop_tangled", which never matched)
    cfg = load_config()
    account = cfg.account("main")
    ks = build_standard(scan_row, direction="long", account=account)
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "daily_not_chop")
    assert rule.score == "N"


def test_cut_at_60_70_y_at_target():
    p = _make_position(pnl_usd=500.0, max_loss_usd=1000.0)
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "Y"


def test_cut_rule_uses_total_cost_when_max_loss_zeroed_by_partial_close():
    # Regression (fixed 2026-06): partial_close zeroes max_loss_usd on the final
    # leg, which previously made _r_cut_at_60_70 auto-pass ("max-loss missing")
    # and blinded the -60/-70% cut check — a deep loss past the cut could score
    # compliant. For options it must use total_cost_usd (1000 in the helper) —
    # so a -$760 loss = -76% > 70% cut threshold → N.
    p = _make_position(instrument="call", pnl_usd=-760.0, max_loss_usd=0.0)
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "N"


def test_cut_rule_passes_at_40pct_loss_with_zeroed_max_loss():
    # Positive control: a -40% loss (within the cut) still passes when
    # max_loss_usd is zeroed, using total_cost_usd as the denominator.
    p = _make_position(instrument="call", pnl_usd=-400.0, max_loss_usd=0.0)
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "Y"


def test_cut_rule_uses_per_account_threshold_lotto_50pct():
    # Per-account cut threshold (2026-06): lotto cuts at -50%, so a lotto trade
    # held to -55% of premium is a violation even though it's under the 70%
    # default. cut_rule_pct is stamped on the kill sheet by the builder.
    p = _make_position(instrument="call", pnl_usd=-550.0, max_loss_usd=0.0)  # 55% of $1000
    ks = _make_kill_sheet(account_key="lotto", cut_rule_pct=-0.50)
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "N"


def test_cut_rule_passes_within_lotto_threshold():
    # -45% is within the -50% lotto cut → Y.
    p = _make_position(instrument="call", pnl_usd=-450.0, max_loss_usd=0.0)  # 45% of $1000
    ks = _make_kill_sheet(account_key="lotto", cut_rule_pct=-0.50)
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "Y"


def test_sqn100_neutral_regime_passes_not_violation():
    # Neutral SQN(100) is a no-bias zone (half-size tradeable per every skill),
    # not "fighting the regime" — it must pass, not score as a violation.
    # (Default chosen 2026-06; see _r_sqn100_authorized.)
    p = _make_position(direction="long", instrument="call")
    ks = _make_kill_sheet(regime="neutral")
    score = score_trade(p, kill_sheet=ks)
    rule = next(r for r in score.rules if r.rule_id == "sqn100_authorized")
    assert rule.score == "Y"


def test_score_trade_stamps_kill_sheet_id_from_sheet():
    # Regression (fixed 2026-06): the score must record the kill_sheet_id it was
    # scored against (was hardcoded None with a stale "no ID yet" TODO).
    p = _make_position()
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    assert score.kill_sheet_id == ks.id


def test_load_kill_sheet_for_returns_none_without_id():
    # No kill_sheet_id on the position → resolver returns None (no disk hit).
    from discipline import load_kill_sheet_for
    p = _make_position()
    assert getattr(p, "kill_sheet_id", None) is None
    assert load_kill_sheet_for(p) is None


def test_portfolio_sleeve_exempt_from_cut_and_tier_rules():
    # Portfolio sleeve exits on thesis-break, sizes on a per-position % cap —
    # the -60/-70% cut rule and the 0.5-3% conviction-tier check don't apply.
    # A shares position closed at a deep loss must score N/A on both, not N.
    p = _make_position(instrument="shares", account_key="portfolio",
                       pnl_usd=-400.0, max_loss_usd=200.0)
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    cut = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    tier = next(r for r in score.rules if r.rule_id == "size_within_tier")
    assert cut.score == "N/A"
    assert tier.score == "N/A"


def test_cut_at_60_70_y_within_band():
    p = _make_position(pnl_usd=-650.0, max_loss_usd=1000.0)  # 65% loss
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "Y"


def test_cut_at_60_70_n_held_too_long():
    p = _make_position(pnl_usd=-900.0, max_loss_usd=1000.0)  # 90% loss
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    rule = next(r for r in score.rules if r.rule_id == "cut_at_60_70")
    assert rule.score == "N"


# ── Aggregate score / profitable-violation ──────────────────────────────────


def test_full_adherence_when_all_y():
    p = _make_position()
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    # Most rules should be Y; some may be N/A. Verify denominator > 0.
    assert score.score_denominator > 0
    if score.score_numerator == score.score_denominator:
        assert score.full_adherence is True
    # Any 'N' would defeat full_adherence
    has_n = any(r.score == "N" for r in score.rules)
    assert score.full_adherence == (not has_n)


def test_profitable_violation_flagged_when_n_and_pnl_positive():
    p = _make_position(pnl_usd=200.0, max_loss_usd=500)  # 4% — fails size_within_tier
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    assert score.profitable_violation is True
    assert score.counterfactual_loss_usd is not None
    # 65% of max_loss_usd, negative
    assert score.counterfactual_loss_usd < 0


def test_profitable_violation_not_flagged_when_pnl_negative():
    p = _make_position(pnl_usd=-200.0, max_loss_usd=500)  # 4% — fails size_within_tier
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    assert score.profitable_violation is False


def test_profitable_violation_not_flagged_when_score_perfect():
    p = _make_position(pnl_usd=200.0, max_loss_usd=200)  # 2% — passes size_within_tier
    ks = _make_kill_sheet()
    score = score_trade(p, kill_sheet=ks)
    if all(r.score in ("Y", "N/A") for r in score.rules):
        assert score.profitable_violation is False


# ── Persistence ─────────────────────────────────────────────────────────────


def test_store_save_load_roundtrip(tmp_path: Path):
    store = DisciplineStore(base_dir=tmp_path)
    p = _make_position()
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    store.save_score(score)
    loaded = store.load_score(score.position_id)
    assert loaded.position_id == score.position_id
    assert loaded.score_numerator == score.score_numerator
    assert loaded.score_denominator == score.score_denominator
    assert loaded.profitable_violation == score.profitable_violation
    assert len(loaded.rules) == 14


def test_store_load_missing_raises(tmp_path: Path):
    store = DisciplineStore(base_dir=tmp_path)
    with pytest.raises(KeyError):
        store.load_score("not-a-real-id")


def test_store_iter_skips_corrupt(tmp_path: Path):
    store = DisciplineStore(base_dir=tmp_path)
    (tmp_path / "junk.json").write_text("garbage")
    p = _make_position()
    score = score_trade(p, kill_sheet=_make_kill_sheet())
    store.save_score(score)
    found = list(store.iter_scores())
    assert len(found) == 1


# ── Stats aggregation ───────────────────────────────────────────────────────


def test_stats_empty_returns_zeros():
    stats = compute_discipline_stats([])
    assert stats.trades_scored == 0
    assert stats.avg_discipline_score == 0.0
    assert stats.profitable_violation_count == 0


def test_stats_aggregate_three_trades():
    p1 = _make_position(pnl_usd=200, max_loss_usd=200)
    p2 = _make_position(pnl_usd=-300, max_loss_usd=300)
    p3 = _make_position(pnl_usd=400, max_loss_usd=500)  # profitable violation (size 5%)
    ks = _make_kill_sheet()
    s1 = score_trade(p1, kill_sheet=ks)
    s2 = score_trade(p2, kill_sheet=ks)
    s3 = score_trade(p3, kill_sheet=ks)
    stats = compute_discipline_stats([s1, s2, s3])
    assert stats.trades_scored == 3
    assert 0 <= stats.avg_discipline_score <= 1.0
    # p3 is the profitable violation
    assert stats.profitable_violation_count >= 1


def test_stats_drift_trend_improving():
    stats = compute_discipline_stats([], prior_avg_for_drift=None)
    assert stats.drift_trend == "flat"
    # With actual data
    p = _make_position(pnl_usd=100, max_loss_usd=200)
    ks = _make_kill_sheet()
    s = score_trade(p, kill_sheet=ks)
    stats = compute_discipline_stats([s], prior_avg_for_drift=0.3)
    if s.score > 0.35:
        assert stats.drift_trend == "improving"


# ── Weekly review ───────────────────────────────────────────────────────────


def test_week_bounds_returns_sun_to_sat():
    # Friday 2026-05-01 → Sunday 2026-04-26 to Saturday 2026-05-02
    sunday, saturday = week_bounds(date(2026, 5, 1))
    assert sunday.weekday() == 6  # Sunday in Python's weekday: Sun=6
    assert saturday.weekday() == 5  # Saturday=5
    assert (saturday - sunday).days == 6


def test_weekly_review_empty_week(tmp_path: Path):
    store = DisciplineStore(base_dir=tmp_path)
    review = compute_weekly_review(date(2026, 5, 5), store=store)
    assert review.trades_scored == 0
    assert review.avg_discipline_score == 0.0
    assert review.profitable_violation_count == 0


def test_weekly_review_picks_up_trades_in_window(tmp_path: Path):
    store = DisciplineStore(base_dir=tmp_path)
    # Trade closed mid-week
    p = _make_position(closed_date="2026-05-05T12:00:00+00:00", pnl_usd=100, max_loss_usd=300)
    s = score_trade(p, kill_sheet=_make_kill_sheet())
    store.save_score(s)
    review = compute_weekly_review(date(2026, 5, 5), store=store)
    assert review.trades_scored == 1


def test_weekly_review_lockdown_persisted(tmp_path: Path):
    store = DisciplineStore(base_dir=tmp_path)
    review = WeeklyReview(
        week_start="2026-05-03", week_end="2026-05-09",
        trades_scored=2, avg_discipline_score=0.85,
        full_adherence_count=1, any_violation_count=1,
        profitable_violation_count=0,
        most_violated_rule="size_within_tier",
        drift_trend="flat", pnl_usd=300,
    )
    store.save_weekly(review)
    updated = store.update_lockdown("2026-05-03", "Always recheck SQN(20) before sizing up")
    assert updated.lockdown_behavior == "Always recheck SQN(20) before sizing up"
    reload = store.load_weekly("2026-05-03")
    assert reload is not None
    assert reload.lockdown_behavior == "Always recheck SQN(20) before sizing up"


# ── KillSheet builder regime gate ───────────────────────────────────────────


def test_builder_authorizes_long_in_bull():
    scan_row = _make_scan_row(regime="bull")
    cfg = load_config()
    ks = build_standard(scan_row, direction="long", account=cfg.account("main"))
    assert ks.status == "AUTHORIZED"
    assert ks.rejection_reason is None


def test_builder_rejects_long_in_bear_without_thesis():
    scan_row = _make_scan_row(regime="bear", sqn_value=-1.0)
    cfg = load_config()
    ks = build_standard(scan_row, direction="long", account=cfg.account("main"))
    assert ks.status == "REJECTED"
    assert ks.rejection_reason is not None
    assert "regime" in ks.rejection_reason.lower()


def test_builder_authorizes_with_divergence_thesis():
    scan_row = _make_scan_row(regime="bear", sqn_value=-1.0)
    cfg = load_config()
    ks = build_standard(
        scan_row, direction="long", account=cfg.account("main"),
        divergence_thesis="VIX spike post-Powell; bottom signal forming",
    )
    assert ks.status == "AUTHORIZED"
    assert ks.divergence_thesis is not None
    assert ks.discipline_attestation is not None
    assert ks.discipline_attestation.divergence_thesis_documented is True


def test_builder_auto_attests_iv_rank_over_70():
    scan_row = _make_scan_row(regime="bull")
    cfg = load_config()
    options = OptionsStructure(
        strike=500, contract_type="call", expiry="2026-07-01",
        dte=60, premium=10.0, delta=0.55, iv_rank=85,
    )
    ks = build_standard(
        scan_row, direction="long", account=cfg.account("main"), options=options,
    )
    assert ks.discipline_attestation is not None
    assert ks.discipline_attestation.iv_rank_over_70 is True


def test_builder_auto_attests_dte_under_7():
    scan_row = _make_scan_row(regime="bull")
    cfg = load_config()
    options = OptionsStructure(
        strike=500, contract_type="call", expiry="2026-05-05",
        dte=3, premium=2.0,
    )
    ks = build_standard(
        scan_row, direction="long", account=cfg.account("lotto"), options=options,
    )
    assert ks.discipline_attestation is not None
    assert ks.discipline_attestation.dte_under_7 is True


def test_builder_attestation_user_inputs_propagate_to_kill_sheet():
    """Tier-3 closure: API/UI passes attestation_user_inputs as a dict; builder maps to flags."""
    scan_row = _make_scan_row(regime="bull")
    cfg = load_config()
    options = OptionsStructure(
        strike=500, contract_type="call", expiry="2026-05-05",
        dte=3, premium=2.0, iv_rank=85,
    )
    ks = build_standard(
        scan_row, direction="long", account=cfg.account("lotto"), options=options,
        attestation_user_inputs={
            "explicit_post_earnings_crush_thesis": True,
            "explicit_0dte_framing": True,
        },
    )
    assert ks.discipline_attestation is not None
    assert ks.discipline_attestation.iv_rank_over_70 is True
    assert ks.discipline_attestation.dte_under_7 is True
    # User cleared both auto-flagged anti-patterns → entry authorized
    assert ks.discipline_attestation.entry_authorized is True


def test_builder_entry_authorized_requires_attestation_when_iv_high():
    scan_row = _make_scan_row(regime="bull")
    cfg = load_config()
    options = OptionsStructure(
        strike=500, contract_type="call", expiry="2026-07-01",
        dte=60, premium=10.0, iv_rank=85,
    )
    ks = build_standard(
        scan_row, direction="long", account=cfg.account("main"), options=options,
    )
    # No attestation provided → entry NOT authorized
    assert ks.discipline_attestation is not None
    assert ks.discipline_attestation.entry_authorized is False
    # With attestation, builder re-runs and authorizes
    ks2 = build_standard(
        scan_row, direction="long", account=cfg.account("main"), options=options,
        attestation_user_inputs={"explicit_post_earnings_crush_thesis": True},
    )
    assert ks2.discipline_attestation.entry_authorized is True
