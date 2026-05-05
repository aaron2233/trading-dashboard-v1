"""Tier 3 — Breadth readings.

v1 ships only the RSP/SPY ratio 5d slope as a breadth proxy. The
constituent-list-based %SPX>200DMA indicator is deferred to v2
(would require maintaining an S&P 500 universe + N concurrent
yfinance calls, vs. one extra ticker for the ratio).

Reader uses yfinance load_bars for both RSP and SPY (already-tested
in scan_ticker pipeline). Computes 5-day slope of the ratio:
  slope_pct = (latest_ratio / 5_days_ago_ratio - 1) * 100
Negative slope = equal-weight RSP underperforming cap-weight SPY,
the classic late-cycle "5 stocks holding up the index" tell.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from regime_health.model import IndicatorReading, TierBundle
from regime_health.thresholds import (
    DEFAULT_THRESHOLDS,
    NumericThreshold,
    ThresholdConfig,
)


logger = logging.getLogger(__name__)


def _default_load_fn() -> Callable[..., Any]:
    from data.yfinance_loader import load_bars
    return load_bars


def read_rsp_spy_5d_slope(
    *,
    threshold: NumericThreshold | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> IndicatorReading:
    """RSP/SPY ratio, 5-day percentage change of the ratio.

    Negative slope = equal-weight underperforming → breadth cracking.
    Threshold defaults from spec (⚠️ UNVERIFIED): amber at -0.5%/5d,
    red at -1.5%/5d.
    """
    fn = load_fn or _default_load_fn()
    cfg = threshold or DEFAULT_THRESHOLDS.rsp_spy_5d_slope
    label, indicator_id = "RSP/SPY 5d slope", "rsp_spy_5d_slope"
    note = "amber if -0.5%/5d; red if -1.5%/5d (equal-weight failure)"

    try:
        rsp = fn("RSP", period="1mo", interval="1d")
        spy = fn("SPY", period="1mo", interval="1d")
    except Exception as exc:
        logger.exception("yfinance load failed for RSP or SPY")
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=3,
            status="error", source="yfinance", error=str(exc),
            threshold_note=note,
        )

    if rsp is None or len(rsp) < 6 or spy is None or len(spy) < 6:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=3,
            status="unknown", source="yfinance",
            error="Need ≥6 bars of RSP + SPY for 5d slope",
            threshold_note=note,
        )

    close_col_rsp = "close" if "close" in rsp.columns else "Close"
    close_col_spy = "close" if "close" in spy.columns else "Close"

    rsp_now = float(rsp[close_col_rsp].iloc[-1])
    rsp_then = float(rsp[close_col_rsp].iloc[-6])
    spy_now = float(spy[close_col_spy].iloc[-1])
    spy_then = float(spy[close_col_spy].iloc[-6])

    if rsp_then == 0 or spy_then == 0:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=3,
            status="unknown", source="yfinance",
            error="Zero close in 5d-prior bars",
            threshold_note=note,
        )

    ratio_now = rsp_now / spy_now
    ratio_then = rsp_then / spy_then
    slope_pct = (ratio_now / ratio_then - 1.0) * 100.0
    status = cfg.evaluate(slope_pct)

    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=3,
        status=status, value=slope_pct,
        formatted_value=f"{slope_pct:+.2f}%/5d",
        threshold_note=note, source="yfinance",
    )


def assemble_tier3(
    *,
    thresholds: ThresholdConfig | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> TierBundle:
    """Tier 3 bundle. Sprint 3 ships one reader; v2 adds %SPX>200DMA."""
    cfg = thresholds or DEFAULT_THRESHOLDS
    readings = [
        read_rsp_spy_5d_slope(
            threshold=cfg.rsp_spy_5d_slope, load_fn=load_fn,
        ),
    ]
    bundle_error: str | None = None
    if all(r.status == "error" for r in readings):
        bundle_error = "Tier 3 breadth reader failed — yfinance unavailable"
    return TierBundle(
        tier=3, label="Breadth", readings=readings, error=bundle_error,
    )
