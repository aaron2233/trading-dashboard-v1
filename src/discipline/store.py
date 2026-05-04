"""Discipline-score persistence.

Per DISCIPLINE-LAYER-ADDITION.md:
    ~/.trading-dashboard/discipline/<position_id>.json

Legacy positions (closed before 2026-05-02) are exempt from scoring per the
spec's open-question 1 recommendation. The store accepts any score; the
caller (CLI/API) enforces legacy exemption when appropriate.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from discipline.model import DisciplineScore, WeeklyReview


DEFAULT_DISCIPLINE_DIR = Path.home() / ".trading-dashboard" / "discipline"
WEEKLY_SUBDIR = "weekly"

# Trades closed BEFORE this date are exempt from discipline scoring.
LEGACY_CUTOFF: date = date(2026, 5, 2)


def is_legacy_position(closed_date_iso: str | None) -> bool:
    """Return True if the position closed before the discipline-layer rollout."""
    if not closed_date_iso:
        return False
    try:
        closed = datetime.fromisoformat(closed_date_iso.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            closed = datetime.strptime(closed_date_iso, "%Y-%m-%d").date()
        except ValueError:
            return False
    return closed < LEGACY_CUTOFF


class DisciplineStore:
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or DEFAULT_DISCIPLINE_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)
        (self.base_dir / WEEKLY_SUBDIR).mkdir(parents=True, exist_ok=True)

    # ── Per-trade scores ────────────────────────────────────────────────────

    def _score_path(self, position_id: str) -> Path:
        return self.base_dir / f"{position_id}.json"

    def save_score(self, score: DisciplineScore) -> Path:
        path = self._score_path(score.position_id)
        path.write_text(json.dumps(score.to_dict(), indent=2, default=str))
        return path

    def load_score(self, position_id: str) -> DisciplineScore:
        path = self._score_path(position_id)
        if not path.exists():
            raise KeyError(f"No discipline score for position_id={position_id}")
        return DisciplineScore.from_dict(json.loads(path.read_text()))

    def has_score(self, position_id: str) -> bool:
        return self._score_path(position_id).exists()

    def delete_score(self, position_id: str) -> bool:
        path = self._score_path(position_id)
        if not path.exists():
            return False
        path.unlink()
        return True

    def iter_scores(self) -> Iterable[DisciplineScore]:
        for path in sorted(self.base_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                yield DisciplineScore.from_dict(data)
            except (json.JSONDecodeError, OSError, TypeError, ValueError):
                continue

    def list_scores(self) -> list[DisciplineScore]:
        return list(self.iter_scores())

    # ── Weekly reviews ──────────────────────────────────────────────────────

    def _weekly_path(self, week_start: str) -> Path:
        return self.base_dir / WEEKLY_SUBDIR / f"{week_start}.json"

    def save_weekly(self, review: WeeklyReview) -> Path:
        path = self._weekly_path(review.week_start)
        path.write_text(json.dumps(review.to_dict(), indent=2, default=str))
        return path

    def load_weekly(self, week_start: str) -> WeeklyReview | None:
        path = self._weekly_path(week_start)
        if not path.exists():
            return None
        return WeeklyReview.from_dict(json.loads(path.read_text()))

    def update_lockdown(self, week_start: str, lockdown_behavior: str) -> WeeklyReview:
        review = self.load_weekly(week_start)
        if review is None:
            raise KeyError(f"No saved weekly review for {week_start}")
        review.lockdown_behavior = lockdown_behavior
        self.save_weekly(review)
        return review
