"""Tier 2 — FRED macro readings.

Indicators (5):
  - HY OAS (BAMLH0A0HYM2) — credit cycle leading indicator
  - 2s10s curve (T10Y2Y) — recession trigger via dis-inversion
  - 3m10s curve (T10Y3M) — Fed-preferred curve, corroborates 2s10s
  - 5Y breakeven (T5YIE) — inflation reflation removes Fed put
  - Broad dollar (DTWEXBGS) — surge = global liquidity squeeze

Series IDs verified against FRED metadata (2026-05-05):
  - BAMLH0A0HYM2 → "ICE BofA US High Yield Index Option-Adjusted Spread"
    (units: %)
  - T5YIE        → "5-Year Breakeven Inflation Rate" (units: %)
  - DTWEXBGS     → "Nominal Broad U.S. Dollar Index" (Index Jan 2006=100)
  - T10Y2Y, T10Y3M → canonical FRED IDs for the 10y-2y / 10y-3m spreads.
    Runtime confirms via the API on first authenticated call; bad IDs
    surface as status="error", never silently wrong data.

For curves and dollar, "dis-inversion" / "3-mo % surge" detection requires
multiple observations — implemented where cheap, deferred to v2 where it'd
balloon scope (e.g., DTWEXBGS dis-inversion of dollar trend).
"""
from __future__ import annotations

import logging
from typing import Callable

from regime_health.fred_client import (
    FetchFn,
    FredFetchError,
    fetch_latest,
    fetch_observations,
)
from regime_health.model import IndicatorReading, TierBundle
from regime_health.thresholds import (
    BreakevenThreshold,
    DEFAULT_THRESHOLDS,
    NumericThreshold,
    ThresholdConfig,
)


logger = logging.getLogger(__name__)


# Series IDs — verified against FRED 2026-05-05 (see module docstring).
SERIES_HY_OAS = "BAMLH0A0HYM2"          # ICE BofA US HY Index OAS (pct)
SERIES_T10Y2Y = "T10Y2Y"                # 10Y minus 2Y Treasury (pp)
SERIES_T10Y3M = "T10Y3M"                # 10Y minus 3M Treasury (pp)
SERIES_T5YIE = "T5YIE"                  # 5Y breakeven inflation (pct)
SERIES_DTWEXBGS = "DTWEXBGS"            # Nominal Broad USD Index (Jan2006=100)


def _key_not_configured(indicator_id: str, label: str, threshold_note: str) -> IndicatorReading:
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=2,
        status="unknown",
        formatted_value="—",
        threshold_note=threshold_note,
        source="fred",
        error="FRED API key not configured (set FRED_API_KEY env var)",
    )


def _fred_error(
    indicator_id: str, label: str, threshold_note: str, exc: Exception,
) -> IndicatorReading:
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=2,
        status="error",
        formatted_value="—",
        threshold_note=threshold_note,
        source="fred",
        error=str(exc),
    )


# ── Individual readers ──────────────────────────────────────────────────────


def read_hy_oas(
    *,
    threshold: NumericThreshold | None = None,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> IndicatorReading:
    """HY OAS in basis points. FRED stores as decimal pct (e.g. 4.50 → 450 bps)."""
    cfg = threshold or DEFAULT_THRESHOLDS.hy_oas_bps
    label, indicator_id = "HY OAS", "hy_oas"
    note = cfg.describe()
    try:
        obs = fetch_latest(SERIES_HY_OAS, fetch=fetch, api_key=api_key)
    except FredFetchError as exc:
        if "not configured" in str(exc):
            return _key_not_configured(indicator_id, label, note)
        return _fred_error(indicator_id, label, note, exc)

    bps = obs.value * 100.0  # pct → bps
    status = cfg.evaluate(bps)
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=2,
        status=status, value=bps,
        formatted_value=f"{bps:.0f} bps",
        threshold_note=note, source="fred",
    )


def _read_curve(
    *,
    series_id: str,
    label: str,
    indicator_id: str,
    fetch: FetchFn | None,
    api_key: str | None,
) -> IndicatorReading:
    """Yield-curve reader.

    v1 status logic (kept simple — full dis-inversion detection deferred):
      - value > 0  → green (curve right-side-up)
      - value <= 0 → amber (inverted — recession watch but not yet trigger)

    The dis-inversion *trigger* (curve crossing back from inverted to
    positive) is a v2 enhancement. Detecting it cleanly requires ≥30 obs
    and a state-change rule that doesn't false-positive on noise.
    """
    note = "green if positive (right-side-up); amber if inverted"
    try:
        obs = fetch_latest(series_id, fetch=fetch, api_key=api_key)
    except FredFetchError as exc:
        if "not configured" in str(exc):
            return _key_not_configured(indicator_id, label, note)
        return _fred_error(indicator_id, label, note, exc)

    pp = obs.value
    status = "green" if pp > 0 else "amber"
    formatted = f"{pp:+.2f} pp"
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=2,
        status=status, value=pp,
        formatted_value=formatted,
        threshold_note=note, source="fred",
    )


def read_2s10s(
    *,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> IndicatorReading:
    """10Y minus 2Y Treasury constant maturity, in percentage points."""
    return _read_curve(
        series_id=SERIES_T10Y2Y, label="2s10s curve",
        indicator_id="t10y2y_curve", fetch=fetch, api_key=api_key,
    )


def read_3m10s(
    *,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> IndicatorReading:
    """10Y minus 3M Treasury constant maturity, in percentage points."""
    return _read_curve(
        series_id=SERIES_T10Y3M, label="3m10s curve",
        indicator_id="t10y3m_curve", fetch=fetch, api_key=api_key,
    )


def read_5y_breakeven(
    *,
    threshold: BreakevenThreshold | None = None,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> IndicatorReading:
    """5-Year Breakeven Inflation Rate (T5YIE). Two-sided threshold —
    too low (deflation risk) and too high (reflation) both flag."""
    cfg = threshold or DEFAULT_THRESHOLDS.five_year_breakeven
    label, indicator_id = "5Y breakeven", "t5yie_breakeven"
    note = cfg.describe()
    try:
        obs = fetch_latest(SERIES_T5YIE, fetch=fetch, api_key=api_key)
    except FredFetchError as exc:
        if "not configured" in str(exc):
            return _key_not_configured(indicator_id, label, note)
        return _fred_error(indicator_id, label, note, exc)

    pct = obs.value
    status = cfg.evaluate(pct)
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=2,
        status=status, value=pct,
        formatted_value=f"{pct:.2f}%",
        threshold_note=note, source="fred",
    )


def read_broad_dollar(
    *,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> IndicatorReading:
    """Broad dollar index (DTWEXBGS). Status by 3-month % change.

    Threshold (from spec, ⚠️ UNVERIFIED): green stable, amber +5%/3mo,
    red +10%/3mo. Dollar surges signal global liquidity squeeze.
    """
    label, indicator_id = "Broad dollar", "broad_dollar"
    note = "green stable; amber if +5%/3mo; red if +10%/3mo"
    try:
        # Need ~90 calendar days (≈63 trading days) of daily values to
        # compute 3-month % change. Pull 90 to be safe (accounts for
        # weekends + occasional missing values).
        obs_list = fetch_observations(
            SERIES_DTWEXBGS, limit=90, sort_order="desc",
            fetch=fetch, api_key=api_key,
        )
    except FredFetchError as exc:
        if "not configured" in str(exc):
            return _key_not_configured(indicator_id, label, note)
        return _fred_error(indicator_id, label, note, exc)

    if not obs_list:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=2,
            status="unknown", formatted_value="—",
            threshold_note=note, source="fred",
            error="No DTWEXBGS observations",
        )

    # obs_list is desc — newest first. Last observation = newest.
    latest = obs_list[0].value
    # 3mo prior is at the back (or as close as we have).
    prior = obs_list[-1].value
    if prior == 0:
        # Defensive — shouldn't happen for an FX index.
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=2,
            status="unknown", value=latest,
            formatted_value=f"{latest:.2f}",
            threshold_note=note, source="fred",
            error="3-month prior value is zero",
        )

    pct_change = (latest - prior) / prior * 100.0
    if pct_change >= 10.0:
        status = "red"
    elif pct_change >= 5.0:
        status = "amber"
    else:
        status = "green"

    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=2,
        status=status, value=latest,
        formatted_value=f"{latest:.2f} ({pct_change:+.1f}%/3mo)",
        threshold_note=note, source="fred",
    )


# ── Tier assembly ────────────────────────────────────────────────────────────


def assemble_tier2(
    *,
    thresholds: ThresholdConfig | None = None,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> TierBundle:
    """Run every Tier 2 reader. Per-indicator failures degrade gracefully.

    When the FRED key isn't configured, every reader emits a "key not
    configured" unknown reading and bundle.error mirrors that — the panel
    can render a single tier-level message rather than five copies of it.
    """
    cfg = thresholds or DEFAULT_THRESHOLDS
    readings: list[IndicatorReading] = [
        read_hy_oas(threshold=cfg.hy_oas_bps, fetch=fetch, api_key=api_key),
        read_2s10s(fetch=fetch, api_key=api_key),
        read_3m10s(fetch=fetch, api_key=api_key),
        read_5y_breakeven(
            threshold=cfg.five_year_breakeven, fetch=fetch, api_key=api_key,
        ),
        read_broad_dollar(fetch=fetch, api_key=api_key),
    ]

    bundle_error: str | None = None
    if all(
        (r.error or "").startswith("FRED API key not configured")
        for r in readings
    ):
        bundle_error = (
            "FRED API key not configured — set FRED_API_KEY in env to "
            "activate Tier 2 macro indicators. Free key at "
            "fred.stlouisfed.org/docs/api/api_key.html"
        )
    elif readings and all(r.status == "error" for r in readings):
        bundle_error = "All Tier 2 FRED readers failed — check FRED API availability"

    return TierBundle(
        tier=2, label="Macro (FRED)", readings=readings, error=bundle_error,
    )
