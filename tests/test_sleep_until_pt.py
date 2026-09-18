"""Tests for scripts/sleep_until_pt.py — next-slot selection.

The publish workflow fires many crons and passes the full slot list; the
contract under test:
  - the run sleeps to the EARLIEST slot still ahead of it, not a cron-mapped one
  - a slot that already passed is skipped in favour of the next one
  - all slots passed -> proceed immediately (cloud-data must still advance)
  - a single target still works (lotto-scan.yml passes one)
  - the sanity cap still refuses absurd sleeps
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "sleep_until_pt.py"
_spec = importlib.util.spec_from_file_location("sleep_until_pt", _SCRIPT)
sleep_until_pt = importlib.util.module_from_spec(_spec)
sys.modules["sleep_until_pt"] = sleep_until_pt
_spec.loader.exec_module(sleep_until_pt)

choose_target = sleep_until_pt.choose_target
parse_targets = sleep_until_pt.parse_targets
PT = ZoneInfo("America/Los_Angeles")
SLOTS = parse_targets("05:40,08:31,10:31,12:31")


def at(hh, mm, ss=0):
    return datetime(2026, 9, 16, hh, mm, ss, tzinfo=PT)


def test_parse_single_and_list():
    assert parse_targets("08:31") == [(8, 31)]
    assert parse_targets("05:40, 12:31") == [(5, 40), (12, 31)]


def test_parse_rejects_junk():
    with pytest.raises(ValueError):
        parse_targets("25:00")
    with pytest.raises(ValueError):
        parse_targets("nope")


def test_sleeps_to_earliest_slot_ahead():
    remaining, msg = choose_target(at(6, 0), SLOTS)
    assert remaining == pytest.approx(2 * 3600 + 31 * 60)
    assert "until 08:31 PT" in msg


def test_skips_passed_slot_for_next_one():
    # The 2026-09-16 case: cron for 08:31 fired at 10:12 PT. Old script
    # published immediately (late); new one waits for 10:31.
    remaining, msg = choose_target(at(10, 12, 21), SLOTS)
    assert remaining == pytest.approx(18 * 60 + 39)
    assert "until 10:31 PT" in msg


def test_all_passed_proceeds_immediately():
    remaining, msg = choose_target(at(13, 21), SLOTS)
    assert remaining == 0
    assert "already passed" in msg


def test_single_target_behaves_like_before():
    assert choose_target(at(12, 10), [(12, 31)])[0] == pytest.approx(21 * 60)
    assert choose_target(at(12, 37), [(12, 31)])[0] == 0


def test_cap_refuses_absurd_sleep():
    remaining, msg = choose_target(at(0, 5), SLOTS)
    assert remaining == 0
    assert "refusing" in msg
