from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf


# yfinance returns US-equity/ETF data in exchange-local time, which _load_native
# strips to naive timestamps — so "exchange-local" here means America/New_York.
_EXCHANGE_TZ = ZoneInfo("America/New_York")
_MARKET_CLOSE_HOUR = 16  # 16:00 ET regular-session close

# Calendar durations used to decide whether an intraday bar/bucket has fully
# elapsed. Daily/weekly are handled separately (session-aware).
_INTRADAY_DURATION = {
    "1h": timedelta(hours=1),
    "60m": timedelta(hours=1),
    "30m": timedelta(minutes=30),
    "15m": timedelta(minutes=15),
    "5m": timedelta(minutes=5),
    "4h": timedelta(hours=4),
    "2h": timedelta(hours=2),
}


def _now_exchange_local() -> datetime:
    """Current wall-clock time in exchange-local (ET), naive — matches the index."""
    return datetime.now(_EXCHANGE_TZ).replace(tzinfo=None)


def _last_bar_incomplete(last_idx: pd.Timestamp, interval: str, now_et: datetime) -> bool:
    """Is the final bar still forming as of ``now_et`` (naive ET)?

    - 1d: today's bar is in-progress until the 16:00 ET session close.
    - 1wk: the current week's bar (Monday-anchored, per yfinance US-equity
      behavior verified 2026-06) is in-progress until Friday 16:00 ET.
    - intraday / resampled 2h/4h: the bucket is in-progress until
      bucket_start + interval duration has elapsed.
    """
    ts = last_idx.to_pydatetime()
    if interval in ("1d", "1D"):
        if ts.date() != now_et.date():
            return False  # not today's bar → a completed prior session
        return now_et.hour < _MARKET_CLOSE_HOUR
    if interval in ("1wk", "1w", "7d"):
        week_start = datetime(ts.year, ts.month, ts.day)  # Monday 00:00
        friday_close = week_start + timedelta(days=4, hours=_MARKET_CLOSE_HOUR)
        return now_et < friday_close
    duration = _INTRADAY_DURATION.get(interval)
    if duration is None:
        return False  # unknown interval → don't second-guess, keep the bar
    return (ts + duration) > now_et


def _drop_incomplete_last_bar(
    df: pd.DataFrame, interval: str, now_et: datetime
) -> pd.DataFrame:
    """Drop the trailing bar if it has not fully closed (anti-repaint).

    Keeps the frame non-empty: a sole in-progress bar is returned as-is rather
    than dropped to nothing.
    """
    if len(df) <= 1:
        return df
    if _last_bar_incomplete(df.index[-1], interval, now_et):
        return df.iloc[:-1]
    return df


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
    include_partial: bool = False,
    _now: datetime | None = None,
) -> pd.DataFrame:
    """Fetch OHLCV bars for a ticker via yfinance.

    Period defaults to a sensible window for the given interval if not supplied
    (1d=2y, 1wk=10y, 4h=730d resampled from 1h, etc).

    Returns a DataFrame indexed by naive datetime with lowercase columns
    open, high, low, close, volume.

    By default the trailing **in-progress** bar is dropped (anti-repaint): a
    mid-session daily bar, the current partial week, or a still-forming 2h/4h
    bucket would otherwise let a 19/39 cross or full_bull stack appear and then
    vanish by the bar's close, firing premature entries. Pass
    ``include_partial=True`` to keep the forming bar (e.g. an intraday lotto
    flow that genuinely wants live tape). ``_now`` overrides the clock for tests.

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
        if not include_partial:
            out = _drop_incomplete_last_bar(out, interval, _now or _now_exchange_local())
        return out

    out = _load_native(ticker, period=period, interval=interval, auto_adjust=auto_adjust)
    if not include_partial:
        out = _drop_incomplete_last_bar(out, interval, _now or _now_exchange_local())
    return out
