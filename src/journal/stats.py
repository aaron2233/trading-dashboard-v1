"""Aggregate stats across closed positions.

Open positions are excluded from win/loss math (no P&L yet) but counted
separately for context.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from positions.model import Position


@dataclass
class JournalStats:
    label: str = "all"
    total_trades_closed: int = 0
    open_trades: int = 0
    wins: int = 0
    losses: int = 0
    breakevens: int = 0

    win_rate: float = 0.0
    total_pnl_usd: float = 0.0
    avg_win_usd: float = 0.0
    avg_loss_usd: float = 0.0
    largest_win_usd: float = 0.0
    largest_loss_usd: float = 0.0

    profit_factor: float | None = None  # gross_wins / |gross_losses|
    expectancy_usd: float = 0.0          # average $ result per closed trade

    # Capital deployed
    total_cost_invested_usd: float = 0.0
    total_max_loss_taken_usd: float = 0.0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "total_trades_closed": self.total_trades_closed,
            "open_trades": self.open_trades,
            "wins": self.wins,
            "losses": self.losses,
            "breakevens": self.breakevens,
            "win_rate": round(self.win_rate, 4),
            "total_pnl_usd": round(self.total_pnl_usd, 2),
            "avg_win_usd": round(self.avg_win_usd, 2),
            "avg_loss_usd": round(self.avg_loss_usd, 2),
            "largest_win_usd": round(self.largest_win_usd, 2),
            "largest_loss_usd": round(self.largest_loss_usd, 2),
            "profit_factor": (
                round(self.profit_factor, 3) if self.profit_factor is not None else None
            ),
            "expectancy_usd": round(self.expectancy_usd, 2),
            "total_cost_invested_usd": round(self.total_cost_invested_usd, 2),
            "total_max_loss_taken_usd": round(self.total_max_loss_taken_usd, 2),
        }


def compute_stats(positions: Iterable[Position], label: str = "all") -> JournalStats:
    positions = list(positions)
    closed = [p for p in positions if p.status == "closed" and p.pnl_usd is not None]
    open_positions = [p for p in positions if p.status == "open"]

    stats = JournalStats(
        label=label,
        total_trades_closed=len(closed),
        open_trades=len(open_positions),
    )

    wins = [p for p in closed if p.pnl_usd > 0]
    losses = [p for p in closed if p.pnl_usd < 0]
    breakevens = [p for p in closed if p.pnl_usd == 0]

    stats.wins = len(wins)
    stats.losses = len(losses)
    stats.breakevens = len(breakevens)

    decided = wins + losses
    if decided:
        stats.win_rate = len(wins) / len(decided)

    # Realized P&L from partial-close legs on still-open positions. Closed
    # positions are excluded here because their pnl_usd already aggregates
    # every leg (partial_close sets pnl_usd = sum(partial_exits[].pnl_usd)
    # when the final contract closes). Including both would double-count.
    partial_pnl = 0.0
    for p in open_positions:
        for leg in (p.partial_exits or []):
            leg_pnl = leg.get("pnl_usd")
            if leg_pnl is not None:
                partial_pnl += float(leg_pnl)

    if closed or partial_pnl:
        closed_pnl = sum(p.pnl_usd for p in closed)
        stats.total_pnl_usd = closed_pnl + partial_pnl
        # Expectancy stays per-fully-closed-trade so it reflects completed
        # decisions, not realized slices on still-open positions.
        if closed:
            stats.expectancy_usd = closed_pnl / len(closed)

    if wins:
        win_pnls = [p.pnl_usd for p in wins]
        stats.avg_win_usd = sum(win_pnls) / len(wins)
        stats.largest_win_usd = max(win_pnls)
    if losses:
        loss_pnls = [p.pnl_usd for p in losses]
        stats.avg_loss_usd = sum(loss_pnls) / len(losses)
        stats.largest_loss_usd = min(loss_pnls)

    gross_wins = sum(p.pnl_usd for p in wins)
    gross_losses_abs = abs(sum(p.pnl_usd for p in losses))
    if gross_losses_abs > 0:
        stats.profit_factor = gross_wins / gross_losses_abs
    elif gross_wins > 0:
        stats.profit_factor = float("inf")  # all wins, no losses

    stats.total_cost_invested_usd = sum(p.total_cost_usd for p in closed)
    stats.total_max_loss_taken_usd = sum(p.max_loss_usd for p in closed)

    return stats


def _group(positions: Iterable[Position], key) -> dict[str, JournalStats]:
    positions = list(positions)
    groups: dict[str, list[Position]] = {}
    for p in positions:
        groups.setdefault(key(p), []).append(p)
    return {k: compute_stats(v, label=k) for k, v in sorted(groups.items())}


def by_account(positions: Iterable[Position]) -> dict[str, JournalStats]:
    return _group(positions, lambda p: p.account_key)


def by_instrument(positions: Iterable[Position]) -> dict[str, JournalStats]:
    return _group(positions, lambda p: p.instrument)


def by_direction(positions: Iterable[Position]) -> dict[str, JournalStats]:
    # Group by THESIS expressed as long/short: every long option stores
    # direction="long" regardless of call/put, so a raw direction group collapses
    # bullish calls and bearish puts together. thesis_direction restores the
    # bullish→long / bearish→short split.
    return _group(
        positions,
        lambda p: "long" if p.thesis_direction == "bullish" else "short",
    )
