"""Polygon (now Massive) market data loader for US-listed equities.

Endpoint: GET https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{from}/{to}
Auth:     ?apiKey=<key> resolved from POLYGON_API_KEY env var, falling back
          to ~/.trading-dashboard/.env if not set in os.environ.

Free tier (Massive Stocks Starter free): end-of-day delayed daily / weekly /
monthly / hourly aggregates; rate-limited at 5 requests/min; unauthorized for
options snapshot / last-trade / real-time endpoints.

Same DataFrame shape as yfinance_loader.load_bars: naive datetime index,
lowercase open / high / low / close / volume columns. Drop-in replacement for
the dispatcher in scan.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


POLYGON_API_BASE = "https://api.polygon.io"
USER_AGENT = "trading-dashboard/0.1.0"
ENV_FILE = Path.home() / ".trading-dashboard" / ".env"

# Free-tier Polygon caps intraday aggregate responses at roughly 900-1000 bars
# per call, regardless of the limit= param. With sort=desc we get the MOST
# RECENT N bars (correct for live indicator math). Hourly default of 180d
# typically yields ~860-900 bars on free tier — enough for MA200 on 2h
# (200 × 2h = 400 trading hours ≈ 60 trading days ≈ 90 calendar days, with
# headroom). Daily/weekly do not hit this cap at typical request sizes.
_DEFAULT_PERIODS = {
    "1d": "2y",
    "1wk": "10y",
    "1mo": "max",
    "1h": "180d",
    "60m": "180d",
    "30m": "30d",
    "15m": "30d",
    "5m": "15d",
}

_RESAMPLE_RULES = {"4h": "4h", "2h": "2h"}
_RESAMPLE_BASE_INTERVAL = "1h"

_INTERVAL_MAP: dict[str, tuple[int, str]] = {
    "1d": (1, "day"),
    "1wk": (1, "week"),
    "1mo": (1, "month"),
    "1h": (1, "hour"),
    "60m": (1, "hour"),
    "30m": (30, "minute"),
    "15m": (15, "minute"),
    "5m": (5, "minute"),
    "1m": (1, "minute"),
}


class PolygonFetchError(Exception):
    """REST call failed, response shape unexpected, or auth/quota error."""


def _load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    """Parse simple KEY=VALUE pairs from a .env file, ignoring blank lines and
    comments. Does not mutate os.environ — caller decides what to do with the
    result. Tolerates surrounding quotes (KEY="value" or KEY='value').
    """
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _api_key() -> str | None:
    """Resolve API key from os.environ first, then ~/.trading-dashboard/.env.
    Returns None if not set anywhere — dispatcher uses this to choose fallback.
    """
    key = os.environ.get("POLYGON_API_KEY")
    if key:
        return key
    return _load_env_file().get("POLYGON_API_KEY")


def is_available() -> bool:
    """True iff POLYGON_API_KEY is resolvable from env or .env file."""
    return _api_key() is not None


def _http_get_json(url: str, timeout: float = 15.0) -> dict:
    """GET + parse JSON, with one retry on HTTP 429 (free tier 5 req/min)."""
    import time as _time

    request = Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    for attempt in (0, 1):
        try:
            with urlopen(request, timeout=timeout) as resp:
                body = resp.read()
            break
        except HTTPError as exc:
            if exc.code == 429 and attempt == 0:
                _time.sleep(13)  # ~5 req/min budget — wait one window
                continue
            raise PolygonFetchError(f"HTTP request failed: {exc}") from exc
        except (URLError, OSError) as exc:
            raise PolygonFetchError(f"HTTP request failed: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise PolygonFetchError("Could not parse Polygon response as JSON") from exc


def _default_period(interval: str) -> str:
    if interval in _RESAMPLE_RULES:
        return _DEFAULT_PERIODS[_RESAMPLE_BASE_INTERVAL]
    return _DEFAULT_PERIODS.get(interval, "2y")


def _period_to_dates(
    period: str, end: datetime | None = None
) -> tuple[str, str]:
    """Translate yfinance-style period strings ('2y', '730d', 'max', '10y') to
    (from_date, to_date) in YYYY-MM-DD form for Polygon's range endpoint.
    """
    end = end or datetime.now(timezone.utc)
    p = period.strip().lower()
    if p == "max":
        # Polygon's earliest equity coverage is ~2003-09-10. Use a safe far date.
        from_dt = datetime(2003, 9, 10, tzinfo=timezone.utc)
    elif p.endswith("y"):
        years = int(p[:-1])
        from_dt = end - timedelta(days=years * 365 + 1)
    elif p.endswith("mo"):
        months = int(p[:-2])
        from_dt = end - timedelta(days=months * 31)
    elif p.endswith("d"):
        days = int(p[:-1])
        from_dt = end - timedelta(days=days)
    else:
        raise PolygonFetchError(f"Unsupported period format: {period!r}")
    return from_dt.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def _resample(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    if bars.empty:
        return bars
    resampled = bars.resample(rule, origin="start").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return resampled.dropna(subset=["close"])


def _bars_from_payload(payload: dict, ticker: str) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise PolygonFetchError("Unexpected response: not an object")
    status = payload.get("status")
    if status == "NOT_AUTHORIZED":
        raise PolygonFetchError(
            f"Polygon NOT_AUTHORIZED for {ticker}: "
            f"{payload.get('message', '<no message>')} "
            f"(endpoint may require a paid Massive plan)"
        )
    if status not in ("OK", "DELAYED"):
        raise PolygonFetchError(
            f"Polygon returned status {status!r} for {ticker}: "
            f"{payload.get('message', payload.get('error', '<no message>'))}"
        )
    rows: Iterable = payload.get("results") or []
    parsed = []
    for row in rows:
        try:
            parsed.append(
                {
                    "ts": int(row["t"]),
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row["v"]),
                }
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise PolygonFetchError(
                f"Malformed aggregate row for {ticker}: {row!r}"
            ) from exc
    if not parsed:
        raise PolygonFetchError(f"Polygon returned no aggregates for {ticker}")
    df = pd.DataFrame(parsed)
    df.index = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop(columns=["ts"])
    return df.sort_index()[["open", "high", "low", "close", "volume"]]


def _load_native(
    ticker: str,
    period: str,
    interval: str,
    adjusted: bool,
    fetch=_http_get_json,
) -> pd.DataFrame:
    if interval not in _INTERVAL_MAP:
        raise PolygonFetchError(
            f"Unsupported Polygon interval {interval!r}; "
            f"supported native: {sorted(_INTERVAL_MAP)}"
        )
    api_key = _api_key()
    if not api_key:
        raise PolygonFetchError(
            "POLYGON_API_KEY not set in os.environ or ~/.trading-dashboard/.env"
        )
    multiplier, timespan = _INTERVAL_MAP[interval]
    from_date, to_date = _period_to_dates(period)
    # sort=desc fetches MOST RECENT bars first; free-tier hourly caps responses
    # at ~900 bars regardless of `limit`, so asc would give us stale history.
    # pandas sort_index() in _bars_from_payload restores ascending order.
    qs = urlencode(
        {
            "adjusted": "true" if adjusted else "false",
            "sort": "desc",
            "limit": 50000,
            "apiKey": api_key,
        }
    )
    url = (
        f"{POLYGON_API_BASE}/v2/aggs/ticker/{ticker.upper()}"
        f"/range/{multiplier}/{timespan}/{from_date}/{to_date}?{qs}"
    )
    payload = fetch(url)
    return _bars_from_payload(payload, ticker)


def load_bars(
    ticker: str,
    period: str | None = None,
    interval: str = "1d",
    auto_adjust: bool = False,
    fetch=_http_get_json,
) -> pd.DataFrame:
    """Fetch OHLCV bars for a US-listed equity via Polygon/Massive.

    Same signature as yfinance_loader.load_bars so the dispatcher in scan.py
    can swap implementations without changing call sites. ``auto_adjust`` maps
    to Polygon's ``adjusted=`` query param.

    For 4h/2h timeframes (no native Polygon support), pulls 1h bars and
    resamples — same approach as yfinance_loader.

    Returns DataFrame indexed by naive datetime with columns
    open / high / low / close / volume.

    Raises PolygonFetchError on auth, transport, parse, or empty-results.
    """
    if period is None:
        period = _default_period(interval)
    if interval in _RESAMPLE_RULES:
        hourly = _load_native(
            ticker, period, _RESAMPLE_BASE_INTERVAL, auto_adjust, fetch
        )
        rule = _RESAMPLE_RULES[interval]
        out = _resample(hourly, rule)
        if out.empty:
            raise PolygonFetchError(
                f"Resample produced empty frame for {ticker!r} interval={interval!r}"
            )
        return out
    return _load_native(ticker, period, interval, auto_adjust, fetch)
