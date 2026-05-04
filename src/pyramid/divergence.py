"""Stochastic / price divergence detection for pyramid exit signals.

Bearish divergence: price makes a higher swing-high while Stochastic %K makes
a lower swing-high — momentum is fading even though price is still rising.
This is the classic "exhaustion" pattern that warrants trimming a long.

Bullish divergence (mirror): price makes a lower swing-low while Stoch %K
makes a higher swing-low — selling momentum is fading.

Pivot detection mirrors src/pyramid/structure.py — local extremum within ±N
bars on each side. We compare the two most recent pivots in the lookback
window. Returns False when there isn't enough data — never raises so it can
be called from the exit cascade without try/except.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


# Mirrors structure.py — same window so divergence picks up on the same
# swings the structure read uses for higher-low / lower-high checks.
DEFAULT_LOOKBACK = 30
PIVOT_BARS = 3


@dataclass
class DivergenceResult:
    """Why-line for the directive — surfaces the underlying pivot values so the
    user can verify on chart instead of trusting a boolean.
    """
    confirmed: bool
    price_pivot_recent: float | None = None
    price_pivot_prior: float | None = None
    stoch_pivot_recent: float | None = None
    stoch_pivot_prior: float | None = None
    note: str = ""


def _find_pivot_highs(values: pd.Series, n: int = PIVOT_BARS) -> list[tuple[int, float]]:
    if len(values) < (2 * n + 1):
        return []
    pivots: list[tuple[int, float]] = []
    arr = values.values
    for i in range(n, len(values) - n):
        window = arr[i - n : i + n + 1]
        if arr[i] == window.max() and (window == arr[i]).sum() == 1:
            pivots.append((i, float(arr[i])))
    return pivots


def _find_pivot_lows(values: pd.Series, n: int = PIVOT_BARS) -> list[tuple[int, float]]:
    if len(values) < (2 * n + 1):
        return []
    pivots: list[tuple[int, float]] = []
    arr = values.values
    for i in range(n, len(values) - n):
        window = arr[i - n : i + n + 1]
        if arr[i] == window.min() and (window == arr[i]).sum() == 1:
            pivots.append((i, float(arr[i])))
    return pivots


def _aligned_stoch_at(stoch_k: pd.Series, idx: int) -> float | None:
    """Return stoch %K value at the same window-index as the price pivot.

    The caller already truncated to the same lookback window for both series,
    so a positional lookup works. Falls back to None on bounds errors.
    """
    if idx < 0 or idx >= len(stoch_k):
        return None
    val = stoch_k.iloc[idx]
    if pd.isna(val):
        return None
    return float(val)


def detect_bearish_divergence(
    closes: pd.Series,
    stoch_k: pd.Series,
    lookback: int = DEFAULT_LOOKBACK,
) -> DivergenceResult:
    """Bearish divergence: higher price-pivot-high + lower stoch-pivot-high.

    Operates on the most recent `lookback` bars. Both series must align on the
    same index — caller is responsible for that (typical use is the daily
    bars + Stochastic.compute() output, which share the same DatetimeIndex).
    """
    if len(closes) < (2 * PIVOT_BARS + 1) or len(stoch_k) < (2 * PIVOT_BARS + 1):
        return DivergenceResult(confirmed=False, note="insufficient bars for pivot detection")

    # Truncate to lookback window
    p_window = closes.tail(lookback)
    s_window = stoch_k.tail(lookback)

    price_highs = _find_pivot_highs(p_window)
    if len(price_highs) < 2:
        return DivergenceResult(
            confirmed=False, note="fewer than 2 price pivot highs in lookback window"
        )

    # Most recent two price pivots
    (idx_recent, price_recent) = price_highs[-1]
    (idx_prior, price_prior) = price_highs[-2]

    stoch_recent = _aligned_stoch_at(s_window, idx_recent)
    stoch_prior = _aligned_stoch_at(s_window, idx_prior)
    if stoch_recent is None or stoch_prior is None:
        return DivergenceResult(
            confirmed=False,
            price_pivot_recent=price_recent, price_pivot_prior=price_prior,
            note="stochastic value missing at one of the pivots",
        )

    # Bearish: price higher AND stoch lower
    higher_price = price_recent > price_prior
    lower_stoch = stoch_recent < stoch_prior
    confirmed = higher_price and lower_stoch
    note = (
        f"price {price_prior:.2f}→{price_recent:.2f}, "
        f"stoch %K {stoch_prior:.1f}→{stoch_recent:.1f}"
    )
    if not higher_price:
        note += " — price didn't make a higher high"
    elif not lower_stoch:
        note += " — stoch didn't make a lower high"
    return DivergenceResult(
        confirmed=confirmed,
        price_pivot_recent=price_recent, price_pivot_prior=price_prior,
        stoch_pivot_recent=stoch_recent, stoch_pivot_prior=stoch_prior,
        note=note,
    )


def detect_bullish_divergence(
    closes: pd.Series,
    stoch_k: pd.Series,
    lookback: int = DEFAULT_LOOKBACK,
) -> DivergenceResult:
    """Bullish divergence: lower price-pivot-low + higher stoch-pivot-low.

    Mirror of detect_bearish_divergence — used for short-side Stoch <20
    exhaustion exits.
    """
    if len(closes) < (2 * PIVOT_BARS + 1) or len(stoch_k) < (2 * PIVOT_BARS + 1):
        return DivergenceResult(confirmed=False, note="insufficient bars for pivot detection")

    p_window = closes.tail(lookback)
    s_window = stoch_k.tail(lookback)

    price_lows = _find_pivot_lows(p_window)
    if len(price_lows) < 2:
        return DivergenceResult(
            confirmed=False, note="fewer than 2 price pivot lows in lookback window"
        )

    (idx_recent, price_recent) = price_lows[-1]
    (idx_prior, price_prior) = price_lows[-2]

    stoch_recent = _aligned_stoch_at(s_window, idx_recent)
    stoch_prior = _aligned_stoch_at(s_window, idx_prior)
    if stoch_recent is None or stoch_prior is None:
        return DivergenceResult(
            confirmed=False,
            price_pivot_recent=price_recent, price_pivot_prior=price_prior,
            note="stochastic value missing at one of the pivots",
        )

    # Bullish: price lower AND stoch higher
    lower_price = price_recent < price_prior
    higher_stoch = stoch_recent > stoch_prior
    confirmed = lower_price and higher_stoch
    note = (
        f"price {price_prior:.2f}→{price_recent:.2f}, "
        f"stoch %K {stoch_prior:.1f}→{stoch_recent:.1f}"
    )
    if not lower_price:
        note += " — price didn't make a lower low"
    elif not higher_stoch:
        note += " — stoch didn't make a higher low"
    return DivergenceResult(
        confirmed=confirmed,
        price_pivot_recent=price_recent, price_pivot_prior=price_prior,
        stoch_pivot_recent=stoch_recent, stoch_pivot_prior=stoch_prior,
        note=note,
    )
