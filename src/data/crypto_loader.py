"""Crypto bar loader via Crypto.com Exchange public REST API.

Endpoint: GET https://api.crypto.com/exchange/v1/public/get-candlestick
Params: instrument_name (e.g. BTC_USDT), timeframe (1m..1M), count (max 300).

Response shape:
    {
      "code": 0,
      "result": {
        "instrument_name": "BTC_USDT",
        "interval": "1D",
        "data": [{"t": 1640000000000, "o": "47000", "h": "48000",
                  "l": "46000", "c": "47500", "v": "1234.5"}, ...]
      }
    }

Public endpoint, no auth. Indicator math (MA Ribbon, Stochastic, SQN) is
symbol-agnostic, so the same modules run unchanged on crypto bars.
"""
from __future__ import annotations

import json
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd


CRYPTO_API_URL = "https://api.crypto.com/exchange/v1/public/get-candlestick"
CRYPTO_TICKER_URL = "https://api.crypto.com/exchange/v1/public/get-tickers"
CRYPTO_INSTRUMENTS_URL = "https://api.crypto.com/exchange/v1/public/get-instruments"
USER_AGENT = "trading-dashboard/0.1.0"

# Map our internal timeframe strings to Crypto.com Exchange `timeframe` codes.
_TIMEFRAME_MAP: dict[str, str] = {
    "1d": "1D",
    "1D": "1D",
    "1wk": "7D",
    "7d": "7D",
    "4h": "4h",
    "1h": "1h",
    "30m": "30m",
    "15m": "15m",
}


class CryptoFetchError(Exception):
    """REST call failed or response shape was unexpected."""


def is_crypto_symbol(symbol: str) -> bool:
    """Crypto.com instruments use underscore separators (BTC_USDT, ETH_USDT)."""
    return "_" in symbol


def _map_timeframe(timeframe: str) -> str:
    if timeframe not in _TIMEFRAME_MAP:
        raise CryptoFetchError(
            f"Unsupported crypto timeframe {timeframe!r}; "
            f"supported: {sorted(_TIMEFRAME_MAP)}"
        )
    return _TIMEFRAME_MAP[timeframe]


def _http_get_json(url: str, timeout: float = 10.0) -> dict:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as resp:
            body = resp.read()
    except (HTTPError, URLError, OSError) as exc:
        raise CryptoFetchError(f"HTTP request failed: {exc}") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise CryptoFetchError(f"Could not parse Crypto.com response as JSON") from exc


def _bars_from_payload(payload: dict) -> pd.DataFrame:
    if not isinstance(payload, dict):
        raise CryptoFetchError(f"Unexpected response: not an object")
    if payload.get("code", 0) != 0:
        raise CryptoFetchError(
            f"Crypto.com returned error code {payload.get('code')}: "
            f"{payload.get('message', payload.get('msg', '<no message>'))}"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CryptoFetchError("Crypto.com response missing 'result' object")
    rows: Iterable = result.get("data") or []
    parsed = []
    for row in rows:
        try:
            ts = int(row["t"])
            parsed.append({
                "ts": ts,
                "open": float(row["o"]),
                "high": float(row["h"]),
                "low": float(row["l"]),
                "close": float(row["c"]),
                "volume": float(row["v"]),
            })
        except (KeyError, TypeError, ValueError) as exc:
            raise CryptoFetchError(f"Malformed candlestick row: {row!r}") from exc

    if not parsed:
        raise CryptoFetchError("Crypto.com returned an empty data array")

    df = pd.DataFrame(parsed)
    df.index = pd.to_datetime(df["ts"], unit="ms")
    df = df.drop(columns=["ts"])
    return df.sort_index()[["open", "high", "low", "close", "volume"]]


def load_crypto_bars(
    symbol: str,
    timeframe: str = "1d",
    count: int = 300,
    api_url: str = CRYPTO_API_URL,
    fetch=_http_get_json,
) -> pd.DataFrame:
    """Fetch OHLCV candles from Crypto.com Exchange public REST.

    Args:
        symbol: instrument name e.g. "BTC_USDT", "ETH_USDT".
        timeframe: one of {1d, 1wk, 4h, 1h, 30m, 15m}.
        count: max 300 per Crypto.com limit. We default to 300 to maximise
               warmup window for the 200-period MA.
        api_url: override for tests.
        fetch: injected fetcher for tests; takes a URL, returns parsed JSON.

    Returns DataFrame indexed by naive datetime with columns
    open / high / low / close / volume — same shape as yfinance_loader.
    """
    if not is_crypto_symbol(symbol):
        raise CryptoFetchError(
            f"{symbol!r} doesn't look like a Crypto.com instrument "
            f"(expected underscore form like BTC_USDT)"
        )
    if count <= 0 or count > 300:
        raise CryptoFetchError("count must be between 1 and 300")

    qs = urlencode({
        "instrument_name": symbol.upper(),
        "timeframe": _map_timeframe(timeframe),
        "count": count,
    })
    url = f"{api_url}?{qs}"
    payload = fetch(url)
    return _bars_from_payload(payload)


def _safe_float(value, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_ticker(
    symbol: str,
    api_url: str = CRYPTO_TICKER_URL,
    fetch=_http_get_json,
) -> dict:
    """Fetch live ticker for one Crypto.com instrument.

    Returns a dict with the fields most useful for analysis:
      - instrument_name (echoed)
      - last_price (float)
      - change_24h_pct (float; may be None when API doesn't include it)
      - volume_24h (float, base-currency units)
      - high_24h, low_24h (float)
      - bid, ask (float)
      - source_timestamp_ms (int) — server's stamp on the read

    Crypto.com `get-tickers` response (single-instrument form):
        {"code": 0, "result": {"data": [{"i": "BTC_USDT", "a": "47500.0",
                                          "h": "48000", "l": "46000", ...}]}}
    Field codes per the API docs:
      a = last trade, b = best bid, k = best ask, c = 24h change pct,
      h = 24h high, l = 24h low, v = 24h volume, t = timestamp.
    """
    if not is_crypto_symbol(symbol):
        raise CryptoFetchError(
            f"{symbol!r} doesn't look like a Crypto.com instrument "
            f"(expected underscore form like BTC_USDT)"
        )
    qs = urlencode({"instrument_name": symbol.upper()})
    url = f"{api_url}?{qs}"
    payload = fetch(url)

    if not isinstance(payload, dict):
        raise CryptoFetchError("Unexpected ticker response: not an object")
    if payload.get("code", 0) != 0:
        raise CryptoFetchError(
            f"Crypto.com ticker error code {payload.get('code')}: "
            f"{payload.get('message', payload.get('msg', '<no message>'))}"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CryptoFetchError("Ticker response missing 'result' object")
    rows = result.get("data") or []
    if not rows:
        raise CryptoFetchError(f"No ticker data returned for {symbol}")
    row = rows[0]

    return {
        "instrument_name": row.get("i") or symbol.upper(),
        "last_price": _safe_float(row.get("a")),
        "bid": _safe_float(row.get("b")),
        "ask": _safe_float(row.get("k")),
        "change_24h_pct": _safe_float(row.get("c")),
        "high_24h": _safe_float(row.get("h")),
        "low_24h": _safe_float(row.get("l")),
        "volume_24h": _safe_float(row.get("v")),
        "source_timestamp_ms": int(row["t"]) if row.get("t") is not None else None,
    }


def fetch_instruments(
    api_url: str = CRYPTO_INSTRUMENTS_URL,
    fetch=_http_get_json,
    quote_filter: tuple[str, ...] | None = ("USDT", "USD"),
) -> list[dict]:
    """Fetch the list of supported Crypto.com Exchange instruments.

    Returns a list of dicts: {"instrument_name", "base_ccy", "quote_ccy"}.
    `quote_filter` defaults to USDT/USD only — the dashboard targets quote
    currencies the user actually trades against; other quotes (BTC pairs,
    margin instruments) get filtered out. Pass quote_filter=None for the
    full list.
    """
    payload = fetch(api_url)
    if not isinstance(payload, dict):
        raise CryptoFetchError("Unexpected instruments response: not an object")
    if payload.get("code", 0) != 0:
        raise CryptoFetchError(
            f"Crypto.com instruments error code {payload.get('code')}: "
            f"{payload.get('message', payload.get('msg', '<no message>'))}"
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise CryptoFetchError("Instruments response missing 'result' object")
    rows = result.get("data") or []

    out: list[dict] = []
    quote_filter_upper = (
        tuple(q.upper() for q in quote_filter) if quote_filter else None
    )
    for row in rows:
        name = row.get("symbol") or row.get("instrument_name")
        if not name or "_" not in name:
            continue
        base = row.get("base_ccy") or name.split("_", 1)[0]
        quote = row.get("quote_ccy") or name.split("_", 1)[1]
        if quote_filter_upper and quote.upper() not in quote_filter_upper:
            continue
        out.append({
            "instrument_name": name,
            "base_ccy": base,
            "quote_ccy": quote,
        })

    out.sort(key=lambda r: r["instrument_name"])
    return out
