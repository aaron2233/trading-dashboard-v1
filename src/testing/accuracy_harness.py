from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class AccuracyResult:
    rows_checked: int
    rows_passed: int
    failing_rows: list[dict] = field(default_factory=list)

    @property
    def pass_rate(self) -> float:
        return self.rows_passed / self.rows_checked if self.rows_checked else 0.0

    def ok(self, threshold: float = 0.95) -> bool:
        return self.pass_rate >= threshold

    def report(self, sample: int = 5) -> str:
        lines = [
            f"{self.rows_passed}/{self.rows_checked} rows within tolerance "
            f"({self.pass_rate:.1%})"
        ]
        if self.failing_rows:
            shown = self.failing_rows[:sample]
            lines.append(f"Failing rows (first {len(shown)} of {len(self.failing_rows)}):")
            for row in shown:
                lines.append(f"  {row}")
        return "\n".join(lines)


def check_accuracy(
    expected: pd.DataFrame,
    actual: pd.DataFrame,
    numeric_tolerance: float = 0.01,
    categorical_columns: list[str] | None = None,
) -> AccuracyResult:
    categorical = set(categorical_columns or [])
    common = expected.index.intersection(actual.index)
    if len(common) == 0:
        raise ValueError(
            "No overlapping dates between expected and actual DataFrames"
        )

    rows_passed = 0
    failing_rows: list[dict] = []

    for date in common:
        exp_row = expected.loc[date]
        act_row = actual.loc[date]
        row_ok = True
        diffs: dict = {}

        for col in expected.columns:
            if col not in actual.columns:
                row_ok = False
                diffs[col] = "missing from actual"
                continue

            exp_val = exp_row[col]
            act_val = act_row[col]

            if col in categorical:
                if str(exp_val) != str(act_val):
                    row_ok = False
                    diffs[col] = {"expected": exp_val, "actual": act_val}
                continue

            exp_na = pd.isna(exp_val)
            act_na = pd.isna(act_val)
            if exp_na and act_na:
                continue
            if exp_na or act_na:
                row_ok = False
                diffs[col] = {"expected": exp_val, "actual": act_val}
                continue

            try:
                exp_f = float(exp_val)
                act_f = float(act_val)
            except (TypeError, ValueError):
                row_ok = False
                diffs[col] = "non-numeric value in numeric column"
                continue

            denom = abs(exp_f) if exp_f != 0.0 else 1.0
            rel = abs(act_f - exp_f) / denom
            if rel > numeric_tolerance:
                row_ok = False
                diffs[col] = {
                    "expected": exp_f,
                    "actual": act_f,
                    "delta_pct": round(rel, 6),
                }

        if row_ok:
            rows_passed += 1
        else:
            failing_rows.append({"date": str(date), **diffs})

    return AccuracyResult(
        rows_checked=len(common),
        rows_passed=rows_passed,
        failing_rows=failing_rows,
    )


def load_truth_csv(path: str | Path, date_column: str = "date") -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=[date_column])
    return df.set_index(date_column).sort_index()
