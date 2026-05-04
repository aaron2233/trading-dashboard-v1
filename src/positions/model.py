"""Position data model.

Tracks one open or closed trade across its lifecycle. Persisted as JSON in
~/.trading-dashboard/positions.json by PositionStore.
"""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class Position:
    id: str = field(default_factory=_new_id)
    ticker: str = ""
    direction: str = "long"        # long | short
    instrument: str = "call"       # call | put | shares
    account_key: str = "main"

    # Entry
    entry_date: str = field(default_factory=_now_iso)
    entry_underlying_price: float | None = None
    contracts: int | None = None
    shares: int | None = None
    strike: float | None = None
    expiry: str | None = None
    premium_paid_per_contract: float | None = None

    # Risk profile at entry
    total_cost_usd: float = 0.0
    max_loss_usd: float = 0.0
    target_price: float | None = None
    invalidation_price: float | None = None

    # Lifecycle
    status: str = "open"             # open | closed
    closed_date: str | None = None
    pnl_usd: float | None = None
    notes: str | None = None

    # Skill / tier tagging (Sprint B polish, 2026-05-02 — orchestrator
    # rule 11 needs to filter the QQQ/GLD portfolio cap by tier). Nullable
    # by design: legacy positions stay None, no migration. New positions
    # populate via OpenPositionRequest.skill / .tier. tier_portfolio_rules
    # treats None as "in scope of Tier 1+2 cap" (conservative — no false
    # negatives on legacy data).
    skill: str | None = None
    tier: int | None = None

    @classmethod
    def open_options_position(
        cls,
        ticker: str,
        direction: str,
        contract_type: str,
        account_key: str,
        strike: float,
        expiry: str,
        premium: float,
        contracts: int,
        underlying_price: float | None = None,
        target_price: float | None = None,
        invalidation_price: float | None = None,
        notes: str | None = None,
        skill: str | None = None,
        tier: int | None = None,
    ) -> "Position":
        if contracts <= 0:
            raise ValueError("contracts must be positive")
        if premium <= 0:
            raise ValueError("premium must be positive")
        cost = premium * 100.0 * contracts
        return cls(
            ticker=ticker.upper(),
            direction=direction.lower(),
            instrument=contract_type.lower(),
            account_key=account_key,
            entry_underlying_price=underlying_price,
            contracts=contracts,
            strike=strike,
            expiry=expiry,
            premium_paid_per_contract=premium,
            total_cost_usd=cost,
            max_loss_usd=cost,    # for long options, max loss = premium paid
            target_price=target_price,
            invalidation_price=invalidation_price,
            notes=notes,
            skill=skill,
            tier=tier,
        )

    @classmethod
    def open_shares_position(
        cls,
        ticker: str,
        direction: str,
        account_key: str,
        shares: int,
        entry_price: float,
        invalidation_price: float,
        target_price: float | None = None,
        notes: str | None = None,
        skill: str | None = None,
        tier: int | None = None,
    ) -> "Position":
        if shares <= 0:
            raise ValueError("shares must be positive")
        if entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if direction.lower() == "long":
            stop_distance = entry_price - invalidation_price
        else:
            stop_distance = invalidation_price - entry_price
        if stop_distance <= 0:
            raise ValueError(
                "invalidation_price must be on the protective side of entry_price"
            )
        return cls(
            ticker=ticker.upper(),
            direction=direction.lower(),
            instrument="shares",
            account_key=account_key,
            entry_underlying_price=entry_price,
            shares=shares,
            total_cost_usd=shares * entry_price,
            max_loss_usd=shares * stop_distance,
            target_price=target_price,
            invalidation_price=invalidation_price,
            notes=notes,
            skill=skill,
            tier=tier,
        )

    def close(self, pnl_usd: float | None = None, notes: str | None = None) -> "Position":
        if self.status == "closed":
            raise ValueError(f"Position {self.id} is already closed")
        self.status = "closed"
        self.closed_date = _now_iso()
        if pnl_usd is not None:
            self.pnl_usd = pnl_usd
        if notes is not None:
            # Append a close note rather than overwriting entry notes
            self.notes = f"{self.notes}\nclose: {notes}" if self.notes else f"close: {notes}"
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "Position":
        return cls(**payload)
