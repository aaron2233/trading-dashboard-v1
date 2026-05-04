"""Price-structure analysis for the pyramid gate.

Identifies recent swing highs/lows, whether the most recent pullback held key
MAs, and whether higher-low / lower-high structure is intact. Used by gate and
tranche evaluators to answer:

- "Did the most recent pullback hold the 20MA?"  (long-pyramid gate condition 4)
- "Is a higher-low confirmed?"                   (long-pyramid gate condition 5)
- Mirror conditions for short pyramid.

These are simple heuristics over a short lookback window (default 30 bars). They
intentionally avoid fancy pivot-detection — the goal is "is the trend structure
healthy" not "exact pivot identification."
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


# Lookback window (in bars) for swing-high / swing-low detection.
DEFAULT_LOOKBACK = 30
# A pivot is "local" if it's the lowest/highest within ±N bars on each side.
PIVOT_BARS = 3


@dataclass
class StructureRead:
    recent_swing_high: float | None
    recent_swing_high_date: str | None
    recent_swing_low: float | None
    recent_swing_low_date: str | None
    prior_swing_high: float | None
    prior_swing_low: float | None

    pullback_held_20ma: bool        # most recent pullback's low close >= 20MA at that bar
    pullback_held_50ma: bool        # … >= 50MA at that bar
    rally_rejected_at_20ma: bool    # mirror for short — most recent rally's high close <= 20MA
    rally_rejected_at_50ma: bool

    higher_low_confirmed: bool      # last swing low > prior swing low
    lower_high_confirmed: bool      # last swing high < prior swing high


def _find_pivot_lows(closes: pd.Series, n: int = PIVOT_BARS) -> list[tuple[int, float]]:
    """Return list of (bar_index, close) for local pivot lows in the series.

    A pivot low is a bar whose close is <= every close within ±n bars. Returned
    in chronological order (oldest first).
    """
    if len(closes) < (2 * n + 1):
        return []
    pivots = []
    values = closes.values
    for i in range(n, len(closes) - n):
        window = values[i - n : i + n + 1]
        if values[i] == window.min() and (window == values[i]).sum() == 1:
            pivots.append((i, float(values[i])))
    return pivots


def _find_pivot_highs(closes: pd.Series, n: int = PIVOT_BARS) -> list[tuple[int, float]]:
    if len(closes) < (2 * n + 1):
        return []
    pivots = []
    values = closes.values
    for i in range(n, len(closes) - n):
        window = values[i - n : i + n + 1]
        if values[i] == window.max() and (window == values[i]).sum() == 1:
            pivots.append((i, float(values[i])))
    return pivots


def analyze_structure(
    bars: pd.DataFrame,
    ma_20: pd.Series,
    ma_50: pd.Series,
    lookback: int = DEFAULT_LOOKBACK,
) -> StructureRead:
    """Analyze recent price structure on a daily bars DataFrame.

    Args:
        bars: DataFrame indexed by date with at least a 'close' column.
        ma_20, ma_50: Series of MA values aligned to bars.index.
        lookback: how many recent bars to consider for swing detection.
    """
    if bars.empty or "close" not in bars.columns:
        return _empty_read()

    recent = bars.tail(lookback)
    if len(recent) < (2 * PIVOT_BARS + 1):
        return _empty_read()

    closes = recent["close"]
    pivot_lows = _find_pivot_lows(closes)
    pivot_highs = _find_pivot_highs(closes)

    # Swing high/low: take the two most recent pivots
    last_low_close = pivot_lows[-1][1] if pivot_lows else None
    last_low_idx = pivot_lows[-1][0] if pivot_lows else None
    prior_low = pivot_lows[-2][1] if len(pivot_lows) >= 2 else None

    last_high_close = pivot_highs[-1][1] if pivot_highs else None
    last_high_idx = pivot_highs[-1][0] if pivot_highs else None
    prior_high = pivot_highs[-2][1] if len(pivot_highs) >= 2 else None

    # Pullback hold: at the last pivot low, was close above 20MA / 50MA at that bar?
    pullback_20 = False
    pullback_50 = False
    rally_20 = False
    rally_50 = False

    if last_low_idx is not None:
        # Translate recent-window index back to global index
        global_low_date = recent.index[last_low_idx]
        ma20_at_low = ma_20.get(global_low_date)
        ma50_at_low = ma_50.get(global_low_date)
        if ma20_at_low is not None and not pd.isna(ma20_at_low):
            pullback_20 = last_low_close >= float(ma20_at_low)
        if ma50_at_low is not None and not pd.isna(ma50_at_low):
            pullback_50 = last_low_close >= float(ma50_at_low)

    if last_high_idx is not None:
        global_high_date = recent.index[last_high_idx]
        ma20_at_high = ma_20.get(global_high_date)
        ma50_at_high = ma_50.get(global_high_date)
        if ma20_at_high is not None and not pd.isna(ma20_at_high):
            rally_20 = last_high_close <= float(ma20_at_high)
        if ma50_at_high is not None and not pd.isna(ma50_at_high):
            rally_50 = last_high_close <= float(ma50_at_high)

    higher_low = (
        last_low_close is not None and prior_low is not None
        and last_low_close > prior_low
    )
    lower_high = (
        last_high_close is not None and prior_high is not None
        and last_high_close < prior_high
    )

    def _date_str(window_idx: int | None) -> str | None:
        if window_idx is None:
            return None
        return recent.index[window_idx].strftime("%Y-%m-%d")

    return StructureRead(
        recent_swing_high=last_high_close,
        recent_swing_high_date=_date_str(last_high_idx),
        recent_swing_low=last_low_close,
        recent_swing_low_date=_date_str(last_low_idx),
        prior_swing_high=prior_high,
        prior_swing_low=prior_low,
        pullback_held_20ma=pullback_20,
        pullback_held_50ma=pullback_50,
        rally_rejected_at_20ma=rally_20,
        rally_rejected_at_50ma=rally_50,
        higher_low_confirmed=higher_low,
        lower_high_confirmed=lower_high,
    )


def _empty_read() -> StructureRead:
    return StructureRead(
        recent_swing_high=None,
        recent_swing_high_date=None,
        recent_swing_low=None,
        recent_swing_low_date=None,
        prior_swing_high=None,
        prior_swing_low=None,
        pullback_held_20ma=False,
        pullback_held_50ma=False,
        rally_rejected_at_20ma=False,
        rally_rejected_at_50ma=False,
        higher_low_confirmed=False,
        lower_high_confirmed=False,
    )
