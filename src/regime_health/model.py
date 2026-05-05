"""Dataclasses for the Regime Health snapshot."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


IndicatorStatus = Literal["green", "amber", "red", "unknown", "error"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@dataclass
class IndicatorReading:
    """One indicator read — value + traffic-light status + display metadata."""
    indicator_id: str            # e.g. "spy_sqn_100"
    label: str                   # e.g. "SPY SQN(100)"
    tier: int                    # 1 | 2 | 3 | 4
    status: IndicatorStatus
    # Raw value (numeric for FRED + market data; string for categorical
    # indicators like the MA stack state). None when unknown/error.
    value: float | str | None = None
    formatted_value: str = "—"
    threshold_note: str = ""     # human-readable threshold rule
    source: str = ""             # "yfinance" | "scan_ticker" | "fred" | "manual"
    error: str | None = None
    fetched_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TierBundle:
    tier: int
    label: str                   # "Structural & Volatility"
    readings: list[IndicatorReading] = field(default_factory=list)
    error: str | None = None     # tier-level fatal error (e.g. FRED unavailable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "label": self.label,
            "readings": [r.to_dict() for r in self.readings],
            "error": self.error,
        }


@dataclass
class RegimeHealthSnapshot:
    """Aggregate snapshot — Tier 1-4 bundles + overall status.

    Overall status is the worst non-unknown reading across Tier 1 + Tier 2.
    Tier 3 (breadth) is informational; Tier 4 (capex calendar) is a leading
    signal but not part of the structural-regime gate.
    """
    snapshot_date: str           # YYYY-MM-DD (calendar date the snapshot represents)
    fetched_at: str              # ISO timestamp of assembly
    overall_status: IndicatorStatus
    tiers: list[TierBundle] = field(default_factory=list)
    # Tier-1/2 contributions to overall_status, in case the UI wants to
    # explain "why amber" without re-walking every reading.
    overall_drivers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_date": self.snapshot_date,
            "fetched_at": self.fetched_at,
            "overall_status": self.overall_status,
            "tiers": [t.to_dict() for t in self.tiers],
            "overall_drivers": list(self.overall_drivers),
        }

    @classmethod
    def empty(cls, snapshot_date: str | None = None) -> "RegimeHealthSnapshot":
        """Build a placeholder snapshot — used when a tier fully fails on
        first cold call so the API still returns a well-shaped response."""
        from datetime import date
        return cls(
            snapshot_date=snapshot_date or date.today().isoformat(),
            fetched_at=_now_iso(),
            overall_status="unknown",
            tiers=[],
            overall_drivers=[],
        )


# Status precedence used by the snapshot assembler. Higher = "worse" — the
# overall status is the maximum precedence across Tier 1 + Tier 2 readings.
_STATUS_PRECEDENCE: dict[IndicatorStatus, int] = {
    "green": 0,
    "unknown": 0,    # ignored for overall — fail-open
    "error": 0,      # ignored for overall — fail-open
    "amber": 1,
    "red": 2,
}


def worst_status(*statuses: IndicatorStatus) -> IndicatorStatus:
    """Return the worst status (red > amber > green). unknown/error fail-open."""
    if not statuses:
        return "unknown"
    ranked = max(statuses, key=lambda s: _STATUS_PRECEDENCE.get(s, 0))
    if _STATUS_PRECEDENCE.get(ranked, 0) == 0:
        # Every status was green / unknown / error. Prefer "green" if any
        # are green (signal: at least something is healthy); else "unknown".
        if "green" in statuses:
            return "green"
        return "unknown"
    return ranked
