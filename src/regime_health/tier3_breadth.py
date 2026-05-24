"""Tier 3 — Breadth readings.

v1 ships two breadth proxies, both single-ticker-pair ratios so each adds
just two yfinance calls (vs. the deferred %SPX>200DMA which would need 500
concurrent calls and a maintained S&P 500 universe — punt to v2 once we're
on a paid Massive plan):

  - RSP/SPY 5d slope: equal-weight vs cap-weight S&P 500. Negative slope =
    classic late-cycle "5 stocks holding up the index" tell.
  - IWM/SPY 5d slope: small-cap vs large-cap. Negative slope = leadership
    narrowing into mega-caps; complementary angle to cap concentration.

Thresholds are backtest-calibrated — see thresholds.py and
scripts/backtest_rsp_spy_5d_slope.py.
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


def _read_ratio_5d_slope(
    *,
    numerator: str,
    denominator: str,
    indicator_id: str,
    label: str,
    threshold: NumericThreshold,
    threshold_note: str,
    load_fn: Callable[..., Any] | None = None,
) -> IndicatorReading:
    fn = load_fn or _default_load_fn()
    try:
        num = fn(numerator, period="1mo", interval="1d")
        den = fn(denominator, period="1mo", interval="1d")
    except Exception as exc:
        logger.exception(
            "yfinance load failed for %s or %s", numerator, denominator
        )
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=3,
            status="error", source="yfinance", error=str(exc),
            threshold_note=threshold_note,
        )

    if num is None or len(num) < 6 or den is None or len(den) < 6:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=3,
            status="unknown", source="yfinance",
            error=f"Need ≥6 bars of {numerator} + {denominator} for 5d slope",
            threshold_note=threshold_note,
        )

    num_col = "close" if "close" in num.columns else "Close"
    den_col = "close" if "close" in den.columns else "Close"

    num_now = float(num[num_col].iloc[-1])
    num_then = float(num[num_col].iloc[-6])
    den_now = float(den[den_col].iloc[-1])
    den_then = float(den[den_col].iloc[-6])

    if num_then == 0 or den_then == 0:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=3,
            status="unknown", source="yfinance",
            error="Zero close in 5d-prior bars",
            threshold_note=threshold_note,
        )

    ratio_now = num_now / den_now
    ratio_then = num_then / den_then
    slope_pct = (ratio_now / ratio_then - 1.0) * 100.0
    status = threshold.evaluate(slope_pct)

    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=3,
        status=status, value=slope_pct,
        formatted_value=f"{slope_pct:+.2f}%/5d",
        threshold_note=threshold_note, source="yfinance",
    )


def read_rsp_spy_5d_slope(
    *,
    threshold: NumericThreshold | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> IndicatorReading:
    """RSP/SPY ratio, 5-day percentage change of the ratio.

    Negative slope = equal-weight underperforming → cap-concentration breadth
    cracking. Backtest-calibrated thresholds: amber at -1.5%/5d (1.78x lift
    vs base, ~7/yr), red at -2.5%/5d (3.30x lift, ~1.5/yr) — see
    scripts/backtest_rsp_spy_5d_slope.py.
    """
    cfg = threshold or DEFAULT_THRESHOLDS.rsp_spy_5d_slope
    return _read_ratio_5d_slope(
        numerator="RSP",
        denominator="SPY",
        indicator_id="rsp_spy_5d_slope",
        label="RSP/SPY 5d slope",
        threshold=cfg,
        threshold_note="amber at -1.5%/5d; red at -2.5%/5d (cap-concentration)",
        load_fn=load_fn,
    )


def read_iwm_spy_5d_slope(
    *,
    threshold: NumericThreshold | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> IndicatorReading:
    """IWM/SPY ratio, 5-day percentage change of the ratio.

    Negative slope = small-caps lagging large-caps → size-factor breadth
    narrowing. Backtest-calibrated thresholds: amber at -2.0%/5d
    (2.41x lift @ 5d×5%, ~19/yr), red at -3.0%/5d (3.66x lift, ~6/yr) —
    see scripts/backtest_iwm_spy_5d_slope_output.json.
    """
    cfg = threshold or DEFAULT_THRESHOLDS.iwm_spy_5d_slope
    return _read_ratio_5d_slope(
        numerator="IWM",
        denominator="SPY",
        indicator_id="iwm_spy_5d_slope",
        label="IWM/SPY 5d slope",
        threshold=cfg,
        threshold_note="amber at -2.0%/5d; red at -3.0%/5d (size-factor)",
        load_fn=load_fn,
    )


def assemble_tier3(
    *,
    thresholds: ThresholdConfig | None = None,
    load_fn: Callable[..., Any] | None = None,
) -> TierBundle:
    """Tier 3 bundle. Two single-ratio breadth indicators."""
    cfg = thresholds or DEFAULT_THRESHOLDS
    readings = [
        read_rsp_spy_5d_slope(
            threshold=cfg.rsp_spy_5d_slope, load_fn=load_fn,
        ),
        read_iwm_spy_5d_slope(
            threshold=cfg.iwm_spy_5d_slope, load_fn=load_fn,
        ),
    ]
    bundle_error: str | None = None
    if all(r.status == "error" for r in readings):
        bundle_error = "Tier 3 breadth readers failed — yfinance unavailable"
    return TierBundle(
        tier=3, label="Breadth", readings=readings, error=bundle_error,
    )
