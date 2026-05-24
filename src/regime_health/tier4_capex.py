"""Tier 4 — AI Capex Calendar.

Two-cohort manual-config tier (as of 2026-05-17). User maintains:

  regime_health.capex.buyers      — hyperscalers committing capex
                                    (default: MSFT/GOOGL/META/AMZN/ORCL)
  regime_health.capex.suppliers   — chain capacity selling into them
                                    (default: NVDA/AVGO/TSM/ASML/MU)
  regime_health.capex.private_flows
                                  — sporadic raises to OpenAI/Anthropic/xAI;
                                    surfaced as trailing-N-day dollar total,
                                    NOT folded into the cohort aggregates.

Each cohort has its own next_prints + directions + 0/1/2+ traffic-light
aggregate. The split exists because buyers report their OWN capex while
suppliers report customer-demand commentary — averaging them into one
signal would smooth chain disconnects (4 buyers raise + 3 suppliers cut)
into a misleading amber.

Aggregate logic (per cohort):
  - 0 cuts = green  ("tailwind intact")
  - 1-2 cuts = amber  ("partial moderation — watch")
  - 3+ cuts = red    ("the air pocket is here")

When the config is missing, the indicator returns unknown — the panel
surfaces the upcoming print dates as a "watch this date" reminder either
way.
"""
from __future__ import annotations

import logging
from datetime import date as _date, timedelta
from pathlib import Path
from typing import Any

from regime_health.model import IndicatorReading, TierBundle


logger = logging.getLogger(__name__)


DEFAULT_BUYERS: tuple[str, ...] = ("MSFT", "GOOGL", "META", "AMZN", "ORCL")
DEFAULT_SUPPLIERS: tuple[str, ...] = ("NVDA", "AVGO", "TSM", "ASML", "MU")
DEFAULT_PRIVATE_FLOW_LOOKBACK_DAYS = 90

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


def _aggregate_cohort(
    cohort_cfg: dict[str, Any] | None,
    *,
    defaults: tuple[str, ...],
    label: str,
    indicator_id: str,
    note: str,
) -> IndicatorReading:
    """Shared aggregate logic for buyers and suppliers cohorts."""
    if not isinstance(cohort_cfg, dict):
        cohort_cfg = {}

    tickers = cohort_cfg.get("tickers") or list(defaults)
    directions = cohort_cfg.get("directions") or {}
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


def _missing_capex_reading(indicator_id: str, label: str, note: str) -> IndicatorReading:
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


def read_buyer_aggregate(
    *,
    config: dict[str, Any] | None = None,
) -> IndicatorReading:
    """Aggregate capex direction across the hyperscaler BUYER cohort."""
    cfg = config if config is not None else _load_capex_config()
    label = "AI Capex Buyer Aggregate"
    indicator_id = "ai_capex_buyer_aggregate"
    note = "0 cuts = green; 1-2 = amber; 3+ = red (hyperscaler capex commitments)"

    if cfg is None:
        return _missing_capex_reading(indicator_id, label, note)

    return _aggregate_cohort(
        cfg.get("buyers"), defaults=DEFAULT_BUYERS,
        label=label, indicator_id=indicator_id, note=note,
    )


def read_supplier_aggregate(
    *,
    config: dict[str, Any] | None = None,
) -> IndicatorReading:
    """Aggregate capex direction across the SUPPLIER cohort (chain capacity).

    Note: supplier "direction" reads the company's commentary on customer
    capex demand + their own order book, not the supplier's own spend.
    """
    cfg = config if config is not None else _load_capex_config()
    label = "AI Capex Supplier Aggregate"
    indicator_id = "ai_capex_supplier_aggregate"
    note = "0 cuts = green; 1-2 = amber; 3+ = red (supplier-side demand read)"

    if cfg is None:
        return _missing_capex_reading(indicator_id, label, note)

    return _aggregate_cohort(
        cfg.get("suppliers"), defaults=DEFAULT_SUPPLIERS,
        label=label, indicator_id=indicator_id, note=note,
    )


def _collect_next_prints(cfg: dict[str, Any]) -> dict[str, str]:
    """Merge next_prints across buyers + suppliers into a single ticker→date map."""
    out: dict[str, str] = {}
    for cohort in ("buyers", "suppliers"):
        cc = cfg.get(cohort) or {}
        prints = cc.get("next_prints") or {}
        if isinstance(prints, dict):
            for k, v in prints.items():
                out[str(k)] = str(v)
    return out


def read_capex_calendar(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> IndicatorReading:
    """List upcoming earnings dates (next 90 days) across both cohorts.

    Status is informational — always 'green' when at least one upcoming
    date is configured, 'unknown' otherwise. The point is to surface the
    dates inline, not to gate trading.
    """
    cfg = config if config is not None else _load_capex_config()
    label = "Upcoming capex prints"
    indicator_id = "ai_capex_calendar"
    note = "Hyperscaler + supplier earnings dates — manual entry per quarter"

    if cfg is None:
        return _missing_capex_reading(indicator_id, label, note)

    next_prints = _collect_next_prints(cfg)

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
        status="green",
        value=len(upcoming),
        formatted_value=formatted,
        threshold_note=note, source="manual",
    )


def read_private_flows(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> IndicatorReading:
    """Trailing-N-day total of private AI capex commitments.

    Entries are sporadic raises to OpenAI/Anthropic/xAI/etc. Each entry:
        {lab: str, amount_usd: int, date: "YYYY-MM-DD", note: str}

    Status: green when at least one entry lands in the trailing window;
    unknown otherwise. The trailing total is informational — NOT folded
    into the buyer/supplier aggregates, by design (sporadic cadence
    would mix old + new commitments and break the cohort signal logic).
    """
    cfg = config if config is not None else _load_capex_config()
    label = "Private AI capex flows"
    indicator_id = "ai_capex_private_flows"
    note = "Trailing-N-day total of OpenAI/Anthropic/xAI/etc. raises (informational)"

    if cfg is None:
        return _missing_capex_reading(indicator_id, label, note)

    pf = cfg.get("private_flows") or {}
    if not isinstance(pf, dict):
        pf = {}

    try:
        lookback_days = int(pf.get("lookback_days") or DEFAULT_PRIVATE_FLOW_LOOKBACK_DAYS)
    except (TypeError, ValueError):
        lookback_days = DEFAULT_PRIVATE_FLOW_LOOKBACK_DAYS
    if lookback_days < 1:
        lookback_days = DEFAULT_PRIVATE_FLOW_LOOKBACK_DAYS

    entries = pf.get("entries") or []
    if not isinstance(entries, list):
        entries = []

    today_d = today or _date.today()
    cutoff = today_d - timedelta(days=lookback_days)

    total_usd = 0
    in_window_count = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        try:
            d = _date.fromisoformat(str(e.get("date")))
            amt = float(e.get("amount_usd") or 0)
        except (TypeError, ValueError):
            continue
        if cutoff <= d <= today_d:
            in_window_count += 1
            total_usd += amt

    note_full = f"Trailing {lookback_days}d total of private-lab raises (informational)"

    if not entries:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=4,
            status="unknown", formatted_value="No private flow entries logged",
            threshold_note=note_full, source="manual",
        )
    if in_window_count == 0:
        return IndicatorReading(
            indicator_id=indicator_id, label=label, tier=4,
            status="unknown",
            formatted_value=f"No entries in trailing {lookback_days}d",
            threshold_note=note_full, source="manual",
        )

    formatted = (
        f"${total_usd / 1e9:.1f}B in trailing {lookback_days}d "
        f"({in_window_count} {'entry' if in_window_count == 1 else 'entries'})"
    )
    return IndicatorReading(
        indicator_id=indicator_id, label=label, tier=4,
        status="green",
        value=total_usd,
        formatted_value=formatted,
        threshold_note=note_full, source="manual",
    )


def find_pending_capex_updates(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> list[dict[str, str]]:
    """Tickers whose next_prints date has passed but directions is still
    'unknown' — the user hasn't logged what the company guided.

    Scans BOTH cohorts (buyers + suppliers). Each result carries a
    `cohort` key so the UI can group / color them.

    Returns a list of {"ticker": "...", "print_date": "YYYY-MM-DD", "cohort": "buyers"|"suppliers"}
    sorted by print_date ascending (oldest = most overdue first).
    """
    cfg = config if config is not None else _load_capex_config()
    if cfg is None:
        return []

    today_d = today or _date.today()
    pending: list[tuple[str, _date, str]] = []

    for cohort_name, defaults in (
        ("buyers", DEFAULT_BUYERS),
        ("suppliers", DEFAULT_SUPPLIERS),
    ):
        cc = cfg.get(cohort_name) or {}
        if not isinstance(cc, dict):
            continue
        tickers = cc.get("tickers") or list(defaults)
        directions = cc.get("directions") or {}
        next_prints = cc.get("next_prints") or {}
        if not isinstance(directions, dict):
            directions = {}
        if not isinstance(next_prints, dict):
            next_prints = {}

        for ticker in tickers:
            date_str = next_prints.get(ticker)
            if not date_str:
                continue
            try:
                print_date = _date.fromisoformat(str(date_str))
            except (TypeError, ValueError):
                continue
            if print_date > today_d:
                continue
            d_value = str(directions.get(ticker, "unknown")).lower()
            if d_value == "unknown":
                pending.append((ticker, print_date, cohort_name))

    pending.sort(key=lambda x: x[1])
    return [
        {"ticker": t, "print_date": d.isoformat(), "cohort": c}
        for t, d, c in pending
    ]


def assemble_tier4(
    *,
    config: dict[str, Any] | None = None,
    today: _date | None = None,
) -> TierBundle:
    """Tier 4 bundle.

    Four readings: buyer aggregate + supplier aggregate + upcoming
    calendar + private-flows trailing total.
    """
    readings = [
        read_buyer_aggregate(config=config),
        read_supplier_aggregate(config=config),
        read_capex_calendar(config=config, today=today),
        read_private_flows(config=config, today=today),
    ]
    bundle_error: str | None = None
    if all(
        r.status == "unknown" and (r.error or "").startswith("No regime_health.capex")
        for r in readings
    ):
        bundle_error = (
            "Tier 4 capex calendar not configured — add a "
            "regime_health.capex block to ~/.trading-dashboard/config.yaml. "
            "See spec for format."
        )
    return TierBundle(
        tier=4, label="AI Capex Calendar", readings=readings, error=bundle_error,
    )
