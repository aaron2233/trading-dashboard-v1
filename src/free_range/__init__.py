"""Free-range scanner — 3-phase candidate surfacing per orchestrator rule 12.

See ~/CLAUDE.md "Free-Range Scan" section and SESSION-HANDOFF-2026-05-02.md
for sprint context.
"""
from free_range.filters import (
    FREE_RANGE_MIN_SCORE,
    PRICE_MAX_SINGLE_STOCK,
    PRICE_MIN_SINGLE_STOCK,
    best_direction,
    build_why_now,
    price_band_violation,
    score_direction,
)
from free_range.scanner import (
    BASELINE_TICKERS,
    build_snapshot,
    run_free_range_scan,
)
from free_range.snapshot import CandidateSnapshot, FreeRangeScan
from free_range.universe import (
    KNOWN_ETFS,
    NASDAQ_100,
    NASDAQ_100_SNAPSHOT_DATE,
    free_range_universe,
    is_etf,
)

__all__ = [
    "BASELINE_TICKERS",
    "CandidateSnapshot",
    "FREE_RANGE_MIN_SCORE",
    "FreeRangeScan",
    "KNOWN_ETFS",
    "NASDAQ_100",
    "NASDAQ_100_SNAPSHOT_DATE",
    "PRICE_MAX_SINGLE_STOCK",
    "PRICE_MIN_SINGLE_STOCK",
    "best_direction",
    "build_snapshot",
    "build_why_now",
    "free_range_universe",
    "is_etf",
    "price_band_violation",
    "run_free_range_scan",
    "score_direction",
]
