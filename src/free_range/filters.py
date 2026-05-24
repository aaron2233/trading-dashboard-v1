"""Free-range filters: price band, indicator alignment, liquid options.

Each filter returns either a violation reason string (truthy = rejected) or
None (passed). This lets the scanner accumulate filter notes without raising
on the first failure, which is useful for diagnostic logging when a candidate
just missed.

Indicator alignment per orchestrator rule 12 in ~/CLAUDE.md:

    Tier 1 = Weekly MA Ribbon + Weekly Stoch + supportive SQN(100)
    Tier 2 = Daily bias + 2H trigger + supportive SQN(100)/SQN(20)

For free-range V1 we use Daily MA + Daily Stoch + SQN(100) regime as the
common alignment baseline. Tier-specific timeframes (Weekly for Tier 1, 2H
for Tier 2) are a v2 extension — V1 surfaces both tiers from the same Daily
read, with the user expected to drop down to the trigger TF before pulling
the trigger via kill sheet.
"""
from __future__ import annotations

from typing import Any, Literal

from free_range.universe import is_etf


ScoringVersion = Literal["v1", "v2", "v3"]
# Default scoring version. v1 is the original heavily-MA-weighted scorer.
#
# v2 halves the MA stack contribution and softens the chop penalty.
# Backtest (2026-05-11, QQQ + GLD daily, 1999-2026): v2 LOST on every
# primary metric — total return roughly halved on QQQ, max DD 15pp worse
# on GLD. The MA chop block was filtering useful trades; softening it
# admitted neutral-regime losers. v2 path kept for future experiments
# but is NOT recommended for production.
#
# v3 layers a price-action signal (close through prior 5-bar swing high/
# low, worth 0-20 pts) on TOP of unchanged v1 MA scoring. Targets the
# original concern (MA lags trending entries) by adding a leading signal
# rather than removing a lagging gate. Backtest result drives the
# production switch decision.
DEFAULT_SCORING_VERSION: ScoringVersion = "v1"


# Single-stock price band per orchestrator (account profile in ~/CLAUDE.md):
#   $10-50 for single stocks, ETFs at any price.
# 2026-05-14: lowered floor from $15 → $10 per backtest evidence on the
# focused lotto universe (~/Documents/App Development/Trading Dashboard/v0.1/
# scripts/lotto_focused_10_30_universe_2y.csv). The $10-$50 slice produced
# PF 2.54 / mean R +1.38 vs the prior $15-$50 baseline at PF 1.31 / +0.27.
# Captures RDW/MARA/RGTI-style cheap-premium high-vol names without
# amputating PLTR/IONQ runs at higher prices. Applies to lotto + free-range
# scanners (the only consumers of price_band_violation).
PRICE_MIN_SINGLE_STOCK: float = 10.0
PRICE_MAX_SINGLE_STOCK: float = 50.0


# ─────────────────────────────────────────────────────────────────────────
# Price band
# ─────────────────────────────────────────────────────────────────────────

def price_band_violation(ticker: str, current_price: float | None) -> str | None:
    """Return rejection reason if ticker fails the price-band filter, else None.

    ETFs (per universe.is_etf) are exempt — any price is acceptable.
    Missing price data is a hard reject (we can't size or filter anything).
    """
    if current_price is None:
        return "no current price"
    if is_etf(ticker):
        return None
    if current_price < PRICE_MIN_SINGLE_STOCK:
        return f"price ${current_price:.2f} below ${PRICE_MIN_SINGLE_STOCK:.0f} single-stock floor"
    if current_price > PRICE_MAX_SINGLE_STOCK:
        return f"price ${current_price:.2f} above ${PRICE_MAX_SINGLE_STOCK:.0f} single-stock cap"
    return None


# ─────────────────────────────────────────────────────────────────────────
# Indicator alignment + scoring
# ─────────────────────────────────────────────────────────────────────────
#
# Scoring mirrors focus.sunday_scan but is regime-direction-agnostic — we
# evaluate both long and short setups and let the higher score win. The
# direction of the winning side becomes the candidate's recommended bias.

def _stack_score(
    stack_state: str | None,
    direction: str,
    *,
    scoring_version: ScoringVersion = DEFAULT_SCORING_VERSION,
) -> int:
    """MA Ribbon stack alignment. Returns 0 for unknown stack states.

    v1 — Original weighting. MA stack worth up to +30 / -25 per direction;
    chop is -25 ("tangled MAs = no trade").

    v2 — MA lag mitigation. Halves both the aligned (+30 → +15) and
    opposed (-20 → -10) contributions, and softens chop from -25 to -10.
    Combined with unchanged Stoch (0-30) and SQN (0-30) scoring, this
    drops MA from ~33% of max score to ~20%, letting price-action via
    Stoch + regime drive the entry signal earlier on trending names.
    """
    if stack_state is None:
        return 0
    s = stack_state.lower()
    long_ = direction == "long"

    if scoring_version == "v2":
        if s == "full_bull":
            return 15 if long_ else -10
        if s == "bull_developing":
            return 10 if long_ else -5
        if s == "compression":
            return 5
        if s in ("chop", "tangled"):
            return -10
        if s == "bear_developing":
            return -5 if long_ else 10
        if s == "full_bear":
            return -10 if long_ else 15
        return 0

    # v1 (default)
    if s == "full_bull":
        return 30 if long_ else -20
    if s == "bull_developing":
        return 20 if long_ else -10
    if s == "compression":
        return 5
    if s in ("chop", "tangled"):
        return -25  # orchestrator rule: tangled MAs = no trade, ever
    if s == "bear_developing":
        return -10 if long_ else 20
    if s == "full_bear":
        return -20 if long_ else 30
    return 0


def _stoch_score(zone: str | None, signal: str | None, direction: str) -> int:
    """Stochastic alignment. Wrong-side signals carry penalties."""
    if zone is None and signal is None:
        return 0
    z = (zone or "").lower()
    sig = (signal or "").lower()
    long_ = direction == "long"
    if long_ and sig in ("bull_cross_oversold", "bullish_divergence", "bull_continuation"):
        return 30
    if not long_ and sig in ("bear_cross_overbought", "bearish_divergence", "bear_continuation"):
        return 30
    if long_ and sig in ("bear_cross_overbought", "bearish_divergence"):
        return -20
    if not long_ and sig in ("bull_cross_oversold", "bullish_divergence"):
        return -20
    if long_ and z == "oversold":
        return 15
    if not long_ and z == "overbought":
        return 15
    if long_ and z == "overbought":
        return -10
    if not long_ and z == "oversold":
        return -10
    return 0


def _regime_score(sqn_100: str | None, direction: str) -> int:
    """SQN(100) regime alignment. Strong-aligned = bonus, opposed = penalty.

    Same direction-long-in-bull / direction-short-in-bear logic as focus.sunday_scan,
    but parameterized only on the direction (not asset) since free-range is
    asset-agnostic.
    """
    if sqn_100 is None:
        return 0
    r = sqn_100.lower()
    long_ = direction == "long"
    if r == "strong_bull":
        return 30 if long_ else -25
    if r == "bull":
        return 20 if long_ else -15
    if r == "neutral":
        return 5  # neutral regime = small benefit-of-the-doubt either way
    if r == "bear":
        return -15 if long_ else 20
    if r == "strong_bear":
        return -25 if long_ else 30
    return 0


def _price_action_score(
    price_action: dict[str, Any] | None,
    direction: str,
    *,
    scoring_version: ScoringVersion = DEFAULT_SCORING_VERSION,
) -> int:
    """Leading price-action signal — fires when current close pierces the
    prior 5-bar swing high (long) or swing low (short). 0 outside v3.

    Active only under v3. v1/v2 return 0 unconditionally so non-v3 callers
    that don't supply `price_action` see no scoring change.

    Expected keys on the `price_action` dict (callers populate from bars):
      - breakout_5bar_long  : bool — close > prior 5-bar high
      - breakout_5bar_short : bool — close < prior 5-bar low
    Missing keys are treated as False (no contribution, not a penalty).
    """
    if scoring_version != "v3" or not price_action:
        return 0
    if direction == "long" and price_action.get("breakout_5bar_long"):
        return 20
    if direction == "short" and price_action.get("breakout_5bar_short"):
        return 20
    return 0


def score_direction(
    scan_row: dict[str, Any],
    direction: str,
    *,
    scoring_version: ScoringVersion = DEFAULT_SCORING_VERSION,
) -> tuple[int, list[str]]:
    """Score a (scan_row, direction) pair and return (total, blockers).

    `scoring_version` selects the weighting profile:
      - v1: MA stack (0-30) + Stoch (0-30) + SQN (0-30) — current production.
      - v2: halved MA stack + same Stoch/SQN. Backtest underperforms v1.
      - v3: full v1 MA stack + Stoch + SQN + price-action breakout (0-20).
    """
    stack = (scan_row.get("ma_ribbon") or {}).get("stack_state")
    stoch_zone = (scan_row.get("stochastic") or {}).get("zone")
    stoch_signal = (scan_row.get("stochastic") or {}).get("signal")
    sqn_100 = (scan_row.get("sqn") or {}).get("regime")
    price_action = scan_row.get("price_action")

    stack_pts = _stack_score(stack, direction, scoring_version=scoring_version)
    stoch_pts = _stoch_score(stoch_zone, stoch_signal, direction)
    regime_pts = _regime_score(sqn_100, direction)
    pa_pts = _price_action_score(
        price_action, direction, scoring_version=scoring_version,
    )

    blockers: list[str] = []
    if stack and stack.lower() in ("chop", "tangled"):
        blockers.append("MA tangle — orchestrator says no trend, no trade")
    if regime_pts <= -20:
        blockers.append(f"SQN(100) {sqn_100} opposes {direction}")

    return stack_pts + stoch_pts + regime_pts + pa_pts, blockers


# Minimum score for a free-range candidate to make the top-5 cut.
# Calibration: a "watch" Sunday-scan setup at FOCUS_THRESHOLD=30 means
# components add to a meaningful directional bias. We use the same floor
# here so free-range isn't more permissive than the focused scan.
FREE_RANGE_MIN_SCORE: int = 30


def best_direction(
    scan_row: dict[str, Any],
    *,
    scoring_version: ScoringVersion = DEFAULT_SCORING_VERSION,
) -> tuple[str, int, list[str]]:
    """Pick the better of (long, short) for this scan_row.

    Returns (direction, score, blockers). When both sides are blocked or
    score below the floor, returns the higher-scoring side anyway — caller
    decides whether to keep it via the score floor + blockers list.
    """
    long_score, long_blockers = score_direction(
        scan_row, "long", scoring_version=scoring_version,
    )
    short_score, short_blockers = score_direction(
        scan_row, "short", scoring_version=scoring_version,
    )
    if long_score >= short_score:
        return "long", long_score, long_blockers
    return "short", short_score, short_blockers


# ─────────────────────────────────────────────────────────────────────────
# Why-now string
# ─────────────────────────────────────────────────────────────────────────

def build_why_now(direction: str, scan_row: dict[str, Any]) -> str:
    """One-line trigger summary for the snapshot card."""
    stack = (scan_row.get("ma_ribbon") or {}).get("stack_state") or "?"
    stoch_zone = (scan_row.get("stochastic") or {}).get("zone")
    stoch_signal = (scan_row.get("stochastic") or {}).get("signal")
    regime = (scan_row.get("sqn") or {}).get("regime") or "?"

    parts: list[str] = []
    if stoch_signal and stoch_signal not in ("none", None):
        parts.append(stoch_signal.replace("_", " "))
    elif stoch_zone in ("oversold", "overbought"):
        parts.append(f"stoch {stoch_zone}")

    parts.append(f"{stack} stack")
    parts.append(f"SQN(100) {regime}")

    return f"{direction.upper()}: " + " · ".join(parts)
