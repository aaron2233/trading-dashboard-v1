"""Attribute Sunday scan recommendations to actual journal positions.

The point: for each historical scan, did the user follow the recommendation?
If so, how did the trade work out? Closes the discipline-tool loop the skill
asks for ("did I follow my Sunday scan? did the recommendation work?").

Match logic: a position counts as "following" the scan if it was opened
within `window_days` calendar days of the scan date AND matches the top
setup's ticker + direction. The default 7-day window covers the focus
skill's 1-2 trades/week tempo without bleeding into the next week's scan.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from focus.sunday_scan import iter_saved_scans
from pathlib import Path
from positions.model import Position


DEFAULT_WINDOW_DAYS: int = 7


@dataclass
class MatchedPosition:
    id: str
    ticker: str
    direction: str
    instrument: str
    entry_date: str
    status: str
    pnl_usd: float | None
    max_loss_usd: float
    contracts: int | None
    strike: float | None
    expiry: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FocusOutcome:
    scan_date: str
    recommendation: str
    top_setup: dict[str, Any] | None
    window_days: int
    followed: bool
    matched: list[MatchedPosition] = field(default_factory=list)
    realized_pnl_usd: float = 0.0   # sum of pnl from CLOSED matched positions
    open_count: int = 0
    closed_count: int = 0
    aggregate_status: str = "skipped"   # see docstring below

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_date": self.scan_date,
            "recommendation": self.recommendation,
            "top_setup": self.top_setup,
            "window_days": self.window_days,
            "followed": self.followed,
            "matched": [m.to_dict() for m in self.matched],
            "realized_pnl_usd": self.realized_pnl_usd,
            "open_count": self.open_count,
            "closed_count": self.closed_count,
            "aggregate_status": self.aggregate_status,
        }


def _parse_iso_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        # Position.entry_date is an ISO datetime string with tz suffix
        dt = datetime.fromisoformat(s)
        return dt.date()
    except ValueError:
        return None


def find_matched_positions(
    scan_date: str,
    top_setup: dict[str, Any] | None,
    positions: Iterable[Position],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[Position]:
    """Return positions matching the top setup, opened within window_days
    of scan_date (inclusive on both ends).
    """
    if top_setup is None:
        return []
    asset = (top_setup.get("asset") or "").upper()
    direction = (top_setup.get("direction") or "").lower()
    if not asset or not direction:
        return []
    # The setup direction is the THESIS ("long"=bullish, "short"=bearish). Match
    # on the position's THESIS, not its stored contract direction: every long
    # option stores direction="long" (a long put is a bearish thesis), so a raw
    # direction compare would never match a bearish put to a "short" setup.
    setup_thesis = "bullish" if direction == "long" else "bearish"

    try:
        scan_dt = datetime.strptime(scan_date, "%Y-%m-%d").date()
    except ValueError:
        return []
    window_end = scan_dt + timedelta(days=window_days)

    matched: list[Position] = []
    for p in positions:
        if p.ticker.upper() != asset:
            continue
        if p.thesis_direction != setup_thesis:
            continue
        entry = _parse_iso_date(p.entry_date)
        if entry is None:
            continue
        if entry < scan_dt or entry > window_end:
            continue
        matched.append(p)
    return matched


def _aggregate_status(matched: list[Position], top_setup_present: bool) -> str:
    """Roll up status for the outcome banner.

    - "skipped"       : top_setup existed but no matched positions
    - "no_recommendation": no top_setup at all (cash week)
    - "open"          : all matches still open
    - "closed_winner" : all matches closed, total pnl > 0
    - "closed_loser"  : all matches closed, total pnl <= 0
    - "mixed"         : some open + some closed
    """
    if not matched:
        return "skipped" if top_setup_present else "no_recommendation"
    open_count = sum(1 for p in matched if p.status == "open")
    closed = [p for p in matched if p.status == "closed"]
    if open_count and closed:
        return "mixed"
    if open_count:
        return "open"
    realized = sum((p.pnl_usd or 0.0) for p in closed)
    return "closed_winner" if realized > 0 else "closed_loser"


def build_outcome(
    scan_date: str,
    scan_payload: dict[str, Any],
    positions: Iterable[Position],
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> FocusOutcome:
    """Assemble the full outcome record for a scan."""
    setups = scan_payload.get("setups") or []
    top_setup = setups[0] if setups else None
    recommendation = scan_payload.get("recommendation", "cash")

    matched = find_matched_positions(scan_date, top_setup, positions, window_days)
    matched_records = [
        MatchedPosition(
            id=p.id,
            ticker=p.ticker,
            direction=p.direction,
            instrument=p.instrument,
            entry_date=p.entry_date,
            status=p.status,
            pnl_usd=p.pnl_usd,
            max_loss_usd=p.max_loss_usd,
            contracts=p.contracts,
            strike=p.strike,
            expiry=p.expiry,
        )
        for p in matched
    ]
    closed = [p for p in matched if p.status == "closed"]
    realized = sum((p.pnl_usd or 0.0) for p in closed)

    return FocusOutcome(
        scan_date=scan_date,
        recommendation=recommendation,
        top_setup=top_setup,
        window_days=window_days,
        followed=bool(matched),
        matched=matched_records,
        realized_pnl_usd=realized,
        open_count=sum(1 for p in matched if p.status == "open"),
        closed_count=len(closed),
        aggregate_status=_aggregate_status(matched, top_setup is not None),
    )


@dataclass
class FocusRecentSummary:
    """Roll-up of outcomes across the last N weeks of saved Sunday scans."""
    weeks: int
    scans_count: int
    trade_recs: int
    watch_recs: int
    cash_recs: int
    followed_count: int
    skipped_count: int          # trade-recommended but no matching position
    realized_pnl_usd: float
    open_count: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarize_recent_outcomes(
    weeks: int,
    positions: Iterable[Position],
    sunday_scans_dir: Path | None = None,
    today: date | None = None,
) -> FocusRecentSummary:
    """Aggregate outcomes for scans in the last `weeks` calendar weeks.

    Reads scans via `iter_saved_scans` so the on-disk directory is the source
    of truth (same as the recent-listing endpoint).
    """
    if today is None:
        today = date.today()
    cutoff = today - timedelta(weeks=weeks)

    positions_list = list(positions)
    scans_count = trade = watch_ = cash_ = followed = skipped = 0
    realized = 0.0
    open_count = 0

    for date_str, payload in iter_saved_scans(sunday_scans_dir):
        try:
            scan_dt = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if scan_dt < cutoff or scan_dt > today:
            continue
        scans_count += 1
        rec = payload.get("recommendation", "cash")
        if rec == "trade":
            trade += 1
        elif rec == "watch":
            watch_ += 1
        else:
            cash_ += 1

        outcome = build_outcome(date_str, payload, positions_list)
        if outcome.followed:
            followed += 1
        elif rec == "trade":
            # Only count as "skipped" when the scan actually recommended a
            # trade. Cash/watch weeks not taken are correct behavior, not
            # missed opportunities.
            skipped += 1
        realized += outcome.realized_pnl_usd
        open_count += outcome.open_count

    return FocusRecentSummary(
        weeks=weeks,
        scans_count=scans_count,
        trade_recs=trade,
        watch_recs=watch_,
        cash_recs=cash_,
        followed_count=followed,
        skipped_count=skipped,
        realized_pnl_usd=realized,
        open_count=open_count,
    )
