"""Auto-scoring engine for the 15-rule discipline checklist.

Per DISCIPLINE-LAYER-ADDITION.md: 13 of 15 rules are auto-evaluable from
KillSheet + Position data already captured. The two manual ones
(`trade_devil_passed`, `no_average_down`) default to `Y` with `auto_evaluated=False`
when we can't determine — the user can override via the persistence endpoint.

Lotto override (per open question 2 in the spec): lotto trades use a relaxed
DTE_min rule (DTE >= 0 instead of DTE >= 7) since lotto explicitly trades 0DTE.

Counter-Weekly auto-pass (per open question 3): if the kill sheet has a
`counter_weekly_thesis`, rule 11 (`weekly_not_opposing`) auto-passes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from discipline.model import (
    RULE_IDS,
    RULE_TEXT,
    DisciplineScore,
    RuleResult,
)
from kill_sheet.model import KillSheet
from positions.model import Position


# Cut-loss threshold for the counterfactual computation — midpoint of the
# 60-70% band. Used when flagging profitable violations.
COUNTERFACTUAL_CUT_FRACTION = 0.65


# MA stack states that count as "chop" (rule 10 fails when at entry).
CHOP_STATES = {"chop_tangled", "compression"}


@dataclass
class ScoringContext:
    """External data the scorer needs beyond the position + kill sheet."""
    kill_sheet: KillSheet | None = None
    pyramid_active_at_entry: bool | None = None  # True if a pyramid was active on
                                                  # same ticker+direction at entry
    earlier_open_position_at_entry: bool | None = None  # for averaging-down


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None


def _result(rule_id: str, score: str, auto: bool, note: str | None = None) -> RuleResult:
    return RuleResult(rule_id=rule_id, score=score, auto_evaluated=auto, note=note)  # type: ignore[arg-type]


# ── Per-rule evaluators ──────────────────────────────────────────────────────


def _r_kill_sheet_complete(p: Position, ctx: ScoringContext) -> RuleResult:
    if ctx.kill_sheet is None:
        return _result("kill_sheet_complete", "N", True,
                       note="No kill sheet on file for this position")
    if ctx.kill_sheet.status == "REJECTED":
        return _result("kill_sheet_complete", "N", True,
                       note=f"Kill sheet was REJECTED: {ctx.kill_sheet.rejection_reason}")
    return _result("kill_sheet_complete", "Y", True)


def _r_sqn100_authorized(p: Position, ctx: ScoringContext) -> RuleResult:
    ks = ctx.kill_sheet
    if ks is None:
        return _result("sqn100_authorized", "N", True, note="No kill sheet")
    long_ok = p.direction == "long" and ks.regime in ("bull", "strong_bull")
    short_ok = p.direction == "short" and ks.regime in ("bear", "strong_bear")
    if long_ok or short_ok:
        return _result("sqn100_authorized", "Y", True,
                       note=f"SQN(100)={ks.sqn_value}, regime={ks.regime}")
    if ks.divergence_thesis:
        return _result("sqn100_authorized", "Y", True,
                       note=f"Counter-regime trade authorized by divergence thesis")
    return _result("sqn100_authorized", "N", True,
                   note=f"SQN(100) regime '{ks.regime}' did not authorize {p.direction}")


def _r_sqn20_respected(p: Position, ctx: ScoringContext) -> RuleResult:
    """SQN(20) tactical state respected.

    Anti-patterns: chasing longs when SQN(20) > +2.5; chasing puts when
    SQN(20) < -1.9 alongside ATH. If SQN(20) data is missing on the kill sheet
    (legacy fixture), default to Y with auto_evaluated=False.
    """
    ks = ctx.kill_sheet
    if ks is None or ks.sqn_20_value is None:
        return _result("sqn20_respected", "Y", False,
                       note="SQN(20) not captured on kill sheet")
    if p.direction == "long" and ks.sqn_20_value > 2.5:
        return _result("sqn20_respected", "N", True,
                       note=f"SQN(20)={ks.sqn_20_value:.2f} > +2.5 (chase zone) at long entry")
    if p.direction == "short" and ks.sqn_20_value < -2.5:
        return _result("sqn20_respected", "N", True,
                       note=f"SQN(20)={ks.sqn_20_value:.2f} < -2.5 (capitulation extreme) at short entry")
    return _result("sqn20_respected", "Y", True,
                   note=f"SQN(20)={ks.sqn_20_value:.2f} ({ks.regime_20})")


def _r_size_within_tier(p: Position, ctx: ScoringContext) -> RuleResult:
    ks = ctx.kill_sheet
    if ks is None or ks.account_balance_usd <= 0:
        return _result("size_within_tier", "Y", False,
                       note="Account balance unavailable")
    pct = p.max_loss_usd / ks.account_balance_usd
    if pct <= 0.03:
        return _result("size_within_tier", "Y", True,
                       note=f"Risk {pct*100:.2f}% of ${ks.account_balance_usd:,.0f}")
    return _result("size_within_tier", "N", True,
                   note=f"Risk {pct*100:.2f}% exceeds 3% tier cap")


def _r_trigger_dte_match(p: Position, ctx: ScoringContext) -> RuleResult:
    """Orchestrator rule 6: trigger TF → DTE band match."""
    if p.instrument == "shares":
        return _result("trigger_dte_match", "N/A", True, note="Shares position")
    ks = ctx.kill_sheet
    if ks is None or ks.options is None:
        return _result("trigger_dte_match", "Y", False, note="No options structure")
    dte = ks.options.dte
    tf = ks.trigger_tf
    # Per orchestrator rule 6
    if tf == "2H" and 0 <= dte <= 14:
        return _result("trigger_dte_match", "Y", True, note=f"2H trigger / {dte} DTE")
    if tf == "4H" and 14 <= dte <= 30:
        return _result("trigger_dte_match", "Y", True, note=f"4H trigger / {dte} DTE")
    if tf == "Daily" and 21 <= dte <= 45:
        return _result("trigger_dte_match", "Y", True, note=f"Daily trigger / {dte} DTE")
    if tf == "Weekly" and dte >= 120:
        return _result("trigger_dte_match", "Y", True, note=f"Weekly trigger / {dte} DTE")
    return _result("trigger_dte_match", "N", True,
                   note=f"Trigger TF {tf} mismatch with {dte} DTE")


def _r_iv_rank_under_70(p: Position, ctx: ScoringContext) -> RuleResult:
    if p.instrument == "shares":
        return _result("iv_rank_under_70", "N/A", True, note="Shares position")
    ks = ctx.kill_sheet
    if ks is None or ks.options is None or ks.options.iv_rank is None:
        return _result("iv_rank_under_70", "Y", False,
                       note="IV Rank not captured")
    iv = ks.options.iv_rank
    if iv <= 70:
        return _result("iv_rank_under_70", "Y", True, note=f"IV Rank {iv:.1f}%")
    # IV > 70 — Y only with documented thesis on the attestation block
    att = ks.discipline_attestation
    if att is not None and att.explicit_post_earnings_crush_thesis:
        return _result("iv_rank_under_70", "Y", True,
                       note=f"IV Rank {iv:.1f}% — post-earnings crush thesis attested")
    return _result("iv_rank_under_70", "N", True,
                   note=f"IV Rank {iv:.1f}% > 70% without crush thesis")


def _r_dte_min_7(p: Position, ctx: ScoringContext) -> RuleResult:
    if p.instrument == "shares":
        return _result("dte_min_7", "N/A", True, note="Shares position")
    if not p.expiry:
        return _result("dte_min_7", "Y", False, note="Expiry missing")
    entry_d = _parse_date(p.entry_date)
    expiry_d = _parse_date(p.expiry)
    if entry_d is None or expiry_d is None:
        return _result("dte_min_7", "Y", False, note="Could not parse dates")
    dte = (expiry_d - entry_d).days
    # Lotto override: 0DTE allowed
    if p.account_key == "lotto" and dte >= 0:
        return _result("dte_min_7", "Y", True,
                       note=f"DTE={dte}; lotto account override")
    if dte >= 7:
        return _result("dte_min_7", "Y", True, note=f"DTE={dte}")
    # < 7 — Y if explicit 0DTE framing attested
    ks = ctx.kill_sheet
    if ks is not None and ks.discipline_attestation is not None:
        if ks.discipline_attestation.explicit_0dte_framing:
            return _result("dte_min_7", "Y", True,
                           note=f"DTE={dte}; explicit 0DTE framing attested")
    return _result("dte_min_7", "N", True,
                   note=f"DTE={dte} < 7 without 0DTE framing")


def _r_trade_devil_passed(p: Position, ctx: ScoringContext) -> RuleResult:
    """Trade-devil verdict not currently stored on kill sheet — default Y manual."""
    return _result("trade_devil_passed", "Y", False,
                   note="Trade-devil verdict not captured at scoring time; review manually")


def _r_no_spreads_margin(p: Position, ctx: ScoringContext) -> RuleResult:
    if p.instrument in ("call", "put", "shares"):
        return _result("no_spreads_margin", "Y", True,
                       note=f"Instrument: {p.instrument}")
    return _result("no_spreads_margin", "N", True,
                   note=f"Non-cash instrument: {p.instrument}")


def _r_daily_not_chop(p: Position, ctx: ScoringContext) -> RuleResult:
    ks = ctx.kill_sheet
    if ks is None:
        return _result("daily_not_chop", "Y", False, note="No kill sheet")
    if ks.ma_stack in CHOP_STATES:
        return _result("daily_not_chop", "N", True,
                       note=f"Daily MA stack: {ks.ma_stack}")
    return _result("daily_not_chop", "Y", True, note=f"Daily MA stack: {ks.ma_stack}")


def _r_weekly_not_opposing(p: Position, ctx: ScoringContext) -> RuleResult:
    ks = ctx.kill_sheet
    if ks is None or not ks.weekly_alignment:
        return _result("weekly_not_opposing", "Y", False, note="Weekly alignment unknown")
    # Counter-Weekly auto-pass when thesis documented (open question 3 recommendation)
    if ks.counter_weekly_thesis:
        return _result("weekly_not_opposing", "Y", True,
                       note=f"Counter-Weekly thesis documented: {ks.counter_weekly_thesis[:60]}…"
                            if len(ks.counter_weekly_thesis) > 60
                            else f"Counter-Weekly thesis documented: {ks.counter_weekly_thesis}")
    if "opposing" in ks.weekly_alignment.lower() or "counter" in ks.weekly_alignment.lower():
        return _result("weekly_not_opposing", "N", True,
                       note=f"Weekly alignment: {ks.weekly_alignment}")
    return _result("weekly_not_opposing", "Y", True,
                   note=f"Weekly alignment: {ks.weekly_alignment}")


def _r_cut_at_60_70(p: Position, ctx: ScoringContext) -> RuleResult:
    if p.pnl_usd is None or p.max_loss_usd <= 0:
        return _result("cut_at_60_70", "Y", False, note="P&L or max-loss missing")
    if p.pnl_usd >= 0:
        return _result("cut_at_60_70", "Y", True, note=f"Closed at +${p.pnl_usd:,.0f}")
    loss_ratio = abs(p.pnl_usd) / p.max_loss_usd
    if loss_ratio <= 0.7:
        return _result("cut_at_60_70", "Y", True,
                       note=f"Cut at {loss_ratio*100:.0f}% of max loss")
    return _result("cut_at_60_70", "N", True,
                   note=f"Held to {loss_ratio*100:.0f}% of max loss > 70% cut threshold")


def _r_exit_within_dte_band(p: Position, ctx: ScoringContext) -> RuleResult:
    if p.instrument == "shares":
        return _result("exit_within_dte_band", "N/A", True, note="Shares position")
    if not p.expiry or p.status != "closed":
        return _result("exit_within_dte_band", "Y", False, note="Position not closed or no expiry")
    entry_d = _parse_date(p.entry_date)
    expiry_d = _parse_date(p.expiry)
    closed_d = _parse_date(p.closed_date)
    if not all((entry_d, expiry_d, closed_d)):
        return _result("exit_within_dte_band", "Y", False, note="Date parse failed")
    dte_at_entry = (expiry_d - entry_d).days  # type: ignore[operator]
    if dte_at_entry <= 0:
        return _result("exit_within_dte_band", "Y", False, note="Non-positive DTE at entry")
    held = (closed_d - entry_d).days  # type: ignore[operator]
    held_fraction = held / dte_at_entry
    # apex/lotto rule: exit before 50% DTE elapsed.
    # weekly-trend-trader rule: held with >60 DTE remaining.
    # Without skill identification, use apex/lotto (most common per ~/CLAUDE.md).
    if held_fraction <= 0.5:
        return _result("exit_within_dte_band", "Y", True,
                       note=f"Held {held}d of {dte_at_entry}d DTE ({held_fraction*100:.0f}%)")
    return _result("exit_within_dte_band", "N", True,
                   note=f"Held {held}d of {dte_at_entry}d DTE > 50% threshold")


def _r_no_average_down(p: Position, ctx: ScoringContext) -> RuleResult:
    """Detection requires snapshot of open positions at entry; default Y manual."""
    if ctx.earlier_open_position_at_entry is True:
        return _result("no_average_down", "N", True,
                       note="Earlier open position on same ticker+direction at entry")
    if ctx.earlier_open_position_at_entry is False:
        return _result("no_average_down", "Y", True, note="No earlier open position")
    return _result("no_average_down", "Y", False,
                   note="Earlier-open-position lookup not supplied; review manually")


def _r_no_pyramid_double_up(p: Position, ctx: ScoringContext) -> RuleResult:
    if ctx.pyramid_active_at_entry is True:
        return _result("no_pyramid_double_up", "N", True,
                       note="Active pyramid same ticker+direction at entry")
    if ctx.pyramid_active_at_entry is False:
        return _result("no_pyramid_double_up", "Y", True, note="No active pyramid")
    # Fallback: check kill sheet attestation if no explicit context
    ks = ctx.kill_sheet
    if ks is not None and ks.discipline_attestation is not None:
        if ks.discipline_attestation.doubling_pyramid_direction:
            return _result("no_pyramid_double_up", "N", True,
                           note="Attestation flagged pyramid double-up at entry")
        return _result("no_pyramid_double_up", "Y", True,
                       note="Attestation cleared pyramid check")
    return _result("no_pyramid_double_up", "Y", False,
                   note="Pyramid lookup not supplied")


# Mapping rule_id → evaluator function. Keeps `score_trade` declarative.
_EVALUATORS = {
    "kill_sheet_complete":  _r_kill_sheet_complete,
    "sqn100_authorized":    _r_sqn100_authorized,
    "sqn20_respected":      _r_sqn20_respected,
    "size_within_tier":     _r_size_within_tier,
    "trigger_dte_match":    _r_trigger_dte_match,
    "iv_rank_under_70":     _r_iv_rank_under_70,
    "dte_min_7":            _r_dte_min_7,
    "trade_devil_passed":   _r_trade_devil_passed,
    "no_spreads_margin":    _r_no_spreads_margin,
    "daily_not_chop":       _r_daily_not_chop,
    "weekly_not_opposing":  _r_weekly_not_opposing,
    "cut_at_60_70":         _r_cut_at_60_70,
    "exit_within_dte_band": _r_exit_within_dte_band,
    "no_average_down":      _r_no_average_down,
    "no_pyramid_double_up": _r_no_pyramid_double_up,
}


# ── Top-level scorer ─────────────────────────────────────────────────────────


def _counterfactual_loss(p: Position) -> float | None:
    """Counterfactual: dollars lost if the trade had been cut at -65% per rule 12."""
    if p.max_loss_usd <= 0:
        return None
    return -COUNTERFACTUAL_CUT_FRACTION * p.max_loss_usd


def score_trade(
    position: Position,
    *,
    kill_sheet: KillSheet | None = None,
    pyramid_active_at_entry: bool | None = None,
    earlier_open_position_at_entry: bool | None = None,
    notes: str = "",
    user_overrides: dict[str, RuleResult] | None = None,
) -> DisciplineScore:
    """Score a closed Position against the 15-rule checklist.

    Args:
        position: the closed Position to score.
        kill_sheet: the kill sheet generated at entry (optional).
        pyramid_active_at_entry: True if a pyramid was active on same
            ticker+direction at entry. None → fall back to attestation.
        earlier_open_position_at_entry: True if another open position existed
            on same ticker+direction at entry. None → manual review.
        notes: free-form narrative notes on the trade.
        user_overrides: optional dict of rule_id → RuleResult to override
            the auto-evaluated verdicts.
    """
    ctx = ScoringContext(
        kill_sheet=kill_sheet,
        pyramid_active_at_entry=pyramid_active_at_entry,
        earlier_open_position_at_entry=earlier_open_position_at_entry,
    )

    rule_results: list[RuleResult] = []
    overrides = user_overrides or {}
    for rule_id in RULE_IDS:
        if rule_id in overrides:
            rule_results.append(overrides[rule_id])
            continue
        evaluator = _EVALUATORS[rule_id]
        rule_results.append(evaluator(position, ctx))

    y_count = sum(1 for r in rule_results if r.score == "Y")
    n_count = sum(1 for r in rule_results if r.score == "N")
    na_count = sum(1 for r in rule_results if r.score == "N/A")
    denom = y_count + n_count

    pnl = position.pnl_usd if position.pnl_usd is not None else 0.0
    profitable_violation = (n_count > 0) and (pnl > 0)
    counterfactual = _counterfactual_loss(position) if profitable_violation else None

    return DisciplineScore.stamp(
        position_id=position.id,
        kill_sheet_id=None,  # KillSheet doesn't carry an ID yet — TODO (v2 spec)
        closed_at=position.closed_date or "",
        rules=rule_results,
        pnl_usd=position.pnl_usd,
        ticker=position.ticker,
        direction=position.direction,
        instrument=position.instrument,
        entry_at=position.entry_date,
        score_numerator=y_count,
        score_denominator=denom,
        profitable_violation=profitable_violation,
        counterfactual_loss_usd=counterfactual,
        notes=notes,
    )
