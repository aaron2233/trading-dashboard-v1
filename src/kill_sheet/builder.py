"""Build a Standard KillSheet from a scan_ticker result + account config."""
from __future__ import annotations

from typing import Any, Literal

from config import AccountConfig, SkillConfig
from kill_sheet.bias import derive_bias, derive_confidence
from kill_sheet.model import DisciplineAttestation, KillSheet
from kill_sheet.multi_tf import extract_tf, pullback_status, weekly_alignment
from kill_sheet.options import OptionsStructure
from kill_sheet.sizing import calculate_position_size


VALID_DIRECTIONS = {"long", "short"}
VALID_INTENTS = {"SCALP", "SWING", "TREND CAPTURE", "POSITION"}
VALID_TRIGGER_TFS = {"2H", "4H", "Daily", "Weekly"}

# MA stack states that count as "Daily chop" for the auto-attestation flag.
# `compression` and `chop_tangled` are produced by indicators.ma_ribbon when
# the ribbon is tangled / no clear order.
DAILY_CHOP_STATES = {"chop_tangled", "compression"}

# SQN(100) regimes that authorize each direction.
REGIME_AUTHORIZES_LONG = {"bull", "strong_bull"}
REGIME_AUTHORIZES_SHORT = {"bear", "strong_bear"}


def _dte_band_for(account_key: str, intent: str, trigger_tf: str) -> str:
    """Account-aware DTE band recommendation for the kill sheet text.

    [src: ~/CLAUDE.md account profile + trading-edge/SKILL.md:201-203]
    """
    if account_key == "lotto":
        return "5–14 DTE (lotto band; 0DTE allowed once/week, $50 cap)"
    if account_key == "weekly" or intent == "POSITION" or trigger_tf == "Weekly":
        return "120–180 DTE (position trade; never hold below 60 DTE)"
    if intent == "SCALP" or trigger_tf == "2H":
        return "0–14 DTE (2H trigger / scalp)"
    if intent == "TREND CAPTURE" or trigger_tf == "Daily":
        return "21–45 DTE (Daily trigger / trend capture)"
    # Default: SWING
    return "14–30 DTE (4H trigger / swing)"


def _regime_authorizes(direction: str, regime: str | None) -> bool:
    if regime is None:
        return False
    if direction == "long":
        return regime in REGIME_AUTHORIZES_LONG
    if direction == "short":
        return regime in REGIME_AUTHORIZES_SHORT
    return False


def _compute_entry_authorized(att: DisciplineAttestation) -> bool:
    """Final gate per DISCIPLINE-LAYER-ADDITION.md.

    Hard blocks (no user override): spreads_or_margin, daily_chop,
    doubling_pyramid_direction.

    Conditional anti-patterns require their corresponding user attestation.
    """
    if att.spreads_or_margin or att.daily_chop or att.doubling_pyramid_direction:
        return False
    if att.iv_rank_over_70 and not att.explicit_post_earnings_crush_thesis:
        return False
    if att.dte_under_7 and not att.explicit_0dte_framing:
        return False
    if att.fighting_sqn_regime and not att.divergence_thesis_documented:
        return False
    if att.averaging_down and not att.new_signal_for_average_down:
        return False
    return True


def build_standard(
    scan_row: dict[str, Any],
    direction: str,
    account: AccountConfig,
    account_key: str = "main",
    intent: str = "SWING",
    trigger_tf: str = "Daily",
    risk_conviction: str = "high",
    multi_tf: dict[str, dict[str, Any]] | None = None,
    options: OptionsStructure | None = None,
    target_price: float | None = None,
    invalidation_price: float | None = None,
    trigger_description: str | None = None,
    notes: str | None = None,
    *,
    divergence_thesis: str | None = None,
    counter_weekly_thesis: str | None = None,
    attestation_user_inputs: dict[str, bool] | None = None,
    open_positions: list[Any] | None = None,
    active_pyramids: list[Any] | None = None,
    skill: SkillConfig | str | None = None,
    scan_phase: Literal["baseline", "user_submitted", "free_range"] | None = None,
) -> KillSheet:
    """Generate a Standard kill sheet from a scan_ticker output dict.

    Args:
        scan_row: Daily-timeframe dict produced by scan.scan_ticker(...)
        direction: "long" | "short"
        account: loaded AccountConfig
        account_key: account name in the config (e.g. "main", "lotto")
        intent: SCALP | SWING | TREND CAPTURE
        trigger_tf: 2H | 4H | Daily
        risk_conviction: high | medium | speculative | default
        multi_tf: optional dict from compute_multi_tf() containing additional
                  timeframe rows (e.g. "1wk", "4h"). When provided, the kill
                  sheet's Weekly Context and 4H Swing Timing sections are
                  populated; otherwise they render as [TBD].
    """
    direction = direction.lower()
    intent = intent.upper()
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {VALID_DIRECTIONS}, got {direction!r}")
    if intent not in VALID_INTENTS:
        raise ValueError(f"intent must be one of {VALID_INTENTS}, got {intent!r}")
    if trigger_tf not in VALID_TRIGGER_TFS:
        raise ValueError(f"trigger_tf must be one of {VALID_TRIGGER_TFS}, got {trigger_tf!r}")

    if "error" in scan_row:
        raise ValueError(f"scan_row contains an error: {scan_row['error']}")

    bias = derive_bias(scan_row)
    confidence, reason = derive_confidence(scan_row)

    risk_pct = account.risk_pct(risk_conviction)
    # When options are supplied, premium-per-contract = max loss per unit, so
    # we can compute the contract count.
    max_loss_per_unit: float | None = None
    if options is not None and options.premium > 0:
        # premium is quoted per share — 1 contract = 100 shares
        max_loss_per_unit = options.premium * 100.0

    # Lotto-style accounts cap a single trade in absolute dollars regardless of
    # risk_pct. main/weekly typically don't (max_per_trade_usd absent → no cap).
    max_per_trade_usd = account.raw.get("max_per_trade_usd")

    sizing = calculate_position_size(
        account.balance_usd,
        risk_pct,
        max_loss_per_unit=max_loss_per_unit,
        max_per_trade_usd=max_per_trade_usd,
    )

    ma = scan_row["ma_ribbon"]
    stoch = scan_row["stochastic"]
    sqn = scan_row["sqn"]

    weekly_stack: str | None = None
    weekly_align: str | None = None
    tf_4h_stack: str | None = None
    tf_4h_pullback: str | None = None

    if multi_tf is not None:
        weekly_row = extract_tf(multi_tf, "1wk")
        if weekly_row is not None:
            weekly_stack = (weekly_row.get("ma_ribbon") or {}).get("stack_state") or "n/a"
            weekly_align = weekly_alignment(weekly_stack, direction)

        tf_4h_row = extract_tf(multi_tf, "4h")
        if tf_4h_row is not None:
            tf_4h_ma = tf_4h_row.get("ma_ribbon") or {}
            tf_4h_stack = tf_4h_ma.get("stack_state") or "n/a"
            tf_4h_pullback = pullback_status(
                tf_4h_row.get("close"), tf_4h_ma.get("ma_20")
            )

    regime = sqn.get("regime") or "n/a"
    sqn_authorizes = _regime_authorizes(direction, regime)

    # ── Regime gate (Story 2 / DISCIPLINE-LAYER-ADDITION.md) ───────────────
    # If SQN(100) doesn't authorize direction AND user hasn't supplied a
    # divergence thesis, the kill sheet is REJECTED. Trader must document a
    # thesis to override, which is recorded on the kill sheet for audit.
    status: str = "AUTHORIZED"
    rejection_reason: str | None = None
    if not sqn_authorizes and not divergence_thesis:
        status = "REJECTED"
        rejection_reason = (
            f"SQN(100) regime '{regime}' does not authorize {direction.upper()} "
            f"direction. Document a divergence thesis to override."
        )

    # ── Auto-attestation (6 anti-pattern flags from data) ──────────────────
    iv_rank = options.iv_rank if options is not None else None
    dte = options.dte if options is not None else None
    ma_stack_state = ma.get("stack_state") or "n/a"

    # averaging_down: any open position on same ticker+direction
    averaging_down = False
    if open_positions:
        ticker_upper = scan_row["ticker"].upper()
        for p in open_positions:
            p_ticker = getattr(p, "ticker", "").upper()
            p_dir = getattr(p, "direction", "").lower()
            p_status = getattr(p, "status", "")
            if p_ticker == ticker_upper and p_dir == direction and p_status == "open":
                averaging_down = True
                break

    # doubling_pyramid_direction: active pyramid on same ticker+direction
    doubling_pyramid = False
    if active_pyramids:
        ticker_upper = scan_row["ticker"].upper()
        for pyr in active_pyramids:
            pyr_ticker = getattr(pyr, "ticker", "").upper()
            pyr_dir = getattr(pyr, "direction", "").lower()
            pyr_status = getattr(pyr, "status", "")
            if (
                pyr_ticker == ticker_upper
                and pyr_dir == direction
                and pyr_status in ("pending", "active")
            ):
                doubling_pyramid = True
                break

    user_inputs = attestation_user_inputs or {}
    attestation = DisciplineAttestation(
        iv_rank_over_70=(iv_rank is not None and iv_rank > 70),
        dte_under_7=(dte is not None and dte < 7),
        daily_chop=(ma_stack_state in DAILY_CHOP_STATES),
        fighting_sqn_regime=(not sqn_authorizes),
        averaging_down=averaging_down,
        doubling_pyramid_direction=doubling_pyramid,
        spreads_or_margin=user_inputs.get("spreads_or_margin", False),
        explicit_post_earnings_crush_thesis=user_inputs.get(
            "explicit_post_earnings_crush_thesis", False
        ),
        explicit_0dte_framing=user_inputs.get("explicit_0dte_framing", False),
        # Auto-clear divergence_thesis_documented when divergence_thesis is
        # populated — the act of supplying a thesis IS the attestation.
        divergence_thesis_documented=(
            user_inputs.get("divergence_thesis_documented", False)
            or bool(divergence_thesis)
        ),
        new_signal_for_average_down=user_inputs.get(
            "new_signal_for_average_down", False
        ),
    )
    attestation.entry_authorized = _compute_entry_authorized(attestation)

    # Skill / tier tagging (Sprint A). Caller may pass a SkillConfig (from
    # load_config().skill("weekly-trend-trader")) or just a string name.
    skill_name: str | None
    skill_tier: int | None
    if isinstance(skill, SkillConfig):
        skill_name = skill.name
        skill_tier = skill.tier
    elif isinstance(skill, str):
        skill_name = skill
        skill_tier = None  # caller didn't supply config; we don't infer
    else:
        skill_name = None
        skill_tier = None

    return KillSheet(
        ticker=scan_row["ticker"],
        direction=direction,
        intent=intent,
        trigger_tf=trigger_tf,
        bias=bias,
        confidence=confidence,
        confidence_reason=reason,
        account_key=account_key,
        account_name=account.name,
        account_balance_usd=account.balance_usd,
        risk_conviction=risk_conviction,
        risk_pct=risk_pct,
        max_risk_usd=sizing.max_risk_usd,
        bar_date=scan_row.get("bar_date", "n/a"),
        close_at_generation=float(scan_row.get("close") or 0.0),
        sqn_value=sqn.get("sqn_value"),
        regime=regime,
        ma_10=float(ma.get("ma_10") or 0.0),
        ma_20=float(ma.get("ma_20") or 0.0),
        ma_50=float(ma.get("ma_50") or 0.0),
        ma_200=float(ma.get("ma_200") or 0.0),
        ma_stack=ma_stack_state,
        stoch_k=float(stoch.get("k") or 0.0),
        stoch_d=float(stoch.get("d") or 0.0),
        stoch_signal=stoch.get("signal") or "n/a",
        stoch_zone=stoch.get("zone") or "n/a",
        weekly_stack=weekly_stack,
        weekly_alignment=weekly_align,
        tf_4h_stack=tf_4h_stack,
        tf_4h_pullback=tf_4h_pullback,
        options=options,
        target_price=target_price,
        invalidation_price=invalidation_price,
        trigger_description=trigger_description,
        notes=notes,
        risk_capped_by_max_trade=sizing.capped_by == "max_per_trade_usd",
        dte_band_label=_dte_band_for(account_key, intent, trigger_tf),
        sqn_20_value=sqn.get("sqn_20_value"),
        regime_20=sqn.get("regime_20"),
        sqn_diagnostic=sqn.get("diagnostic"),
        status=status,
        rejection_reason=rejection_reason,
        divergence_thesis=divergence_thesis,
        counter_weekly_thesis=counter_weekly_thesis,
        discipline_attestation=attestation,
        skill=skill_name,
        tier=skill_tier,
        scan_phase=scan_phase,
    )
