"""Tests for focus.sunday_scan scoring + run_sunday_scan composer."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from focus.sunday_scan import (
    FIRES_THRESHOLD,
    WATCH_THRESHOLD,
    list_recent_sunday_scans,
    load_sunday_scan,
    persist_sunday_scan,
    rank_setups,
    run_sunday_scan,
    score_setup,
)


def _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold",
         sqn_regime="bull", sqn_value=1.0):
    return {
        "ticker": "X", "timeframe": "1d", "bar_date": "2026-04-24", "close": 100.0,
        "ma_ribbon": {"ma_10": 100, "ma_20": 99, "ma_50": 95, "ma_200": 88,
                      "stack_state": stack},
        "stochastic": {"k": 25, "d": 23, "zone": zone, "signal": signal},
        "sqn": {"sqn_value": sqn_value, "regime": sqn_regime},
    }


# ─────────────────────────────────────────────────────────────────────────
# score_setup
# ─────────────────────────────────────────────────────────────────────────

def test_qqq_long_in_strong_bull_with_pullback_setup_fires():
    spy = _row(sqn_regime="strong_bull")
    qqq = _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold")
    setup = score_setup("QQQ", "long", qqq, spy)
    assert setup.status == "fires"
    assert setup.score >= FIRES_THRESHOLD


def test_qqq_short_in_strong_bull_blocked():
    spy = _row(sqn_regime="strong_bull")
    qqq = _row(stack="full_bull", zone="overbought", signal="bear_cross_overbought")
    setup = score_setup("QQQ", "short", qqq, spy)
    assert setup.status == "blocked"
    assert "regime" in " ".join(setup.blockers).lower()


def test_chop_blocks_regardless_of_other_axes():
    spy = _row(sqn_regime="bull")
    qqq = _row(stack="chop", zone="oversold", signal="bull_cross_oversold")
    setup = score_setup("QQQ", "long", qqq, spy)
    assert setup.status == "blocked"
    assert any("MA tangle" in b for b in setup.blockers)


def test_gld_long_in_bear_regime_fires():
    spy = _row(sqn_regime="bear")
    gld = _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold")
    setup = score_setup("GLD", "long", gld, spy)
    assert setup.status == "fires"


def test_neutral_regime_with_clean_long_fires_via_stack_and_stoch():
    spy = _row(sqn_regime="neutral")
    qqq = _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold")
    setup = score_setup("QQQ", "long", qqq, spy)
    # Neutral gives 15, full_bull long gives 30, bull_cross gives 30 → 75
    assert setup.score == 75
    assert setup.status == "fires"


def test_components_sum_to_total():
    spy = _row(sqn_regime="bull")
    qqq = _row()
    setup = score_setup("QQQ", "long", qqq, spy)
    c = setup.components
    assert c["regime"] + c["stack"] + c["stoch"] == setup.score


def test_watch_status_for_marginal_setup():
    # Bull regime, compression stack (5), zone-only oversold (15) → 30+5+15 = 50 → watch
    spy = _row(sqn_regime="bull")
    qqq = _row(stack="compression", zone="oversold", signal="none")
    setup = score_setup("QQQ", "long", qqq, spy)
    assert WATCH_THRESHOLD <= setup.score < FIRES_THRESHOLD
    assert setup.status == "watch"


# ─────────────────────────────────────────────────────────────────────────
# rank_setups
# ─────────────────────────────────────────────────────────────────────────

def test_rank_returns_four_setups_sorted_descending():
    spy = _row(sqn_regime="bull")
    qqq = _row()
    gld = _row()
    setups = rank_setups(qqq, gld, spy)
    assert len(setups) == 4
    assert setups == sorted(setups, key=lambda s: s.score, reverse=True)
    asset_dirs = {(s.asset, s.direction) for s in setups}
    assert asset_dirs == {
        ("QQQ", "long"), ("QQQ", "short"),
        ("GLD", "long"), ("GLD", "short"),
    }


# ─────────────────────────────────────────────────────────────────────────
# run_sunday_scan
# ─────────────────────────────────────────────────────────────────────────

def _make_scan_fn(rows: dict[str, Any], errors: dict[str, Exception] | None = None):
    errors = errors or {}

    def _fn(ticker: str) -> dict[str, Any]:
        if ticker in errors:
            raise errors[ticker]
        return rows[ticker]
    return _fn


def test_run_sunday_scan_recommends_trade_when_top_setup_fires():
    rows = {
        "SPY": _row(sqn_regime="strong_bull"),
        "QQQ": _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold"),
        "GLD": _row(stack="chop", zone="neutral", signal="none"),
    }
    result = run_sunday_scan(_make_scan_fn(rows))
    assert result.recommendation == "trade"
    assert result.setups[0].asset == "QQQ"
    assert result.setups[0].direction == "long"
    assert "QQQ long" in result.headline
    assert result.errors == {}


def test_run_sunday_scan_recommends_cash_when_nothing_fires():
    # All chop = all blocked. Top setup status = blocked → cash.
    rows = {
        "SPY": _row(sqn_regime="neutral"),
        "QQQ": _row(stack="chop"),
        "GLD": _row(stack="chop"),
    }
    result = run_sunday_scan(_make_scan_fn(rows))
    assert result.recommendation == "cash"
    assert "Cash week" in result.headline


def test_run_sunday_scan_handles_partial_failure():
    rows = {
        "SPY": _row(sqn_regime="bull"),
        "GLD": _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold"),
    }
    errors = {"QQQ": RuntimeError("yfinance refused")}
    result = run_sunday_scan(_make_scan_fn(rows, errors))
    # Only GLD setups in the ranking
    assets = {s.asset for s in result.setups}
    assert assets == {"GLD"}
    assert "QQQ" in result.errors


def test_run_sunday_scan_handles_total_failure():
    errors = {
        "SPY": RuntimeError("nope"),
        "QQQ": RuntimeError("nope"),
        "GLD": RuntimeError("nope"),
    }
    result = run_sunday_scan(_make_scan_fn({}, errors))
    assert result.setups == []
    assert result.recommendation == "cash"
    assert len(result.errors) == 3


def test_run_sunday_scan_watch_recommendation_for_marginal_top():
    rows = {
        "SPY": _row(sqn_regime="bull"),
        "QQQ": _row(stack="compression", zone="oversold", signal="none"),
        "GLD": _row(stack="chop"),
    }
    result = run_sunday_scan(_make_scan_fn(rows))
    assert result.recommendation == "watch"
    assert "Watch" in result.headline


# ─────────────────────────────────────────────────────────────────────────
# persist_sunday_scan
# ─────────────────────────────────────────────────────────────────────────

def test_persist_sunday_scan_writes_json(tmp_path: Path):
    rows = {
        "SPY": _row(sqn_regime="strong_bull"),
        "QQQ": _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold"),
        "GLD": _row(stack="chop"),
    }
    scan = run_sunday_scan(_make_scan_fn(rows))

    fixed = datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc)
    path = persist_sunday_scan(scan, sunday_scans_dir=tmp_path, now=fixed)

    assert path == tmp_path / "2026-04-28.json"
    payload = json.loads(path.read_text())
    assert payload["recommendation"] == "trade"
    assert payload["scan_time_utc"] == fixed.isoformat()
    assert payload["spy"] is not None
    assert payload["qqq"] is not None
    assert payload["gld"] is not None
    assert len(payload["setups"]) == 4
    # Top setup matches what run_sunday_scan ranked
    assert payload["setups"][0]["asset"] == "QQQ"
    assert payload["setups"][0]["direction"] == "long"


def test_persist_sunday_scan_overwrites_same_day(tmp_path: Path):
    rows1 = {
        "SPY": _row(sqn_regime="bull"),
        "QQQ": _row(stack="full_bull"),
        "GLD": _row(stack="chop"),
    }
    rows2 = {
        "SPY": _row(sqn_regime="strong_bull"),
        "QQQ": _row(stack="full_bull", zone="oversold", signal="bull_cross_oversold"),
        "GLD": _row(stack="chop"),
    }
    fixed = datetime(2026, 4, 28, 14, 0, tzinfo=timezone.utc)

    persist_sunday_scan(run_sunday_scan(_make_scan_fn(rows1)),
                        sunday_scans_dir=tmp_path, now=fixed)
    path = persist_sunday_scan(run_sunday_scan(_make_scan_fn(rows2)),
                               sunday_scans_dir=tmp_path, now=fixed)

    # Only one file for the day; second write overwrote
    assert list(tmp_path.glob("*.json")) == [path]
    payload = json.loads(path.read_text())
    # The second scan had the bullish stoch signal, so its score is higher
    assert payload["spy"]["sqn"]["regime"] == "strong_bull"


def test_persist_sunday_scan_creates_dir_if_missing(tmp_path: Path):
    rows = {"SPY": _row(), "QQQ": _row(), "GLD": _row()}
    nested = tmp_path / "deep" / "nested" / "sunday_scans"
    assert not nested.exists()

    path = persist_sunday_scan(run_sunday_scan(_make_scan_fn(rows)),
                               sunday_scans_dir=nested)
    assert path.exists()
    assert nested.is_dir()


def test_persist_sunday_scan_writes_are_atomic(tmp_path: Path):
    """No .tmp sibling left after persist — confirms atomic write behavior."""
    rows = {"SPY": _row(), "QQQ": _row(), "GLD": _row()}
    persist_sunday_scan(run_sunday_scan(_make_scan_fn(rows)),
                        sunday_scans_dir=tmp_path)
    persist_sunday_scan(run_sunday_scan(_make_scan_fn(rows)),
                        sunday_scans_dir=tmp_path)  # rewrite

    json_files = list(tmp_path.glob("*.json"))
    tmp_files = [f for f in tmp_path.iterdir() if ".tmp" in f.name]
    assert len(json_files) == 1
    assert tmp_files == []


# ─────────────────────────────────────────────────────────────────────────
# list_recent_sunday_scans
# ─────────────────────────────────────────────────────────────────────────

def _write_scan(dir_: Path, date_str: str, recommendation: str = "trade",
                top_setup: dict | None = None) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_time_utc": f"{date_str}T14:00:00+00:00",
        "spy": None, "qqq": None, "gld": None,
        "setups": [top_setup] if top_setup else [],
        "recommendation": recommendation,
        "headline": f"headline for {date_str}",
        "errors": {},
    }
    (dir_ / f"{date_str}.json").write_text(json.dumps(payload))


def test_list_recent_returns_empty_when_dir_missing(tmp_path: Path):
    summaries = list_recent_sunday_scans(sunday_scans_dir=tmp_path / "missing")
    assert summaries == []


def test_list_recent_returns_summaries_newest_first(tmp_path: Path):
    top = {"asset": "QQQ", "direction": "long", "score": 75, "status": "fires"}
    _write_scan(tmp_path, "2026-04-21", recommendation="cash")
    _write_scan(tmp_path, "2026-04-28", recommendation="trade", top_setup=top)
    _write_scan(tmp_path, "2026-04-14", recommendation="watch")

    summaries = list_recent_sunday_scans(sunday_scans_dir=tmp_path)
    assert [s.date for s in summaries] == ["2026-04-28", "2026-04-21", "2026-04-14"]
    assert summaries[0].recommendation == "trade"
    assert summaries[0].top_setup == top
    assert summaries[1].top_setup is None  # cash week, no setups


def test_list_recent_respects_limit(tmp_path: Path):
    for i in range(15):
        _write_scan(tmp_path, f"2026-04-{i+1:02d}")
    summaries = list_recent_sunday_scans(limit=5, sunday_scans_dir=tmp_path)
    assert len(summaries) == 5
    assert summaries[0].date == "2026-04-15"


def test_list_recent_skips_malformed_files(tmp_path: Path):
    _write_scan(tmp_path, "2026-04-28")
    (tmp_path / "2026-04-21.json").write_text("not valid json {{")
    (tmp_path / "not-a-date.json").write_text("{}")  # filename filter
    (tmp_path / "readme.txt").write_text("ignored")  # non-json

    summaries = list_recent_sunday_scans(sunday_scans_dir=tmp_path)
    assert [s.date for s in summaries] == ["2026-04-28"]


# ─────────────────────────────────────────────────────────────────────────
# load_sunday_scan
# ─────────────────────────────────────────────────────────────────────────

def test_load_sunday_scan_returns_payload(tmp_path: Path):
    _write_scan(tmp_path, "2026-04-28", recommendation="trade",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 75, "status": "fires",
                           "components": {}, "blockers": []})
    payload = load_sunday_scan("2026-04-28", sunday_scans_dir=tmp_path)
    assert payload is not None
    assert payload["recommendation"] == "trade"
    assert payload["setups"][0]["asset"] == "QQQ"


def test_load_sunday_scan_returns_none_when_missing(tmp_path: Path):
    assert load_sunday_scan("2026-01-01", sunday_scans_dir=tmp_path) is None


@pytest.mark.parametrize("bad_date", ["", "2026", "2026-04", "20260428",
                                       "2026/04/28", "today"])
def test_load_sunday_scan_returns_none_for_invalid_date(tmp_path: Path, bad_date):
    assert load_sunday_scan(bad_date, sunday_scans_dir=tmp_path) is None


def test_load_sunday_scan_raises_on_corrupt_file(tmp_path: Path):
    (tmp_path / "2026-04-28.json").write_text("not json {{")
    with pytest.raises(json.JSONDecodeError):
        load_sunday_scan("2026-04-28", sunday_scans_dir=tmp_path)


def test_run_sunday_scan_records_weekly_trend_log():
    # Every Sunday scan must leave one line per default-watchlist ticker
    # explaining why weekly-trend did or didn't fire (dormancy audit trail).
    rows = {
        "SPY": _row(sqn_regime="bull"),
        "QQQ": _row(stack="full_bull", zone="overbought", signal="none"),
        "GLD": _row(stack="chop"),
    }
    result = run_sunday_scan(_make_scan_fn(rows))
    assert len(result.weekly_trend_log) == 2
    joined = " | ".join(result.weekly_trend_log)
    assert "QQQ" in joined and "GLD" in joined
    assert "stack=" in result.weekly_trend_log[0]
    # Log survives round-trip into the persisted payload
    assert result.to_dict()["weekly_trend_log"] == result.weekly_trend_log
