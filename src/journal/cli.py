"""CLI for `python -m journal`.

Subcommands:
  stats               aggregate stats across all closed positions (or one account)
  recent              last N closed positions, most recent first
  export <path>       dump positions to CSV
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from journal.stats import (
    JournalStats,
    by_account,
    by_direction,
    by_instrument,
    compute_stats,
)
from positions.store import PositionStore


def _format_stats_block(stats: JournalStats) -> str:
    lines: list[str] = []
    lines.append(f"  Closed trades:    {stats.total_trades_closed} "
                 f"(open: {stats.open_trades})")
    if stats.total_trades_closed == 0:
        lines.append("  (no closed positions yet)")
        return "\n".join(lines)
    lines.append(f"  Wins / losses:    {stats.wins} / {stats.losses}"
                 f" (breakeven: {stats.breakevens})")
    lines.append(f"  Win rate:         {stats.win_rate:.1%}")
    lines.append(f"  Total P&L:        ${stats.total_pnl_usd:+,.2f}")
    lines.append(f"  Avg win / loss:   ${stats.avg_win_usd:+,.2f} / ${stats.avg_loss_usd:+,.2f}")
    lines.append(f"  Largest win/loss: ${stats.largest_win_usd:+,.2f} / "
                 f"${stats.largest_loss_usd:+,.2f}")
    pf = stats.profit_factor
    if pf is None:
        pf_str = "n/a"
    elif pf == float("inf"):
        pf_str = "∞ (all wins)"
    else:
        pf_str = f"{pf:.2f}"
    lines.append(f"  Profit factor:    {pf_str}")
    lines.append(f"  Expectancy/trade: ${stats.expectancy_usd:+,.2f}")
    lines.append(f"  Capital deployed: ${stats.total_cost_invested_usd:,.2f} "
                 f"(max-loss: ${stats.total_max_loss_taken_usd:,.2f})")
    return "\n".join(lines)


def _cmd_stats(args, store: PositionStore) -> int:
    positions = store.list_all()
    if args.account:
        positions = [p for p in positions if p.account_key == args.account]

    overall = compute_stats(positions, label=args.account or "all")
    print(f"\n=== Journal: {overall.label} ===")
    print(_format_stats_block(overall))

    if not args.no_breakdown and not args.account:
        print("\n=== By account ===")
        for key, stats in by_account(positions).items():
            print(f"\n[{key}]")
            print(_format_stats_block(stats))

        if args.detail:
            print("\n=== By instrument ===")
            for key, stats in by_instrument(positions).items():
                print(f"\n[{key}]")
                print(_format_stats_block(stats))
            print("\n=== By direction ===")
            for key, stats in by_direction(positions).items():
                print(f"\n[{key}]")
                print(_format_stats_block(stats))
    return 0


def _cmd_recent(args, store: PositionStore) -> int:
    closed = [p for p in store.list_all() if p.status == "closed"]
    closed.sort(key=lambda p: p.closed_date or "", reverse=True)
    closed = closed[: args.limit]
    if not closed:
        print("(no closed positions)")
        return 0
    header = (
        f"{'Closed':<22}{'Acct':<8}{'Tkr':<8}{'Inst':<8}"
        f"{'Strike':<10}{'P/L':>12}"
    )
    print(header)
    print("─" * len(header))
    for p in closed:
        strike = f"${p.strike:g}" if p.strike is not None else "—"
        pnl_str = f"${p.pnl_usd:+,.2f}" if p.pnl_usd is not None else "n/a"
        closed_at = (p.closed_date or "")[:19].replace("T", " ")
        print(f"{closed_at:<22}{p.account_key:<8}{p.ticker:<8}{p.instrument:<8}"
              f"{strike:<10}{pnl_str:>12}")
    return 0


def _cmd_export(args, store: PositionStore) -> int:
    out_path = Path(args.path)
    positions = store.list_all()

    if not positions:
        print("(no positions to export)")
        return 0

    fieldnames = list(positions[0].to_dict().keys())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for p in positions:
            row = p.to_dict()
            writer.writerow(row)
    print(f"Wrote {len(positions)} position(s) to {out_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="journal",
        description="Performance analytics over closed positions.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("stats", help="Aggregate P&L stats")
    ps.add_argument("--account",
                    help="Limit to one account (e.g. main, lotto, weekly)")
    ps.add_argument("--no-breakdown", action="store_true",
                    help="Skip the per-account breakdown")
    ps.add_argument("--detail", action="store_true",
                    help="Include per-instrument and per-direction breakdowns")

    pr = sub.add_parser("recent", help="List most recent closed trades")
    pr.add_argument("--limit", type=int, default=10)

    pe = sub.add_parser("export", help="Export positions to CSV")
    pe.add_argument("path")

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = PositionStore()
    if args.cmd == "stats":
        return _cmd_stats(args, store)
    if args.cmd == "recent":
        return _cmd_recent(args, store)
    if args.cmd == "export":
        return _cmd_export(args, store)
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
