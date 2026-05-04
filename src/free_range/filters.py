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

from typing import Any

from free_range.universe import is_etf


# Single-stock price band per orchestrator (account profile in ~/CLAUDE.md):
#   $15-50 for single stocks, ETFs at any price.
PRICE_MIN_SINGLE_STOCK: float = 15.0
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

def _stack_score(stack_state: str | None, direction: str) -> int:
    """MA Ribbon stack alignment. Returns 0 for unknown / chop / tangled."""
    if stack_state is None:
        return 0
    s = stack_state.lower()
    long_ = direction == "long"
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


def score_direction(scan_row: dict[str, Any], direction: str) -> tuple[int, list[str]]:
    """Score a (scan_row, direction) pair and return (total, blockers)."""
    stack = (scan_row.get("ma_ribbon") or {}).get("stack_state")
    stoch_zone = (scan_row.get("stochastic") or {}).get("zone")
    stoch_signal = (scan_row.get("stochastic") or {}).get("signal")
    sqn_100 = (scan_row.get("sqn") or {}).get("regime")

    stack_pts = _stack_score(stack, direction)
    stoch_pts = _stoch_score(stoch_zone, stoch_signal, direction)
    regime_pts = _regime_score(sqn_100, direction)

    blockers: list[str] = []
    if stack and stack.lower() in ("chop", "tangled"):
        blockers.append("MA tangle — orchestrator says no trend, no trade")
    if regime_pts <= -20:
        blockers.append(f"SQN(100) {sqn_100} opposes {direction}")

    return stack_pts + stoch_pts + regime_pts, blockers


# Minimum score for a free-range candidate to make the top-5 cut.
# Calibration: a "watch" Sunday-scan setup at FOCUS_THRESHOLD=30 means
# components add to a meaningful directional bias. We use the same floor
# here so free-range isn't more permissive than the focused scan.
FREE_RANGE_MIN_SCORE: int = 30


def best_direction(scan_row: dict[str, Any]) -> tuple[str, int, list[str]]:
    """Pick the better of (long, short) for this scan_row.

    Returns (direction, score, blockers). When both sides are blocked or
    score below the floor, returns the higher-scoring side anyway — caller
    decides whether to keep it via the score floor + blockers list.
    """
    long_score, long_blockers = score_direction(scan_row, "long")
    short_score, short_blockers = score_direction(scan_row, "short")
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
