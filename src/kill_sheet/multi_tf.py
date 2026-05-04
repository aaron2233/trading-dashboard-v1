"""Helpers for deriving Weekly + 4H context from multi_tf scan results."""
from __future__ import annotations

from typing import Any


_BULL_STACKS = {"full_bull", "bull_developing"}
_BEAR_STACKS = {"full_bear", "bear_developing"}


def weekly_alignment(weekly_stack: str | None, direction: str) -> str:
    if not weekly_stack or weekly_stack in {"chop", "compression", "n/a"}:
        return "Neutral"
    if weekly_stack in _BULL_STACKS:
        return "With trade" if direction == "long" else "Counter-trend"
    if weekly_stack in _BEAR_STACKS:
        return "With trade" if direction == "short" else "Counter-trend"
    return "Neutral"


def pullback_status(close: float | None, ma_20: float | None,
                    near_threshold_pct: float = 0.005) -> str | None:
    if close is None or ma_20 is None or ma_20 == 0:
        return None
    rel = abs(close - ma_20) / ma_20
    if rel < near_threshold_pct:
        return "Price at 20 MA"
    return "Price above 20 MA" if close > ma_20 else "Price below 20 MA"


def extract_tf(multi_tf: dict[str, dict[str, Any]], tf_key: str) -> dict[str, Any] | None:
    """Return the scan row for a timeframe, or None if it errored / missing."""
    row = multi_tf.get(tf_key)
    if not row or "error" in row:
        return None
    return row
