"""Kill-sheet routes — generate, fetch, and the paste-extract input feeder."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from api.models import (
    KillSheetRequest,
    KillSheetResponse,
    OptionsTextRequest,
    ParsedOptionsResponse,
    RuleViolationResponse,
)
from api.routes._helpers import devil_to_response
from kill_sheet.builder import build_standard
from kill_sheet.options import OptionsStructure, compute_dte
from lotto import LOTTO_ACCOUNT_KEY, check_lotto_cooldown
from options_input import parse_options_text
from positions import check_proposed_trade, check_tier_portfolio_trade
from scan import compute_multi_tf, populate_trigger_bar, scan_ticker
from trade_devil import run_devil


def make_kill_sheet_router(store_factory, config_loader) -> APIRouter:
    router = APIRouter()

    @router.post(
        "/api/v1/options/extract/text",
        response_model=ParsedOptionsResponse,
    )
    def options_extract_text(req: OptionsTextRequest):
        """Parse pasted brokerage clipboard text into structured fields.

        Lenient regex extraction — unmatched fields stay None and the user
        completes them in the kill sheet form. Per anti-fabrication rules,
        the parser does not invent values for missing fields.
        """
        parsed = parse_options_text(req.text)
        return ParsedOptionsResponse(
            **parsed.to_dict(),
            extraction_source="paste",
        )

    @router.post("/api/v1/kill_sheet", response_model=KillSheetResponse)
    def kill_sheet(req: KillSheetRequest):
        config = config_loader()
        try:
            account = config.account(req.account)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            scan_row = scan_ticker(req.ticker.upper(), period=req.period)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")

        # G4 trigger-bar capture for 2H lotto sheets — soft-fails on yfinance hiccup.
        scan_row = populate_trigger_bar(scan_row, req.ticker, req.trigger_tf)

        multi_tf = None
        if req.include_multi_tf:
            multi_tf = compute_multi_tf(req.ticker.upper(),
                                        timeframes=("1wk", "4h"))

        # Build options structure if all three required fields present
        options = None
        if req.strike is not None and req.premium is not None and req.expiry is not None:
            contract_type = req.contract_type or (
                "call" if req.direction == "long" else "put"
            )
            options = OptionsStructure(
                strike=float(req.strike),
                contract_type=contract_type,
                expiry=req.expiry,
                dte=compute_dte(req.expiry),
                premium=float(req.premium),
                delta=req.delta,
                iv_rank=req.iv_rank,
                open_interest=req.oi,
                bid_ask_spread=req.spread,
            )

        # Pull current open positions so the builder can auto-flag
        # averaging-down on the attestation.
        attestation_store = store_factory()
        attestation_open = attestation_store.list_open()

        # Resolve skill tag: caller passes name; resolve to SkillConfig if
        # known so builder gets full metadata (tier, defaults). Unknown names
        # fall through as the bare string — builder still uses .name for gates.
        skill_arg = None
        if req.skill:
            try:
                skill_arg = config.skill(req.skill)
            except KeyError:
                skill_arg = req.skill

        sheet = build_standard(
            scan_row=scan_row,
            direction=req.direction,
            account=account,
            account_key=req.account,
            intent=req.intent,
            trigger_tf=req.trigger_tf,
            risk_conviction=req.conviction,
            multi_tf=multi_tf,
            options=options,
            target_price=req.target,
            invalidation_price=req.invalidation,
            trigger_description=req.trigger_desc,
            notes=req.notes,
            divergence_thesis=req.divergence_thesis,
            counter_weekly_thesis=req.counter_weekly_thesis,
            attestation_user_inputs=req.attestation_user_inputs,
            open_positions=attestation_open,
            skill=skill_arg,
        )

        # Pre-check: account rules
        rules_blocked = False
        violations: list[RuleViolationResponse] = []
        violation_dicts: list = []  # plain dicts persisted onto the sheet
        store = store_factory()
        open_positions: list = []
        if not req.skip_rules:
            open_positions = store.list_open()
            raw = check_proposed_trade(
                proposed_max_loss_usd=sheet.max_risk_usd,
                account=account,
                account_key=req.account,
                open_positions=open_positions,
                pool_account_keys=config.pool_account_keys(req.account),
            )
            # ─ Tier 1+2 portfolio rule (orchestrator rule 11) ─
            # Fires whenever ticker is QQQ/GLD.
            # Applies the 2-concurrent / no-same-direction-pair / 3-day cool-off
            # check across all open QQQ/GLD positions. The check_tier_portfolio_trade
            # helper short-circuits to [] for non-QQQ/GLD tickers.
            tier_closed_positions = [
                p for p in store.list_all() if p.status == "closed"
            ]
            raw = list(raw) + check_tier_portfolio_trade(
                ticker=req.ticker,
                direction=req.direction,
                open_positions=open_positions,
                closed_positions=tier_closed_positions,
            )

            # ─ Lotto anti-greed (24h post-big-win, 48h post-3-loss, size lock) ─
            # Fires only on lotto-account kill sheets — short-circuits for
            # main/weekly. Per ~/.claude/skills/user/lotto-options/SKILL.md.
            if req.account == LOTTO_ACCOUNT_KEY:
                lotto_base = float(account.balance_usd) if account else 1_000.0
                raw = list(raw) + check_lotto_cooldown(
                    open_positions=open_positions,
                    closed_positions=tier_closed_positions,
                    base_balance_usd=lotto_base,
                )

            violation_dicts = [v.to_dict() for v in raw]
            violations = [RuleViolationResponse(**d) for d in violation_dicts]
            # Only severity="block" gates the trade. Warn-level violations
            # (e.g. lotto_size_lock) surface for the user but don't stop kill
            # sheet generation — they're advisory, not a hard gate.
            if any(v.severity == "block" for v in violations) and not req.bypass_rules:
                rules_blocked = True

        devil_payload = None
        if not req.skip_devil and not rules_blocked:
            report = run_devil(
                sheet, force=req.force_devil, open_positions=open_positions,
            )
            if report is not None:
                devil_payload = devil_to_response(report)

        # Phase B: persist authorized kill sheets so the position-open
        # endpoint can validate kill_sheet_id against the canonical record.
        # Rejected kill sheets stay transient — they're diagnostic, not
        # load-bearing.
        # Persist the rule-engine outcome on the sheet (journal-first: the trade
        # isn't blocked, but the breach must be visible when the scorer loads
        # this sheet at close). Set before save so it lands in the record.
        sheet.rules_blocked = rules_blocked
        sheet.rule_violations = violation_dicts

        kill_sheet_id: str | None = None
        if sheet.status == "AUTHORIZED":
            try:
                from kill_sheet.store import KillSheetStore
                ks_store = KillSheetStore()
                ks_store.save(sheet)
                kill_sheet_id = sheet.id
            except Exception:
                # Persistence failure shouldn't break sheet generation —
                # the user still sees the analysis. The position-open
                # endpoint will simply have no record to validate against.
                import logging
                logging.getLogger(__name__).exception(
                    "kill sheet persistence failed for id=%s", sheet.id
                )

        return KillSheetResponse(
            kill_sheet=sheet.to_dict(),
            rendered_text=sheet.to_text(),
            rule_violations=violations,
            rules_blocked=rules_blocked,
            devil=devil_payload,
            kill_sheet_id=kill_sheet_id,
        )

    @router.get("/api/v1/kill_sheet/{kill_sheet_id}")
    def get_kill_sheet(kill_sheet_id: str) -> dict[str, Any]:
        """Fetch a previously-generated kill sheet by ID. Used by the
        position-open authorization gate and for review UI."""
        from kill_sheet.store import KillSheetStore
        ks = KillSheetStore().load(kill_sheet_id)
        if ks is None:
            raise HTTPException(
                status_code=404,
                detail=f"No kill sheet with id={kill_sheet_id}",
            )
        return ks.to_dict()

    return router
