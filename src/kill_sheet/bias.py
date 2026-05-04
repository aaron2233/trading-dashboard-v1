"""Bias and confidence derivation from a scan_ticker result.

A scan row from src/scan.py has the shape:
    {
      "ma_ribbon": {"stack_state": "full_bull" | ...},
      "stochastic": {"signal": ...,"zone": ...},
      "sqn": {"regime": "bull" | ...},
    }

Bias rules:
  BULLISH  - bullish stack (full_bull / bull_developing) and not contradicted
  BEARISH  - bearish stack (full_bear / bear_developing) and not contradicted
  NEUTRAL  - chop / compression / contradicted

Confidence rules (3-axis alignment: stack, stoch_signal direction, regime):
  HIGH    - all 3 aligned with bias
  MEDIUM  - 2 of 3 aligned
  LOW     - bias only, or contradicted on 2+ axes
"""
from __future__ import annotations

from typing import Any


_BULL_STACKS = {"full_bull", "bull_developing"}
_BEAR_STACKS = {"full_bear", "bear_developing"}
_BULL_SIGNALS = {"bull_cross_oversold", "bull_continuation", "bullish_divergence"}
_BEAR_SIGNALS = {"bear_cross_overbought", "bear_continuation", "bearish_divergence"}
_BULL_REGIMES = {"strong_bull", "bull"}
_BEAR_REGIMES = {"strong_bear", "bear"}


def _stack(scan_row: dict[str, Any]) -> str | None:
    return (scan_row.get("ma_ribbon") or {}).get("stack_state")


def _signal(scan_row: dict[str, Any]) -> str | None:
    return (scan_row.get("stochastic") or {}).get("signal")


def _regime(scan_row: dict[str, Any]) -> str | None:
    return (scan_row.get("sqn") or {}).get("regime")


def derive_bias(scan_row: dict[str, Any]) -> str:
    stack = _stack(scan_row)
    if stack in _BULL_STACKS:
        return "BULLISH"
    if stack in _BEAR_STACKS:
        return "BEARISH"
    return "NEUTRAL"


def derive_confidence(scan_row: dict[str, Any]) -> tuple[str, str]:
    """Return (HIGH/MEDIUM/LOW, one-sentence reason)."""
    bias = derive_bias(scan_row)
    stack = _stack(scan_row)
    signal = _signal(scan_row)
    regime = _regime(scan_row)

    if bias == "NEUTRAL":
        return ("LOW", f"Daily stack is {stack or 'unknown'} — no directional setup")

    aligned_signals = _BULL_SIGNALS if bias == "BULLISH" else _BEAR_SIGNALS
    aligned_regimes = _BULL_REGIMES if bias == "BULLISH" else _BEAR_REGIMES

    stoch_aligned = signal in aligned_signals
    regime_aligned = regime in aligned_regimes

    aligned_count = 1 + int(stoch_aligned) + int(regime_aligned)

    bias_word = "bullish" if bias == "BULLISH" else "bearish"
    if aligned_count == 3:
        return (
            "HIGH",
            f"Daily {stack} + {bias_word} regime ({regime}) + stoch {signal}",
        )
    if aligned_count == 2:
        flagged = []
        if not stoch_aligned:
            flagged.append(f"stoch {signal}")
        if not regime_aligned:
            flagged.append(f"regime {regime}")
        return (
            "MEDIUM",
            f"Daily {stack} aligned but {', '.join(flagged)} not confirming",
        )
    return (
        "LOW",
        f"Daily {stack} but neither stoch ({signal}) nor regime ({regime}) confirm",
    )
