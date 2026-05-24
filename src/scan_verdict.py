"""Unified Buy / Wait / No-Go verdict mapping for all scan outputs.

Every per-ticker scan setup (weekly-trend, lotto, index-swing) maps its
internal classification into a uniform 3-state verdict for UI display.
This module is the single source of truth for that mapping.

  BUY    — actionable trade today; entry/stop are concrete and the regime
           gate is clean. User pre-fills options data and ships the kill sheet.
  WAIT   — setup forming or marginal; entry exists but quality filters fail
           or a confirmation is still pending. User sets alerts, doesn't enter.
  NO_GO  — blocked by hard rule (chop, regime conflict, universe violation,
           chase warning, etc.). No entry under any circumstance.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Verdict = Literal["buy", "wait", "no_go"]


@dataclass
class TradeVerdict:
    """Unified per-setup verdict + reason."""
    verdict: Verdict
    reason: str  # one-line UI-ready explanation

    def to_dict(self) -> dict[str, str]:
        return {"verdict": self.verdict, "reason": self.reason}


# ─────────────────────────────────────────────────────────────────────────
# Weekly-trend confluence → verdict
# ─────────────────────────────────────────────────────────────────────────

_WEEKLY_BUY = {
    "high_conviction_long",
    "high_conviction_short",
    "continuation_long",
    "continuation_short",
    "track_a_cross_long",
    "track_a_cross_short",
}
_WEEKLY_WAIT = {"compression", "no_setup"}
_WEEKLY_NO_GO = {"chop"}

# Continuation requires a fresh stochastic event, not just structural state.
# Tightened 2026-05-15 — prior version classed "stack-aligned-state with no
# fresh trigger" as BUY, surfacing trickle-up names like ABNB without a
# discrete entry. Now those map to WAIT until a momentum event fires.
_CONTINUATION_FRESH_SIGNALS_LONG = {"bull_continuation", "bull_cross_oversold"}
_CONTINUATION_FRESH_SIGNALS_SHORT = {"bear_continuation", "bear_cross_overbought"}

# Track A 19/39 cross requires meaningful separation to count as a real
# transition. Razor-thin crosses (e.g., 19WMA $27.97 vs 39WMA $27.92 = 0.05
# separation on $29 spot = 0.17% apart) are statistical noise and the
# strongest base-rate driver of false Track A signals.
TRACK_A_MIN_SEPARATION_PCT = 0.5  # 19WMA vs 39WMA, % of spot

# Track A stretch ceiling. A Track A cross is the EARLY signal — designed
# to catch the transition before the full ribbon confirms. When the close
# is already this far above the 19WMA stop, the entry is too late: the
# stop distance becomes the framework's max-loss anchor (e.g., ARM at $211
# with 19WMA $146 = 44% drawdown to stop, unworkable for LEAPS sizing). The
# better entry is the retest of the broken structure.
TRACK_A_MAX_STRETCH_PCT = 15.0  # close vs 19WMA, % above


def weekly_verdict(
    confluence: str,
    direction: str,
    sqn_100_regime: str | None,
    blockers: list[str],
    *,
    stoch_signal: str | None = None,
    track_a_separation_pct: float | None = None,
    bar_is_bullish: bool | None = None,
    track_a_stretch_pct: float | None = None,
) -> TradeVerdict:
    """Map weekly-trend confluence + regime to Buy / Wait / No-Go.

    Counter-trend regime downgrades a BUY to WAIT (still permitted with
    divergence thesis but the verdict surface should not say BUY without
    that thesis). HARD blockers (chop) override everything else.

    `stoch_signal` (added 2026-05-15) — when provided, `continuation_*`
    confluence requires a fresh trigger ("bull_continuation"/"bull_cross_oversold"
    for longs, mirror for shorts). Pure stack-aligned state without a fresh
    momentum event downgrades to WAIT.

    `track_a_separation_pct` (added 2026-05-15) — when provided, Track A
    crosses with sub-`TRACK_A_MIN_SEPARATION_PCT` separation downgrade to
    WAIT. Razor-thin crosses (e.g., 0.05/29 = 0.17%) are noise.

    `bar_is_bullish` (added 2026-05-15) — when provided, every LONG BUY
    requires bar_is_bullish=True (close > open = green candle), every
    SHORT BUY requires bar_is_bullish=False (red candle). A signal that
    fires on a counter-color bar is treated as a reversal, not a
    confirmation, and is downgraded to WAIT.

    `track_a_stretch_pct` (added 2026-05-15) — when provided, Track A
    longs with `(close - 19WMA) / 19WMA * 100 > TRACK_A_MAX_STRETCH_PCT`
    (default 15%) downgrade to WAIT. Captures late-firing Track A signals
    after a vertical move (e.g., ARM at $211 vs 19WMA $146 = 44% stretched).
    """
    if confluence in _WEEKLY_NO_GO:
        return TradeVerdict("no_go", "MA ribbon tangled — no trend, no trade")

    if confluence in _WEEKLY_WAIT:
        if confluence == "compression":
            return TradeVerdict("wait", "MAs compressing — wait for breakout direction")
        return TradeVerdict("wait", "Stack present but no Stochastic trigger yet")

    if confluence in _WEEKLY_BUY:
        # Check for counter-trend regime — downgrade to WAIT if so
        opposing_long = direction == "long" and sqn_100_regime in ("bear", "strong_bear")
        opposing_short = direction == "short" and sqn_100_regime in ("bull", "strong_bull")
        if opposing_long or opposing_short:
            return TradeVerdict(
                "wait",
                f"Setup fires but SQN(100) {sqn_100_regime} opposes — "
                f"requires divergence thesis",
            )

        # Green-candle confirmation (2026-05-15). A long setup that fires on
        # a red weekly bar is a reversal pattern, not a confirmation. Mirror
        # for shorts on green bars. When bar_is_bullish is None we skip the
        # check (preserves back-compat for callers that don't pass it).
        if bar_is_bullish is not None:
            if direction == "long" and not bar_is_bullish:
                return TradeVerdict(
                    "wait",
                    "Long setup but weekly bar closed red (close < open) — "
                    "wait for a green-candle confirmation",
                )
            if direction == "short" and bar_is_bullish:
                return TradeVerdict(
                    "wait",
                    "Short setup but weekly bar closed green (close > open) — "
                    "wait for a red-candle confirmation",
                )
        # Track A on a blocked asset → downgrade to WAIT
        if confluence in ("track_a_cross_long", "track_a_cross_short"):
            if any("Track A blocked list" in b for b in blockers):
                return TradeVerdict(
                    "wait",
                    "Track A signal fires but asset is on the Track A blocked list — "
                    "use Track B (full ribbon) or skip",
                )
            # Tightening (2026-05-15): require minimum 19/39 separation.
            if (
                track_a_separation_pct is not None
                and track_a_separation_pct < TRACK_A_MIN_SEPARATION_PCT
            ):
                return TradeVerdict(
                    "wait",
                    f"Track A cross fires but 19/39 separation only "
                    f"{track_a_separation_pct:.2f}% of price — needs "
                    f"≥{TRACK_A_MIN_SEPARATION_PCT}% to confirm transition",
                )
            # Tightening (2026-05-15): stretch ceiling — Track A entries
            # are stop-anchored to the 19WMA; >15% extension makes the
            # framework stop unworkable. Wait for retest of structure.
            if (
                track_a_stretch_pct is not None
                and abs(track_a_stretch_pct) > TRACK_A_MAX_STRETCH_PCT
            ):
                direction_word = "above" if track_a_stretch_pct > 0 else "below"
                return TradeVerdict(
                    "wait",
                    f"Track A cross fires but close is "
                    f"{abs(track_a_stretch_pct):.1f}% {direction_word} the 19WMA "
                    f"(>{TRACK_A_MAX_STRETCH_PCT}% ceiling) — stretched entry, "
                    f"wait for retest of broken structure",
                )
            label = "Track A 19/39 cross" if confluence.startswith("track_a") else None
            if label:
                return TradeVerdict("buy", f"{label} fires with regime confirmation")
        if confluence.startswith("high_conviction"):
            return TradeVerdict("buy", "Full stack + high-conviction Stochastic trigger")
        if confluence.startswith("continuation"):
            # Tightening (2026-05-15): require fresh Stoch trigger, not just
            # state. When stoch_signal is provided, gate on it.
            if stoch_signal is not None:
                fresh_set = (
                    _CONTINUATION_FRESH_SIGNALS_LONG if direction == "long"
                    else _CONTINUATION_FRESH_SIGNALS_SHORT
                )
                if stoch_signal not in fresh_set:
                    return TradeVerdict(
                        "wait",
                        f"Stack-aligned continuation but Stochastic signal "
                        f"\"{stoch_signal}\" — wait for fresh "
                        f"{'bullish' if direction == 'long' else 'bearish'} cross",
                    )
            return TradeVerdict("buy", "Trend continuation in stack-aligned direction")
        return TradeVerdict("buy", confluence.replace("_", " "))

    return TradeVerdict("wait", "Insufficient signal data")


# ─────────────────────────────────────────────────────────────────────────
# Index-swing confluence → verdict
# ─────────────────────────────────────────────────────────────────────────


def index_swing_verdict(
    confluence: str, confluence_count: int | None,
) -> TradeVerdict:
    """Map index-swing confluence to Buy / Wait / No-Go."""
    if confluence == "breakout_high_conviction":
        return TradeVerdict(
            "buy",
            f"Breakout above prior swing high with "
            f"{confluence_count or 3}/5 quality filters",
        )
    if confluence == "breakout_standard":
        return TradeVerdict(
            "wait",
            f"Breakout fires but only {confluence_count or 1}/5 quality filters — "
            f"size at speculative tier or wait for re-test",
        )
    if confluence == "no_breakout":
        return TradeVerdict("wait", "No 2H close above prior swing high yet")
    if confluence == "skip_bear_volatile":
        return TradeVerdict(
            "no_go",
            "Structural bear-volatile regime — only net-negative regime in backtest",
        )
    if confluence == "skip_low_volume":
        return TradeVerdict(
            "no_go",
            "Breakout volume below 0.7× average — false-breakout risk too high",
        )
    if confluence == "skip_macro_event":
        return TradeVerdict("no_go", "Major macro event in next 3 sessions — gap risk")
    if confluence == "universe_violation":
        return TradeVerdict(
            "no_go",
            "Outside QQQ/IWM/SPY hard universe",
        )
    return TradeVerdict("wait", "Setup state unrecognized")


# ─────────────────────────────────────────────────────────────────────────
# Lotto confluence → verdict (used by lotto/scanner)
# ─────────────────────────────────────────────────────────────────────────


LOTTO_VERDICT_VERSION = 2  # bumped 2026-05-12 — cohort-derived gates added


def lotto_verdict(
    daily_stack: str | None,
    sqn_100_regime: str | None,
    sqn_20_value: float | None,
    h2_signal: str | None,
    h2_zone: str | None,
    direction: str,
) -> TradeVerdict:
    """Classify a lotto setup. Direction is the proposed lotto direction.

    Hard NO-GO conditions:
      - Daily stack chop
      - SQN(100) opposes (Strong Bear + bullish lotto, Strong Bull + bearish lotto)
      - SQN(20) > +2.5 + bullish lotto (chase warning)
      - Structural Bear-Volatile (SQN-100 Strong Bear, or SQN-100 Bear + SQN-20 < -1.9)
        + bullish lotto
      - SQN(100) Strong Bear + bearish lotto (mean-reversion zone, v2)
      - Stack `bull_developing` + SQN(20) < +0.5 long (soft-setup drag, v2)
      - Stack `full_bull` + SQN(20) in +0.5..+1.4 long (mid-momentum chop, v2)

    BUY: Daily stack supports + 2H Stoch cross from extreme + regime aligned
    WAIT: Daily supports + no 2H trigger yet, or marginal 2H signal

    Version 2 gates (the last three above) are empirically derived from a
    620-trade 2y backtest across 25 tickers (see scripts/lotto_options_backtest.py
    and scripts/lotto_cohort_analysis.py for the supporting analysis).
    """
    # Hard NO-GO conditions first
    if daily_stack in ("chop", "tangled", None):
        return TradeVerdict("no_go", "Daily MA stack is chop — no trend, no trade")

    if direction == "long":
        if sqn_100_regime == "strong_bear":
            return TradeVerdict(
                "no_go", "SQN(100) Strong Bear — bullish lotto regime conflict",
            )
        if sqn_100_regime == "bear" and sqn_20_value is not None and sqn_20_value < -1.9:
            return TradeVerdict(
                "no_go",
                "SQN(100) Bear + SQN(20) < -1.9 — structural Bear-Volatile, hard skip",
            )
        if sqn_20_value is not None and sqn_20_value > 2.5:
            return TradeVerdict(
                "no_go",
                f"SQN(20) {sqn_20_value:.2f} > +2.5 — chase warning, wait for reset",
            )
        # Direction conflict with daily stack
        if daily_stack in ("full_bear", "bear_developing"):
            return TradeVerdict(
                "no_go",
                f"Daily stack {daily_stack} opposes long lotto direction",
            )
        # v2 cohort gates (longs)
        # The "bull_developing + sqn20 < +0.5" cohort was the largest stealth
        # drag in backtest (n≈80, avgR≈-0.5). A developing trend without
        # momentum confirmation is a soft setup that hard-stops on the chop.
        if daily_stack == "bull_developing" and (
            sqn_20_value is None or sqn_20_value < 0.5
        ):
            return TradeVerdict(
                "no_go",
                "Stack bull_developing without momentum (SQN(20) < +0.5) — "
                "soft-setup drag cohort (v2)",
            )
        # "full_bull + sqn20 in bull band (+0.5..+1.4)" was the biggest single
        # losing cohort (n=162, avgR -0.31). The pattern: price in clean
        # uptrend but momentum mid-range = consolidation chop kills the
        # 0.20-delta lotto. Full_bull works ONLY with SQN(20) in neutral
        # (pullback waiting for cross) or strong_bull (acceleration).
        if (
            daily_stack == "full_bull"
            and sqn_20_value is not None
            and 0.5 <= sqn_20_value < 1.4
        ):
            return TradeVerdict(
                "no_go",
                f"Stack full_bull + SQN(20) {sqn_20_value:.2f} in mid-momentum band "
                "(+0.5..+1.4) — consolidation-chop cohort (v2)",
            )
    else:  # short
        if sqn_100_regime == "strong_bull":
            return TradeVerdict(
                "no_go", "SQN(100) Strong Bull — bearish lotto regime conflict",
            )
        # v2: extend strong_bear skip to shorts too. The most extended bear
        # is mean-reversion territory — backtest short cohort avgR -0.45 (n=10).
        if sqn_100_regime == "strong_bear":
            return TradeVerdict(
                "no_go",
                "SQN(100) Strong Bear — mean-reversion zone, short lotto fails here (v2)",
            )
        if sqn_100_regime in ("bull", "neutral"):
            return TradeVerdict(
                "no_go",
                f"SQN(100) {sqn_100_regime} — bearish lotto requires Bear regime + thesis",
            )
        if daily_stack in ("full_bull", "bull_developing"):
            return TradeVerdict(
                "no_go",
                f"Daily stack {daily_stack} opposes short lotto direction",
            )

    # Now BUY vs WAIT based on 2H trigger. Divergence is included on both
    # sides for parity with src/free_range/filters.py::_stoch_score, which
    # treats divergence as a full-strength bullish/bearish signal. Earlier
    # omission was an oversight; measurement showed it doubled the live
    # signal rate on QQQ + GLD without weakening any other gate.
    long_signals = {
        "bull_cross_oversold", "bull_continuation", "bullish_divergence",
    }
    short_signals = {
        "bear_cross_overbought", "bear_continuation", "bearish_divergence",
    }

    if direction == "long" and h2_signal in long_signals and h2_zone in ("oversold", "mid"):
        return TradeVerdict("buy", f"2H {h2_signal} from {h2_zone} — long lotto trigger")
    if direction == "short" and h2_signal in short_signals and h2_zone in ("overbought", "mid"):
        return TradeVerdict(
            "buy", f"2H {h2_signal} from {h2_zone} — short lotto trigger",
        )

    # Daily stack supports but no 2H trigger
    return TradeVerdict(
        "wait",
        f"Daily stack {daily_stack or 'unknown'} supports — wait for 2H Stoch trigger",
    )


__all__ = [
    "TradeVerdict",
    "Verdict",
    "weekly_verdict",
    "index_swing_verdict",
    "lotto_verdict",
    "LOTTO_VERDICT_VERSION",
]
