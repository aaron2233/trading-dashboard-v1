"""Pyramid + Tranche dataclasses for the trend-pyramid module.

A Pyramid is a multi-tranche scaled entry on a single instrument. It always
has exactly three tranches (T1, T2, T3) deployed in equal thirds against a
total allocation. Each tranche is condition-gated — if its triggers never
fire, it never fills.

State machine:
    pending  →  active  →  completed   (all 3 tranches filled, exited cleanly)
                       →  stopped_out  (regime flip / MA break / trail stop)

Per-tranche state machine:
    pending  →  filled
             →  skipped   (gate flipped before this tranche could fire)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
import uuid


Direction = Literal["long", "short"]
PyramidStatus = Literal["pending", "active", "completed", "stopped_out"]
TrancheStatus = Literal["pending", "filled", "skipped"]
Vehicle = Literal["shares", "leaps_call", "leaps_put", "barbell", "etf"]


@dataclass
class Tranche:
    """One of three tranches in a pyramid. Tranche 1 = initial entry,
    Tranche 2 = retest confirmation, Tranche 3 = continuation breakout."""

    id: int  # 1, 2, or 3
    target_pct: float = 1.0 / 3.0  # equal thirds by default
    status: TrancheStatus = "pending"

    # Populated when status == "filled":
    filled_date: str | None = None         # ISO date
    vehicle: Vehicle | None = None
    cost_basis_per_unit: float | None = None  # share price OR option premium per contract
    quantity: int | None = None            # shares or option contracts
    strike: float | None = None            # for options
    expiry: str | None = None              # for options, ISO date
    notes: str | None = None

    # Structure snapshot at fill time (v2 addition). Captured so the T3
    # evaluator can compare "new swing high since T1 fill" against an exact
    # reference instead of the looser recent vs. prior swing comparison.
    # Both fields nullable so legacy persisted tranches still deserialize and
    # callers that don't want to capture structure (e.g. retroactive fills)
    # can leave them None — the T3 evaluator falls back to the structure read.
    swing_high_at_fill: float | None = None
    swing_low_at_fill: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Tranche":
        return cls(**d)

    def fill(
        self,
        vehicle: Vehicle,
        cost_basis_per_unit: float,
        quantity: int,
        *,
        strike: float | None = None,
        expiry: str | None = None,
        filled_date: str | None = None,
        notes: str | None = None,
        swing_high_at_fill: float | None = None,
        swing_low_at_fill: float | None = None,
    ) -> None:
        if self.status == "filled":
            raise ValueError(f"Tranche {self.id} already filled")
        if self.status == "skipped":
            raise ValueError(f"Tranche {self.id} was skipped")
        self.status = "filled"
        self.vehicle = vehicle
        self.cost_basis_per_unit = float(cost_basis_per_unit)
        self.quantity = int(quantity)
        self.strike = strike
        self.expiry = expiry
        self.filled_date = filled_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if notes is not None:
            self.notes = notes
        if swing_high_at_fill is not None:
            self.swing_high_at_fill = float(swing_high_at_fill)
        if swing_low_at_fill is not None:
            self.swing_low_at_fill = float(swing_low_at_fill)

    def total_cost_usd(self) -> float | None:
        if self.cost_basis_per_unit is None or self.quantity is None:
            return None
        # Options: premium is per share, 1 contract = 100 shares
        if self.vehicle in ("leaps_call", "leaps_put"):
            return self.cost_basis_per_unit * self.quantity * 100
        return self.cost_basis_per_unit * self.quantity


@dataclass
class Pyramid:
    """A scaled entry trade with three tranches deployed over time."""

    id: str
    ticker: str
    direction: Direction
    benchmark: str = "SPY"
    total_allocation_usd: float = 0.0
    horizon: str = "6-18 months"
    status: PyramidStatus = "pending"
    created_date: str = ""
    closed_date: str | None = None
    tranches: list[Tranche] = field(default_factory=lambda: [
        Tranche(id=1), Tranche(id=2), Tranche(id=3),
    ])
    aggregate_pnl_usd: float | None = None
    notes: str | None = None

    @classmethod
    def create(
        cls,
        ticker: str,
        direction: Direction,
        total_allocation_usd: float,
        *,
        benchmark: str = "SPY",
        horizon: str = "6-18 months",
        notes: str | None = None,
    ) -> "Pyramid":
        if direction not in ("long", "short"):
            raise ValueError(f"direction must be 'long' or 'short', got {direction!r}")
        if total_allocation_usd < 0:
            raise ValueError("total_allocation_usd must be non-negative")
        return cls(
            id=str(uuid.uuid4())[:12],
            ticker=ticker.upper(),
            direction=direction,
            benchmark=benchmark.upper(),
            total_allocation_usd=float(total_allocation_usd),
            horizon=horizon,
            status="pending",
            created_date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            notes=notes,
        )

    def get_tranche(self, tranche_id: int) -> Tranche:
        for t in self.tranches:
            if t.id == tranche_id:
                return t
        raise KeyError(f"No tranche with id={tranche_id}")

    def filled_tranches(self) -> list[Tranche]:
        return [t for t in self.tranches if t.status == "filled"]

    def next_pending_tranche(self) -> Tranche | None:
        for t in sorted(self.tranches, key=lambda x: x.id):
            if t.status == "pending":
                return t
        return None

    def total_filled_cost_usd(self) -> float:
        return sum(
            t.total_cost_usd() or 0.0 for t in self.filled_tranches()
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **{k: v for k, v in asdict(self).items() if k != "tranches"},
            "tranches": [t.to_dict() for t in self.tranches],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Pyramid":
        d = dict(d)
        tranches_raw = d.pop("tranches", [])
        tranches = [Tranche.from_dict(t) for t in tranches_raw]
        return cls(tranches=tranches, **d)
