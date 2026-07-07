"""Tests for scripts/publish_results.py — the cloud-data freshness contract.

The routine drafts from these files; the contract under test:
  - STATUS discriminates OK / NO_SETUPS / SKIPPED / FAILED
  - anything unrecognized (crash, empty stdout, nonzero exit) is FAILED,
    never "no actionable setups"
  - ACTIONABLE=YES for OK and FAILED (failures must draft a notice)
  - write_result emits the 4 header lines + --- + body
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "publish_results.py"
_spec = importlib.util.spec_from_file_location("publish_results", _SCRIPT)
publish_results = importlib.util.module_from_spec(_spec)
sys.modules["publish_results"] = publish_results
_spec.loader.exec_module(publish_results)

classify_lotto = publish_results.classify_lotto
write_result = publish_results.write_result
index_swing_section = publish_results.index_swing_section


def test_classify_ok():
    assert classify_lotto("# Lotto Scan — 2026-07-02 08:31 PDT\nbody", 0) == "OK"


def test_classify_no_setups():
    out = "LOTTO SCAN — 2026-07-02 08:31 PDT: no actionable setups this window."
    assert classify_lotto(out, 0) == "NO_SETUPS"


def test_classify_skipped():
    out = "LOTTO SCAN — 2026-07-04 08:31 PDT: market closed or no fresh 2H bar (latest 2H bar: ...), skipping."
    assert classify_lotto(out, 0) == "SKIPPED"


def test_classify_data_failed_is_failed():
    out = "LOTTO SCAN — 2026-07-02 08:31 PDT: DATA FETCH FAILED (40 tickers errored, 0 ok)"
    assert classify_lotto(out, 0) == "FAILED"


def test_classify_crash_traceback_is_failed():
    assert classify_lotto("Traceback (most recent call last): ...", 1) == "FAILED"


def test_classify_empty_output_is_failed():
    assert classify_lotto("", 0) == "FAILED"


def test_classify_nonzero_exit_is_failed_even_with_ok_header():
    assert classify_lotto("# Lotto Scan — partial then crashed", 1) == "FAILED"


def test_index_swing_high_conviction_is_actionable():
    out = "INDEX-SWING 2H: HIGH-CONVICTION BREAKOUT\n  QQQ: breakout_high_conviction close 722.82"
    section, actionable, subject = index_swing_section(out, 0)
    assert actionable is True
    assert subject == "Index-swing 2H -- HIGH-CONVICTION BREAKOUT"
    assert "breakout_high_conviction" in section


def test_index_swing_quiet_is_not_actionable():
    section, actionable, subject = index_swing_section(
        "INDEX-SWING 2H: quiet\n  QQQ: no_breakout close 722.82", 0)
    assert actionable is False
    assert subject is None
    assert "no_breakout" in section


def test_index_swing_standard_breakout_is_info_only():
    section, actionable, subject = index_swing_section(
        "INDEX-SWING 2H: standard breakout (info only)\n  SPY: breakout_standard", 0)
    assert actionable is False
    assert subject is None


def test_index_swing_crash_fails_loud():
    section, actionable, subject = index_swing_section("Traceback ...", 1)
    assert actionable is True
    assert "MONITOR FAILED" in section
    assert subject == "Index-swing 2H -- MONITOR FAILED (needs attention)"


def test_index_swing_empty_output_fails_loud():
    _, actionable, subject = index_swing_section("", 0)
    assert actionable is True
    assert subject is not None


def test_write_result_contract(tmp_path):
    p = tmp_path / "lotto_result.md"
    write_result(p, "2026-07-02T15:31:00Z", True, "OK",
                 "Lotto 2H scan — 08:31 PT", "body text")
    lines = p.read_text().splitlines()
    assert lines[0] == "GENERATED: 2026-07-02T15:31:00Z"
    assert lines[1] == "ACTIONABLE: YES"
    assert lines[2] == "STATUS: OK"
    assert lines[3] == "SUBJECT: Lotto 2H scan — 08:31 PT"
    assert lines[4] == "---"
    assert lines[5] == "body text"
