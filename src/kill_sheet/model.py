"""KillSheet data model + text/JSON rendering.

Format anchored on trading-edge/SKILL.md sections 144-198. Fields not derivable
from a Daily scan in v0.2 are rendered as [TBD] placeholders for the user to
fill in (or for later modules to populate: weekly-trend-trader for the Weekly
context, etc).

Discipline-layer extensions (2026-05-02, per
~/Documents/Product Specs/Trading Dashboard/DISCIPLINE-LAYER-ADDITION.md):
- `status` rejects entries whose SQN(100) doesn't authorize without thesis
- `DisciplineAttestation` records the 6 auto-flagged anti-patterns + 5 user
  attestations + final entry_authorized boolean
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal

from kill_sheet.options import OptionsStructure, breakeven, iv_rank_label


@dataclass
class DisciplineAttestation:
    """Section 8 of the discipline-skill kill-sheet template.

    Six fields auto-flagged from data; five fields captured from the user as
    explicit attestations. `entry_authorized` is the final gate: True iff every
    auto-flagged anti-pattern has its corresponding user attestation cleared.
    """
    # Auto-flagged from data
    iv_rank_over_70: bool = False
    dte_under_7: bool = False
    daily_chop: bool = False
    fighting_sqn_regime: bool = False
    averaging_down: bool = False
    # Chase-warning gate: lotto longs (any underlying) when SQN(20) > +2.5.
    # Backtest 2026-05-07 found lotto-QQQ in extended uptrends loses ~5% avg
    # @ 0% win — calibration of the canonical "stop chasing" signal in
    # ~/CLAUDE.md orchestrator rule 12.
    lotto_chase_warning: bool = False
    # Weekly-trend-trader asset gate. Backtest 2026-05-07: IWM Sharpe -0.72
    # over 26yr (33% win, all bull-regime trades lost) → hard block. SPY
    # Sharpe 0.80, MaxDD -26% → soft warn (informational only, does not gate).
    # QQQ (1.16) + GLD (1.92) pass and are not flagged. Update the constants
    # in builder.py if forward data revises these findings.
    weekly_trend_asset_blocked: bool = False
    weekly_trend_asset_marginal: bool = False  # informational; does not gate
    # Index-swing skill universe gate. Backtest 2026-05-09 (370 trades):
    # strategy is hard-locked to QQQ/IWM/SPY. Single-name extension is
    # unvalidated. NO override path — kill sheets generated under
    # skill="index-swing" with non-universe tickers are rejected outright.
    index_swing_universe_violation: bool = False
    # Index-swing structural Bear-Volatile gate. Backtest's only net-negative
    # regime (n=24, WR 37.5%, avgR -0.06) — labeled "Bear Volatile" but the
    # underlying SQN measure is 100-day with a realized-vol overlay, NOT
    # SQN(20) alone. In-code analog: SQN(100) Strong Bear, OR SQN(100) Bear
    # AND SQN(20) < -1.9 (extreme low). SQN(20) < -1.9 inside SQN(100) Bull
    # is the OPPOSITE — buy-the-dip zone per orchestrator rule 12.
    # Hard block, no override.
    bear_volatile_block: bool = False  # rule 18: index-swing + lotto longs
    # Track A (19/39 weekly cross) per-asset gate for weekly-trend-trader.
    # Backtest 2026-05-09: QQQ/GLD/SPY/AMZN/NFLX/AMD/TSLA had net-negative
    # avg R on the 19/39 cross signal in recent data. These tickers should
    # use Track B (10/20/50/200 ribbon) instead.
    weekly_trend_track_a_asset_blocked: bool = False
    # Regime-levered-trend deployment gate. R1/R2 recovery rules (2026-05-13,
    # no override path) cap main-account premium at $300 — Layer 1 core
    # positions ($1.5K-4K premium) cannot fit. The skill deploys only in a
    # dedicated sleeve. Hard block on main/lotto, no override attestation.
    # Remove when the recovery plan retires at $20K.
    regime_levered_deployment_blocked: bool = False

    # User-attested (UI checkboxes)
    spreads_or_margin: bool = False  # MUST be False for cash-account compliance
    explicit_post_earnings_crush_thesis: bool = False  # required if iv_rank_over_70
    explicit_0dte_framing: bool = False                # required if dte_under_7
    divergence_thesis_documented: bool = False         # required if fighting_sqn_regime
    new_signal_for_average_down: bool = False          # required if averaging_down
    lotto_chase_documented: bool = False               # required if lotto_chase_warning
    weekly_trend_asset_override_documented: bool = False  # required if weekly_trend_asset_blocked
    weekly_trend_track_a_override_documented: bool = False  # required if weekly_trend_track_a_asset_blocked

    # Final
    entry_authorized: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class KillSheet:
    # Header
    ticker: str
    direction: str  # "long" | "short"
    intent: str     # "SCALP" | "SWING" | "TREND CAPTURE"
    trigger_tf: str  # "2H" | "4H" | "Daily"

    # Bias / confidence (derived from indicator alignment)
    bias: str
    confidence: str
    confidence_reason: str

    # Account context
    account_key: str
    account_name: str
    account_balance_usd: float
    risk_conviction: str  # "high" | "medium" | "speculative" | "default"
    risk_pct: float
    max_risk_usd: float

    # Indicator readings
    bar_date: str
    close_at_generation: float
    sqn_value: float | None
    regime: str

    ma_10: float
    ma_20: float
    ma_50: float
    ma_200: float
    ma_stack: str

    stoch_k: float
    stoch_d: float
    stoch_signal: str
    stoch_zone: str

    # Was the max_risk_usd budget hit by the per-trade absolute cap?
    risk_capped_by_max_trade: bool = False

    # Account-aware DTE band recommendation (lotto: 5-14, weekly: 120-180, etc).
    dte_band_label: str | None = None

    # Multi-timeframe context (None = not computed / unavailable)
    weekly_stack: str | None = None
    weekly_alignment: str | None = None
    tf_4h_stack: str | None = None
    tf_4h_pullback: str | None = None

    # G4 trigger-bar momentum (tracked, NOT a gate as of 2026-05-16).
    # Captures the most recently closed 2H trigger bar's open/close for the
    # forward-data collection that will decide whether to promote G4 to a
    # code-enforced gate. Backtest analysis in
    # [[project-lotto-g4-trigger-bar]] showed G4 lifts PF on the curated
    # LOTTO_HIGH_VOL_WATCHLIST but hurts on broad NDX-100 / broad ETFs,
    # so it's not a universal hard rule yet.
    #   trigger_bar_color: "green" | "red" | "doji" | None
    #   trigger_bar_in_direction: True if color matches direction
    #     (long+green or short+red), False otherwise. Doji → False.
    trigger_bar_open: float | None = None
    trigger_bar_close: float | None = None
    trigger_bar_color: str | None = None
    trigger_bar_in_direction: bool | None = None

    # User-supplied (placeholders if None)
    target_price: float | None = None
    trigger_description: str | None = None
    invalidation_price: float | None = None
    notes: str | None = None

    # Options structure (None = render Standard placeholder)
    options: OptionsStructure | None = None

    # Tactical 20-day SQN window (Tier 1 propagation, 2026-05-02).
    sqn_20_value: float | None = None
    regime_20: str | None = None
    sqn_diagnostic: str | None = None

    # Discipline-layer (DISCIPLINE-LAYER-ADDITION.md, 2026-05-02).
    status: Literal["AUTHORIZED", "REJECTED"] = "AUTHORIZED"
    rejection_reason: str | None = None
    divergence_thesis: str | None = None
    counter_weekly_thesis: str | None = None  # auto-passes rule 11 when populated

    # Account-rules outcome at generation. The dashboard is journal-first —
    # opening a position is never blocked (2026-05-10 decision) — so a breach
    # of the hard gates (premium-at-risk, max-open, cool-off, etc.) must be
    # persisted ON the sheet to stay visible at scoring/review time, not just
    # in the transient generation response. (2026-06.)
    rules_blocked: bool = False
    rule_violations: list = field(default_factory=list)

    # Account's cut-rule % (e.g. lotto -0.50, main/weekly -0.60), stamped from
    # config so the discipline scorer can apply the per-account cut threshold
    # without re-reading config. None → scorer uses the 70% band outer bound.
    cut_rule_pct: float | None = None
    discipline_attestation: DisciplineAttestation | None = None

    # Skill / tier tagging (Sprint A of orchestrator-change 2026-05-02).
    # Nullable defaults preserve every pre-existing test fixture; populated
    # going forward when build_standard receives a `skill` arg.
    skill: str | None = None
    tier: int | None = None
    scan_phase: Literal["baseline", "user_submitted", "free_range"] | None = None

    # Metadata
    generated_at: str = field(default_factory=_now_iso)
    # Stable identifier — required for the position-open authorization gate
    # (Phase B). Generated when the sheet is built; persisted only on
    # AUTHORIZED sheets so the position record can reference its origin.
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.options is not None:
            d["options"] = self.options.to_dict()
        return d

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=str)

    def to_text(self) -> str:
        bar = "═" * 51
        lines: list[str] = []
        lines.append(bar)
        lines.append(f"KILL SHEET: {self.ticker.upper()}")
        lines.append(bar)
        if self.status == "REJECTED":
            lines.append(f"⛔ REJECTED — {self.rejection_reason or 'regime gate failed'}")
            lines.append(
                "   Document divergence_thesis to override (kill sheet rebuilds with thesis)."
            )
            lines.append(bar)
        lines.append(
            f"Generated: {self.generated_at} | Bar: {self.bar_date} | "
            f"Close: ${self.close_at_generation:,.2f}"
        )
        lines.append(f"Direction: {self.direction.upper()}")
        lines.append(
            f"Account:   {self.account_name} (${self.account_balance_usd:,.0f}, "
            f"{self.risk_conviction} conviction)"
        )
        lines.append(f"Intent:    {self.intent}")
        lines.append(f"Trigger TF: {self.trigger_tf}")
        lines.append("")
        lines.append(f"BIAS:        {self.bias}")
        lines.append(f"CONFIDENCE:  {self.confidence} — {self.confidence_reason}")
        lines.append("")
        sqn_str = f"{self.sqn_value:.2f}" if self.sqn_value is not None else "n/a"
        lines.append(f"REGIME (SQN 100d): {self.regime} ({sqn_str})")
        sqn_20_str = (
            f"{self.sqn_20_value:.2f}" if self.sqn_20_value is not None else "n/a"
        )
        lines.append(
            f"REGIME (SQN 20d):  {self.regime_20 or 'n/a'} ({sqn_20_str})"
            + (f" — {self.sqn_diagnostic}" if self.sqn_diagnostic else "")
        )
        if self.divergence_thesis:
            lines.append(f"DIVERGENCE THESIS: {self.divergence_thesis}")
        if self.counter_weekly_thesis:
            lines.append(f"COUNTER-WEEKLY THESIS: {self.counter_weekly_thesis}")
        lines.append("")
        lines.append("WEEKLY CONTEXT:")
        if self.weekly_stack:
            lines.append(f"  Stack:     {self.weekly_stack}")
            lines.append(f"  Alignment: {self.weekly_alignment or '—'}")
        else:
            lines.append("  Stack:     [TBD — weekly bars unavailable]")
            lines.append("  Alignment: [TBD]")
        lines.append("")
        lines.append("MA RIBBON (Daily) — DIRECTION:")
        lines.append(f"  10 MA:     ${self.ma_10:,.2f}")
        lines.append(f"  20 MA:     ${self.ma_20:,.2f}")
        lines.append(f"  50 MA:     ${self.ma_50:,.2f}")
        lines.append(f"  200 MA:    ${self.ma_200:,.2f}")
        lines.append(f"  Stack:     {self.ma_stack}")
        lines.append("")
        lines.append("MA RIBBON (4H) — SWING TIMING:")
        if self.tf_4h_stack:
            lines.append(f"  Stack:     {self.tf_4h_stack}")
            lines.append(f"  Pullback:  {self.tf_4h_pullback or '—'}")
        else:
            lines.append("  Stack:     [TBD — 4H bars unavailable]")
            lines.append("  Pullback:  [TBD]")
        lines.append("")
        lines.append("STOCHASTIC (14,7,7) — Daily:")
        lines.append(f"  %K / %D:   {self.stoch_k:.1f} / {self.stoch_d:.1f}")
        lines.append(f"  Signal:    {self.stoch_signal}")
        lines.append(f"  Zone:      {self.stoch_zone}")
        lines.append("  TF Source: Daily")
        lines.append("")
        if self.trigger_bar_open is not None and self.trigger_bar_close is not None:
            lines.append("TRIGGER BAR (2H, most recent closed) — tracked, not gated:")
            lines.append(f"  Open:      ${self.trigger_bar_open:,.2f}")
            lines.append(f"  Close:     ${self.trigger_bar_close:,.2f}")
            color_label = (self.trigger_bar_color or "n/a").capitalize()
            in_dir_label = (
                "Y" if self.trigger_bar_in_direction
                else ("N" if self.trigger_bar_in_direction is False else "n/a")
            )
            lines.append(f"  Color:     {color_label}")
            lines.append(f"  In-direction: {in_dir_label}  (G4 informational)")
            lines.append("")
        lines.append("POSITION SIZING:")
        lines.append(f"  Account balance:   ${self.account_balance_usd:,.2f}")
        lines.append(
            f"  Risk %:            {self.risk_pct:.2%} ({self.risk_conviction})"
        )
        cap_note = " [capped by max_per_trade_usd]" if self.risk_capped_by_max_trade else ""
        lines.append(f"  Max loss budget:   ${self.max_risk_usd:,.2f}{cap_note}")
        if self.options is not None and self.options.premium > 0:
            cost_per_contract = self.options.premium * 100.0
            contracts = int(self.max_risk_usd // cost_per_contract)
            lines.append(
                f"  Contracts:         {contracts} "
                f"(premium ${self.options.premium:.2f} × 100 = ${cost_per_contract:.2f}/contract)"
            )
        else:
            lines.append(
                "  Units:             [set after invalidation/premium is defined]"
            )
        lines.append("")
        target = f"${self.target_price:,.2f}" if self.target_price else "[TBD — fill price]"
        invalidation = (
            f"${self.invalidation_price:,.2f}" if self.invalidation_price else
            "[TBD — fill price; thesis wrong below/above this]"
        )
        trigger = self.trigger_description or "[TBD — describe entry condition]"
        notes = self.notes or "[TBD]"
        lines.append(f"TARGET:        {target}")
        lines.append(f"TRIGGER:       {trigger}")
        lines.append(f"INVALIDATION:  {invalidation}")
        lines.append("")
        if self.dte_band_label:
            lines.append(f"DTE GUIDANCE:  {self.dte_band_label}")
            lines.append("")
        if self.options is None:
            lines.append("OPTION STRUCTURE: [pass --strike/--premium/... for options template]")
        else:
            o = self.options
            be = breakeven(o.strike, o.premium, o.contract_type)
            lines.append("OPTION STRUCTURE:")
            contract_label = f"{o.contract_type.upper()[0]} ({o.contract_type})"
            lines.append(
                f"  Contract:  {self.ticker} ${o.strike:g} {contract_label} exp {o.expiry}"
            )
            lines.append(f"  DTE:       {o.dte}")
            delta_str = f"{o.delta:.2f}" if o.delta is not None else "n/a"
            lines.append(f"  Delta:     ~{delta_str}")
            lines.append(f"  Premium:   ${o.premium:.2f} per contract (= max risk/contract)")
            lines.append(f"  Breakeven: ${be:.2f}")
            iv_label = iv_rank_label(o.iv_rank)
            iv_str = (
                f"{o.iv_rank:.1f}% ({iv_label})" if o.iv_rank is not None else "n/a"
            )
            lines.append(f"  IV Rank:   {iv_str}")
            oi_str = f"{o.open_interest:,}" if o.open_interest is not None else "n/a"
            lines.append(f"  Open Int:  {oi_str}")
            spread_str = (
                f"${o.bid_ask_spread:.2f}" if o.bid_ask_spread is not None else "n/a"
            )
            lines.append(f"  Spread:    {spread_str}")
        lines.append("")
        lines.append("EXIT PLAN:")
        lines.append("  - Take 50% at first target")
        lines.append("  - Trail stop on remainder when momentum persists")
        lines.append("  - Hard stop at invalidation")
        lines.append("  - Time stop at 50% DTE remaining if not working")
        lines.append("")
        lines.append(f"NOTES: {notes}")
        if self.discipline_attestation is not None:
            a = self.discipline_attestation
            lines.append("")
            lines.append("DISCIPLINE ATTESTATION (§8):")
            flagged: list[str] = []
            if a.iv_rank_over_70:
                flagged.append(
                    f"IV rank >70% (post-earnings crush thesis: "
                    f"{'YES' if a.explicit_post_earnings_crush_thesis else 'MISSING'})"
                )
            if a.dte_under_7:
                flagged.append(
                    f"DTE <7 (0DTE framing: "
                    f"{'YES' if a.explicit_0dte_framing else 'MISSING'})"
                )
            if a.daily_chop:
                flagged.append("Daily MA chop")
            if a.fighting_sqn_regime:
                flagged.append(
                    f"Fighting SQN regime (divergence thesis: "
                    f"{'YES' if a.divergence_thesis_documented else 'MISSING'})"
                )
            if a.averaging_down:
                flagged.append(
                    f"Averaging down (new signal: "
                    f"{'YES' if a.new_signal_for_average_down else 'MISSING'})"
                )
            if a.spreads_or_margin:
                flagged.append("⛔ Spreads/margin (HARD BLOCK — cash account)")
            if flagged:
                for f in flagged:
                    lines.append(f"  - {f}")
            else:
                lines.append("  - No anti-patterns flagged")
            lines.append(
                f"  ENTRY AUTHORIZED: {'YES' if a.entry_authorized else 'NO'}"
            )
        lines.append(bar)
        return "\n".join(lines)
