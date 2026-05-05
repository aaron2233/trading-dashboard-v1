"""CLI for `python -m positions`.

Subcommands:
  list                     show all open positions (or all with --all)
  open OPTIONS              open a new position
  close ID [--pnl --notes]  close a position
  show ID                   show full details on one position
"""
from __future__ import annotations

import argparse
import json
import sys

from positions.alerts import evaluate_all_open, sort_alerts
from positions.model import Position
from positions.store import PositionStore


def _format_table(positions: list[Position]) -> str:
    if not positions:
        return "(no positions)"
    header = (
        f"{'ID':<10}{'Status':<8}{'Account':<8}{'Ticker':<8}"
        f"{'Inst':<8}{'Strike':<10}{'Expiry':<12}"
        f"{'Cost':<10}{'MaxLoss':<10}"
    )
    sep = "─" * len(header)
    lines = [header, sep]
    for p in positions:
        strike = f"${p.strike:g}" if p.strike is not None else "—"
        expiry = p.expiry or "—"
        lines.append(
            f"{p.id:<10}{p.status:<8}{p.account_key:<8}{p.ticker:<8}"
            f"{p.instrument:<8}{strike:<10}{expiry:<12}"
            f"${p.total_cost_usd:<9,.0f}${p.max_loss_usd:<9,.0f}"
        )
    return "\n".join(lines)


def _cmd_list(args, store: PositionStore) -> int:
    if args.all:
        positions = store.list_all()
    else:
        positions = store.list_open(account_key=args.account)
    print(_format_table(positions))
    return 0


def _cmd_open(args, store: PositionStore) -> int:
    if args.instrument == "shares":
        if args.shares is None or args.entry_price is None or args.invalidation is None:
            print("⚠ shares require --shares, --entry-price, and --invalidation",
                  file=sys.stderr)
            return 2
        position = Position.open_shares_position(
            ticker=args.ticker,
            direction=args.direction,
            account_key=args.account,
            shares=args.shares,
            entry_price=args.entry_price,
            invalidation_price=args.invalidation,
            target_price=args.target,
            notes=args.notes,
        )
    else:
        required = ("strike", "expiry", "premium", "contracts")
        missing = [r for r in required if getattr(args, r) is None]
        if missing:
            print(f"⚠ {args.instrument} requires --{', --'.join(missing)}",
                  file=sys.stderr)
            return 2
        position = Position.open_options_position(
            ticker=args.ticker,
            direction=args.direction,
            contract_type=args.instrument,
            account_key=args.account,
            strike=args.strike,
            expiry=args.expiry,
            premium=args.premium,
            contracts=args.contracts,
            underlying_price=args.entry_price,
            target_price=args.target,
            invalidation_price=args.invalidation,
            notes=args.notes,
        )
    store.add(position)
    print(f"Opened {position.id}: {position.ticker} {position.instrument}")
    print(f"  cost ${position.total_cost_usd:,.2f}, max loss ${position.max_loss_usd:,.2f}")
    return 0


def _cmd_close(args, store: PositionStore) -> int:
    try:
        position = store.close(args.id, pnl_usd=args.pnl, notes=args.notes)
    except KeyError as exc:
        print(f"⚠ {exc}", file=sys.stderr)
        return 1
    pnl_str = f"P/L ${position.pnl_usd:,.2f}" if position.pnl_usd is not None else "P/L not recorded"
    print(f"Closed {position.id}: {position.ticker} {position.instrument} — {pnl_str}")

    # Auto-score on close (Tier 3 closure). Skip for legacy positions and any
    # failure here is non-fatal — the close itself succeeded.
    try:
        from discipline import (
            DisciplineStore as _DS,
            is_legacy_position as _is_legacy,
            score_trade as _score_trade,
        )
    except ImportError:
        return 0  # discipline module unavailable

    if _is_legacy(position.closed_date):
        print(f"  (legacy position — skipping discipline score)")
        return 0

    try:
        score = _score_trade(position)
        _DS().save_score(score)
        viol = "⚠ profitable-violation" if score.profitable_violation else (
            "100% adherence" if score.full_adherence else f"{score.score_numerator}/{score.score_denominator}"
        )
        print(f"  Discipline score: {score.score*100:.0f}% — {viol}")
    except Exception as exc:
        print(f"  ⚠ auto-score failed: {exc}", file=sys.stderr)

    return 0


def _cmd_show(args, store: PositionStore) -> int:
    try:
        position = store.get(args.id)
    except KeyError as exc:
        print(f"⚠ {exc}", file=sys.stderr)
        return 1
    print(json.dumps(position.to_dict(), indent=2, default=str))
    return 0


_SEVERITY_BADGE = {"action": "🛑 ACTION", "warn": "⚠️  WARN  ", "info": "ℹ️  INFO  "}


def _cmd_alerts(args, store: PositionStore) -> int:
    by_position = evaluate_all_open(store)
    if not by_position:
        print("(no open positions)")
        return 0

    flat = []
    for pid, alerts in by_position.items():
        flat.extend(alerts)

    if not flat:
        print("All open positions clean — no alerts.")
        return 0

    flat = sort_alerts(flat)
    has_action = any(a.severity == "action" for a in flat)

    for a in flat:
        badge = _SEVERITY_BADGE.get(a.severity, a.severity)
        print(f"{badge}  [{a.ticker}/{a.position_id}]  {a.rule}: {a.message}")

    return 5 if has_action else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="positions",
        description="Manage open and closed positions for the discipline engine.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # list
    pl = sub.add_parser("list", help="List positions")
    pl.add_argument("--account", help="Filter to one account (e.g. main, lotto)")
    pl.add_argument("--all", action="store_true",
                    help="Include closed positions (default: open only)")

    # open
    po = sub.add_parser("open", help="Open a new position")
    po.add_argument("ticker")
    po.add_argument("--direction", choices=["long", "short"], default="long")
    po.add_argument("--instrument", choices=["call", "put", "shares"], default="call")
    po.add_argument("--account", default="main",
                    help="Account key (default: main)")
    po.add_argument("--strike", type=float)
    po.add_argument("--expiry")
    po.add_argument("--premium", type=float, help="Premium per share for options")
    po.add_argument("--contracts", type=int)
    po.add_argument("--shares", type=int)
    po.add_argument("--entry-price", type=float,
                    help="Underlying price at entry (options) or fill price (shares)")
    po.add_argument("--target", type=float, help="Target price for the underlying")
    po.add_argument("--invalidation", type=float,
                    help="Invalidation price (required for shares; recommended for options)")
    po.add_argument("--notes")

    # close
    pc = sub.add_parser("close", help="Close a position")
    pc.add_argument("id")
    pc.add_argument("--pnl", type=float, help="Realized P/L in USD")
    pc.add_argument("--notes")

    # show
    ps = sub.add_parser("show", help="Show full details of a position")
    ps.add_argument("id")

    # alerts
    sub.add_parser(
        "alerts",
        help="Evaluate alert rules for all open positions against fresh scans. "
             "Exit code 5 if any 'action' severity alerts fire.",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    store = PositionStore()
    if args.cmd == "list":
        return _cmd_list(args, store)
    if args.cmd == "open":
        return _cmd_open(args, store)
    if args.cmd == "close":
        return _cmd_close(args, store)
    if args.cmd == "show":
        return _cmd_show(args, store)
    if args.cmd == "alerts":
        return _cmd_alerts(args, store)
    parser.error(f"Unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
