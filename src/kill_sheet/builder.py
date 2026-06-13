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
# indicators.ma_ribbon emits "chop" (tangled / no clear order) and
# "compression" (tightening, pre-breakout). NOTE: the token is "chop", not
# "chop_tangled" — the old value never matched the indicator, so this hard
# block silently never fired on real chop. Fixed 2026-06.
DAILY_CHOP_STATES = {"chop", "compression"}

# SQN(100) regimes that authorize each direction.
REGIME_AUTHORIZES_LONG = {"bull", "strong_bull"}
REGIME_AUTHORIZES_SHORT = {"bear", "strong_bear"}

# Weekly-trend-trader asset gate. Backed by the 2026-05-07 multi-strategy
# backtest (scripts/backtest_strategies.py) — see the per-asset Sharpe panel
# in ~/.claude/projects/.../project_strategy_backtests_2026_05_07.md. Edit
# these sets when forward-test data revises the per-asset findings.
#   BLOCKED: weekly-trend signal failed cleanly in historical sample.
#   MARGINAL: passing but with high MaxDD or low Sharpe — surface a warning
#             on the kill sheet but do not gate authorization.
# QQQ + GLD are not in either set (Sharpe 1.16 / 1.92, ship as-is). Other
# tickers are not in either set either — the strategy can run on them, just
# without code-backed historical edge data (caller's judgment).
WEEKLY_TREND_BLOCKED_TICKERS: frozenset[str] = frozenset({"IWM"})
WEEKLY_TREND_MARGINAL_TICKERS: frozenset[str] = frozenset({"SPY"})

# Index-swing skill universe gate. Backed by the 2026-05-09 price-action
# backtest (370 trades, 1999-2022) — long-only QQQ/IWM/SPY only, with IWM as
# the workhorse (n=184, +0.96 avgR). Single-name extension is unvalidated.
# A kill sheet generated under skill="index-swing" with a non-universe ticker
# is BLOCKED — no override path. SQN(20) Bear Volatile is also a hard block
# (only net-negative regime in the backtest, n=24, WR 37.5%, avgR -0.06).
INDEX_SWING_ALLOWED_TICKERS: frozenset[str] = frozenset({"QQQ", "IWM", "SPY"})

# Track A (19/39 weekly cross) per-asset gate for weekly-trend-trader.
# Backed by 2026-05-09 19/39 backtest (1968-2021 + 2014-2026 corroboration).
# Track A is structurally distinct from Track B (10/20/50/200 ribbon) and has
# a different asset profile: high-beta single names dominate, index ETFs
# underperform. These tickers had net-negative avg R in the recent decade.
WEEKLY_TREND_TRACK_A_BLOCKED_TICKERS: frozenset[str] = frozenset({
    "QQQ", "GLD", "SPY", "AMZN", "NFLX", "AMD", "TSLA"
})


def _trigger_bar_fields_from_scan(
    scan_row: dict[str, Any], direction: str
) -> dict[str, Any]:
    """Extract G4 trigger-bar fields from a scan row.

    Reads `trigger_bar_open` and `trigger_bar_close` from scan_row (most
    recently closed 2H trigger bar's OHLC). When both are present, derives
    `trigger_bar_color` (green/red/doji at 0.05% tolerance) and
    `trigger_bar_in_direction` (True if green+long or red+short).

    Tracked, not gated, per [[project-lotto-g4-trigger-bar]] memory and
    SKILL.md "Trigger-Bar Confirmation" section. When scan_row doesn't
    carry the fields, returns None for everything — keeps backwards compat
    with scanners that haven't been wired to populate trigger-bar data.
    """
    o = scan_row.get("trigger_bar_open")
    c = scan_row.get("trigger_bar_close")
    if o is None or c is None:
        return {
            "trigger_bar_open": None,
            "trigger_bar_close": None,
            "trigger_bar_color": None,
            "trigger_bar_in_direction": None,
        }
    try:
        o_f = float(o)
        c_f = float(c)
    except (TypeError, ValueError):
        return {
            "trigger_bar_open": None,
            "trigger_bar_close": None,
            "trigger_bar_color": None,
            "trigger_bar_in_direction": None,
        }
    if o_f <= 0:
        color = "doji"
    elif abs(c_f - o_f) / o_f < 0.0005:
        color = "doji"
    elif c_f > o_f:
        color = "green"
    else:
        color = "red"
    in_direction = (
        (direction == "long" and color == "green")
        or (direction == "short" and color == "red")
    )
    return {
        "trigger_bar_open": o_f,
        "trigger_bar_close": c_f,
        "trigger_bar_color": color,
        "trigger_bar_in_direction": in_direction,
    }


def _dte_band_for(
    account_key: str,
    intent: str,
    trigger_tf: str,
    *,
    skill_name: str | None = None,
    is_track_a: bool = False,
) -> str:
    """Account-aware + skill-aware DTE band recommendation.

    [src: ~/CLAUDE.md account profile + per-skill SKILL.md DTE specs]

    Skill-specific overrides take precedence over the account/intent/TF
    defaults. The order matters: index-swing is hard-locked to 30-60 DTE
    regardless of the trigger_tf the caller provides; Track A weekly trades
    use LEAPS (365+ DTE) instead of the 120-180 DTE band Track B uses.
    """
    # ── Skill-specific overrides (highest precedence) ──
    if skill_name == "index-swing":
        return "30–60 DTE (index-swing; 50–65 delta long calls; never below 21 DTE)"
    if skill_name == "weekly-trend-trader" and is_track_a:
        return "365+ DTE LEAPS (Track A; 75–90 delta deep ITM; roll at 180 DTE)"

    # ── Account / intent / TF defaults ──
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
    index_swing_universe_violation, bear_volatile_block (rule 18 — index-swing
    and lotto longs).

    Conditional anti-patterns require their corresponding user attestation.
    """
    if att.spreads_or_margin or att.daily_chop:
        return False
    # Index-swing hard universe gate (no override path — universe-locked).
    if att.index_swing_universe_violation:
        return False
    # Rule 18 structural Bear-Volatile hard skip (tight-stop bullish entries).
    if att.bear_volatile_block:
        return False
    if att.iv_rank_over_70 and not att.explicit_post_earnings_crush_thesis:
        return False
    if att.dte_under_7 and not att.explicit_0dte_framing:
        return False
    if att.fighting_sqn_regime and not att.divergence_thesis_documented:
        return False
    if att.averaging_down and not att.new_signal_for_average_down:
        return False
    if att.lotto_chase_warning and not att.lotto_chase_documented:
        return False
    if att.weekly_trend_asset_blocked and not att.weekly_trend_asset_override_documented:
        return False
    if att.weekly_trend_track_a_asset_blocked and not att.weekly_trend_track_a_override_documented:
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
    _regime_early = (scan_row.get("sqn") or {}).get("regime")
    # Neutral SQN(100) is a no-bias zone, not a no-trade zone: every skill
    # treats it as half-size tradeable. Halve the conviction-tier risk before
    # sizing (the dollar cap, if any, still applies as a ceiling). Decision
    # 2026-06: authorize neutral at half size rather than reject-unless-thesis.
    if _regime_early == "neutral":
        risk_pct = risk_pct * 0.5
        reason = f"{reason} · Neutral SQN(100) → half size"
    # Rule 17: a counter-regime index short (QQQ/IWM/SPY put) is authorized only
    # via a divergence thesis, and then ONLY at speculative-tier size — shorts on
    # these names are net-unprofitable outside Bear regimes. Clamp before sizing.
    if (
        direction == "short"
        and (scan_row.get("ticker") or "").upper() in INDEX_SWING_ALLOWED_TICKERS
        and not _regime_authorizes("short", _regime_early)
        and divergence_thesis
    ):
        spec_pct = account.risk_pct("speculative")
        if risk_pct > spec_pct:
            risk_pct = spec_pct
            reason = f"{reason} · Rule 17: counter-regime index short → speculative size"
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
    # Neutral is authorized (at half size, applied above) — only a regime that
    # actively OPPOSES the direction (long in bear, short in bull) is rejected
    # without a divergence thesis. (Decision 2026-06.)
    if not sqn_authorizes and regime != "neutral" and not divergence_thesis:
        status = "REJECTED"
        rejection_reason = (
            f"SQN(100) regime '{regime}' opposes {direction.upper()} "
            f"direction. Document a divergence thesis to override."
        )

    # ── Counter-Weekly / 4H-opposing lotto gate ───────────────────────────
    # SKILL.md instant disqualifier ("counter-Weekly lotto = bad R/R"; "4H
    # opposes Daily") + orchestrator rule 2/6 (counter-weekly needs a documented
    # divergence thesis). Uses the weekly/4H stacks already computed from
    # multi_tf above — no extra fetches. Lotto only (the weekly-trend skill IS
    # weekly-anchored, so "counter-weekly" doesn't apply there). A
    # counter_weekly_thesis or divergence_thesis is the documented override.
    # (Decision 2026-06: enforce at the kill-sheet layer, not the broad scanner.)
    if (
        account_key == "lotto"
        and status != "REJECTED"
        and not (counter_weekly_thesis or divergence_thesis)
    ):
        tf_4h_align = weekly_alignment(tf_4h_stack, direction) if tf_4h_stack else None
        if weekly_align == "Counter-trend":
            status = "REJECTED"
            rejection_reason = (
                f"Weekly stack ({weekly_stack}) opposes {direction.upper()} lotto "
                "— counter-Weekly = bad R/R. Document a counter-weekly thesis to override."
            )
        elif tf_4h_align == "Counter-trend":
            status = "REJECTED"
            rejection_reason = (
                f"4H stack ({tf_4h_stack}) opposes {direction.upper()} lotto "
                "— 4H fights Daily. Document a divergence thesis to override."
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

    # Lotto chase-warning auto-flag: lotto-account longs when SQN(20) > +2.5.
    # Backtest 2026-05-07 (~/.claude/projects/.../project_strategy_backtests_2026_05_07.md):
    # lotto QQQ in strong_bull regime returned -5.21% avg @ 0% win on n=3 trades.
    # Threshold value (+2.5) sourced from CLAUDE.md orchestrator rule 12.
    sqn_20_val = sqn.get("sqn_20_value")
    # bool() cast: scan_row's sqn_20_value is a numpy scalar; the chained
    # comparison returns numpy.bool, which Pydantic can't serialize → 500.
    lotto_chase_warning = bool(
        account_key == "lotto"
        and direction == "long"
        and sqn_20_val is not None
        and float(sqn_20_val) > 2.5
    )

    # Weekly-trend-trader asset gate. Backtest 2026-05-07 over 21-33yr samples:
    # IWM Sharpe -0.72 / 33% win → BLOCK. SPY Sharpe 0.80 / MaxDD -26% →
    # MARGINAL warn (informational). QQQ (1.16) + GLD (1.92) pass and are not
    # flagged. Other tickers are not flagged either — the strategy can be run
    # on them, just without code-backed historical edge data.
    skill_name_for_gate: str | None = None
    if isinstance(skill, SkillConfig):
        skill_name_for_gate = skill.name
    elif isinstance(skill, str):
        skill_name_for_gate = skill
    is_weekly_trend = skill_name_for_gate == "weekly-trend-trader"
    is_index_swing = skill_name_for_gate == "index-swing"
    ticker_upper = scan_row["ticker"].upper()
    weekly_trend_asset_blocked = bool(
        is_weekly_trend and ticker_upper in WEEKLY_TREND_BLOCKED_TICKERS
    )
    weekly_trend_asset_marginal = bool(
        is_weekly_trend and ticker_upper in WEEKLY_TREND_MARGINAL_TICKERS
    )
    # Track A (19/39 cross) asset gate — fires when caller explicitly tags
    # the kill sheet as Track A via attestation_user_inputs["weekly_trend_track_a"].
    is_weekly_trend_track_a = bool(
        is_weekly_trend
        and (attestation_user_inputs or {}).get("weekly_trend_track_a", False)
    )
    weekly_trend_track_a_asset_blocked = bool(
        is_weekly_trend_track_a
        and ticker_upper in WEEKLY_TREND_TRACK_A_BLOCKED_TICKERS
    )

    # Index-swing hard universe gate — no override path. Skill is locked to
    # QQQ/IWM/SPY per the 370-trade backtest.
    index_swing_universe_violation = bool(
        is_index_swing and ticker_upper not in INDEX_SWING_ALLOWED_TICKERS
    )
    # Index-swing structural Bear-Volatile block — only net-negative regime
    # in the backtest (n=24, WR 37.5%, avgR -0.06). The backtest's "Bear
    # Volatile" label is SQN(100) + realized-vol overlay, NOT SQN(20) alone.
    # In-code analog: SQN(100) Strong Bear, OR SQN(100) Bear + SQN(20) < -1.9.
    sqn_100_regime_value = sqn.get("regime")  # SQN-100 primary regime
    sqn_20_value_raw = sqn.get("sqn_20_value")
    try:
        sqn_20_value = float(sqn_20_value_raw) if sqn_20_value_raw is not None else None
    except (TypeError, ValueError):
        sqn_20_value = None
    bear_volatile_regime = (
        sqn_100_regime_value == "strong_bear"
        or (
            sqn_100_regime_value == "bear"
            and sqn_20_value is not None
            and sqn_20_value < -1.9
        )
    )
    # Rule 18: structural Bear-Volatile is a hard skip for tight-stop BULLISH
    # entries — index-swing (always long) AND lotto longs. Non-overridable.
    # (Generalized from index-swing-only 2026-06.)
    bear_volatile_block = bool(
        bear_volatile_regime
        and (is_index_swing or account_key == "lotto")
        and direction == "long"
    )

    user_inputs = attestation_user_inputs or {}
    attestation = DisciplineAttestation(
        iv_rank_over_70=(iv_rank is not None and iv_rank > 70),
        dte_under_7=(dte is not None and dte < 7),
        daily_chop=(ma_stack_state in DAILY_CHOP_STATES),
        fighting_sqn_regime=(not sqn_authorizes and regime != "neutral"),
        averaging_down=averaging_down,
        lotto_chase_warning=lotto_chase_warning,
        weekly_trend_asset_blocked=weekly_trend_asset_blocked,
        weekly_trend_asset_marginal=weekly_trend_asset_marginal,
        weekly_trend_track_a_asset_blocked=weekly_trend_track_a_asset_blocked,
        index_swing_universe_violation=index_swing_universe_violation,
        bear_volatile_block=bear_volatile_block,
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
        lotto_chase_documented=user_inputs.get("lotto_chase_documented", False),
        weekly_trend_asset_override_documented=user_inputs.get(
            "weekly_trend_asset_override_documented", False
        ),
        weekly_trend_track_a_override_documented=user_inputs.get(
            "weekly_trend_track_a_override_documented", False
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
        cut_rule_pct=account.raw.get("cut_rule_pct"),
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
        **_trigger_bar_fields_from_scan(scan_row, direction),
        options=options,
        target_price=target_price,
        invalidation_price=invalidation_price,
        trigger_description=trigger_description,
        notes=notes,
        risk_capped_by_max_trade=sizing.capped_by == "max_per_trade_usd",
        dte_band_label=_dte_band_for(
            account_key, intent, trigger_tf,
            skill_name=skill_name_for_gate,
            is_track_a=is_weekly_trend_track_a,
        ),
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
