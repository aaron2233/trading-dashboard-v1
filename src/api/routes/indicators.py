"""Per-ticker indicator + sparkline + action-gate verdict reads."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from api.models import ScanResult, SparklineResponse
from api.routes._helpers import scan_to_response
from scan import compute_multi_tf, scan_ticker


def make_indicators_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/scan/{ticker}", response_model=ScanResult)
    def scan(ticker: str,
             timeframe: str = Query("1d", description="1d | 1wk | 4h | 2h"),
             period: str | None = Query(None,
                 description="yfinance period; defaults are timeframe-aware")):
        try:
            row = scan_ticker(ticker.upper(), period=period, timeframe=timeframe)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Scan failed: {exc}")
        return scan_to_response(row)

    @router.get("/api/v1/sparkline/{ticker}", response_model=SparklineResponse)
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

    @router.get("/api/v1/scan/{ticker}/multi", response_model=dict[str, ScanResult | dict])
    def scan_multi(ticker: str):
        rows = compute_multi_tf(
            ticker.upper(), timeframes=("1d", "1wk", "4h"),
        )
        out: dict[str, Any] = {}
        for tf, row in rows.items():
            if "error" in row:
                out[tf] = {"error": row["error"], "ticker": row.get("ticker")}
            else:
                out[tf] = scan_to_response(row).model_dump()
        return out

    @router.get("/api/v1/action-gate/verdict/{ticker}")
    def action_gate_verdict(
        ticker: str,
        skill: str = Query(..., pattern="^(lotto|weekly)$"),
        direction: str = Query(..., pattern="^(long|short)$"),
    ) -> dict[str, Any]:
        """Compute an action verdict for a single ticker against a chosen
        skill context. Used by ScanView's opt-in verdict feature — user
        picks a tier (lotto/weekly) + direction; backend fetches
        the right TFs and runs the matching classifier.

        Skill → required reads:
          lotto  → 1d + 2h
          weekly → 1wk
        """
        from action_gate import (
            classify_lotto_action,
            classify_weekly_trend_action,
        )
        ticker_u = ticker.upper()
        try:
            if skill == "weekly":
                weekly_row = scan_ticker(ticker_u, timeframe="1wk")
                verdict = classify_weekly_trend_action({"1wk": weekly_row}, direction)
            else:
                daily = scan_ticker(ticker_u, timeframe="1d")
                two_h = scan_ticker(ticker_u, timeframe="2h")
                reads = {"1d": daily, "2h": two_h}
                verdict = classify_lotto_action(reads, direction)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"verdict computation failed for {ticker_u}: {exc}",
            )
        return verdict.to_dict()

    return router
