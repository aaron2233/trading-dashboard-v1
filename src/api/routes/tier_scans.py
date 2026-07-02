"""Tier-scanner routes — weekly trend, index swing, free range."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.models import (
    CandidateSnapshotResponse,
    DipBuySignalResponse,
    FreeRangeScanRequest,
    FreeRangeScanResponse,
    IndexSwingScanRequest,
    IndexSwingScanResponse,
    IndexSwingSetupResponse,
    RegimeLeveredScanRequest,
    RegimeLeveredScanResponse,
    RegimeLeveredSetupResponse,
    WeeklyScanRequest,
    WeeklyScanResponse,
    WeeklySetupResponse,
)
from free_range import run_free_range_scan
from index_swing import scan_index_swing_watchlist
from regime_levered import scan_regime_levered
from weekly_trend import scan_weekly_watchlist


def make_tier_scans_router() -> APIRouter:
    router = APIRouter()

    @router.post("/api/v1/weekly/scan", response_model=WeeklyScanResponse)
    def weekly_scan(req: WeeklyScanRequest):
        """Sunday-scan workflow: weekly TF + benchmark regime over a watchlist.

        Two modes:
          - Explicit tickers — fast, scans exactly the names you pass.
          - Universe sweep — accepts ["nasdaq_100", "sp500_top_50",
            "russell_2000_top_50"]; each setup is tagged with
            `source_universe` so the UI can group by index. Slower
            (~60-120s for ~200 names, Track A 5y weekly bars per ticker).
        """
        if not req.tickers and not req.universe:
            raise HTTPException(
                status_code=400,
                detail="Provide either `tickers` or `universe` — both empty",
            )
        try:
            result = scan_weekly_watchlist(
                tickers=req.tickers,
                universe=req.universe,
                benchmark=req.benchmark,
                top_n=req.top_n,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Weekly scan failed: {exc}")

        def _to_response(s) -> WeeklySetupResponse:
            return WeeklySetupResponse(**s.to_dict())

        return WeeklyScanResponse(
            scan_time_utc=result.scan_time_utc,
            benchmark=result.benchmark,
            benchmark_regime=result.benchmark_regime,
            setups=[_to_response(s) for s in result.setups],
            top_setups=[_to_response(s) for s in result.top_setups],
            errors=result.errors,
        )

    @router.post("/api/v1/index-swing/scan", response_model=IndexSwingScanResponse)
    def index_swing_scan(req: IndexSwingScanRequest):
        """Daily-TF index-swing scan: breakout above prior 5-bar swing high on
        the hard-locked QQQ/IWM/SPY universe. Tickers outside the universe are
        rejected with `confluence="universe_violation"`. SQN(20) Bear Volatile
        is a hard skip per backtest evidence (only net-negative regime in
        370-trade 1999-2022 sample).
        """
        try:
            result = scan_index_swing_watchlist(req.tickers)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Index-swing scan failed: {exc}",
            )

        def _to_response(s) -> IndexSwingSetupResponse:
            return IndexSwingSetupResponse(**s.to_dict())

        return IndexSwingScanResponse(
            scan_time_utc=result.scan_time_utc,
            setups=[_to_response(s) for s in result.setups],
            actionable_setups=[_to_response(s) for s in result.actionable_setups],
            errors=result.errors,
        )

    @router.post("/api/v1/regime-levered/scan", response_model=RegimeLeveredScanResponse)
    def regime_levered_scan(req: RegimeLeveredScanRequest):
        """Regime-levered-trend Layer 1 core scan + rule-19 dip-buy check.

        Weekly Full Bull ribbon + own SQN(100) >= +0.7 + Stoch reset-turn,
        gated by broad SQN(100) Bull. BUY verdicts capped at 2 (Layer 1
        concurrent-slot limit). FORWARD-TEST cohort; deployment blocked in
        the main account while R1/R2 recovery rules are active — the
        response carries the gate note.
        """
        try:
            result = scan_regime_levered(
                tickers=req.tickers,
                benchmark=req.benchmark,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Regime-levered scan failed: {exc}",
            )

        def _to_response(s) -> RegimeLeveredSetupResponse:
            return RegimeLeveredSetupResponse(**s.to_dict())

        return RegimeLeveredScanResponse(
            scan_time_utc=result.scan_time_utc,
            benchmark=result.benchmark,
            broad_sqn_100=result.broad_sqn_100,
            broad_regime=result.broad_regime,
            layer1_live=result.layer1_live,
            deployment_note=result.deployment_note,
            setups=[_to_response(s) for s in result.setups],
            core_candidates=[_to_response(s) for s in result.core_candidates],
            dip_buy_signals=[
                DipBuySignalResponse(**d.to_dict()) for d in result.dip_buy_signals
            ],
            errors=result.errors,
        )

    @router.post("/api/v1/free-range-scan", response_model=FreeRangeScanResponse)
    def free_range_scan(req: FreeRangeScanRequest):
        """3-phase scan: QQQ+GLD baseline → user-submitted → free-range top-N.

        Per orchestrator rule 12 in ~/CLAUDE.md. Returns brief snapshots, NOT
        kill sheets — kill sheets only generate when the user picks a
        candidate for actual deployment.
        """
        try:
            result = run_free_range_scan(
                user_tickers=req.user_tickers,
                free_range_cap=req.free_range_cap,
                universe=req.universe,
                enable_free_range=req.enable_free_range,
            )
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Free-range scan failed: {exc}")

        def _snap_to_response(s) -> CandidateSnapshotResponse:
            return CandidateSnapshotResponse(**s.to_dict())

        return FreeRangeScanResponse(
            scan_time_utc=result.scan_time_utc,
            baseline=[_snap_to_response(s) for s in result.baseline],
            user_submitted=[_snap_to_response(s) for s in result.user_submitted],
            free_range=[_snap_to_response(s) for s in result.free_range],
            universe_size=result.universe_size,
            free_range_cap=result.free_range_cap,
            notes=result.notes,
            errors=result.errors,
        )

    return router
