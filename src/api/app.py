"""FastAPI application — HTTP wrappers around the existing CLI surface.

API version /api/v1/ baked in from day one (per Winston's architectural
recommendation: future agent terminal will consume a superset of these endpoints
and unversioned URLs would break notebooks written against v1).

Persistence (positions.json, events.jsonl) and config (config.yaml) load from
the same locations the CLIs use; a fresh user gets the baked-in defaults.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from api.models import (
    AlertResponse,
    CandidateSnapshotResponse,
    DashboardStateResponse,
    OptionsTextRequest,
    ParsedOptionsResponse,
    UnreviewedWeekResponse,
    ClosePositionRequest,
    DevilCategoryResult,
    DevilReportResponse,
    DisciplineScoreOverridesRequest,
    DisciplineScoreResponse,
    DisciplineStatsResponse,
    FocusOutcomeResponse,
    FocusRecentSummaryResponse,
    FocusSetup,
    FocusTopSetupSummary,
    FreeRangeScanRequest,
    FreeRangeScanResponse,
    HealthResponse,
    JournalReportResponse,
    JournalStatsResponse,
    KillSheetRequest,
    KillSheetResponse,
    LockdownRequest,
    LottoCooldownResponse,
    LottoStateResponse,
    LottoTradeSummaryResponse,
    WeeklyScanRequest,
    WeeklyScanResponse,
    WeeklySetupResponse,
    MatchedPositionResponse,
    OpenPositionRequest,
    PositionResponse,
    RuleResultResponse,
    RuleViolationResponse,
    ScanResult,
    SparklineResponse,
    SundayScanResponse,
    SundayScanSummaryResponse,
    WeeklyReviewResponse,
)
from config import load_config
from focus import (
    build_outcome,
    list_recent_sunday_scans,
    load_sunday_scan,
    persist_sunday_scan,
    run_sunday_scan,
    summarize_recent_outcomes,
)
from free_range import run_free_range_scan
from lotto import LOTTO_ACCOUNT_KEY, check_lotto_cooldown, compute_lotto_state
from options_input import parse_options_text
from vision.options_extractor import ExtractError, extract_options_chain
from weekly_trend import scan_weekly_watchlist
from kill_sheet.builder import build_standard
from kill_sheet.options import OptionsStructure, compute_dte
from positions import (
    FOCUS_TICKERS,
    PositionStore,
    check_focus_options_structure,
    check_focus_trade,
    check_proposed_trade,
    check_tier_portfolio_trade,
    evaluate_all_open,
)
from positions.model import Position
from discipline import (
    DisciplineStore,
    compute_dashboard_state,
    compute_discipline_stats,
    get_or_compute_weekly,
    is_legacy_position,
    score_trade,
)
from discipline.model import RuleResult
from scan import compute_multi_tf, scan_ticker
from trade_devil import AGGREGATE_KILL, run_devil
from journal import (
    by_account as journal_by_account,
    by_direction as journal_by_direction,
    by_instrument as journal_by_instrument,
    compute_stats,
)


VERSION = "0.1.0"


def _scan_to_response(row: dict[str, Any]) -> ScanResult:
    return ScanResult(
        ticker=row["ticker"],
        timeframe=row.get("timeframe", "1d"),
        bar_date=row.get("bar_date"),
        close=row.get("close"),
        ma_ribbon=row.get("ma_ribbon", {}) or {},
        stochastic=row.get("stochastic", {}) or {},
        sqn=row.get("sqn", {}) or {},
    )


def _devil_to_response(report) -> DevilReportResponse:
    return DevilReportResponse(
        aggregate=report.aggregate,
        kills=report.kills,
        flags=report.flags,
        passes=report.passes,
        triggered_by_risk_threshold=report.triggered_by_risk_threshold,
        results=[
            DevilCategoryResult(
                category=r.category, verdict=r.verdict.value, reason=r.reason,
            )
            for r in report.results
        ],
    )


def _position_to_response(p: Position) -> PositionResponse:
    return PositionResponse(**p.to_dict())


def _stats_to_response(stats) -> JournalStatsResponse:
    return JournalStatsResponse(**stats.to_dict())


def create_app(
    store_factory=PositionStore,
    config_loader=load_config,
    cache_factory=None,
) -> FastAPI:
    """Build a FastAPI app. store_factory, config_loader, and cache_factory
    are injectable for tests."""
    from api.query_routes import make_query_router
    from storage.cache import get_cache

    if cache_factory is None:
        cache_factory = get_cache
    app = FastAPI(
        title="Trading Dashboard API",
        version=VERSION,
        description=(
            "HTTP wrappers around the discipline-engine CLI surface "
            "(scan, kill sheet, positions, alerts, journal)."
        ),
    )

    # CORS for local React dev (Vite default port 5173). Browser-only project,
    # localhost-only deploy, so wide-open is fine in V1.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/v1/health", response_model=HealthResponse)
    def health():
        return HealthResponse(status="ok", version=VERSION)

    # ─── Lotto state ──────────────────────────────────────────────────────────

    @app.get("/api/v1/lotto/state", response_model=LottoStateResponse)
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

    # ─── Weekly trend scan ────────────────────────────────────────────────────

    @app.post("/api/v1/weekly/scan", response_model=WeeklyScanResponse)
    def weekly_scan(req: WeeklyScanRequest):
        """Sunday-scan workflow: weekly TF + benchmark regime over a watchlist."""
        if not req.tickers:
            raise HTTPException(status_code=400, detail="tickers list cannot be empty")
        try:
            result = scan_weekly_watchlist(
                req.tickers, benchmark=req.benchmark, top_n=req.top_n,
            )
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

    # ─── Regime Health ────────────────────────────────────────────────────────

    @app.get("/api/v1/regime-health/snapshot")
    def regime_health_snapshot() -> dict[str, Any]:
        """Return today's Regime Health snapshot. Reads cached JSON if it
        exists and is <12h old; otherwise fetches fresh and persists.
        Per-tier failures degrade gracefully — the response always includes
        every tier with its readings (some may be 'unknown' or 'error')."""
        from regime_health import (
            RegimeHealthStore,
            assemble_snapshot,
            is_snapshot_fresh,
        )
        store = RegimeHealthStore(cache=cache_factory())
        cached = store.load_today()
        if cached is not None and is_snapshot_fresh(cached):
            return cached.to_dict()
        try:
            snapshot = assemble_snapshot()
        except Exception as exc:
            # Total failure — return an empty snapshot rather than 500.
            # Frontend renders a "regime health unavailable" panel.
            from regime_health.model import RegimeHealthSnapshot
            placeholder = RegimeHealthSnapshot.empty()
            placeholder.overall_drivers = [f"snapshot assembly failed: {exc}"]
            return placeholder.to_dict()
        try:
            store.save(snapshot)
        except Exception:
            # Persistence failure shouldn't block the response — the user
            # still gets the live read. Logged for diagnosis.
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "regime_health snapshot persistence failed",
            )
        return snapshot.to_dict()

    @app.post("/api/v1/regime-health/refresh")
    def regime_health_refresh() -> dict[str, Any]:
        """Force a fresh snapshot fetch + persist, ignoring cache freshness."""
        from regime_health import RegimeHealthStore, assemble_snapshot
        snapshot = assemble_snapshot()
        store = RegimeHealthStore(cache=cache_factory())
        try:
            store.save(snapshot)
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "regime_health snapshot persistence failed (refresh)",
            )
        return snapshot.to_dict()

    @app.get("/api/v1/regime-health/history")
    def regime_health_history(
        days: int = Query(30, ge=1, le=365),
    ) -> dict[str, Any]:
        """Return the most recent N snapshots (newest first), filesystem-backed.
        SQLite cache is queryable too but JSON is canonical and the directory
        scan is plenty fast for the sizes we're talking about (one snapshot
        per day; 30-365 entries max)."""
        from regime_health import RegimeHealthStore
        store = RegimeHealthStore(cache=cache_factory())
        snapshots = store.list_recent(limit=days)
        return {"snapshots": [s.to_dict() for s in snapshots]}

    # ─── Dashboard state ──────────────────────────────────────────────────────

    @app.get("/api/v1/dashboard/state", response_model=DashboardStateResponse)
    def dashboard_state():
        """Aggregate stage + balance + unreviewed-weeks for the UX banner."""
        config = config_loader()
        store = store_factory()
        closed_positions = [p for p in store.list_all() if p.status == "closed"]
        state = compute_dashboard_state(config, closed_positions)
        return DashboardStateResponse(
            stage=state.stage,
            stage_reminder=state.stage_reminder,
            account_balance_usd=state.account_balance_usd,
            threshold_usd=state.threshold_usd,
            progress_to_threshold=state.progress_to_threshold,
            realized_pnl_usd=state.realized_pnl_usd,
            base_balance_usd=state.base_balance_usd,
            unreviewed_weeks=[
                UnreviewedWeekResponse(
                    week_start=w.week_start,
                    week_end=w.week_end,
                    closed_trade_count=w.closed_trade_count,
                )
                for w in state.unreviewed_weeks
            ],
        )

    # ─── Scan ─────────────────────────────────────────────────────────────────

    @app.get("/api/v1/scan/{ticker}", response_model=ScanResult)
    def scan(ticker: str,
             timeframe: str = Query("1d", description="1d | 1wk | 4h | 2h"),
             period: str | None = Query(None,
                 description="yfinance period; defaults are timeframe-aware")):
        try:
            row = scan_ticker(ticker.upper(), period=period, timeframe=timeframe)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")
        return _scan_to_response(row)

    @app.get("/api/v1/sparkline/{ticker}", response_model=SparklineResponse)
    def sparkline(
        ticker: str,
        timeframe: str = Query("1d", description="1d | 1wk | 4h | 2h"),
        count: int = Query(30, ge=5, le=300, description="Bar count, 5-300"),
    ):
        """Compact close-only price series for inline mini-charts.

        Reuses the same data loaders as scan_ticker but returns just dates +
        closes — much smaller payload than a full ScanResult. Used by the
        Sparkline frontend component for tables/cards.
        """
        from data.crypto_loader import is_crypto_symbol, load_crypto_bars
        from data.yfinance_loader import load_bars

        ticker_u = ticker.upper()
        try:
            if is_crypto_symbol(ticker_u):
                bars = load_crypto_bars(ticker_u, timeframe=timeframe, count=count)
            else:
                bars = load_bars(ticker_u, interval=timeframe)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Sparkline fetch failed: {exc}")

        if bars.empty:
            raise HTTPException(status_code=404, detail=f"No bars for {ticker_u}")

        # Trim to requested count from the tail (most recent) for non-crypto;
        # crypto loader already accepts a count arg.
        recent = bars.tail(count)
        return SparklineResponse(
            ticker=ticker_u,
            timeframe=timeframe,
            dates=[idx.strftime("%Y-%m-%d") for idx in recent.index],
            closes=[float(c) for c in recent["close"].tolist()],
        )

    @app.get("/api/v1/scan/{ticker}/multi", response_model=dict[str, ScanResult | dict])
    def scan_multi(ticker: str):
        rows = compute_multi_tf(
            ticker.upper(), timeframes=("1d", "1wk", "4h"),
        )
        out: dict[str, Any] = {}
        for tf, row in rows.items():
            if "error" in row:
                out[tf] = {"error": row["error"], "ticker": row.get("ticker")}
            else:
                out[tf] = _scan_to_response(row).model_dump()
        return out

    # ─── Focus / Sunday scan ──────────────────────────────────────────────────

    @app.get("/api/v1/focus/sunday-scan", response_model=SundayScanResponse)
    def focus_sunday_scan(
        persist: bool = Query(True, description="Write scan to ~/.trading-dashboard/sunday_scans/"),
    ):
        result = run_sunday_scan(scan_fn=lambda t: scan_ticker(t))
        if persist:
            try:
                persist_sunday_scan(result)
            except OSError as exc:
                # Disk is full / permission denied / etc — log and return the
                # scan anyway. The user still gets the read; they just don't
                # get a saved snapshot.
                import sys as _sys
                print(f"⚠ Failed to persist Sunday scan: {exc}", file=_sys.stderr)
        return SundayScanResponse(
            scan_time_utc=result.scan_time_utc,
            spy=_scan_to_response(result.spy) if result.spy else None,
            qqq=_scan_to_response(result.qqq) if result.qqq else None,
            gld=_scan_to_response(result.gld) if result.gld else None,
            setups=[FocusSetup(**s.to_dict()) for s in result.setups],
            recommendation=result.recommendation,
            headline=result.headline,
            errors=result.errors,
        )

    @app.get(
        "/api/v1/focus/sunday-scan/recent",
        response_model=list[SundayScanSummaryResponse],
    )
    def focus_recent_scans(
        limit: int = Query(10, ge=1, le=50),
    ):
        summaries = list_recent_sunday_scans(limit=limit)
        return [
            SundayScanSummaryResponse(
                date=s.date,
                scan_time_utc=s.scan_time_utc,
                recommendation=s.recommendation,  # type: ignore[arg-type]
                headline=s.headline,
                top_setup=FocusTopSetupSummary(**s.top_setup) if s.top_setup else None,
            )
            for s in summaries
        ]

    @app.get(
        "/api/v1/focus/summary",
        response_model=FocusRecentSummaryResponse,
    )
    def focus_summary(
        weeks: int = Query(4, ge=1, le=52),
    ):
        store = store_factory()
        summary = summarize_recent_outcomes(
            weeks=weeks, positions=store.list_all(),
        )
        return FocusRecentSummaryResponse(**summary.to_dict())

    @app.get(
        "/api/v1/focus/sunday-scan/{date}/outcome",
        response_model=FocusOutcomeResponse,
    )
    def focus_outcome(date: str):
        try:
            payload = load_sunday_scan(date)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read scan for {date}: {exc}",
            )
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"No saved scan for {date}",
            )
        store = store_factory()
        outcome = build_outcome(date, payload, store.list_all())
        top = outcome.top_setup
        return FocusOutcomeResponse(
            scan_date=outcome.scan_date,
            recommendation=outcome.recommendation,  # type: ignore[arg-type]
            top_setup=FocusTopSetupSummary(
                asset=top["asset"],
                direction=top["direction"],
                score=top["score"],
                status=top["status"],
            ) if top else None,
            window_days=outcome.window_days,
            followed=outcome.followed,
            matched=[MatchedPositionResponse(**m.to_dict())
                     for m in outcome.matched],
            realized_pnl_usd=outcome.realized_pnl_usd,
            open_count=outcome.open_count,
            closed_count=outcome.closed_count,
            aggregate_status=outcome.aggregate_status,  # type: ignore[arg-type]
        )

    @app.get(
        "/api/v1/focus/sunday-scan/{date}",
        response_model=SundayScanResponse,
    )
    def focus_sunday_scan_by_date(date: str):
        try:
            payload = load_sunday_scan(date)
        except (json.JSONDecodeError, OSError) as exc:
            raise HTTPException(
                status_code=500,
                detail=f"Failed to read scan for {date}: {exc}",
            )
        if payload is None:
            raise HTTPException(
                status_code=404,
                detail=f"No saved scan for {date}",
            )

        def _scan_or_none(row: Any) -> ScanResult | None:
            return _scan_to_response(row) if row else None

        return SundayScanResponse(
            scan_time_utc=payload.get("scan_time_utc", ""),
            spy=_scan_or_none(payload.get("spy")),
            qqq=_scan_or_none(payload.get("qqq")),
            gld=_scan_or_none(payload.get("gld")),
            setups=[FocusSetup(**s) for s in payload.get("setups", [])],
            recommendation=payload.get("recommendation", "cash"),
            headline=payload.get("headline", ""),
            errors=payload.get("errors", {}),
        )

    # ─── Free-range scan ──────────────────────────────────────────────────────

    @app.post("/api/v1/free-range-scan", response_model=FreeRangeScanResponse)
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

    # ─── Options input (paste / screenshot) ───────────────────────────────────

    @app.post(
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

    @app.post(
        "/api/v1/options/extract/screenshot",
        response_model=ParsedOptionsResponse,
    )
    async def options_extract_screenshot(
        image: UploadFile = File(...),
        ticker: str = Form(""),
        target_strike: float | None = Form(None),
        target_expiry: str | None = Form(None),
        contract_type: str | None = Form(None),
    ):
        """Extract options data from a brokerage screenshot via Anthropic vision.

        Requires ANTHROPIC_API_KEY to be set in the server environment. The
        target_* hints help the vision model pick the right row when the
        screenshot shows multiple strikes.
        """
        image_bytes = await image.read()
        if not image_bytes:
            raise HTTPException(status_code=400, detail="Empty image upload")

        media_type = image.content_type or "image/png"
        try:
            payload = extract_options_chain(
                image_bytes=image_bytes,
                media_type=media_type,
                ticker=ticker,
                target_strike=target_strike,
                target_expiry=target_expiry,
                contract_type=contract_type,
            )
        except ExtractError as exc:
            raise HTTPException(status_code=502, detail=f"Extraction failed: {exc}")

        # Vision payload uses `open_interest`/`bid_ask_spread` — already aligned
        # with ParsedOptionsResponse field names. Surface only the fields that
        # came back non-null in source_fields, mirroring the paste path.
        source_fields = [k for k, v in payload.items() if v is not None]
        return ParsedOptionsResponse(
            strike=payload.get("strike"),
            premium=payload.get("premium"),
            expiry=payload.get("expiry"),
            contract_type=payload.get("contract_type"),
            iv_rank=payload.get("iv_rank"),
            open_interest=payload.get("open_interest"),
            bid_ask_spread=payload.get("bid_ask_spread"),
            source_fields=source_fields,
            warnings=[],
            extraction_source="screenshot",
        )

    # ─── Kill sheet ───────────────────────────────────────────────────────────

    @app.post("/api/v1/kill_sheet", response_model=KillSheetResponse)
    def kill_sheet(req: KillSheetRequest):
        config = config_loader()
        try:
            account = config.account(req.account)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        if req.focus and req.ticker.upper() not in FOCUS_TICKERS:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"focus mode restricts tickers to "
                    f"{', '.join(sorted(FOCUS_TICKERS))}; got {req.ticker.upper()}"
                ),
            )

        try:
            scan_row = scan_ticker(req.ticker.upper(), period=req.period)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")

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
        )

        # Pre-check: account rules
        rules_blocked = False
        violations: list[RuleViolationResponse] = []
        store = store_factory()
        open_positions: list = []
        if not req.skip_rules:
            open_positions = store.list_open()
            raw = check_proposed_trade(
                proposed_max_loss_usd=sheet.max_risk_usd,
                account=account,
                account_key=req.account,
                open_positions=open_positions,
            )
            if req.focus:
                closed_positions = [
                    p for p in store.list_all() if p.status == "closed"
                ]
                raw = list(raw) + check_focus_trade(
                    ticker=req.ticker,
                    direction=req.direction,
                    open_positions=open_positions,
                    closed_positions=closed_positions,
                )
                raw = list(raw) + check_focus_options_structure(
                    ticker=req.ticker,
                    direction=req.direction,
                    max_loss_usd=sheet.max_risk_usd,
                    dte=sheet.options.dte if sheet.options else None,
                )

            # ─ Tier 1+2 portfolio rule (orchestrator rule 11) ─
            # Fires whenever ticker is QQQ/GLD, independent of --focus.
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

            violations = [RuleViolationResponse(**v.to_dict()) for v in raw]
            if violations and not req.bypass_rules:
                rules_blocked = True

        devil_payload = None
        if not req.skip_devil and not rules_blocked:
            report = run_devil(
                sheet, force=req.force_devil, open_positions=open_positions,
            )
            if report is not None:
                devil_payload = _devil_to_response(report)

        # Phase B: persist authorized kill sheets so the position-open
        # endpoint can validate kill_sheet_id against the canonical record.
        # Rejected kill sheets stay transient — they're diagnostic, not
        # load-bearing.
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

    @app.get("/api/v1/kill_sheet/{kill_sheet_id}")
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

    # ─── Positions ────────────────────────────────────────────────────────────

    @app.get("/api/v1/positions", response_model=list[PositionResponse])
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
        return [_position_to_response(p) for p in all_positions]

    @app.post("/api/v1/positions", response_model=PositionResponse, status_code=201)
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
            if ks.status != "AUTHORIZED":
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"kill_sheet_id={req.kill_sheet_id!r} is {ks.status}, "
                        "not AUTHORIZED. Resolve the rejection or document a "
                        "divergence thesis and regenerate."
                    ),
                )
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
            # Discipline attestation must have authorized entry — otherwise the
            # kill sheet rendered for review but the user didn't pass §8.
            if ks.discipline_attestation is not None and not ks.discipline_attestation.entry_authorized:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "kill sheet did not pass discipline §8 attestation "
                        "(entry_authorized=false)"
                    ),
                )
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
                position = Position.open_options_position(
                    ticker=req.ticker,
                    direction=req.direction,
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
        store.add(position)
        return _position_to_response(position)

    @app.get("/api/v1/positions/alerts", response_model=list[AlertResponse])
    def position_alerts():
        store = store_factory()
        by_position = evaluate_all_open(store)
        flat: list[AlertResponse] = []
        for alerts in by_position.values():
            for a in alerts:
                flat.append(AlertResponse(**a.to_dict()))
        return flat

    @app.get("/api/v1/positions/{position_id}", response_model=PositionResponse)
    def get_position(position_id: str):
        store = store_factory()
        try:
            return _position_to_response(store.get(position_id))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/v1/positions/{position_id}/close", response_model=PositionResponse)
    def close_position(position_id: str, req: ClosePositionRequest):
        store = store_factory()
        try:
            position = store.close(position_id, pnl_usd=req.pnl, notes=req.notes)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc))

        # Auto-score on close (Tier 3 closure). Skip for legacy positions and
        # any failure here is non-fatal — the close itself succeeded.
        if not is_legacy_position(position.closed_date):
            try:
                score = score_trade(position)
                DisciplineStore().save_score(score)
            except Exception:
                # Don't let scoring failure block the close response. Log and
                # the user can run `python -m discipline score <id>` later.
                import sys as _sys
                print(f"⚠ Auto-score failed for {position_id}", file=_sys.stderr)

        return _position_to_response(position)

    # ─── Journal ──────────────────────────────────────────────────────────────

    @app.get("/api/v1/journal/stats", response_model=JournalStatsResponse)
    def journal_stats(account: str | None = None):
        store = store_factory()
        positions = store.list_all()
        if account:
            positions = [p for p in positions if p.account_key == account]
        return _stats_to_response(compute_stats(positions, label=account or "all"))

    @app.get("/api/v1/journal/breakdown", response_model=JournalReportResponse)
    def journal_breakdown():
        store = store_factory()
        positions = store.list_all()
        return JournalReportResponse(
            overall=_stats_to_response(compute_stats(positions, label="all")),
            by_account={k: _stats_to_response(s) for k, s in journal_by_account(positions).items()},
            by_instrument={k: _stats_to_response(s) for k, s in journal_by_instrument(positions).items()},
            by_direction={k: _stats_to_response(s) for k, s in journal_by_direction(positions).items()},
        )

    @app.get("/api/v1/journal/recent", response_model=list[PositionResponse])
    def journal_recent(limit: int = Query(10, ge=1, le=200)):
        store = store_factory()
        closed = [p for p in store.list_all() if p.status == "closed"]
        closed.sort(key=lambda p: p.closed_date or "", reverse=True)
        return [_position_to_response(p) for p in closed[:limit]]

    # ─── Discipline ───────────────────────────────────────────────────────────

    def _to_discipline_response(score) -> DisciplineScoreResponse:
        return DisciplineScoreResponse(**score.to_dict())

    @app.get(
        "/api/v1/discipline/score/{position_id}",
        response_model=DisciplineScoreResponse,
    )
    def get_discipline_score(position_id: str, score_legacy: bool = Query(False)):
        """Fetch (or compute) a discipline score for a closed position."""
        dstore = DisciplineStore()
        if dstore.has_score(position_id):
            return _to_discipline_response(dstore.load_score(position_id))

        pstore = store_factory()
        try:
            position = pstore.get(position_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        if position.status != "closed":
            raise HTTPException(
                status_code=409,
                detail=f"Position {position_id} is not closed (status={position.status})",
            )
        if is_legacy_position(position.closed_date) and not score_legacy:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Position closed before discipline-layer rollout "
                    f"({position.closed_date}); pass score_legacy=true to override"
                ),
            )

        # Active pyramid lookup
        pyramid_active: bool | None = False
        try:
            for pyr in PyramidStore().list_active():
                if (
                    pyr.ticker.upper() == position.ticker.upper()
                    and pyr.direction.lower() == position.direction.lower()
                ):
                    pyramid_active = True
                    break
        except Exception:
            pyramid_active = None

        score = score_trade(position, pyramid_active_at_entry=pyramid_active)
        dstore.save_score(score)
        return _to_discipline_response(score)

    @app.post(
        "/api/v1/discipline/score/{position_id}",
        response_model=DisciplineScoreResponse,
    )
    def update_discipline_score(position_id: str, req: DisciplineScoreOverridesRequest):
        """Apply user attestations / rule overrides to a previously-scored trade."""
        dstore = DisciplineStore()
        if not dstore.has_score(position_id):
            # Auto-compute first; user is supplying overrides on top of base score
            pstore = store_factory()
            try:
                position = pstore.get(position_id)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            if position.status != "closed":
                raise HTTPException(status_code=409, detail="position not closed")
            if is_legacy_position(position.closed_date) and not req.score_legacy:
                raise HTTPException(status_code=409, detail="legacy position; pass score_legacy=true")
            score = score_trade(position)
        else:
            score = dstore.load_score(position_id)

        if req.notes is not None:
            score.notes = req.notes
        if req.profitable_violation_resolution is not None:
            score.profitable_violation_resolution = req.profitable_violation_resolution

        if req.rule_overrides:
            updated_rules: list[RuleResult] = []
            for r in score.rules:
                ov = req.rule_overrides.get(r.rule_id)
                if ov is not None:
                    updated_rules.append(RuleResult(
                        rule_id=ov.rule_id,
                        score=ov.score,
                        auto_evaluated=False,
                        note=ov.note,
                    ))
                else:
                    updated_rules.append(r)
            score.rules = updated_rules
            # Recompute aggregate metrics
            score.score_numerator = sum(1 for r in score.rules if r.score == "Y")
            n_count = sum(1 for r in score.rules if r.score == "N")
            score.score_denominator = score.score_numerator + n_count
            pnl = score.pnl_usd or 0.0
            score.profitable_violation = (n_count > 0) and (pnl > 0)

        dstore.save_score(score)
        return _to_discipline_response(score)

    @app.get(
        "/api/v1/discipline/scores",
        response_model=list[DisciplineScoreResponse],
    )
    def list_discipline_scores(limit: int = Query(20, ge=1, le=200)):
        dstore = DisciplineStore()
        scores = dstore.list_scores()
        # Sort by closed_at descending (most recent first)
        scores.sort(key=lambda s: s.closed_at or "", reverse=True)
        return [_to_discipline_response(s) for s in scores[:limit]]

    @app.get(
        "/api/v1/discipline/stats",
        response_model=DisciplineStatsResponse,
    )
    def discipline_stats(range_: str = Query("all", alias="range",
                                              pattern="^(week|month|all)$")):
        from datetime import date, timedelta
        dstore = DisciplineStore()
        scores = list(dstore.iter_scores())
        if range_ == "week":
            cutoff = date.today() - timedelta(days=7)
            scores = [s for s in scores if s.closed_at and s.closed_at[:10] >= cutoff.isoformat()]
        elif range_ == "month":
            cutoff = date.today() - timedelta(days=30)
            scores = [s for s in scores if s.closed_at and s.closed_at[:10] >= cutoff.isoformat()]
        stats = compute_discipline_stats(scores, label=range_)
        return DisciplineStatsResponse(**stats.to_dict())

    @app.get(
        "/api/v1/discipline/weekly-review",
        response_model=WeeklyReviewResponse,
    )
    def weekly_review_endpoint(
        week_of: str | None = Query(None, description="Date inside target week, YYYY-MM-DD"),
        recompute: bool = Query(False),
    ):
        from datetime import datetime
        week_date = None
        if week_of:
            try:
                week_date = datetime.strptime(week_of, "%Y-%m-%d").date()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
        review = get_or_compute_weekly(week_date, force_recompute=recompute)
        return WeeklyReviewResponse(**review.to_dict())

    @app.post(
        "/api/v1/discipline/weekly-review/{week_start}/lockdown",
        response_model=WeeklyReviewResponse,
    )
    def update_lockdown(week_start: str, req: LockdownRequest):
        dstore = DisciplineStore()
        try:
            review = dstore.update_lockdown(week_start, req.behavior)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return WeeklyReviewResponse(**review.to_dict())

    # ── Query API + L0 agent endpoints ────────────────────────────────────
    app.include_router(make_query_router(cache_factory=cache_factory))

    return app


# Module-level app for `uvicorn api.app:app`
app = create_app()
