"""Tier 4 — AI Capex Calendar.

Manual-config tier (per OQ#4 default). User maintains a YAML block
under regime_health.capex with the 5 hyperscaler tickers' next print
dates and most-recent capex-guide direction (raised/held/cut).

Aggregate read: count cuts vs raises across the 5 names.
  - 0 cuts / 5 names = green ("AI capex tailwind intact")
  - 1-2 cuts        = amber ("partial moderation — watch")
  - 3+ cuts         = red   ("the air pocket is here")

When the config is missing or no direction has been logged yet, the
indicator returns unknown — the panel surfaces the upcoming print
dates as a "watch this date" reminder either way.
"""
from __future__ import annotations

import logging
from datetime import date as _date
from pathlib import Path
from typing import Any

from regime_health.model import IndicatorReading, TierBundle


logger = logging.getLogger(__name__)


# Default tickers per the spec — Aaron can override via config.
DEFAULT_CAPEX_TICKERS: tuple[str, ...] = ("MSFT", "GOOGL", "META", "AMZN", "NVDA")

# Direction values — anything else is treated as "unknown" (no signal).
VALID_DIRECTIONS = {"raised", "held", "cut", "unknown"}


def _config_path() -> Path:
    """Resolve config path lazily so test patches to Path.home() apply."""
    return Path.home() / ".trading-dashboard" / "config.yaml"


def _load_capex_config() -> dict[str, Any] | None:
    """Read regime_health.capex section from the user config file.

    Returns None when:
      - config.yaml doesn't exist
      - PyYAML isn't installed (already a project dep but be defensive)
      - regime_health.capex section is missing
    """
    path = _config_path()
    if not path.exists():
        return None
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        text = path.read_text()
        data = yaml.safe_load(text) or {}
    except Exception:
        logger.exception("failed to parse %s", path)
        return None
    rh = data.get("regime_health") if isinstance(data, dict) else None
    if not isinstance(rh, dict):
        return None
    capex = rh.get("capex")
    if not isinstance(capex, dict):
        return None
    return capex


def read_capex_aggregate(
    *,
    config: dict[str, Any] | None = None,
) -> IndicatorReading:
    """Compute the aggregate capex direction across the 5 hyperscalers.

    `config` defaults to the loaded user config; pass an explicit dict
    in tests to avoid touching the filesystem.
    """
    cfg = config if config is not None else _load_capex_config()

    label = "AI Capex Aggregate"
    indicator_id = "ai_capex_aggregate"
    note = "0 cuts = green; 1-2 = amber; 3+ = red (across MSFT/GOOGL/META/AMZN/NVDA)"

    if cfg is None:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=4,
            status="unknown",
            formatted_value="—",
            threshold_note=note,
            source="manual",
            error=(
                "No regime_health.capex section in ~/.trading-dashboard/"
                "config.yaml. See spec for format."
            ),
        )

    tickers = cfg.get("tickers") or list(DEFAULT_CAPEX_TICKERS)
    directions = cfg.get("directions") or {}

    if not isinstance(directions, dict):
        directions = {}

    cuts = 0
    raises = 0
    holds = 0
    unknown_count = 0
    for t in tickers:
        d = str(directions.get(t, "unknown")).lower()
        if d not in VALID_DIRECTIONS:
            d = "unknown"
        if d == "cut":
            cuts += 1
        elif d == "raised":
            raises += 1
        elif d == "held":
            holds += 1
        else:
            unknown_count += 1

    if cuts >= 3:
        status = "red"
    elif cuts >= 1:
        status = "amber"
    else:
        status = "green"

    summary = f"{cuts} cut · {holds} held · {raises} raised"
    if unknown_count > 0:
        summary += f" · {unknown_count} pending"

    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=4,
        status=status,
        value=cuts,
        formatted_value=summary,
        threshold_note=note,
        source="manual",
    )


def read_capex_calendar(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> IndicatorReading:
    """List upcoming earnings dates (next 90 days) from the config.

    Status is informational — always 'green' when at least one upcoming
    date is configured, 'unknown' otherwise. The point of this reading
    is to surface the dates inline, not gate trading.
    """
    cfg = config if config is not None else _load_capex_config()
    label = "Upcoming capex prints"
    indicator_id = "ai_capex_calendar"
    note = "Hyperscaler earnings dates — manual entry per quarter"

    if cfg is None:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=4,
            status="unknown", formatted_value="—",
            threshold_note=note, source="manual",
            error="No regime_health.capex section in config.yaml",
        )

    next_prints = cfg.get("next_prints") or {}
    if not isinstance(next_prints, dict):
        next_prints = {}

    today = today or _date.today()
    upcoming: list[tuple[str, _date]] = []
    for ticker, date_str in next_prints.items():
        try:
            d = _date.fromisoformat(str(date_str))
        except (TypeError, ValueError):
            continue
        if (d - today).days >= 0 and (d - today).days <= 90:
            upcoming.append((str(ticker), d))

    upcoming.sort(key=lambda x: x[1])

    if not upcoming:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=4,
            status="unknown", formatted_value="No upcoming dates configured",
            threshold_note=note, source="manual",
        )

    formatted = " · ".join(f"{t} {d.isoformat()}" for t, d in upcoming[:5])
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=4,
        status="green",  # informational — green when calendar is populated
        value=len(upcoming),
        formatted_value=formatted,
        threshold_note=note, source="manual",
    )


def find_pending_capex_updates(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> list[dict[str, str]]:
    """Tickers whose next_prints date has passed but directions[ticker]
    is still "unknown" — the user hasn't logged what the company guided.

    Used to drive the "⚠ N capex prints pending direction update"
    HomeView CTA. Informational only — does NOT contribute to
    overall_status (per spec, paperwork-pending should not pollute the
    regime read).

    Returns a list of {"ticker": "...", "print_date": "YYYY-MM-DD"}
    sorted by print_date ascending (oldest = most overdue first).
    """
    cfg = config if config is not None else _load_capex_config()
    if cfg is None:
        return []

    tickers = cfg.get("tickers") or list(DEFAULT_CAPEX_TICKERS)
    directions = cfg.get("directions") or {}
    next_prints = cfg.get("next_prints") or {}
    if not isinstance(directions, dict):
        directions = {}
    if not isinstance(next_prints, dict):
        next_prints = {}

    today_d = today or _date.today()
    pending: list[tuple[str, _date]] = []
    for ticker in tickers:
        date_str = next_prints.get(ticker)
        if not date_str:
            continue
        try:
            print_date = _date.fromisoformat(str(date_str))
        except (TypeError, ValueError):
            continue
        if print_date > today_d:
            continue  # not yet — covered by the calendar reading
        d_value = str(directions.get(ticker, "unknown")).lower()
        if d_value == "unknown":
            pending.append((ticker, print_date))

    pending.sort(key=lambda x: x[1])
    return [
        {"ticker": t, "print_date": d.isoformat()}
        for t, d in pending
    ]


def assemble_tier4(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> TierBundle:
    """Tier 4 bundle. Two readings: capex direction aggregate +
    upcoming earnings calendar."""
    readings = [
        read_capex_aggregate(config=config),
        read_capex_calendar(config=config, today=today),
    ]
    bundle_error: str | None = None
    if all(r.status == "unknown" and (r.error or "").startswith("No regime_health.capex") for r in readings):
        bundle_error = (
            "Tier 4 capex calendar not configured — add a "
            "regime_health.capex block to ~/.trading-dashboard/config.yaml. "
            "See spec for format."
        )
    return TierBundle(
        tier=4, label="AI Capex Calendar", readings=readings, error=bundle_error,
    )
