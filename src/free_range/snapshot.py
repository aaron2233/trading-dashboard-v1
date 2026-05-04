"""Free-range candidate snapshot: brief setup card, NOT a kill sheet.

Per orchestrator rule 12 in ~/CLAUDE.md:

    Output format: Brief setup snapshot per candidate — ticker, tier tag,
    current price, MA stack (weekly for Tier 1 / daily for Tier 2), Stoch
    state, IV note, 1-line "why now." Not a full kill sheet — kill sheets
    only generate when the user picks a candidate to actually deploy.

Snapshots are produced from existing scan_ticker() rows + filter results, so
they share the same indicator vocabulary as the rest of the dashboard.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


# Phase tags identify which step of the 3-phase scan produced the snapshot.
# Frontend groups results by phase. "user" comes from explicit user input;
# "free_range" comes from the universe scan.
Phase = Literal["baseline", "user", "free_range"]

# Direction is derived from regime alignment + indicator stack — long when
# bullish stack + supportive regime; short on the inverse. "neutral" means
# the candidate passed price/liquidity but has no clear directional bias
# (rare in the snapshot context — usually filtered out).
Direction = Literal["long", "short"]


@dataclass
class CandidateSnapshot:
    """A single setup candidate from any phase of the free-range scan.

    Snapshots cover price action and indicator alignment ONLY. Options
    liquidity, IV rank, premium, and strike are entered at the kill-sheet
    layer from the user's brokerage (manual paste or screenshot extract) —
    yfinance options data is stale relative to brokerage feeds and would
    smuggle bad data into a discipline-engine claim if auto-gated here.
    """

    ticker: str
    phase: Phase
    tier: str                   # "1" | "2" | "1+2" — string so "1+2" reads cleanly in JSON
    direction: Direction
    is_etf: bool
    current_price: float | None
    ma_stack: str | None
    stoch_zone: str | None
    stoch_signal: str | None
    sqn_100_regime: str | None
    sqn_20_regime: str | None
    score: int                  # additive heuristic, used to rank the free-range top-5
    why_now: str                # one-line trigger summary
    notes: list[str] = field(default_factory=list)  # filter caveats, e.g. "ETF — price band exempt"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FreeRangeScan:
    """Container for a full 3-phase scan result."""

    scan_time_utc: str
    baseline: list[CandidateSnapshot] = field(default_factory=list)
    user_submitted: list[CandidateSnapshot] = field(default_factory=list)
    free_range: list[CandidateSnapshot] = field(default_factory=list)
    universe_size: int = 0          # total tickers considered for free-range phase
    free_range_cap: int = 5         # hard cap per orchestrator rule 12
    notes: list[str] = field(default_factory=list)  # scan-level messages, e.g. "fewer than 5 candidates passed"
    errors: dict[str, str] = field(default_factory=dict)  # ticker → error string

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time_utc": self.scan_time_utc,
            "baseline": [s.to_dict() for s in self.baseline],
            "user_submitted": [s.to_dict() for s in self.user_submitted],
            "free_range": [s.to_dict() for s in self.free_range],
            "universe_size": self.universe_size,
            "free_range_cap": self.free_range_cap,
            "notes": list(self.notes),
            "errors": dict(self.errors),
        }
