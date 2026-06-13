"""Positions routes — list, open, alerts, get, close."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.models import (
    AlertResponse,
    ClosePositionRequest,
    OpenPositionRequest,
    PositionResponse,
)
from api.routes._helpers import position_to_response
from discipline import (
    DisciplineStore,
    is_legacy_position,
    load_kill_sheet_for,
    score_trade,
)
from positions import evaluate_all_open
from positions.model import Position


def make_positions_router(store_factory) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/positions", response_model=list[PositionResponse])
    def list_positions(
        status: str = Query("open", description="open | closed | all"),
        account: str | None = Query(None),
    ):
        store = store_factory()
        all_positions = store.list_all()
        if account:
            all_positions = [p for p in all_positions if p.account_key == account]
        if status == "open":
            all_positions = [p for p in all_positions if p.status == "open"]
        elif status == "closed":
            all_positions = [p for p in all_positions if p.status == "closed"]
        # "all" → no filter
        return [position_to_response(p) for p in all_positions]

    @router.post("/api/v1/positions", response_model=PositionResponse, status_code=201)
    def open_position(req: OpenPositionRequest):
        # Phase B authorization gate: every new position must reference an
        # AUTHORIZED kill sheet whose ticker + direction match, unless the
        # caller explicitly bypasses with a documented reason in notes.
        validated_kill_sheet_id: str | None = None
        if not req.bypass_kill_sheet:
            if not req.kill_sheet_id:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "kill_sheet_id is required to open a position. Generate "
                        "an AUTHORIZED kill sheet first, or set bypass_kill_sheet=true "
                        "with a reason in notes for emergency logging."
                    ),
                )
            from kill_sheet.store import KillSheetStore
            ks = KillSheetStore().load(req.kill_sheet_id)
            if ks is None:
                raise HTTPException(
                    status_code=422,
                    detail=f"kill_sheet_id={req.kill_sheet_id!r} not found",
                )
            # NOTE: kill-sheet status is RECORDED on the position, not used as
            # a hard gate. Per user intent (2026-05-10): journal entries should
            # never be blocked — record everything and reassess discipline
            # adherence retrospectively via the per-trade scorecard.
            # (Was: raise 422 when ks.status != "AUTHORIZED".)
            if ks.ticker.upper() != req.ticker.upper():
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"kill_sheet ticker {ks.ticker!r} doesn't match "
                        f"position ticker {req.ticker!r}"
                    ),
                )
            if ks.direction.lower() != req.direction.lower():
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"kill_sheet direction {ks.direction!r} doesn't match "
                        f"position direction {req.direction!r}"
                    ),
                )
            # NOTE: discipline §8 attestation is RECORDED, not enforced.
            # Per user intent (2026-05-10): journal-entry creation must never
            # be blocked. The attestation state lives on the kill sheet and
            # is surfaced in the per-trade discipline scorecard for
            # retrospective review.
            # (Was: raise 422 when entry_authorized is False.)
            validated_kill_sheet_id = ks.id
        elif req.bypass_kill_sheet and not (req.notes or "").strip():
            # Bypass requires a documented reason for audit
            raise HTTPException(
                status_code=422,
                detail=(
                    "bypass_kill_sheet=true requires a non-empty notes field "
                    "documenting the reason for bypass"
                ),
            )

        store = store_factory()
        try:
            if req.instrument == "shares":
                if req.shares is None or req.entry_price is None or req.invalidation is None:
                    raise HTTPException(
                        status_code=400,
                        detail="shares require shares, entry_price, and invalidation",
                    )
                position = Position.open_shares_position(
                    ticker=req.ticker,
                    direction=req.direction,
                    account_key=req.account,
                    shares=req.shares,
                    entry_price=req.entry_price,
                    invalidation_price=req.invalidation,
                    target_price=req.target,
                    notes=req.notes,
                    skill=req.skill,
                    tier=req.tier,
                )
                position.kill_sheet_id = validated_kill_sheet_id
            else:
                missing = [
                    k for k in ("strike", "expiry", "premium", "contracts")
                    if getattr(req, k) is None
                ]
                if missing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"{req.instrument} requires: {', '.join(missing)}",
                    )
                # Cash account is long-only: every option here is BOUGHT (long
                # the contract). The thesis is carried by req.direction
                # (long=bullish, short=bearish — matching the kill sheet) plus
                # the contract type. Only two combos are coherent as a long
                # contract: bullish -> long CALL, bearish -> long PUT. Reject
                # anything else (bearish 'call' = naked short call; bullish
                # 'put' = inverted thesis) so a sold/short option can never be
                # logged. CLAUDE.md: "Cash account -- long calls and long puts
                # ONLY"; anti-pattern "Never recommend spreads ... or margin".
                if (req.instrument == "call") != (req.direction.lower() == "long"):
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Cash account is long-only: a bullish thesis must be "
                            "a long CALL and a bearish thesis a long PUT. "
                            f"direction={req.direction!r} with "
                            f"instrument={req.instrument!r} would require a "
                            "sold/short option."
                        ),
                    )
                position = Position.open_options_position(
                    ticker=req.ticker,
                    # Stored contract direction is always 'long' — this account
                    # only ever buys options. Bearishness is preserved by the
                    # put instrument + Position.thesis_direction. Dissolves the
                    # inverted short+put and naked short+call storage classes.
                    direction="long",
                    contract_type=req.instrument,
                    account_key=req.account,
                    strike=req.strike,
                    expiry=req.expiry,
                    premium=req.premium,
                    contracts=req.contracts,
                    underlying_price=req.entry_price,
                    target_price=req.target,
                    invalidation_price=req.invalidation,
                    notes=req.notes,
                    skill=req.skill,
                    tier=req.tier,
                    delta=req.delta,
                    gamma=req.gamma,
                    theta=req.theta,
                    vega=req.vega,
                    iv=req.iv,
                    iv_rank=req.iv_rank,
                    premium_stop=req.premium_stop,
                    premium_target=req.premium_target,
                    kill_sheet_id=validated_kill_sheet_id,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        try:
            store.add(position, allow_duplicate=req.allow_duplicate)
        except ValueError as exc:
            # Dedup guard tripped (suspected double-submit). 409 with the
            # message so the client can confirm via allow_duplicate.
            raise HTTPException(status_code=409, detail=str(exc))
        return position_to_response(position)

    @router.get("/api/v1/positions/alerts", response_model=list[AlertResponse])
    def position_alerts():
        store = store_factory()
        by_position = evaluate_all_open(store)
        flat: list[AlertResponse] = []
        for alerts in by_position.values():
            for a in alerts:
                flat.append(AlertResponse(**a.to_dict()))
        return flat

    @router.get("/api/v1/positions/{position_id}", response_model=PositionResponse)
    def get_position(position_id: str):
        store = store_factory()
        try:
            return position_to_response(store.get(position_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @router.post("/api/v1/positions/{position_id}/close", response_model=PositionResponse)
    def close_position(position_id: str, req: ClosePositionRequest):
        store = store_factory()
        try:
            position = store.close(
                position_id,
                pnl_usd=req.pnl,
                notes=req.notes,
                contracts=req.contracts,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        # Auto-score only when the position is fully closed (partial closes
        # leave status="open"). Skip for legacy positions; failures here are
        # non-fatal — the close itself succeeded.
        if position.status == "closed" and not is_legacy_position(position.closed_date):
            try:
                score = score_trade(position, kill_sheet=load_kill_sheet_for(position))
                DisciplineStore().save_score(score)
            except Exception:
                # Don't let scoring failure block the close response. Log and
                # the user can run `python -m discipline score <id>` later.
                import sys as _sys
                print(f"⚠ Auto-score failed for {position_id}", file=_sys.stderr)

        return position_to_response(position)

    return router
