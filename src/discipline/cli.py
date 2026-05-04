"""`python -m discipline` CLI.

Subcommands:
  score <position_id>           Score a closed position (auto-evaluates 13 rules,
                                interactive prompt for the 2 manual ones).
  weekly-review [--week YYYY-MM-DD]  Run the weekly review for a Sun-Sat window.
  list                          List scored trades.
  show <position_id>            Show a scored trade as JSON.
  lockdown <week_start> --behavior "..."   Persist the user's behavior-to-lock-down
                                            for the week.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from discipline.model import RULE_IDS, RULE_TEXT, DisciplineScore, RuleResult
from discipline.score import score_trade
from discipline.stage import current_stage, stage_reminder
from discipline.stats import compute_discipline_stats
from discipline.store import DisciplineStore, is_legacy_position
from discipline.weekly_review import get_or_compute_weekly, week_bounds
from positions import PositionStore


# ── Helpers ──────────────────────────────────────────────────────────────────


def _try_load_kill_sheet_from_disk(position_id: str) -> dict | None:
    """Best-effort lookup of a saved kill sheet matching position_id.

    Kill sheets are saved at `~/.trading-dashboard/kill_sheets/<ts>-<ticker>-<dir>.json`
    and don't carry the position_id (yet). Without an index we can't match
    reliably, so this returns None. The caller falls back to manual entry.
    """
    return None


def _format_score(score: DisciplineScore) -> str:
    lines: list[str] = []
    sep = "═" * 72
    lines.append(sep)
    lines.append(f"DISCIPLINE SCORE — {score.ticker} ({score.direction.upper()}, {score.instrument})")
    lines.append(sep)
    lines.append(f"  Position ID: {score.position_id}")
    lines.append(f"  Closed at:   {score.closed_at}")
    lines.append(f"  Score:       {score.score_numerator}/{score.score_denominator} = "
                 f"{score.score*100:.1f}%")
    pnl_str = f"${score.pnl_usd:,.2f}" if score.pnl_usd is not None else "n/a"
    lines.append(f"  P&L:         {pnl_str}")
    if score.profitable_violation:
        cf = score.counterfactual_loss_usd
        cf_str = f"${cf:,.0f}" if cf is not None else "n/a"
        lines.append("")
        lines.append("  ⚠️  PROFITABLE VIOLATION — highest-risk pattern")
        lines.append(f"     At -65% cut, this trade would have lost {cf_str}.")
        lines.append("     Lock down a specific behavior for next time (use --resolution).")
    lines.append("")
    lines.append("  Rules:")
    for r in score.rules:
        marker = {"Y": "✓", "N": "✗", "N/A": "·"}.get(r.score, "?")
        auto = " (auto)" if r.auto_evaluated else " (manual)"
        text = RULE_TEXT.get(r.rule_id, r.rule_id)
        lines.append(f"    [{marker}] {text}{auto}")
        if r.note:
            lines.append(f"        → {r.note}")
    lines.append(sep)
    return "\n".join(lines)


# ── Subcommands ──────────────────────────────────────────────────────────────


def cmd_score(args: argparse.Namespace) -> int:
    pstore = PositionStore()
    try:
        position = pstore.get(args.position_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if position.status != "closed":
        print(f"error: position {position.id} is not closed (status={position.status})",
              file=sys.stderr)
        return 2

    if is_legacy_position(position.closed_date) and not args.score_legacy:
        print(
            f"error: position closed before discipline-layer rollout "
            f"({position.closed_date}); pass --score-legacy to override",
            file=sys.stderr,
        )
        return 2

    # Active pyramid lookup at scoring time (current state — best we have)
    pyramid_active = None
    try:
        from pyramid import PyramidStore
        active = PyramidStore().list_active()
        for pyr in active:
            if (
                pyr.ticker.upper() == position.ticker.upper()
                and pyr.direction.lower() == position.direction.lower()
            ):
                pyramid_active = True
                break
        else:
            pyramid_active = False
    except Exception:
        pyramid_active = None

    score = score_trade(
        position,
        kill_sheet=None,  # TODO: lookup from disk via index when available
        pyramid_active_at_entry=pyramid_active,
        notes=args.notes or "",
    )

    if args.resolution:
        score.profitable_violation_resolution = args.resolution

    if not args.dry_run:
        path = DisciplineStore().save_score(score)
        print(f"Saved: {path}")

    if args.json:
        print(json.dumps(score.to_dict(), indent=2, default=str))
    else:
        print(_format_score(score))
    return 0


def cmd_weekly(args: argparse.Namespace) -> int:
    week_of: date | None = None
    if args.week:
        try:
            week_of = datetime.strptime(args.week, "%Y-%m-%d").date()
        except ValueError as exc:
            print(f"error: --week must be YYYY-MM-DD ({exc})", file=sys.stderr)
            return 2

    review = get_or_compute_weekly(week_of, force_recompute=args.recompute)
    sunday, saturday = week_bounds(week_of)

    if args.json:
        print(json.dumps(review.to_dict(), indent=2, default=str))
        return 0

    sep = "═" * 72
    print(sep)
    print(f"WEEKLY REVIEW — {sunday} → {saturday}")
    print(sep)
    print(f"  Trades scored:                  {review.trades_scored}")
    score_pct = review.avg_discipline_score * 100
    print(f"  Average discipline score:       {score_pct:.1f}%")
    print(f"  Trades with 100% adherence:     {review.full_adherence_count}")
    print(f"  Trades with any violation:      {review.any_violation_count}")
    print(f"  Profitable violations (red):    {review.profitable_violation_count}")
    print(f"  Most-violated rule:             {review.most_violated_rule or 'none'}")
    print(f"  Drift trend (vs prior 4 weeks): {review.drift_trend}")
    print(f"  P&L this week:                  ${review.pnl_usd:,.2f}")
    if review.lockdown_behavior:
        print("")
        print(f"  Lockdown behavior: {review.lockdown_behavior}")
    print("")
    # Stage reminder — needs current account balance, which we don't track here
    # in v1 (account.balance_usd is per-account static config). Surface a
    # generic stage-1 reminder.
    print(f"  {stage_reminder('stage_1')}")
    print(sep)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = DisciplineStore()
    scores = store.list_scores()
    if not scores:
        print(f"No discipline scores at {store.base_dir}")
        return 0
    if args.json:
        print(json.dumps([s.to_dict() for s in scores], indent=2, default=str))
        return 0
    print(f"{'Position ID':<12}{'Ticker':<8}{'Dir':<6}{'Closed':<22}"
          f"{'Score':<10}{'P&L':<10}Profit-Viol")
    print("─" * 80)
    for s in scores:
        score_pct = f"{s.score*100:.0f}%"
        pnl = f"${s.pnl_usd:,.0f}" if s.pnl_usd is not None else "n/a"
        flag = "⚠ YES" if s.profitable_violation else ""
        print(f"{s.position_id:<12}{s.ticker:<8}{s.direction:<6}{s.closed_at[:19]:<22}"
              f"{score_pct:<10}{pnl:<10}{flag}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = DisciplineStore()
    try:
        score = store.load_score(args.position_id)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(score.to_dict(), indent=2, default=str))
    else:
        print(_format_score(score))
    return 0


def cmd_lockdown(args: argparse.Namespace) -> int:
    store = DisciplineStore()
    try:
        review = store.update_lockdown(args.week_start, args.behavior)
    except KeyError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Updated lockdown for week {review.week_start}: {review.lockdown_behavior}")
    return 0


# ── Parser ───────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="discipline",
        description=(
            "Discipline scorecard CLI — score closed trades against the 15-rule "
            "checklist and run weekly reviews. Stage 1 (account < $100K): "
            "discipline score is the primary KPI, P&L is secondary."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="Score a closed position")
    s.add_argument("position_id")
    s.add_argument("--notes", help="Free-form trade notes")
    s.add_argument("--resolution", help="Lockdown behavior for profitable violation")
    s.add_argument("--score-legacy", action="store_true",
                   help="Score even if closed before 2026-05-02 (legacy exempt by default)")
    s.add_argument("--dry-run", action="store_true",
                   help="Don't persist the score, just print")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_score)

    w = sub.add_parser("weekly-review", help="Compute weekly review")
    w.add_argument("--week", help="Date inside the target week (YYYY-MM-DD)")
    w.add_argument("--recompute", action="store_true",
                   help="Force fresh compute even if a saved review exists")
    w.add_argument("--json", action="store_true")
    w.set_defaults(func=cmd_weekly)

    li = sub.add_parser("list", help="List scored trades")
    li.add_argument("--json", action="store_true")
    li.set_defaults(func=cmd_list)

    sh = sub.add_parser("show", help="Show a scored trade")
    sh.add_argument("position_id")
    sh.add_argument("--json", action="store_true")
    sh.set_defaults(func=cmd_show)

    lk = sub.add_parser("lockdown", help="Set lockdown behavior on a saved weekly review")
    lk.add_argument("week_start", help="Sunday date YYYY-MM-DD")
    lk.add_argument("--behavior", required=True, help="Specific behavior to lock down")
    lk.set_defaults(func=cmd_lockdown)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
