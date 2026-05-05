"""Tier 1 — Structural & Volatility readings.

Indicators:
  - SPY/QQQ SQN(100) regime (existing scan_ticker output)
  - SPY/QQQ Weekly MA stack state (scan_ticker on 1wk timeframe)
  - SQN(20) tactical-divergence flag (worse of SPY / QQQ diagnostics)
  - VIX last close (yfinance ^VIX)
  - VVIX last close (yfinance ^VVIX)

All public reader functions accept injectable `scan_fn` and `load_fn` so
tests can mock without touching live yfinance.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from regime_health.model import IndicatorReading, TierBundle
from regime_health.thresholds import (
    DEFAULT_THRESHOLDS,
    MA_STACK_TO_STATUS,
    SQN_REGIME_TO_STATUS,
    NumericThreshold,
    ThresholdConfig,
)


logger = logging.getLogger(__name__)


# ── Defaults wiring (lazy import to keep test mocking light) ─────────────────


def _default_scan_fn() -> Callable[..., dict[str, Any]]:
    from scan import scan_ticker
    return scan_ticker


def _default_load_fn() -> Callable[..., Any]:
    from data.yfinance_loader import load_bars
    return load_bars


# Diagnostic substrings that flip SQN(20)-vs-SQN(100) reading to amber.
# diagnose_sqn_pair() returns descriptive prose like "regime aligned",
# "divergence — early shift signal", "extreme — chase risk", etc.
# Keep this list narrow — false-amber here adds noise to the panel.
_SQN20_AMBER_TOKENS = (
    "diverg",       # diverging / divergence
    "extreme",      # extreme reading on SQN(20)
    "capitul",      # capitulation reset
    "chase",        # chasing premium warning
    "caution",
)


# ── Readers ──────────────────────────────────────────────────────────────────


def read_sqn_for_ticker(
    ticker: str,
    *,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
) -> IndicatorReading:
    """Read SQN(100) regime + value for a single ticker on daily TF."""
    fn = scan_fn or _default_scan_fn()
    label = f"{ticker} SQN(100)"
    indicator_id = f"{ticker.lower()}_sqn_100"
    try:
        row = fn(ticker, "1d")
    except Exception as exc:
        logger.exception("scan_ticker failed for %s 1d", ticker)
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=1,
            status="error", source="scan_ticker", error=str(exc),
        )

    sqn = row.get("sqn") or {}
    regime = sqn.get("regime")
    value = sqn.get("sqn_value")
    status = SQN_REGIME_TO_STATUS.get(regime or "", "unknown")
    formatted = f"{regime or '—'} ({value:.2f})" if isinstance(value, (int, float)) else str(regime or "—")

    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=1,
        status=status, value=value if isinstance(value, (int, float)) else regime,
        formatted_value=formatted,
        threshold_note="green=Bull/Strong Bull, amber=Neutral, red=Bear/Strong Bear",
        source="scan_ticker",
    )


def read_weekly_ma_for_ticker(
    ticker: str,
    *,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
) -> IndicatorReading:
    """Read Weekly MA stack state for a single ticker."""
    fn = scan_fn or _default_scan_fn()
    label = f"{ticker} Weekly MA"
    indicator_id = f"{ticker.lower()}_weekly_ma"
    try:
        row = fn(ticker, "1wk")
    except Exception as exc:
        logger.exception("scan_ticker failed for %s 1wk", ticker)
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=1,
            status="error", source="scan_ticker", error=str(exc),
        )

    ma = row.get("ma_ribbon") or {}
    stack_state = ma.get("stack_state")
    status = MA_STACK_TO_STATUS.get(stack_state or "", "unknown")
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=1,
        status=status, value=stack_state, formatted_value=str(stack_state or "—"),
        threshold_note="green=Bull stack, amber=Compression/Chop, red=Bear stack",
        source="scan_ticker",
    )


def read_sqn20_diagnostic(
    ticker: str,
    *,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
) -> IndicatorReading:
    """Read SQN(20) tactical-divergence diagnostic for a single ticker.

    Status:
      - green: SQN(20) aligned with SQN(100), no extreme reading
      - amber: divergence / extreme / capitulation reading per
        diagnose_sqn_pair() — tactical attention warranted but not a
        structural-regime change
      - unknown: warmup window or scan_ticker failure
    """
    fn = scan_fn or _default_scan_fn()
    label = f"{ticker} SQN(20) divergence"
    indicator_id = f"{ticker.lower()}_sqn20_diagnostic"
    try:
        row = fn(ticker, "1d")
    except Exception as exc:
        logger.exception("scan_ticker failed for %s 1d (sqn20)", ticker)
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=1,
            status="error", source="scan_ticker", error=str(exc),
        )

    sqn = row.get("sqn") or {}
    diagnostic = sqn.get("diagnostic") or ""
    if not diagnostic:
        status = "unknown"
        formatted = "—"
    else:
        diag_lower = diagnostic.lower()
        if any(tok in diag_lower for tok in _SQN20_AMBER_TOKENS):
            status = "amber"
        else:
            status = "green"
        formatted = diagnostic

    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=1,
        status=status, value=diagnostic or None, formatted_value=formatted,
        threshold_note="amber if SQN(20) diverges from SQN(100) or hits extreme",
        source="scan_ticker",
    )


def _read_yf_index(
    *,
    symbol: str,
    label: str,
    indicator_id: str,
    threshold: NumericThreshold,
    load_fn: Callable[..., Any] | None,
) -> IndicatorReading:
    fn = load_fn or _default_load_fn()
    try:
        bars = fn(symbol, period="1mo", interval="1d")
    except Exception as exc:
        logger.exception("yfinance load failed for %s", symbol)
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=1,
            status="error", source="yfinance", error=str(exc),
            threshold_note=threshold.describe(),
        )

    if bars is None or len(bars) == 0:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=1,
            status="unknown", source="yfinance",
            error=f"no bars returned for {symbol}",
            threshold_note=threshold.describe(),
        )

    # Dataframes from load_bars use lowercase column names.
    close_col = "close" if "close" in bars.columns else "Close"
    last_close = float(bars[close_col].iloc[-1])
    status = threshold.evaluate(last_close)
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=1,
        status=status, value=last_close,
        formatted_value=f"{last_close:.2f}",
        threshold_note=threshold.describe(),
        source="yfinance",
    )


def read_vix(
    *,
    threshold: NumericThreshold | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> IndicatorReading:
    """Read latest VIX close. Threshold default: amber>=18, red>=25."""
    return _read_yf_index(
        symbol="^VIX", label="VIX",
        indicator_id="vix",
        threshold=threshold or DEFAULT_THRESHOLDS.vix,
        load_fn=load_fn,
    )


def read_vvix(
    *,
    threshold: NumericThreshold | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> IndicatorReading:
    """Read latest VVIX close. Threshold default: amber>=100, red>=115."""
    return _read_yf_index(
        symbol="^VVIX", label="VVIX",
        indicator_id="vvix",
        threshold=threshold or DEFAULT_THRESHOLDS.vvix,
        load_fn=load_fn,
    )


# ── Tier assembly ────────────────────────────────────────────────────────────


def assemble_tier1(
    *,
    thresholds: ThresholdConfig | None = None,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> TierBundle:
    """Run every Tier 1 reader and return the bundle.

    Per-indicator failures degrade gracefully — tier-level error is set only
    when every reader raises. Otherwise readings carry their own status and
    error fields so the panel can show partial state.
    """
    cfg = thresholds or DEFAULT_THRESHOLDS
    readings: list[IndicatorReading] = [
        read_sqn_for_ticker("SPY", scan_fn=scan_fn),
        read_sqn_for_ticker("QQQ", scan_fn=scan_fn),
        read_weekly_ma_for_ticker("SPY", scan_fn=scan_fn),
        read_weekly_ma_for_ticker("QQQ", scan_fn=scan_fn),
        read_sqn20_diagnostic("SPY", scan_fn=scan_fn),
        read_sqn20_diagnostic("QQQ", scan_fn=scan_fn),
        read_vix(threshold=cfg.vix, load_fn=load_fn),
        read_vvix(threshold=cfg.vvix, load_fn=load_fn),
    ]

    tier_error: str | None = None
    if readings and all(r.status == "error" for r in readings):
        tier_error = "All Tier 1 readers failed — check yfinance + scan_ticker availability"

    return TierBundle(
        tier=1,
        label="Structural & Volatility",
        readings=readings,
        error=tier_error,
    )
