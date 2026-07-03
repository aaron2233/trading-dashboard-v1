"""Per-skill action verdict classifiers.

Each function takes a `reads` dict (timeframe -> scan_ticker output)
and a direction, applies that skill's rules from ~/CLAUDE.md, and
returns an ActionVerdict.

Rule precedence (highest priority first — first match wins):
  1. DISQUALIFIED — hard blocker (chop on trigger TF, opposing stack
     without divergence thesis, missing TF read)
  2. CHASE_ZONE — orchestrator rule 13 anti-pattern fires
  3. STALE — multi-TF momentum exhaustion
  4. ENTER_NOW — all gates pass + trigger fired this bar
  5. SETUP_FORMING — gates pass but trigger not yet fired (default
     when no other state matches)

Rule citations point at ~/CLAUDE.md so the audit trail is traceable
back to the orchestrator.
"""
from __future__ import annotations

from typing import Any, Literal

from action_gate.model import (
    ActionVerdict,
    CHASE_DIAG_TOKENS,
    CHOP_STACKS,
    STALE_DIAG_TOKENS,
    stack_opposes,
    stack_supports,
)


Direction = Literal["long", "short"]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _diag_has_token(diag: str | None, tokens: tuple[str, ...]) -> bool:
    if not diag:
        return False
    d = str(diag).lower()
    return any(tok in d for tok in tokens)


def _sqn100_opposes(sqn_100: str | None, direction: Direction) -> bool:
    """SQN(100) primary regime actively fights the direction (orchestrator
    rule 1 / "never fight the SQN regime"). Long opposed by Bear/Strong Bear;
    short opposed by Bull/Strong Bull. Neutral opposes neither.
    """
    if direction == "long":
        return sqn_100 in ("bear", "strong_bear")
    if direction == "short":
        return sqn_100 in ("bull", "strong_bull")
    return False


def _is_chase_signal(
    sqn20: str | None,
    stoch_zone: str | None,
    direction: Direction,
) -> bool:
    """SQN(20) pinned at extreme matching direction = chase per rule 13."""
    if direction == "long":
        return sqn20 == "strong_bull" and stoch_zone == "overbought"
    if direction == "short":
        return sqn20 == "strong_bear" and stoch_zone == "oversold"
    return False


def _is_exhaustion(stoch_zone: str | None, direction: Direction) -> bool:
    """Stoch at the matching extreme = the directional move has already
    run to its limit on this TF. Long move exhausted at overbought
    (rally peaked); short move exhausted at oversold (decline bottomed).
    """
    if direction == "long":
        return stoch_zone == "overbought"
    if direction == "short":
        return stoch_zone == "oversold"
    return False


def _trigger_fired(
    stoch_signal: str | None,
    direction: Direction,
) -> bool:
    """The exact stoch signal that fires entry per the orchestrator. K
    just crossed D in the matching direction at the matching extreme.
    Used by the WEEKLY classifier — weekly cross signals are rare by
    design and this narrow whitelist is intentional. For lotto, see
    `_lotto_trigger_fired`."""
    if direction == "long":
        return stoch_signal == "bull_cross_oversold"
    if direction == "short":
        return stoch_signal == "bear_cross_overbought"
    return False


# Lotto trigger whitelist — matches scan_verdict.lotto_verdict so the
# setup scan and the per-candidate action verdict agree on what fires.
# Broader than `_trigger_fired` because 2H bars give continuation signals
# enough sample to be tradeable; weekly bars don't. Divergence removed
# 2026-07-02 in sync with lotto_verdict (backtest: removal PF 1.50 vs
# 1.40 — see scripts/divergence_pivot_backtest.py).
_LOTTO_LONG_TRIGGERS = frozenset({
    "bull_cross_oversold", "bull_continuation",
})
_LOTTO_SHORT_TRIGGERS = frozenset({
    "bear_cross_overbought", "bear_continuation",
})


def _lotto_trigger_fired(
    stoch_signal: str | None,
    direction: Direction,
) -> bool:
    """Lotto 2H trigger: cross_oversold or continuation in the matching
    direction. Mirrors the long_signals / short_signals sets in
    `scan_verdict.lotto_verdict` so both code paths agree."""
    if direction == "long":
        return stoch_signal in _LOTTO_LONG_TRIGGERS
    if direction == "short":
        return stoch_signal in _LOTTO_SHORT_TRIGGERS
    return False


def _format_call_or_put(direction: Direction) -> str:
    return "CALLS" if direction == "long" else "PUTS"


def _missing_tf_blocker(reads: dict, tf: str) -> str | None:
    row = reads.get(tf)
    if not row:
        return f"missing {tf} read"
    if row.get("error"):
        return f"{tf} scan error: {row['error']}"
    return None


def _close_for(reads: dict, tf: str) -> float | None:
    row = reads.get(tf) or {}
    val = row.get("close")
    if isinstance(val, (int, float)):
        return float(val)
    return None


# ── Lotto (Tier 2): Daily filter / 2H trigger / 0-14 DTE ────────────────────


def classify_lotto_action(
    reads: dict[str, dict[str, Any]],
    direction: Direction,
) -> ActionVerdict:
    """Lotto-options verdict. Daily MA = direction filter; 2H stoch =
    trigger. Strict chase guard (orchestrator rule 13). Direction must
    align across daily + 2H stack — chop on either TF disqualifies."""
    skill = "lotto-options"

    daily_missing = _missing_tf_blocker(reads, "1d")
    two_h_missing = _missing_tf_blocker(reads, "2h")
    if daily_missing or two_h_missing:
        return ActionVerdict(
            state="disqualified",
            direction=direction,
            skill=skill,
            headline="SKIP — missing scan data",
            blockers=[b for b in (daily_missing, two_h_missing) if b],
            rule_citations=["~/CLAUDE.md:120 timeframe alignment"],
        )

    daily = reads["1d"]
    two_h = reads["2h"]

    daily_stack = (daily.get("ma_ribbon") or {}).get("stack_state")
    two_h_stack = (two_h.get("ma_ribbon") or {}).get("stack_state")
    daily_stoch = daily.get("stochastic") or {}
    two_h_stoch = two_h.get("stochastic") or {}
    daily_sqn = daily.get("sqn") or {}
    two_h_sqn = two_h.get("sqn") or {}

    # ── DISQUALIFIED ─────────────────────────────────────────────────────────
    if daily_stack in CHOP_STACKS:
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline=f"SKIP — daily stack is {daily_stack}",
            blockers=[f"daily MA = {daily_stack} (no trend = no trade)"],
            rule_citations=["~/CLAUDE.md anti-patterns: never trade chop"],
        )
    if two_h_stack in CHOP_STACKS:
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline=f"SKIP — 2H trigger TF is {two_h_stack}",
            blockers=[f"2H MA = {two_h_stack} (trigger TF must be trending)"],
            rule_citations=["~/CLAUDE.md anti-patterns: never trade chop"],
        )
    if stack_opposes(daily_stack, direction):
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline=f"SKIP — daily stack opposes {direction}",
            blockers=[f"daily MA = {daily_stack}; conflicts with {direction}"],
            rule_citations=["~/CLAUDE.md:120-122 daily MA = direction filter"],
        )

    # ── SQN(100) regime gate (orchestrator rule 1; subsumes rule-18 long) ────
    # The verdict must consult SQN(100), not just the MA stacks: a long in a
    # Bear/Strong-Bear regime (or short in Bull) fights the regime and isn't
    # actionable at scan time — the kill sheet is where a divergence thesis can
    # override. This also covers rule 18's bullish Bear-Volatile hard skip.
    if _sqn100_opposes(daily_sqn.get("regime"), direction):
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline=f"SKIP — SQN(100) {daily_sqn.get('regime')} opposes {direction}",
            blockers=[
                f"SQN(100) regime {daily_sqn.get('regime')} fights {direction} "
                "(rule 1; counter-regime needs a kill-sheet divergence thesis)"
            ],
            rule_citations=["~/CLAUDE.md rule 1: always start with SQN(100) regime"],
        )

    # ── STALE ────────────────────────────────────────────────────────────────
    # Check before CHASE so explicit "weakening" diagnostics + stoch
    # extreme route to STALE rather than CHASE. Chase = move running
    # hot; stale = move running out.
    if _is_exhaustion(daily_stoch.get("zone"), direction) and (
        _diag_has_token(two_h_sqn.get("diagnostic"), STALE_DIAG_TOKENS)
        or _is_exhaustion((reads.get("1wk") or {}).get("stochastic", {}).get("zone"), direction)
    ):
        return ActionVerdict(
            state="stale", direction=direction, skill=skill,
            headline=(
                f"SKIP — {direction} move exhausted "
                f"(daily stoch {daily_stoch.get('zone')}, "
                f"2H diag {two_h_sqn.get('diagnostic')})"
            ),
            blockers=["multi-TF stoch at extreme + momentum weakening"],
            rule_citations=["~/CLAUDE.md anti-patterns: don't fight exhaustion"],
        )

    # ── CHASE_ZONE ───────────────────────────────────────────────────────────
    if _is_chase_signal(daily_sqn.get("regime_20"), daily_stoch.get("zone"), direction):
        return ActionVerdict(
            state="chase_zone", direction=direction, skill=skill,
            headline=(
                f"SKIP — chase zone "
                f"(daily SQN20 {daily_sqn.get('regime_20')} + "
                f"stoch {daily_stoch.get('zone')})"
            ),
            blockers=[
                f"SQN(20) > +2.5 / < -1.9 zone: orchestrator rule 13 forbids chasing premium",
            ],
            rule_citations=["~/CLAUDE.md:13 SQN(20) chase guard"],
        )
    if _diag_has_token(daily.get("sqn", {}).get("diagnostic"), CHASE_DIAG_TOKENS):
        return ActionVerdict(
            state="chase_zone", direction=direction, skill=skill,
            headline=f"SKIP — daily diag = {daily_sqn.get('diagnostic')}",
            blockers=[f"daily diagnostic flags chase: {daily_sqn.get('diagnostic')}"],
            rule_citations=["~/CLAUDE.md:13 SQN(20) chase guard"],
        )

    # ── ENTER_NOW vs SETUP_FORMING ───────────────────────────────────────────
    if (
        stack_supports(daily_stack, direction)
        and stack_supports(two_h_stack, direction)
        and _lotto_trigger_fired(two_h_stoch.get("signal"), direction)
    ):
        spot = _close_for(reads, "2h") or _close_for(reads, "1d")
        return ActionVerdict(
            state="enter_now", direction=direction, skill=skill,
            headline=f"BUY {_format_call_or_put(direction)} @ ${spot:.2f}" if spot else f"BUY {_format_call_or_put(direction)}",
            suggested_entry_price=spot,
            rule_citations=[
                "~/CLAUDE.md:124 2H Stoch = trigger for lotto",
                "~/CLAUDE.md:128 2H trigger → 0-14 DTE",
            ],
        )

    # SETUP_FORMING — daily aligned, waiting for 2H trigger to fire
    advance: list[str] = []
    if not stack_supports(two_h_stack, direction):
        advance.append(f"2H stack must develop into {direction} (currently {two_h_stack})")
    if not _lotto_trigger_fired(two_h_stoch.get("signal"), direction):
        target = (
            "bull_cross_oversold / bull_continuation"
            if direction == "long"
            else "bear_cross_overbought / bear_continuation"
        )
        advance.append(f"2H stoch must fire {target}; currently {two_h_stoch.get('signal') or '—'}")

    return ActionVerdict(
        state="setup_forming", direction=direction, skill=skill,
        headline=f"WAIT — {advance[0]}" if advance else "WAIT — trigger not yet fired",
        advance_conditions=advance,
        rule_citations=["~/CLAUDE.md:124 2H Stoch = trigger"],
    )


# ── Weekly trend trader (Tier 1): Weekly anchor / Weekly trigger ────────────


def classify_weekly_trend_action(
    reads: dict[str, dict[str, Any]],
    direction: Direction,
) -> ActionVerdict:
    """Weekly trend trader verdict. Weekly MA = anchor; weekly close +
    weekly stoch = trigger. Chase guard is loose vs lotto — sustained
    overbought weekly stoch is normal during multi-month uptrends."""
    skill = "weekly-trend-trader"

    missing = _missing_tf_blocker(reads, "1wk")
    if missing:
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline="SKIP — missing weekly read",
            blockers=[missing],
            rule_citations=["~/CLAUDE.md:121 Weekly MA = anchor"],
        )

    weekly = reads["1wk"]
    stack = (weekly.get("ma_ribbon") or {}).get("stack_state")
    stoch = weekly.get("stochastic") or {}
    sqn = weekly.get("sqn") or {}

    # ── DISQUALIFIED ─────────────────────────────────────────────────────────
    if stack in CHOP_STACKS:
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline=f"SKIP — weekly stack is {stack}",
            blockers=[f"weekly MA = {stack} (no trend = no trade)"],
            rule_citations=["~/CLAUDE.md anti-patterns: never trade chop"],
        )
    if stack_opposes(stack, direction):
        return ActionVerdict(
            state="disqualified", direction=direction, skill=skill,
            headline=f"SKIP — weekly opposes {direction}",
            blockers=[f"weekly MA = {stack}; counter-weekly entry needs divergence thesis"],
            rule_citations=["~/CLAUDE.md:45 counter-weekly requires divergence thesis"],
        )

    # ── STALE ────────────────────────────────────────────────────────────────
    # Weekly trends tolerate sustained overbought stoch; STALE only
    # fires when both stoch AND diagnostic agree the move is dying.
    if _is_exhaustion(stoch.get("zone"), direction) and _diag_has_token(
        sqn.get("diagnostic"), STALE_DIAG_TOKENS,
    ):
        return ActionVerdict(
            state="stale", direction=direction, skill=skill,
            headline=f"SKIP — weekly move exhausted (stoch {stoch.get('zone')}, diag {sqn.get('diagnostic')})",
            blockers=["weekly stoch at extreme + diagnostic confirms weakening"],
            rule_citations=["~/CLAUDE.md anti-patterns: don't fight exhaustion"],
        )

    # ── CHASE_ZONE ───────────────────────────────────────────────────────────
    # Weekly chase guard is narrower: only fires on a *fresh* divergence
    # warning at the bar — sustained overbought alone isn't chase.
    sig = stoch.get("signal")
    if (direction == "long" and sig == "bearish_divergence" and stoch.get("zone") == "overbought") or (
        direction == "short" and sig == "bullish_divergence" and stoch.get("zone") == "oversold"
    ):
        return ActionVerdict(
            state="chase_zone", direction=direction, skill=skill,
            headline=f"SKIP — weekly stoch divergence at {stoch.get('zone')} (top/bottom forming)",
            blockers=[f"weekly stoch divergence + extreme zone"],
            rule_citations=["~/CLAUDE.md:13 SQN(20) chase guard"],
        )

    # ── ENTER_NOW vs SETUP_FORMING ───────────────────────────────────────────
    # Weekly trend entry: stack supports + weekly stoch turning up from
    # oversold (long) or down from overbought (short). The exact "cross"
    # signal at weekly TF is rare; treat it as the trigger.
    if stack_supports(stack, direction) and _trigger_fired(sig, direction):
        spot = _close_for(reads, "1wk")
        verb = "BUY CALLS / LEAPS" if direction == "long" else "BUY PUTS"
        return ActionVerdict(
            state="enter_now", direction=direction, skill=skill,
            headline=f"{verb} @ ${spot:.2f}" if spot else verb,
            suggested_entry_price=spot,
            rule_citations=[
                "~/CLAUDE.md Tier 1: Weekly trigger / 120-180 DTE",
            ],
        )

    advance: list[str] = []
    if not stack_supports(stack, direction):
        advance.append(f"weekly stack must develop into {direction} (currently {stack})")
    if not _trigger_fired(sig, direction):
        target = "bull_cross_oversold" if direction == "long" else "bear_cross_overbought"
        advance.append(f"weekly stoch must fire {target}; currently {sig or '—'}")

    return ActionVerdict(
        state="setup_forming", direction=direction, skill=skill,
        headline=f"WAIT — {advance[0]}" if advance else "WAIT — weekly trigger not yet fired",
        advance_conditions=advance,
        rule_citations=["~/CLAUDE.md Tier 1 trigger frame"],
    )


# ── Sunday focus (Tier 4 specialty): Daily filter / 2H trigger / 21-60 DTE ──


def classify_focus_action(
    reads: dict[str, dict[str, Any]],
    direction: Direction,
) -> ActionVerdict:
    """qqq-gld-focus verdict. Same TF mechanics as lotto (daily filter,
    2H trigger) but longer DTE band (21-60). Chase guard is identical
    to lotto since the trigger TF is the same.

    Implementation reuses classify_lotto_action and rebrands the skill
    + rule citations — the underlying rules are the same per
    ~/CLAUDE.md row mapping.
    """
    verdict = classify_lotto_action(reads, direction)
    # Re-skin for focus context — same rule set, different DTE.
    return ActionVerdict(
        state=verdict.state,
        direction=verdict.direction,
        skill="qqq-gld-focus",
        headline=verdict.headline,
        suggested_entry_price=verdict.suggested_entry_price,
        blockers=list(verdict.blockers),
        advance_conditions=list(verdict.advance_conditions),
        rule_citations=[
            *verdict.rule_citations,
            "~/CLAUDE.md Tier 4 focus: 21-60 DTE band",
        ],
    )
