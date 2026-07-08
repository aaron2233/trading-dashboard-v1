"""CLI for `python -m kill_sheet`."""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from config import load_config
from kill_sheet.builder import build_standard
from kill_sheet.options import OptionsStructure, compute_dte
from positions import (
    FOCUS_TICKERS,
    PositionStore,
    check_focus_options_structure,
    check_focus_trade,
    check_proposed_trade,
)
from trade_devil import AGGREGATE_KILL, run_devil


KILL_SHEETS_DIR = Path.home() / ".trading-dashboard" / "kill_sheets"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="kill_sheet",
        description=(
            "Generate a Standard kill sheet for a ticker. Auto-fills indicator "
            "readings from a fresh scan + position sizing from your account config. "
            "Target/trigger/invalidation/notes are placeholders for you to fill in."
        ),
    )
    p.add_argument("ticker", help="Ticker symbol (e.g. SPY, AAPL)")
    p.add_argument(
        "--direction",
        choices=["long", "short"],
        required=True,
        help="Trade direction.",
    )
    p.add_argument(
        "--account",
        default="main",
        help="Account key in config.yaml (default: main; also: lotto, weekly).",
    )
    p.add_argument(
        "--intent",
        choices=["SCALP", "SWING", "TREND CAPTURE", "POSITION"],
        default="SWING",
        help="Trade intent (default: SWING). POSITION = weekly-trend-trader profile.",
    )
    p.add_argument(
        "--trigger-tf",
        choices=["2H", "4H", "Daily", "Weekly"],
        default="Daily",
        help="Trigger timeframe (default: Daily).",
    )
    p.add_argument(
        "--conviction",
        choices=["high", "medium", "speculative", "default"],
        default="high",
        help="Risk-conviction tier (controls risk-per-trade %%; default: high).",
    )
    p.add_argument(
        "--no-persist",
        action="store_true",
        help=f"Skip writing the kill sheet to {KILL_SHEETS_DIR}.",
    )
    p.add_argument(
        "--no-multi-tf",
        action="store_true",
        help="Skip Weekly and 4H bar fetches (those sections render as [TBD]).",
    )
    p.add_argument(
        "--skip-devil",
        action="store_true",
        help="Skip the trade devil gate (it auto-runs when max_risk_usd > $150).",
    )
    p.add_argument(
        "--force-devil",
        action="store_true",
        help="Force trade devil to run regardless of the $150 risk threshold.",
    )
    p.add_argument(
        "--skip-rules",
        action="store_true",
        help="Skip the account-rules pre-check (max positions, premium-at-risk, "
             "cash floor). Discouraged.",
    )
    p.add_argument(
        "--bypass-rules",
        action="store_true",
        help="Run the rules check but allow the kill sheet to render even on a "
             "violation (logged to stderr).",
    )
    p.add_argument(
        "--focus",
        action="store_true",
        help=(
            "qqq-gld-focus mode: ticker must be QQQ or GLD; applies focus rules "
            "(one open position per asset, no same-direction QQQ+GLD pair, "
            "3-trading-day cool-off after a stop). See "
            "~/.claude/skills/user/qqq-gld-focus/."
        ),
    )
    p.add_argument(
        "--period",
        default=None,
        help="yfinance period for the Daily scan (default: timeframe-appropriate).",
    )
    options = p.add_argument_group(
        "Options template",
        "Pass --strike + --premium + --expiry to render the full options block. "
        "Without these flags, the Standard template renders.",
    )
    options.add_argument("--strike", type=float, help="Option strike price")
    options.add_argument("--premium", type=float, help="Option premium (per share)")
    options.add_argument("--expiry", help="Expiry as ISO date YYYY-MM-DD")
    options.add_argument(
        "--type", dest="contract_type", choices=["call", "put"],
        help="Contract type (default inferred from --direction: long→call, short→put)",
    )
    options.add_argument("--delta", type=float, help="Option delta")
    options.add_argument("--iv-rank", type=float, help="IV Rank percentile (0-100)")
    options.add_argument("--oi", type=int, help="Open interest")
    options.add_argument("--spread", type=float, help="Bid-ask spread (in dollars)")
    options.add_argument(
        "--options-json",
        help=(
            "Path to a robinhood-mcp option-quote snapshot JSON (shape documented "
            "in src/options_input/robinhood.py). Fills strike/premium/expiry/type/"
            "delta/oi/spread from the live quote; explicit flags win. Never fills "
            "--iv-rank (the quote carries spot IV, not IV Rank)."
        ),
    )
    options.add_argument(
        "--allow-stale",
        action="store_true",
        help="Accept an --options-json or --balance-json snapshot older than "
             "its staleness cutoff.",
    )

    balance = p.add_argument_group(
        "Balance audit",
        "Pass --balance-json to audit the sizing base against broker truth. "
        "Sizing itself stays on the configured sleeve balance — the audit "
        "proves the book those balances live in still matches the broker.",
    )
    balance.add_argument(
        "--balance-json",
        help=(
            "Path to a robinhood-mcp get_portfolio snapshot JSON (shape "
            "documented in src/kill_sheet/balance_audit.py). Compares "
            "portfolio.total_value against the journal book model (config "
            "balance.anchor + post-anchor realized P&L) and renders a drift "
            "line in the sizing section; warns when |drift| ≥ 2%%."
        ),
    )

    manual = p.add_argument_group(
        "Manual fill (optional)",
        "Fields that are normally [TBD] until you fill them in. Combine with "
        "--interactive to be prompted for any you didn't pass on the CLI.",
    )
    manual.add_argument("--target", type=float, help="Target price")
    manual.add_argument("--invalidation", type=float, help="Invalidation price")
    manual.add_argument("--trigger-desc", help="Trigger condition description")
    manual.add_argument("--notes", help="Free-form notes to attach to the kill sheet")
    manual.add_argument(
        "-i", "--interactive", action="store_true",
        help="Prompt for any --target/--invalidation/--trigger-desc/--notes and "
             "options fields not passed on the CLI.",
    )
    return p


def _prompt(message: str, optional: bool = False, validator=None,
            input_fn=input):
    """Prompt the user; re-prompt on validation error. Returns parsed value or None."""
    while True:
        suffix = " (blank to skip)" if optional else ""
        raw = input_fn(f"{message}{suffix}: ").strip()
        if not raw:
            if optional:
                return None
            print("required, please enter a value")
            continue
        if validator is None:
            return raw
        try:
            return validator(raw)
        except (ValueError, TypeError) as exc:
            print(f"  invalid: {exc}")


def _maybe_interactive_fill(args, input_fn=input) -> None:
    """Mutate args in place, prompting for missing fields when --interactive."""
    if not args.interactive:
        return

    if args.target is None:
        args.target = _prompt("Target price (USD)", optional=True,
                              validator=float, input_fn=input_fn)
    if args.invalidation is None:
        args.invalidation = _prompt("Invalidation price (USD)", optional=True,
                                    validator=float, input_fn=input_fn)
    if args.trigger_desc is None:
        args.trigger_desc = _prompt("Trigger condition", optional=True,
                                    input_fn=input_fn)
    if args.notes is None:
        args.notes = _prompt("Notes", optional=True, input_fn=input_fn)

    # Options block: only prompt if any of the core options fields are set
    # OR the user explicitly says yes to "add options data?"
    any_options = any(v is not None for v in (args.strike, args.premium, args.expiry))
    if not any_options:
        ans = _prompt("Add options data (options template)? [y/N]",
                      optional=True, input_fn=input_fn)
        if ans is None or ans.lower() not in {"y", "yes"}:
            return

    if args.strike is None:
        args.strike = _prompt("Strike price", validator=float, input_fn=input_fn)
    if args.premium is None:
        args.premium = _prompt("Premium per share (USD)", validator=float,
                               input_fn=input_fn)
    if args.expiry is None:
        args.expiry = _prompt("Expiry (YYYY-MM-DD)", input_fn=input_fn)
    if args.contract_type is None:
        ans = _prompt("Contract type [call/put] (blank → inferred from direction)",
                      optional=True, input_fn=input_fn)
        if ans:
            args.contract_type = ans.lower()
    if args.delta is None:
        args.delta = _prompt("Delta", optional=True, validator=float,
                             input_fn=input_fn)
    if args.iv_rank is None:
        args.iv_rank = _prompt("IV Rank percentile (0-100)", optional=True,
                               validator=float, input_fn=input_fn)
    if args.oi is None:
        args.oi = _prompt("Open interest", optional=True, validator=int,
                          input_fn=input_fn)
    if args.spread is None:
        args.spread = _prompt("Bid-ask spread (USD)", optional=True,
                              validator=float, input_fn=input_fn)


def _build_options_from_args(args) -> OptionsStructure | None:
    """Construct OptionsStructure if user passed options flags; else None."""
    has_options = (args.strike is not None and args.premium is not None
                   and args.expiry is not None)
    if not has_options:
        return None

    contract_type = args.contract_type
    if contract_type is None:
        contract_type = "call" if args.direction == "long" else "put"

    return OptionsStructure(
        strike=float(args.strike),
        contract_type=contract_type,
        expiry=args.expiry,
        dte=compute_dte(args.expiry),
        premium=float(args.premium),
        delta=args.delta,
        iv_rank=args.iv_rank,
        open_interest=args.oi,
        bid_ask_spread=args.spread,
    )


def persist(sheet, scans_dir: Path | None = None,
            devil_report=None) -> tuple[Path, Path]:
    if scans_dir is None:
        scans_dir = KILL_SHEETS_DIR
    scans_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"{stamp}-{sheet.ticker.lower()}-{sheet.direction.lower()}"
    json_path = scans_dir / f"{base}.json"
    md_path = scans_dir / f"{base}.md"

    payload = {"kill_sheet": sheet.to_dict()}
    if devil_report is not None:
        payload["trade_devil"] = devil_report.to_dict()

    import json as _json
    json_path.write_text(_json.dumps(payload, indent=2, default=str))

    md_text = f"```\n{sheet.to_text()}\n```\n"
    if devil_report is not None:
        md_text += f"\n```\n{devil_report.to_text()}\n```\n"
    md_path.write_text(md_text)
    return json_path, md_path


def apply_options_snapshot(args, parsed) -> list[str]:
    """Fill unset options args from a ParsedOptions; explicit flags win.

    Returns the list of arg names that were filled (for the CLI notice).
    """
    filled: list[str] = []
    for arg_name, parsed_name in (
        ("strike", "strike"),
        ("premium", "premium"),
        ("expiry", "expiry"),
        ("contract_type", "contract_type"),
        ("delta", "delta"),
        ("oi", "open_interest"),
        ("spread", "bid_ask_spread"),
    ):
        if getattr(args, arg_name) is None:
            value = getattr(parsed, parsed_name)
            if value is not None:
                setattr(args, arg_name, value)
                filled.append(arg_name)
    return filled


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.options_json:
        from options_input.robinhood import (
            STALE_AFTER_MINUTES,
            load_snapshot,
            parse_robinhood_snapshot,
            snapshot_age_minutes,
        )
        try:
            raw = load_snapshot(args.options_json)
        except ValueError as exc:
            parser.error(str(exc))
        age = snapshot_age_minutes(raw)
        if (age is None or age > STALE_AFTER_MINUTES) and not args.allow_stale:
            shown = "unknown age (no fetched_at)" if age is None else f"{age:.0f} min old"
            parser.error(
                f"options snapshot is {shown} (cutoff {STALE_AFTER_MINUTES:.0f} min) — "
                "re-fetch the quote, or pass --allow-stale to use it anyway"
            )
        snapshot = parse_robinhood_snapshot(raw)
        filled = apply_options_snapshot(args, snapshot)
        if filled:
            print(f"options-json filled: {', '.join(filled)}", file=sys.stderr)
        for warning in snapshot.warnings:
            print(f"⚠ options-json: {warning}", file=sys.stderr)

    config = load_config()
    try:
        account = config.account(args.account)
    except KeyError as exc:
        print(f"⚠ {exc}", file=sys.stderr)
        return 2

    balance_audit_line: str | None = None
    if args.balance_json:
        from kill_sheet.balance_audit import (
            STALE_AFTER_MINUTES as BALANCE_STALE_AFTER_MINUTES,
            audit_balance,
            load_balance_snapshot,
        )
        from options_input.robinhood import snapshot_age_minutes
        try:
            raw_balance = load_balance_snapshot(args.balance_json)
        except ValueError as exc:
            parser.error(str(exc))
        bal_age = snapshot_age_minutes(raw_balance)
        if ((bal_age is None or bal_age > BALANCE_STALE_AFTER_MINUTES)
                and not args.allow_stale):
            shown = ("unknown age (no fetched_at)" if bal_age is None
                     else f"{bal_age:.0f} min old")
            parser.error(
                f"balance snapshot is {shown} (cutoff "
                f"{BALANCE_STALE_AFTER_MINUTES:.0f} min) — re-fetch "
                "get_portfolio, or pass --allow-stale to use it anyway"
            )
        try:
            audit = audit_balance(
                raw_balance, config, PositionStore().list_all())
        except ValueError as exc:
            parser.error(str(exc))
        balance_audit_line = audit.line()
        for warning in audit.warnings:
            print(f"⚠ balance-json: {warning}", file=sys.stderr)

    if args.focus and args.ticker.upper() not in FOCUS_TICKERS:
        print(
            f"⚠ --focus restricts tickers to {', '.join(sorted(FOCUS_TICKERS))}; "
            f"got {args.ticker.upper()}",
            file=sys.stderr,
        )
        return 2

    # Lazy import to keep import-time light when only --help is run
    from scan import compute_multi_tf, populate_trigger_bar, scan_ticker

    try:
        scan_row = scan_ticker(args.ticker.upper(), period=args.period)
    except Exception as exc:
        print(f"⚠ Scan failed for {args.ticker}: {exc}", file=sys.stderr)
        return 1

    # G4 trigger-bar capture for 2H lotto sheets — soft-fails on yfinance hiccup.
    scan_row = populate_trigger_bar(scan_row, args.ticker, args.trigger_tf)

    multi_tf: dict | None = None
    if not args.no_multi_tf:
        multi_tf = compute_multi_tf(args.ticker.upper(), timeframes=("1wk", "4h"))
        # If both auxiliary TFs failed, surface a soft warning but continue.
        for tf_key, row in multi_tf.items():
            if "error" in row:
                print(
                    f"⚠ {tf_key} bars unavailable: {row['error']}",
                    file=sys.stderr,
                )

    _maybe_interactive_fill(args)

    options = _build_options_from_args(args)

    sheet = build_standard(
        scan_row=scan_row,
        direction=args.direction,
        account=account,
        account_key=args.account,
        intent=args.intent,
        trigger_tf=args.trigger_tf,
        risk_conviction=args.conviction,
        multi_tf=multi_tf,
        options=options,
        target_price=args.target,
        invalidation_price=args.invalidation,
        trigger_description=args.trigger_desc,
        notes=args.notes,
    )
    sheet.balance_audit = balance_audit_line

    # ─ Pre-check: account rules ─
    rules_blocked = False
    open_positions: list = []
    if not args.skip_rules:
        store = PositionStore()
        open_positions = store.list_open()
        violations = check_proposed_trade(
            proposed_max_loss_usd=sheet.max_risk_usd,
            account=account,
            account_key=args.account,
            open_positions=open_positions,
            pool_account_keys=config.pool_account_keys(args.account),
        )
        if args.focus:
            all_positions = store.list_all()
            closed_positions = [p for p in all_positions if p.status == "closed"]
            violations.extend(check_focus_trade(
                ticker=args.ticker,
                direction=args.direction,
                open_positions=open_positions,
                closed_positions=closed_positions,
            ))
            violations.extend(check_focus_options_structure(
                ticker=args.ticker,
                direction=args.direction,
                max_loss_usd=sheet.max_risk_usd,
                dte=sheet.options.dte if sheet.options else None,
            ))
        if violations:
            print(
                "\n⚠ Account-rules violations:",
                file=sys.stderr,
            )
            for v in violations:
                print(f"  [{v.rule}] {v.message}", file=sys.stderr)
            if args.bypass_rules:
                print(
                    "  (proceeding anyway — --bypass-rules)",
                    file=sys.stderr,
                )
            else:
                rules_blocked = True

    print(sheet.to_text())

    devil_report = None
    if not args.skip_devil and not rules_blocked:
        devil_report = run_devil(
            sheet, force=args.force_devil, open_positions=open_positions,
        )
        if devil_report is not None:
            print()
            print(devil_report.to_text())

    if not args.no_persist:
        try:
            json_path, md_path = persist(sheet, devil_report=devil_report)
            print(f"\nSaved: {md_path}")
            print(f"       {json_path}")
        except Exception as exc:
            print(f"\n⚠ Failed to persist kill sheet: {exc}", file=sys.stderr)
            return 1

    if rules_blocked:
        # PRD FR33-FR35: hard gates. Distinct exit code from devil-kill so
        # scripts can tell them apart.
        return 4
    if devil_report is not None and devil_report.aggregate == AGGREGATE_KILL:
        # Hard blocker per PRD FR31. The kill sheet still saves so the user
        # has the audit trail; the non-zero exit makes scripts know the trade
        # didn't survive.
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
