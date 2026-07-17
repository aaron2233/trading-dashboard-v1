"""Lotto account routes — state, strike suggestions, watchlist scan."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.models import (
    LottoCooldownResponse,
    LottoScanRequest,
    LottoScanResponse,
    LottoSetupResponse,
    LottoStateResponse,
    LottoTradeSummaryResponse,
)
from lotto import LOTTO_ACCOUNT_KEY, compute_lotto_state, scan_lotto_watchlist
from scan import scan_ticker


def make_lotto_router(store_factory, config_loader) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/lotto/state", response_model=LottoStateResponse)
    def lotto_state():
        """Lotto-account dashboard state — anti-greed, growth ladder, cash reserve."""
        config = config_loader()
        try:
            lotto_account = config.account(LOTTO_ACCOUNT_KEY)
            base_balance = float(lotto_account.balance_usd)
        except KeyError:
            base_balance = 1_000.0

        store = store_factory()
        all_positions = store.list_all()
        open_positions = [p for p in all_positions if p.status == "open"]
        closed_positions = [p for p in all_positions if p.status == "closed"]

        state = compute_lotto_state(
            open_positions=open_positions,
            closed_positions=closed_positions,
            base_balance_usd=base_balance,
        )
        return LottoStateResponse(
            account_balance_usd=state.account_balance_usd,
            base_balance_usd=state.base_balance_usd,
            realized_pnl_usd=state.realized_pnl_usd,
            open_premium_usd=state.open_premium_usd,
            cash_available_usd=state.cash_available_usd,
            cash_reserve_status=state.cash_reserve_status,
            growth_ladder_stage=state.growth_ladder_stage,
            cooldown=LottoCooldownResponse(
                active=state.cooldown.active,
                reason=state.cooldown.reason,
                triggered_at=state.cooldown.triggered_at,
                expires_at=state.cooldown.expires_at,
                hours_remaining=state.cooldown.hours_remaining,
                triggering_position_ids=state.cooldown.triggering_position_ids,
            ),
            size_lock_active=state.size_lock_active,
            size_lock_reason=state.size_lock_reason,
            closed_count_last_7d=state.closed_count_last_7d,
            recent_trades=[
                LottoTradeSummaryResponse(
                    position_id=t.position_id, ticker=t.ticker, direction=t.direction,
                    closed_date=t.closed_date, pnl_usd=t.pnl_usd,
                    return_pct=t.return_pct, is_big_win=t.is_big_win, is_loss=t.is_loss,
                )
                for t in state.recent_trades
            ],
            open_position_ids=state.open_position_ids,
        )

    @router.get("/api/v1/lotto/strikes/{ticker}")
    def lotto_strike_suggestions(
        ticker: str,
        direction: str = Query("both", pattern="^(call|put|both)$"),
        increment: float | None = Query(None, ge=0.01, le=100.0),
    ) -> dict[str, Any]:
        """Strike candidates around current spot for a Lotto setup. Returns
        ATM + 1/3/5/7/10% OTM by default. Premium / IV / delta NOT included
        — those flow through the options-input layer at kill-sheet time.

        Spot anchors to the last completed 2H bar, not the daily bar: the
        daily loader drops the in-progress session (anti-repaint), so
        intraday the daily close is yesterday's — up to a full session
        stale. Daily remains the fallback when the 2H fetch fails."""
        from lotto import suggest_strikes
        ticker_u = ticker.upper()
        row: dict[str, Any] | None
        try:
            row = scan_ticker(ticker_u, timeframe="2h")
        except Exception:
            row = None
        if row is None or row.get("close") is None:
            try:
                row = scan_ticker(ticker_u, timeframe="1d")
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Spot fetch failed for {ticker_u}: {exc}",
                )
        spot = row.get("close")
        if spot is None or spot <= 0:
            raise HTTPException(
                status_code=502,
                detail=f"No usable spot price for {ticker_u}",
            )
        dir_arg = None if direction == "both" else direction
        result = suggest_strikes(
            spot=float(spot),
            direction=dir_arg,
            ticker=ticker_u,
            bar_date=row.get("bar_date", ""),
            increment=increment,
        )
        return result.to_dict()

    @router.post("/api/v1/lotto/scan", response_model=LottoScanResponse)
    def lotto_scan(req: LottoScanRequest):
        """Lotto setup scan across the configured universe(s) — defaults to
        the curated lotto high-vol watchlist ("lotto_high_vol", 25 names,
        ~15-25s); broad indexes remain available by passing them explicitly.
        Each ticker yields TWO setups (long + short) classified
        independently. Each setup is tagged with `source_universe` so the
        frontend can group results by index.
        """
        try:
            result = scan_lotto_watchlist(
                tickers=req.tickers, universe=req.universe,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Lotto scan failed: {exc}",
            )

        def _to_response(s) -> LottoSetupResponse:
            return LottoSetupResponse(**s.to_dict())

        return LottoScanResponse(
            scan_time_utc=result.scan_time_utc,
            setups=[_to_response(s) for s in result.setups],
            actionable_setups=[_to_response(s) for s in result.actionable_setups],
            errors=result.errors,
        )

    return router
