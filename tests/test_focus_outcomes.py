"""Tests for focus.outcomes — attribution of Sunday scan recommendations
to actual journal positions."""
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from focus.outcomes import (
    DEFAULT_WINDOW_DAYS,
    build_outcome,
    find_matched_positions,
    summarize_recent_outcomes,
)
from positions.model import Position


def _open_pos(ticker="QQQ", direction="long", days_after_scan=2,
              scan_date="2026-04-26", instrument="call") -> Position:
    pos = Position.open_options_position(
        ticker=ticker, direction=direction, contract_type=instrument,
        account_key="main", strike=500, expiry="2026-06-19",
        premium=1.50, contracts=1,
    )
    base = datetime.strptime(scan_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    pos.entry_date = (base + timedelta(days=days_after_scan)).isoformat()
    return pos


def _closed_pos(ticker="QQQ", direction="long", days_after_scan=2,
                scan_date="2026-04-26", pnl=140.0) -> Position:
    pos = _open_pos(ticker=ticker, direction=direction,
                    days_after_scan=days_after_scan, scan_date=scan_date)
    pos.close(pnl_usd=pnl, notes="took profits")
    return pos


def _scan_payload(asset="QQQ", direction="long", recommendation="trade",
                  has_setup=True):
    setups = []
    if has_setup:
        setups.append({
            "asset": asset, "direction": direction, "score": 75,
            "status": "fires", "components": {}, "blockers": [],
        })
    return {
        "scan_time_utc": "2026-04-26T14:00:00+00:00",
        "spy": None, "qqq": None, "gld": None,
        "setups": setups, "recommendation": recommendation,
        "headline": "test", "errors": {},
    }


# ─────────────────────────────────────────────────────────────────────────
# find_matched_positions
# ─────────────────────────────────────────────────────────────────────────

def test_find_matched_returns_empty_when_no_top_setup():
    matched = find_matched_positions(
        "2026-04-26", None, [_open_pos()],
    )
    assert matched == []


def test_find_matched_inside_window():
    pos = _open_pos(days_after_scan=3)  # 3 days after, well within 7-day window
    matched = find_matched_positions(
        "2026-04-26", {"asset": "QQQ", "direction": "long"}, [pos],
    )
    assert matched == [pos]


def test_find_matched_excludes_outside_window():
    too_late = _open_pos(days_after_scan=10)  # past 7-day window
    too_early = _open_pos(days_after_scan=-3)  # before scan
    in_window = _open_pos(days_after_scan=2)
    matched = find_matched_positions(
        "2026-04-26", {"asset": "QQQ", "direction": "long"},
        [too_late, too_early, in_window],
    )
    assert matched == [in_window]


def test_find_matched_filters_by_ticker_and_direction():
    qqq_long = _open_pos(ticker="QQQ", direction="long")
    qqq_short = _open_pos(ticker="QQQ", direction="short")
    gld_long = _open_pos(ticker="GLD", direction="long")
    matched = find_matched_positions(
        "2026-04-26", {"asset": "QQQ", "direction": "long"},
        [qqq_long, qqq_short, gld_long],
    )
    assert matched == [qqq_long]


def test_find_matched_bearish_long_put_matches_short_setup():
    # A bearish long put is stored direction='long', instrument='put' (thesis
    # bearish). It must match a 'short' (bearish) scan setup — matching by THESIS,
    # not the raw stored contract direction (which is always 'long' for options).
    long_put = _open_pos(ticker="QQQ", direction="long", instrument="put")
    long_call = _open_pos(ticker="QQQ", direction="long", instrument="call")  # bullish
    matched = find_matched_positions(
        "2026-04-26", {"asset": "QQQ", "direction": "short"},
        [long_put, long_call],
    )
    assert matched == [long_put]


def test_find_matched_returns_empty_for_invalid_scan_date():
    matched = find_matched_positions(
        "not-a-date", {"asset": "QQQ", "direction": "long"}, [_open_pos()],
    )
    assert matched == []


def test_find_matched_custom_window():
    far_position = _open_pos(days_after_scan=14)
    matched = find_matched_positions(
        "2026-04-26", {"asset": "QQQ", "direction": "long"}, [far_position],
        window_days=21,
    )
    assert matched == [far_position]


# ─────────────────────────────────────────────────────────────────────────
# build_outcome
# ─────────────────────────────────────────────────────────────────────────

def test_outcome_skipped_when_recommendation_not_followed():
    payload = _scan_payload()
    outcome = build_outcome("2026-04-26", payload, [])
    assert outcome.followed is False
    assert outcome.aggregate_status == "skipped"
    assert outcome.matched == []


def test_outcome_no_recommendation_for_cash_week():
    payload = _scan_payload(recommendation="cash", has_setup=False)
    outcome = build_outcome("2026-04-26", payload, [_open_pos()])
    assert outcome.followed is False
    assert outcome.aggregate_status == "no_recommendation"


def test_outcome_open_when_position_still_running():
    payload = _scan_payload()
    pos = _open_pos()
    outcome = build_outcome("2026-04-26", payload, [pos])
    assert outcome.followed is True
    assert outcome.aggregate_status == "open"
    assert outcome.open_count == 1
    assert outcome.closed_count == 0
    assert outcome.realized_pnl_usd == 0.0


def test_outcome_closed_winner():
    payload = _scan_payload()
    pos = _closed_pos(pnl=140.0)
    outcome = build_outcome("2026-04-26", payload, [pos])
    assert outcome.aggregate_status == "closed_winner"
    assert outcome.realized_pnl_usd == 140.0
    assert outcome.closed_count == 1


def test_outcome_closed_loser():
    payload = _scan_payload()
    pos = _closed_pos(pnl=-90.0)
    outcome = build_outcome("2026-04-26", payload, [pos])
    assert outcome.aggregate_status == "closed_loser"
    assert outcome.realized_pnl_usd == -90.0


def test_outcome_mixed_when_some_open_some_closed():
    payload = _scan_payload()
    pos_open = _open_pos(days_after_scan=1)
    pos_closed = _closed_pos(days_after_scan=2, pnl=140.0)
    outcome = build_outcome("2026-04-26", payload, [pos_open, pos_closed])
    assert outcome.aggregate_status == "mixed"
    assert outcome.open_count == 1
    assert outcome.closed_count == 1
    assert outcome.realized_pnl_usd == 140.0


def test_outcome_window_default_is_seven_days():
    assert DEFAULT_WINDOW_DAYS == 7


def test_outcome_to_dict_serializable():
    payload = _scan_payload()
    pos = _closed_pos(pnl=140.0)
    outcome = build_outcome("2026-04-26", payload, [pos])
    d = outcome.to_dict()
    assert d["scan_date"] == "2026-04-26"
    assert d["matched"][0]["ticker"] == "QQQ"
    assert d["realized_pnl_usd"] == 140.0


# ─────────────────────────────────────────────────────────────────────────
# summarize_recent_outcomes
# ─────────────────────────────────────────────────────────────────────────

def _write_scan(dir_: Path, scan_date: str, recommendation: str = "trade",
                top_setup: dict | None = None) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    payload = {
        "scan_time_utc": f"{scan_date}T14:00:00+00:00",
        "spy": None, "qqq": None, "gld": None,
        "setups": [top_setup] if top_setup else [],
        "recommendation": recommendation,
        "headline": "h", "errors": {},
    }
    (dir_ / f"{scan_date}.json").write_text(json.dumps(payload))


def test_summary_empty_when_no_scans(tmp_path: Path):
    summary = summarize_recent_outcomes(
        weeks=4, positions=[], sunday_scans_dir=tmp_path,
    )
    assert summary.scans_count == 0
    assert summary.realized_pnl_usd == 0.0


def test_summary_counts_recommendation_types(tmp_path: Path):
    today = date(2026, 4, 26)
    _write_scan(tmp_path, "2026-04-26", recommendation="trade",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 75, "status": "fires"})
    _write_scan(tmp_path, "2026-04-19", recommendation="cash")
    _write_scan(tmp_path, "2026-04-12", recommendation="watch")

    summary = summarize_recent_outcomes(
        weeks=4, positions=[], sunday_scans_dir=tmp_path, today=today,
    )
    assert summary.scans_count == 3
    assert summary.trade_recs == 1
    assert summary.watch_recs == 1
    assert summary.cash_recs == 1


def test_summary_window_excludes_old_scans(tmp_path: Path):
    today = date(2026, 4, 26)
    # 2 weeks ago = in window
    _write_scan(tmp_path, "2026-04-12", recommendation="trade",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 75, "status": "fires"})
    # 6 weeks ago = outside 4-week window
    _write_scan(tmp_path, "2026-03-15", recommendation="trade",
                top_setup={"asset": "GLD", "direction": "long",
                           "score": 70, "status": "fires"})

    summary = summarize_recent_outcomes(
        weeks=4, positions=[], sunday_scans_dir=tmp_path, today=today,
    )
    assert summary.scans_count == 1
    assert summary.trade_recs == 1


def test_summary_aggregates_realized_pnl_and_follows(tmp_path: Path):
    today = date(2026, 4, 26)
    _write_scan(tmp_path, "2026-04-19", recommendation="trade",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 75, "status": "fires"})
    _write_scan(tmp_path, "2026-04-12", recommendation="trade",
                top_setup={"asset": "GLD", "direction": "long",
                           "score": 70, "status": "fires"})

    qqq_winner = _closed_pos(ticker="QQQ", direction="long",
                             scan_date="2026-04-19", days_after_scan=2,
                             pnl=140.0)
    gld_loser = _closed_pos(ticker="GLD", direction="long",
                            scan_date="2026-04-12", days_after_scan=2,
                            pnl=-90.0)

    summary = summarize_recent_outcomes(
        weeks=4, positions=[qqq_winner, gld_loser],
        sunday_scans_dir=tmp_path, today=today,
    )
    assert summary.followed_count == 2
    assert summary.skipped_count == 0
    assert summary.realized_pnl_usd == 50.0  # 140 - 90


def test_summary_counts_skipped_trade_recs(tmp_path: Path):
    today = date(2026, 4, 26)
    _write_scan(tmp_path, "2026-04-19", recommendation="trade",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 75, "status": "fires"})
    # No matching position → skipped
    summary = summarize_recent_outcomes(
        weeks=4, positions=[], sunday_scans_dir=tmp_path, today=today,
    )
    assert summary.followed_count == 0
    assert summary.skipped_count == 1


def test_summary_does_not_count_cash_weeks_as_skipped(tmp_path: Path):
    today = date(2026, 4, 26)
    # Cash week with a blocked top_setup (typical when nothing fires)
    _write_scan(tmp_path, "2026-04-19", recommendation="cash",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 25, "status": "blocked"})
    summary = summarize_recent_outcomes(
        weeks=4, positions=[], sunday_scans_dir=tmp_path, today=today,
    )
    assert summary.scans_count == 1
    assert summary.cash_recs == 1
    assert summary.skipped_count == 0  # cash weeks are correct behavior
    assert summary.followed_count == 0


def test_summary_counts_open_positions(tmp_path: Path):
    today = date(2026, 4, 26)
    _write_scan(tmp_path, "2026-04-23", recommendation="trade",
                top_setup={"asset": "QQQ", "direction": "long",
                           "score": 75, "status": "fires"})
    pos = _open_pos(ticker="QQQ", direction="long",
                    scan_date="2026-04-23", days_after_scan=1)
    summary = summarize_recent_outcomes(
        weeks=4, positions=[pos], sunday_scans_dir=tmp_path, today=today,
    )
    assert summary.open_count == 1
    assert summary.followed_count == 1
