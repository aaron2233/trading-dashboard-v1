import pandas as pd
import yfinance as yf


_DEFAULT_PERIODS = {
    "1d": "2y",
    "1wk": "10y",
    "1mo": "max",
    "1h": "730d",
    "60m": "730d",
    "30m": "60d",
    "15m": "60d",
    "5m": "60d",
}

# Timeframes we synthesize via resampling (yfinance has no native bucket).
_RESAMPLE_RULES = {
    "4h": "4h",
    "2h": "2h",
}

# Minimum source interval used for resampled timeframes.
_RESAMPLE_BASE_INTERVAL = "1h"


def _default_period(interval: str) -> str:
    if interval in _RESAMPLE_RULES:
        return _DEFAULT_PERIODS[_RESAMPLE_BASE_INTERVAL]
    return _DEFAULT_PERIODS.get(interval, "2y")


def _resample(bars: pd.DataFrame, rule: str) -> pd.DataFrame:
    if bars.empty:
        return bars
    resampled = bars.resample(rule, origin="start").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return resampled.dropna(subset=["close"])


def _load_native(
    ticker: str, period: str, interval: str, auto_adjust: bool
) -> pd.DataFrame:
    data = yf.Ticker(ticker).history(
        period=period, interval=interval, auto_adjust=auto_adjust
    )
    if data.empty:
        raise ValueError(
            f"No data returned for ticker={ticker!r} period={period!r} interval={interval!r}"
        )
    data = data.rename(columns=str.lower)
    idx = pd.to_datetime(data.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    data.index = idx
    return data[["open", "high", "low", "close", "volume"]]


def load_bars(
    ticker: str,
    period: str | None = None,
    interval: str = "1d",
    auto_adjust: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV bars for a ticker via yfinance.

    Period defaults to a sensible window for the given interval if not supplied
    (1d=2y, 1wk=10y, 4h=730d resampled from 1h, etc).

    Returns a DataFrame indexed by naive datetime with lowercase columns
    open, high, low, close, volume.

    Raises ValueError if yfinance returns an empty frame.
    """
    if period is None:
        period = _default_period(interval)

    if interval in _RESAMPLE_RULES:
        hourly = _load_native(
            ticker, period=period, interval=_RESAMPLE_BASE_INTERVAL, auto_adjust=auto_adjust
        )
        rule = _RESAMPLE_RULES[interval]
        out = _resample(hourly, rule)
        if out.empty:
            raise ValueError(
                f"Resample produced empty frame for {ticker!r} interval={interval!r}"
            )
        return out

    return _load_native(ticker, period=period, interval=interval, auto_adjust=auto_adjust)
