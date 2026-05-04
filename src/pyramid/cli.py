"""`python -m pyramid` CLI for the trend-pyramid module.

Subcommands:
  evaluate <ticker> --direction long|short [--id <pyramid_id>]
  plan <ticker> --direction long|short --allocation <usd>
  list [--all]
  show <id>
  fill <id> --tranche <1|2|3> --vehicle <shares|leaps_call|leaps_put|barbell|etf>
       --cost <per-unit> --qty <count> [--strike <X>] [--expiry <YYYY-MM-DD>]
  close <id> [--pnl <usd>] [--notes <txt>]
  delete <id>

`evaluate` runs a full PyramidEvaluation for a ticker — gate, tranches, exits.
If `--id` is provided, the evaluator is aware of which tranches are filled.
Otherwise it's "planning mode": gate + T1 evaluation against current state.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from pyramid.evaluator import PyramidEvaluation, evaluate_pyramid
from pyramid.model import Pyramid, Tranche
from pyramid.store import PyramidStore


def _format_evaluation(evaluation: PyramidEvaluation) -> str:
    """Render a PyramidEvaluation as a kill-sheet-style text block."""
    e = evaluation
    lines: list[str] = []
    sep = "═" * 75
    lines.append(sep)
    lines.append(f"PYRAMID EVALUATION — {e.ticker}  ({e.direction.upper()})")
    lines.append(sep)
    lines.append(f"  Bar:      {e.bar_date}   Close: ${e.close:.2f}")
    lines.append("")
    lines.append("REGIME (benchmark for SQN diagnostic):")
    sqn100_str = f"{e.sqn_100_value:.3f}" if e.sqn_100_value is not None else "n/a"
    sqn20_str = f"{e.sqn_20_value:.3f}" if e.sqn_20_value is not None else "n/a"
    lines.append(f"  SQN(100): {sqn100_str}  → {e.sqn_100_regime}")
    lines.append(f"  SQN(20):  {sqn20_str}  → {e.sqn_20_regime}")
    lines.append(f"  Diagnostic: {e.sqn_diagnostic}")
    lines.append("")
    lines.append("MA RIBBON (Daily, ticker):")
    for label, val in (("10", e.ma_10), ("20", e.ma_20), ("50", e.ma_50), ("200", e.ma_200)):
        if val is None:
            lines.append(f"  {label} MA: n/a")
        else:
            lines.append(f"  {label} MA: ${val:.2f}")
    lines.append(f"  Stack: {e.ma_stack_state}")
    lines.append("")
    lines.append("STOCHASTIC (14,7,7):")
    lines.append(f"  %K: {e.stoch_k}  %D: {e.stoch_d}")
    lines.append("")
    lines.append("PRICE STRUCTURE:")
    lines.append(f"  Recent swing high: {e.structure.recent_swing_high}  ({e.structure.recent_swing_high_date})")
    lines.append(f"  Recent swing low:  {e.structure.recent_swing_low}  ({e.structure.recent_swing_low_date})")
    lines.append(f"  Higher-low confirmed: {e.structure.higher_low_confirmed}")
    lines.append(f"  Lower-high confirmed: {e.structure.lower_high_confirmed}")
    lines.append(f"  Pullback held 20MA: {e.structure.pullback_held_20ma} | 50MA: {e.structure.pullback_held_50ma}")
    lines.append(f"  Rally rejected 20MA: {e.structure.rally_rejected_at_20ma} | 50MA: {e.structure.rally_rejected_at_50ma}")
    lines.append("")
    lines.append("─── GATE ──────────────────────────────────────────────────")
    lines.append(f"  Permitted: {'✅ GREEN' if e.gate.permitted else '❌ RED'}")
    lines.append(f"  SQN(100): {'✓' if e.gate.sqn_100_pass else '✗'}  "
                 f"SQN(20): {'✓' if e.gate.sqn_20_pass else '✗'}  "
                 f"MA: {'✓' if e.gate.ma_stack_pass else '✗'}  "
                 f"Pullback: {'✓' if e.gate.pullback_pass else '✗'}  "
                 f"Structure: {'✓' if e.gate.structure_pass else '✗'}")
    if e.gate.blockers:
        lines.append("  Blockers:")
        for b in e.gate.blockers:
            lines.append(f"    - {b}")
    lines.append("")
    for label, tr in (("T1", e.t1), ("T2", e.t2), ("T3", e.t3)):
        if tr is None:
            continue
        lines.append(f"─── {label} ────────────────────────────────────────────────")
        lines.append(f"  Should fire: {'✅ YES' if tr.should_fire else '⏸ NO'}")
        if tr.blockers:
            for b in tr.blockers:
                lines.append(f"    - {b}")
        lines.append("")
    lines.append("─── EXITS ─────────────────────────────────────────────────")
    for d in e.exits:
        marker = {"info": "  ", "warn": "⚠ ", "action": "❗"}.get(d.severity, "  ")
        lines.append(f"  {marker}[{d.action}] {d.reason}")
    if e.next_tranche is not None:
        lines.append("")
        lines.append(f"  Next tranche to evaluate: T{e.next_tranche}")
    lines.append(sep)
    return "\n".join(lines)


def cmd_evaluate(args: argparse.Namespace) -> int:
    store = PyramidStore()
    pyramid = None
    if args.id:
        try:
            pyramid = store.load(args.id)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    direction = pyramid.direction if pyramid else args.direction
    ticker = pyramid.ticker if pyramid else args.ticker.upper()
    benchmark = pyramid.benchmark if pyramid else (args.benchmark or "SPY")

    if not direction:
        print("error: --direction is required when --id is not provided", file=sys.stderr)
        return 2
    if not ticker:
        print("error: ticker is required when --id is not provided", file=sys.stderr)
        return 2

    try:
        evaluation = evaluate_pyramid(
            ticker, direction, benchmark=benchmark, pyramid=pyramid,
        )
    except Exception as exc:
        print(f"error: evaluation failed — {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(evaluation.to_dict(), indent=2, default=str))
    else:
        print(_format_evaluation(evaluation))

    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    """Create a new pyramid in pending state."""
    if args.allocation < 0:
        print("error: --allocation must be non-negative", file=sys.stderr)
        return 2

    pyramid = Pyramid.create(
        ticker=args.ticker,
        direction=args.direction,
        total_allocation_usd=args.allocation,
        benchmark=args.benchmark or "SPY",
        notes=args.notes,
    )
    store = PyramidStore()
    path = store.save(pyramid)
    print(f"Created pyramid {pyramid.id} for {pyramid.ticker} {pyramid.direction}")
    print(f"  Allocation: ${pyramid.total_allocation_usd:,.2f}  Benchmark: {pyramid.benchmark}")
    print(f"  Saved: {path}")
    print(f"\nNext: python -m pyramid evaluate --id {pyramid.id}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = PyramidStore()
    pyramids = store.list_all() if args.all else store.list_active()
    if not pyramids:
        scope = "any" if args.all else "active"
        print(f"No {scope} pyramids found at {store.base_dir}")
        return 0
    print(f"{'ID':<14}{'Ticker':<8}{'Dir':<7}{'Status':<14}{'Filled':<8}{'Alloc':<12}Created")
    print("─" * 80)
    for p in pyramids:
        filled = sum(1 for t in p.tranches if t.status == "filled")
        print(
            f"{p.id:<14}{p.ticker:<8}{p.direction:<7}{p.status:<14}"
            f"{filled}/3      ${p.total_allocation_usd:<10,.0f}{p.created_date}"
        )
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = PyramidStore()
    try:
        pyramid = store.load(args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(pyramid.to_dict(), indent=2, default=str))
    return 0


def cmd_fill(args: argparse.Namespace) -> int:
    store = PyramidStore()
    try:
        pyramid = store.load(args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        tr = pyramid.get_tranche(args.tranche)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    swing_high = args.swing_high
    swing_low = args.swing_low
    if args.capture_structure and swing_high is None and swing_low is None:
        try:
            ev = evaluate_pyramid(
                pyramid.ticker, pyramid.direction,
                benchmark=pyramid.benchmark, pyramid=pyramid,
            )
            swing_high = ev.structure.recent_swing_high
            swing_low = ev.structure.recent_swing_low
            print(
                f"  Captured structure: swing_high={swing_high}, swing_low={swing_low}",
                file=sys.stderr,
            )
        except Exception as exc:
            print(
                f"  ⚠ structure capture failed: {exc} — fill proceeds without reference",
                file=sys.stderr,
            )

    try:
        tr.fill(
            vehicle=args.vehicle,
            cost_basis_per_unit=args.cost,
            quantity=args.qty,
            strike=args.strike,
            expiry=args.expiry,
            notes=args.notes,
            swing_high_at_fill=swing_high,
            swing_low_at_fill=swing_low,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # Promote pyramid status if first fill activates it
    if pyramid.status == "pending" and any(t.status == "filled" for t in pyramid.tranches):
        pyramid.status = "active"
    # If all 3 filled, mark completed (but only after explicit close — for now, leave as active)

    store.save(pyramid)
    cost = tr.total_cost_usd()
    cost_str = f"${cost:,.2f}" if cost is not None else "n/a"
    print(f"Filled T{tr.id} on pyramid {pyramid.id}")
    print(f"  Vehicle: {tr.vehicle}  Qty: {tr.quantity}  Cost basis: ${tr.cost_basis_per_unit}")
    print(f"  Total cost: {cost_str}")
    if tr.expiry:
        print(f"  Expiry: {tr.expiry}")
    if tr.swing_high_at_fill is not None or tr.swing_low_at_fill is not None:
        print(
            f"  Structure ref: high={tr.swing_high_at_fill}, low={tr.swing_low_at_fill}"
        )
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    store = PyramidStore()
    try:
        pyramid = store.load(args.id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    pyramid.status = "completed" if args.completed else "stopped_out"
    pyramid.closed_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if args.pnl is not None:
        pyramid.aggregate_pnl_usd = float(args.pnl)
    if args.notes is not None:
        existing_notes = pyramid.notes or ""
        pyramid.notes = (existing_notes + "\n" + args.notes).strip() if existing_notes else args.notes
    store.save(pyramid)
    print(f"Closed pyramid {pyramid.id} as {pyramid.status}")
    if pyramid.aggregate_pnl_usd is not None:
        print(f"  Aggregate P&L: ${pyramid.aggregate_pnl_usd:,.2f}")
    return 0


def cmd_delete(args: argparse.Namespace) -> int:
    store = PyramidStore()
    if not args.force:
        try:
            pyramid = store.load(args.id)
        except KeyError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if pyramid.status == "active":
            print(f"error: pyramid {args.id} is active. Use --force to delete anyway.", file=sys.stderr)
            return 2
    if store.delete(args.id):
        print(f"Deleted pyramid {args.id}")
        return 0
    print(f"error: no pyramid with id={args.id}", file=sys.stderr)
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pyramid",
        description=(
            "Trend-pyramid CLI — multi-tranche scaled entry on confirmed Daily-TF "
            "trends. See ~/.claude/skills/user/trend-pyramid/SKILL.md for the full "
            "methodology."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    # evaluate
    pe = sub.add_parser("evaluate", help="Evaluate gate / tranches / exits for a ticker")
    pe.add_argument("ticker", nargs="?", help="Ticker symbol (e.g. SPY) — required if --id not set")
    pe.add_argument("--direction", choices=("long", "short"),
                    help="Direction — required if --id not set")
    pe.add_argument("--benchmark", default=None, help="Benchmark for SQN (default: SPY)")
    pe.add_argument("--id", help="Existing pyramid id (overrides ticker/direction)")
    pe.add_argument("--json", action="store_true", help="Emit JSON instead of formatted text")
    pe.set_defaults(func=cmd_evaluate)

    # plan
    pp = sub.add_parser("plan", help="Create a new pyramid in pending state")
    pp.add_argument("ticker", help="Ticker symbol")
    pp.add_argument("--direction", choices=("long", "short"), required=True)
    pp.add_argument("--allocation", type=float, required=True,
                    help="Total USD allocation across all 3 tranches")
    pp.add_argument("--benchmark", default=None, help="Benchmark for SQN (default: SPY)")
    pp.add_argument("--notes", help="Optional plan notes / divergence thesis")
    pp.set_defaults(func=cmd_plan)

    # list
    pl = sub.add_parser("list", help="List pyramids")
    pl.add_argument("--all", action="store_true", help="Include closed/stopped pyramids")
    pl.set_defaults(func=cmd_list)

    # show
    ps = sub.add_parser("show", help="Show pyramid details (JSON)")
    ps.add_argument("id")
    ps.set_defaults(func=cmd_show)

    # fill
    pf = sub.add_parser("fill", help="Mark a tranche filled")
    pf.add_argument("id")
    pf.add_argument("--tranche", type=int, choices=(1, 2, 3), required=True)
    pf.add_argument("--vehicle", required=True,
                    choices=("shares", "leaps_call", "leaps_put", "barbell", "etf"))
    pf.add_argument("--cost", type=float, required=True,
                    help="Cost basis per unit (per share, or per-share option premium)")
    pf.add_argument("--qty", type=int, required=True,
                    help="Quantity (shares or option contracts)")
    pf.add_argument("--strike", type=float)
    pf.add_argument("--expiry", help="ISO date YYYY-MM-DD (for options)")
    pf.add_argument("--notes")
    pf.add_argument(
        "--swing-high", type=float, dest="swing_high",
        help="Swing high reference at fill time (T1 esp.) — exact T3 reference",
    )
    pf.add_argument(
        "--swing-low", type=float, dest="swing_low",
        help="Swing low reference at fill time (T1 esp.) — exact T3 reference for shorts",
    )
    pf.add_argument(
        "--capture-structure", action="store_true", dest="capture_structure",
        help="Capture swing high/low from current structure read at fill time",
    )
    pf.set_defaults(func=cmd_fill)

    # close
    pc = sub.add_parser("close", help="Close a pyramid (default = stopped_out)")
    pc.add_argument("id")
    pc.add_argument("--completed", action="store_true",
                    help="Mark as completed rather than stopped_out")
    pc.add_argument("--pnl", type=float, help="Aggregate realized P&L in USD")
    pc.add_argument("--notes")
    pc.set_defaults(func=cmd_close)

    # delete
    pd = sub.add_parser("delete", help="Delete a pyramid file")
    pd.add_argument("id")
    pd.add_argument("--force", action="store_true",
                    help="Required to delete an active pyramid")
    pd.set_defaults(func=cmd_delete)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
