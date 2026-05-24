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
    shares: float | None = None
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

    # Greeks at entry (snapshot — not updated as the trade ages). Captured
    # so the journal has the actual data for after-the-fact analysis. All
    # nullable; legacy positions and shares positions leave them None.
    delta: float | None = None     # rate of premium change w/ underlying
    gamma: float | None = None     # rate of delta change
    theta: float | None = None     # daily premium decay (typically negative)
    vega: float | None = None      # premium change per 1% IV change
    iv: float | None = None        # implied volatility at entry, decimal (0.45 = 45%)
    iv_rank: float | None = None   # IVR percentile 0-100

    # Premium-level exit thresholds — separate from underlying-price target /
    # invalidation. Per CLAUDE.md cut rule (-60 to -70% max loss). Live
    # premium-feed alerts are out of scope for V1 (no real-time options data
    # source); these fields persist the user's intent for audit + future use.
    premium_stop: float | None = None     # exit when premium drops to this $/share
    premium_target: float | None = None   # take profit when premium rises to this $/share

    # Phase B (authorization gate, 2026-05-04): every non-bypassed position
    # references the kill sheet that authorized it. Nullable for legacy
    # positions and for explicit bypasses (recorded with reason in notes).
    kill_sheet_id: str | None = None

    # Partial exits — appended each time the user scales out of an options
    # position. When all contracts are eventually closed, status flips to
    # "closed" and pnl_usd holds the aggregate of every leg's pnl.
    # Each entry: {date, contracts_closed, pnl_usd, notes}.
    partial_exits: list[dict] = field(default_factory=list)

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
        # Greeks/IV snapshot (all optional)
        delta: float | None = None,
        gamma: float | None = None,
        theta: float | None = None,
        vega: float | None = None,
        iv: float | None = None,
        iv_rank: float | None = None,
        # Premium-level thresholds
        premium_stop: float | None = None,
        premium_target: float | None = None,
        # Phase B authorization gate
        kill_sheet_id: str | None = None,
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
            delta=delta,
            gamma=gamma,
            theta=theta,
            vega=vega,
            iv=iv,
            iv_rank=iv_rank,
            premium_stop=premium_stop,
            premium_target=premium_target,
            kill_sheet_id=kill_sheet_id,
        )

    @classmethod
    def open_shares_position(
        cls,
        ticker: str,
        direction: str,
        account_key: str,
        shares: float,
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

    def partial_close(
        self,
        contracts_closed: int,
        pnl_usd: float | None = None,
        notes: str | None = None,
    ) -> "Position":
        """Scale out of an options position by `contracts_closed` contracts.

        Decrements `contracts`, scales `max_loss_usd` proportionally (so
        `open_premium_at_risk` reflects remaining exposure), and appends the
        leg to `partial_exits`. When the final contract closes, the position
        transitions to status="closed" and `pnl_usd` becomes the aggregate of
        every partial leg.

        Options-only. Shares partial close not supported.
        """
        if self.status == "closed":
            raise ValueError(f"Position {self.id} is already closed")
        if self.instrument == "shares":
            raise ValueError("partial_close is not supported for shares positions")
        if self.contracts is None or self.contracts <= 0:
            raise ValueError(f"Position {self.id} has no contracts to close")
        if contracts_closed <= 0:
            raise ValueError("contracts_closed must be positive")
        if contracts_closed > self.contracts:
            raise ValueError(
                f"contracts_closed ({contracts_closed}) exceeds remaining "
                f"({self.contracts})"
            )

        starting_contracts = self.contracts
        leg = {
            "date": _now_iso(),
            "contracts_closed": contracts_closed,
            "pnl_usd": pnl_usd,
            "notes": notes,
        }
        self.partial_exits.append(leg)

        # Scale max_loss_usd proportionally; total_cost_usd is the immutable
        # entry record and stays as-is.
        remaining = starting_contracts - contracts_closed
        if starting_contracts > 0:
            self.max_loss_usd = self.max_loss_usd * (remaining / starting_contracts)
        self.contracts = remaining

        if remaining == 0:
            # Final leg — transition to closed. Aggregate pnl_usd from every
            # leg that supplied one; legs with pnl=None are skipped.
            aggregate = sum(
                float(x["pnl_usd"]) for x in self.partial_exits
                if x.get("pnl_usd") is not None
            )
            self.status = "closed"
            self.closed_date = _now_iso()
            self.pnl_usd = aggregate
            close_note = f"closed final {contracts_closed} contract(s)"
            if notes:
                close_note = f"{close_note} — {notes}"
            self.notes = (
                f"{self.notes}\nclose: {close_note}" if self.notes
                else f"close: {close_note}"
            )
        else:
            # Still open — append a partial-exit breadcrumb to notes.
            leg_note = f"partial close: {contracts_closed} contract(s), {remaining} remaining"
            if notes:
                leg_note = f"{leg_note} — {notes}"
            self.notes = f"{self.notes}\n{leg_note}" if self.notes else leg_note

        return self

    @property
    def thesis_direction(self) -> str:
        # `direction` is long/short *the contract*, not the thesis on the
        # underlying. A long put profits when the underlying falls, so its
        # thesis is bearish — the alert/discipline engines need that view.
        # Shares: direction maps straight through.
        instrument = (self.instrument or "").lower()
        direction = (self.direction or "").lower()
        if instrument in {"call", "put"}:
            bullish = (direction == "long" and instrument == "call") or (
                direction == "short" and instrument == "put"
            )
            return "bullish" if bullish else "bearish"
        return "bullish" if direction == "long" else "bearish"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> "Position":
        # Tolerate unknown keys (forward-compat: a newer JSON written by a
        # future version with extra fields can still be loaded by an older
        # binary). Missing fields fall back to dataclass defaults.
        from dataclasses import fields as _fields
        known = {f.name for f in _fields(cls)}
        filtered = {k: v for k, v in payload.items() if k in known}
        return cls(**filtered)
