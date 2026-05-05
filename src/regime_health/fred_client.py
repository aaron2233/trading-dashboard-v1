"""Minimal FRED REST client for regime_health Tier 2.

Why hand-rolled: the project already has `requests` available (transitive
dep of yfinance) and we only need one endpoint shape. Adding `fredapi`
just for one route adds a runtime dep with no upside.

Key handling:
  - Reads `FRED_API_KEY` from env. Empty / unset → returns None from
    `fetch_observations` and the tier2 readers fall back to a "key not
    configured" amber state. No fabrication.
  - Future: the regime_health.thresholds config could expose the key
    via YAML, but env-var-only is fine for v1 (matches Anthropic SDK
    pattern in this project).

The client never raises on transport errors — it converts them to
FredFetchError so caller code can short-circuit cleanly.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


logger = logging.getLogger(__name__)


FRED_API_BASE = "https://api.stlouisfed.org/fred/series/observations"
FRED_API_KEY_ENV = "FRED_API_KEY"

# Default request timeout. FRED responses are typically <100ms; this is the
# upper bound before we declare the API unavailable for the snapshot.
DEFAULT_TIMEOUT_SEC = 8.0


class FredFetchError(RuntimeError):
    """Raised when a FRED request fails (network, auth, parse, empty)."""


@dataclass(frozen=True)
class FredObservation:
    """One row of FRED time series data."""
    date: str               # YYYY-MM-DD
    value: float            # parsed; None encoded as float('nan') is avoided
                            # — caller filters via skip_missing in fetch
    series_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.date, "value": self.value, "series_id": self.series_id}


# Type alias for an injectable HTTP fetcher (tests inject a fake instead of
# hitting urllib.request).
FetchFn = Callable[[str], dict[str, Any]]


def _real_fetch(url: str) -> dict[str, Any]:
    """Real urllib-based fetcher. Wraps transport errors in FredFetchError."""
    req = urllib.request.Request(url, headers={"User-Agent": "trading-dashboard/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_SEC) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        # FRED returns JSON error bodies; try to surface error_message.
        try:
            body = json.loads(exc.read().decode("utf-8"))
            msg = body.get("error_message") or body.get("message") or str(exc)
        except Exception:
            msg = str(exc)
        raise FredFetchError(f"FRED HTTP {exc.code}: {msg}") from exc
    except urllib.error.URLError as exc:
        raise FredFetchError(f"FRED transport error: {exc}") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise FredFetchError(f"FRED response not JSON: {exc}") from exc


def get_api_key() -> str | None:
    """Resolve the FRED API key from env. Returns None when unset / empty."""
    key = os.environ.get(FRED_API_KEY_ENV)
    if not key or not key.strip():
        return None
    return key.strip()


def fetch_observations(
    series_id: str,
    *,
    limit: int = 1,
    sort_order: str = "desc",
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> list[FredObservation]:
    """Fetch the most recent `limit` observations for `series_id`.

    Returns observations sorted per `sort_order` (default "desc" — newest
    first). Empty list when no data; raises FredFetchError on bad response,
    auth failure, or transport error.

    `api_key=None` → resolve from env var. If still None, raises
    FredFetchError("FRED API key not configured") so the caller can render
    a "key not configured" amber state without proceeding.
    """
    key = api_key if api_key is not None else get_api_key()
    if key is None:
        raise FredFetchError("FRED API key not configured")
    if not series_id:
        raise FredFetchError("series_id is required")
    if limit < 1 or limit > 100000:
        raise FredFetchError(f"limit out of range: {limit}")

    fetch_fn = fetch or _real_fetch
    params = {
        "series_id": series_id,
        "api_key": key,
        "file_type": "json",
        "sort_order": sort_order,
        "limit": str(limit),
    }
    url = f"{FRED_API_BASE}?{urllib.parse.urlencode(params)}"
    logger.debug("FRED fetch: %s limit=%d", series_id, limit)
    payload = fetch_fn(url)

    if not isinstance(payload, dict):
        raise FredFetchError(f"FRED payload not an object: {type(payload).__name__}")
    if "error_code" in payload:
        msg = payload.get("error_message") or "unknown error"
        raise FredFetchError(f"FRED error {payload['error_code']}: {msg}")

    obs_raw = payload.get("observations")
    if not isinstance(obs_raw, list):
        raise FredFetchError(f"FRED missing 'observations' for {series_id}")

    out: list[FredObservation] = []
    for row in obs_raw:
        if not isinstance(row, dict):
            continue
        date_str = row.get("date")
        val_str = row.get("value")
        if date_str is None:
            continue
        # FRED encodes missing values as ".".
        if val_str is None or val_str == "." or val_str == "":
            continue
        try:
            value = float(val_str)
        except (TypeError, ValueError):
            logger.warning("FRED bad numeric for %s on %s: %r", series_id, date_str, val_str)
            continue
        out.append(FredObservation(date=date_str, value=value, series_id=series_id))
    return out


def fetch_latest(
    series_id: str,
    *,
    fetch: FetchFn | None = None,
    api_key: str | None = None,
) -> FredObservation:
    """Convenience: fetch the single most recent observation. Raises if empty."""
    obs = fetch_observations(series_id, limit=1, fetch=fetch, api_key=api_key)
    if not obs:
        raise FredFetchError(f"No observations returned for {series_id}")
    return obs[0]
