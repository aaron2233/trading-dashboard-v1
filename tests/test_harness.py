import pandas as pd
import pytest

from testing.accuracy_harness import check_accuracy


def _df(values: dict, dates: list[str]) -> pd.DataFrame:
    return pd.DataFrame(values, index=pd.to_datetime(dates))


def test_perfect_match_passes():
    expected = _df({"value": [100.0, 101.0, 102.0]},
                   ["2026-01-02", "2026-01-03", "2026-01-06"])
    actual = expected.copy()
    result = check_accuracy(expected, actual)
    assert result.pass_rate == 1.0
    assert result.ok(0.95)


def test_small_delta_within_tolerance():
    expected = _df({"value": [100.0]}, ["2026-01-02"])
    actual = _df({"value": [100.5]}, ["2026-01-02"])
    result = check_accuracy(expected, actual, numeric_tolerance=0.01)
    assert result.ok(0.95)


def test_large_delta_fails():
    expected = _df({"value": [100.0]}, ["2026-01-02"])
    actual = _df({"value": [105.0]}, ["2026-01-02"])
    result = check_accuracy(expected, actual, numeric_tolerance=0.01)
    assert not result.ok(0.95)
    assert len(result.failing_rows) == 1
    assert result.failing_rows[0]["value"]["delta_pct"] == pytest.approx(0.05)


def test_categorical_column_exact_match():
    expected = _df({"state": ["full_bull", "full_bear"]},
                   ["2026-01-02", "2026-01-03"])
    actual = _df({"state": ["full_bull", "full_bull"]},
                 ["2026-01-02", "2026-01-03"])
    result = check_accuracy(expected, actual, categorical_columns=["state"])
    assert result.rows_checked == 2
    assert result.rows_passed == 1


def test_no_overlap_raises():
    expected = _df({"value": [100.0]}, ["2026-01-02"])
    actual = _df({"value": [100.0]}, ["2026-02-02"])
    with pytest.raises(ValueError):
        check_accuracy(expected, actual)


def test_missing_actual_column_fails_row():
    expected = _df({"a": [1.0], "b": [2.0]}, ["2026-01-02"])
    actual = _df({"a": [1.0]}, ["2026-01-02"])
    result = check_accuracy(expected, actual)
    assert result.rows_passed == 0
    assert "b" in result.failing_rows[0]


def test_both_nan_counts_as_match():
    expected = _df({"value": [float("nan")]}, ["2026-01-02"])
    actual = _df({"value": [float("nan")]}, ["2026-01-02"])
    result = check_accuracy(expected, actual)
    assert result.rows_passed == 1


def test_partial_overlap_only_checks_common_dates():
    expected = _df({"value": [100.0, 101.0, 102.0]},
                   ["2026-01-02", "2026-01-03", "2026-01-06"])
    actual = _df({"value": [101.0, 102.0]},
                 ["2026-01-03", "2026-01-06"])
    result = check_accuracy(expected, actual)
    assert result.rows_checked == 2
    assert result.rows_passed == 2


def test_report_includes_pass_rate_and_failing_rows():
    expected = _df({"value": [100.0, 100.0]}, ["2026-01-02", "2026-01-03"])
    actual = _df({"value": [100.0, 110.0]}, ["2026-01-02", "2026-01-03"])
    result = check_accuracy(expected, actual, numeric_tolerance=0.01)
    report = result.report()
    assert "1/2 rows within tolerance" in report
    assert "50.0%" in report
    assert "Failing rows" in report
