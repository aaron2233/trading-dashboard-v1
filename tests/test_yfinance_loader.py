"""Tests for the completed-bar (anti-repaint) filter in data.yfinance_loader.

The filter drops the trailing in-progress bar so a mid-session daily bar, the
current partial week, or a still-forming 2h/4h bucket can't feed a 19/39 cross
or full_bull stack that vanishes by the bar's close (premature-entry repaint).
The clock is injected via ``_now`` so these are deterministic and network-free.

Calendar anchors used below: 2026-06-08 is a Monday, 2026-06-12 the Friday of
that week (verified against the yfinance weekly-bar anchor in the 2026-06 review).
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from data.yfinance_loader import (
    _drop_incomplete_last_bar,
    _last_bar_incomplete,
)


def _frame(dates: list[str]) -> pd.DataFrame:
    idx = pd.to_datetime(dates)
    n = len(dates)
    return pd.DataFrame(
        {"open": [1.0] * n, "high": [1.0] * n, "low": [1.0] * n,
         "close": [1.0] * n, "volume": [1.0] * n},
        index=idx,
    )


# ── Daily ────────────────────────────────────────────────────────────────────

def test_daily_today_bar_dropped_before_close():
    df = _frame(["2026-06-11", "2026-06-12"])
    now = datetime(2026, 6, 12, 10, 0)  # Friday 10:00 ET, pre-close
    out = _drop_incomplete_last_bar(df, "1d", now)
    assert list(out.index) == [pd.Timestamp("2026-06-11")]


def test_daily_today_bar_kept_after_close():
    df = _frame(["2026-06-11", "2026-06-12"])
    now = datetime(2026, 6, 12, 16, 30)  # Friday 16:30 ET, post-close
    out = _drop_incomplete_last_bar(df, "1d", now)
    assert out.index[-1] == pd.Timestamp("2026-06-12")  # complete → kept


def test_daily_prior_session_kept_on_weekend():
    df = _frame(["2026-06-11", "2026-06-12"])
    now = datetime(2026, 6, 14, 9, 0)  # Sunday — last bar (Fri) is complete
    out = _drop_incomplete_last_bar(df, "1d", now)
    assert out.index[-1] == pd.Timestamp("2026-06-12")


# ── Weekly ───────────────────────────────────────────────────────────────────

def test_weekly_current_week_dropped_midweek():
    df = _frame(["2026-06-01", "2026-06-08"])  # weeks anchored Mondays
    now = datetime(2026, 6, 10, 12, 0)  # Wednesday of the 06-08 week
    out = _drop_incomplete_last_bar(df, "1wk", now)
    assert out.index[-1] == pd.Timestamp("2026-06-01")


def test_weekly_kept_after_friday_close():
    df = _frame(["2026-06-01", "2026-06-08"])
    now = datetime(2026, 6, 13, 9, 0)  # Saturday — week 06-08 fully closed
    out = _drop_incomplete_last_bar(df, "1wk", now)
    assert out.index[-1] == pd.Timestamp("2026-06-08")


def test_weekly_friday_pre_close_still_incomplete():
    assert _last_bar_incomplete(pd.Timestamp("2026-06-08"), "1wk",
                                datetime(2026, 6, 12, 15, 0)) is True
    assert _last_bar_incomplete(pd.Timestamp("2026-06-08"), "1wk",
                                datetime(2026, 6, 12, 16, 30)) is False


# ── Intraday / resampled ──────────────────────────────────────────────────────

def test_2h_bucket_dropped_while_forming():
    df = _frame(["2026-06-12 12:00", "2026-06-12 14:00"])  # last bucket 14:00-16:00
    now = datetime(2026, 6, 12, 15, 0)  # still inside the 14:00-16:00 window
    out = _drop_incomplete_last_bar(df, "2h", now)
    assert out.index[-1] == pd.Timestamp("2026-06-12 12:00")


def test_2h_bucket_kept_once_elapsed():
    df = _frame(["2026-06-12 12:00", "2026-06-12 14:00"])
    now = datetime(2026, 6, 12, 16, 30)  # past the 16:00 bucket end
    out = _drop_incomplete_last_bar(df, "2h", now)
    assert out.index[-1] == pd.Timestamp("2026-06-12 14:00")


# ── Guards ─────────────────────────────────────────────────────────────────────

def test_sole_bar_never_dropped_to_empty():
    df = _frame(["2026-06-12"])
    now = datetime(2026, 6, 12, 10, 0)  # would be "incomplete", but it's the only row
    out = _drop_incomplete_last_bar(df, "1d", now)
    assert len(out) == 1


def test_unknown_interval_keeps_bar():
    assert _last_bar_incomplete(pd.Timestamp("2026-06-12"), "1mo",
                                datetime(2026, 6, 12, 10, 0)) is False


def test_load_bars_drops_partial_and_include_partial_keeps_it(monkeypatch):
    # Integration: patch the native fetch and verify load_bars applies the filter
    # by default and honors the include_partial escape hatch + _now injection.
    from data import yfinance_loader as yl
    df = _frame(["2026-06-11", "2026-06-12"])
    monkeypatch.setattr(yl, "_load_native", lambda *a, **k: df.copy())
    now = datetime(2026, 6, 12, 10, 0)  # Friday pre-close
    default = yl.load_bars("QQQ", interval="1d", _now=now)
    assert default.index[-1] == pd.Timestamp("2026-06-11")  # partial dropped
    kept = yl.load_bars("QQQ", interval="1d", include_partial=True, _now=now)
    assert kept.index[-1] == pd.Timestamp("2026-06-12")  # escape hatch
