"""Free-range scanner — 3-phase orchestrator.

Implements the workflow from ~/CLAUDE.md "Free-Range Scan" section:

    Phase 1: QQQ + GLD baseline (Tier 1 + Tier 2)
    Phase 2: User-submitted tickers analyzed against Tier 1/2 criteria
    Phase 3: Free-range scan, up to 5 candidates max, tagged Tier 1/Tier 2/both

Hard cap 5; if fewer pass filters, scan returns fewer with an explicit note.
Padding to fill the slot count is forbidden.

The orchestrator is parameterized by a `scan_fn` (defaults to scan_ticker
from src/scan.py) so tests can inject deterministic fixtures without yfinance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from action_gate import classify_lotto_action
from free_range.filters import (
    FREE_RANGE_MIN_SCORE,
    best_direction,
    build_why_now,
    price_band_violation,
)
from free_range.snapshot import CandidateSnapshot, FreeRangeScan
from free_range.universe import free_range_universe, is_etf


import logging as _logging
_logger = _logging.getLogger(__name__)


BASELINE_TICKERS: tuple[str, ...] = ("QQQ", "GLD")


def _tier_tag(direction: str, scan_row: dict[str, Any]) -> str:
    """Tag a snapshot with the tier(s) it qualifies for.

    Heuristic for V1:
      - Strong directional setup (score-driving signals on both Stoch + Stack)
        → "1+2" (qualifies for both weekly trend trader and lotto)
      - Otherwise → "2" (lotto-style short-horizon read on the Daily timeframe)

    True Tier 1 qualification requires Weekly TF — V1 surfaces from the same
    Daily read with the user expected to verify Weekly alignment via the
    `/scan` view before pulling the trigger.
    """
    stack = (scan_row.get("ma_ribbon") or {}).get("stack_state") or ""
    signal = (scan_row.get("stochastic") or {}).get("signal") or ""
    sqn = (scan_row.get("sqn") or {}).get("regime") or ""

    strong_stack = stack.lower() in ("full_bull", "full_bear", "bull_developing", "bear_developing")
    strong_signal = signal.lower() in (
        "bull_cross_oversold", "bear_cross_overbought",
        "bullish_divergence", "bearish_divergence",
        "bull_continuation", "bear_continuation",
    )
    aligned_regime = (
        (direction == "long" and sqn.lower() in ("bull", "strong_bull"))
        or (direction == "short" and sqn.lower() in ("bear", "strong_bear"))
    )

    if strong_stack and (strong_signal or aligned_regime):
        return "1+2"
    return "2"


def build_snapshot(
    ticker: str,
    phase: str,
    scan_row: dict[str, Any],
    *,
    notes: list[str] | None = None,
) -> CandidateSnapshot | None:
    """Construct a CandidateSnapshot from a scan_ticker() row.

    Returns None when the row's score falls below FREE_RANGE_MIN_SCORE on the
    free_range or baseline phase. User-submitted snapshots ignore the floor —
    if the user named the ticker, surface the read regardless.
    """
    direction, score, blockers = best_direction(scan_row)
    if phase != "user" and score < FREE_RANGE_MIN_SCORE:
        return None

    snap_notes = list(notes or [])
    snap_notes.extend(blockers)
    if is_etf(ticker):
        snap_notes.append("ETF — price band exempt")

    return CandidateSnapshot(
        ticker=ticker.upper(),
        phase=phase,  # type: ignore[arg-type]
        tier=_tier_tag(direction, scan_row),
        direction=direction,  # type: ignore[arg-type]
        is_etf=is_etf(ticker),
        current_price=scan_row.get("close"),
        ma_stack=(scan_row.get("ma_ribbon") or {}).get("stack_state"),
        stoch_zone=(scan_row.get("stochastic") or {}).get("zone"),
        stoch_signal=(scan_row.get("stochastic") or {}).get("signal"),
        sqn_100_regime=(scan_row.get("sqn") or {}).get("regime"),
        sqn_20_regime=(scan_row.get("sqn") or {}).get("regime_20"),
        score=score,
        why_now=build_why_now(direction, scan_row),
        notes=snap_notes,
    )


def _attach_lotto_verdict(
    snap: CandidateSnapshot,
    daily_row: dict[str, Any],
    scan_fn: Callable[..., dict[str, Any]],
) -> None:
    """Attempt a 2H scan + classify_lotto_action; attach the verdict to
    `snap`. Swallows all errors — verdict is best-effort enrichment, not
    load-bearing for the snapshot itself.

    Skips entirely if scan_fn can't accept a timeframe kwarg (legacy
    single-arg fixtures used by older tests)."""
    if snap.direction not in ("long", "short"):
        return
    try:
        two_h_row = scan_fn(snap.ticker, timeframe="2h")
    except TypeError:
        # Old fixture signature `def fn(ticker)` — verdict not computable.
        return
    except Exception as exc:
        _logger.debug("2H scan failed for %s: %s", snap.ticker, exc)
        return
    try:
        reads = {"1d": daily_row, "2h": two_h_row}
        verdict = classify_lotto_action(reads, snap.direction)
        snap.action_verdict = verdict.to_dict()
    except Exception:
        _logger.exception("verdict classification failed for %s", snap.ticker)


def _scan_one(
    ticker: str,
    phase: str,
    scan_fn: Callable[..., dict[str, Any]],
    errors: dict[str, str],
    *,
    enforce_price_band: bool,
) -> CandidateSnapshot | None:
    """Single-ticker scan + filters. Errors get logged, not raised."""
    try:
        scan_row = scan_fn(ticker)
    except Exception as exc:
        errors[ticker.upper()] = str(exc)
        return None

    if enforce_price_band:
        price_violation = price_band_violation(ticker, scan_row.get("close"))
        if price_violation:
            errors[ticker.upper()] = price_violation
            return None

    snap = build_snapshot(ticker, phase, scan_row)
    if snap is not None:
        _attach_lotto_verdict(snap, scan_row, scan_fn)
    return snap


def run_free_range_scan(
    user_tickers: list[str] | None = None,
    *,
    scan_fn: Callable[..., dict[str, Any]] | None = None,
    free_range_cap: int = 5,
    universe_override: tuple[str, ...] | None = None,
    universe: str | list[str] = ("nasdaq_100", "sp500_top_50", "russell_2000_top_50"),
    enable_free_range: bool = True,
) -> FreeRangeScan:
    """Run the 3-phase free-range scan (price + indicator only).

    Options liquidity is NOT auto-gated — yfinance options data is stale
    relative to brokerage feeds and using it would smuggle bad data into a
    discipline-engine claim. Per-candidate manual options entry happens at
    the kill-sheet layer (paste-from-brokerage via src/options_input).

    `scan_fn` defaults to scan_ticker from src/scan.py — left lazy here to
    avoid a circular import on module load.

    `universe` picks the Phase 3 candidate list(s). May be a single name
    ("nasdaq_100" | "sp500_top_50" | "russell_2000_top_50") or a list of
    names. All universes' passers are ranked TOGETHER by score and
    `free_range_cap` is applied GLOBALLY (orchestrator rule 11: hard cap 5
    candidates total, quality-ranked — not 5 per index). Each free-range
    snapshot carries a `source_universe` attribute so the frontend can
    group them by index; the tag is display metadata, not a quota.

    `universe_override` (legacy / tests) takes precedence: when supplied,
    Phase 3 scans only that tuple, tagged with source_universe="custom".

    `enable_free_range=False` skips Phase 3 entirely — returns baseline +
    user-submitted only. Used by views that want a fast read on the QQQ+GLD
    baseline (LottoView verdict banner) without paying the ~30s sweep cost.
    """
    if scan_fn is None:
        from scan import scan_ticker
        scan_fn = scan_ticker

    user_tickers_norm = [t.upper() for t in (user_tickers or [])]
    errors: dict[str, str] = {}
    notes: list[str] = []

    # ─ Phase 1: baseline ─
    baseline: list[CandidateSnapshot] = []
    for tkr in BASELINE_TICKERS:
        snap = _scan_one(tkr, "baseline", scan_fn, errors, enforce_price_band=False)
        if snap is not None:
            baseline.append(snap)

    # ─ Phase 2: user-submitted ─
    user_snaps: list[CandidateSnapshot] = []
    for tkr in user_tickers_norm:
        if tkr in BASELINE_TICKERS:
            continue  # already covered in baseline
        snap = _scan_one(tkr, "user", scan_fn, errors, enforce_price_band=False)
        if snap is not None:
            user_snaps.append(snap)

    # ─ Phase 3: free-range top-N (skipped when enable_free_range=False) ─
    free_range: list[CandidateSnapshot] = []
    universe_size = 0
    if enable_free_range:
        excluded = frozenset(BASELINE_TICKERS) | frozenset(user_tickers_norm)

        if universe_override is not None:
            # Legacy/test path — single custom universe, untagged.
            scan_groups: list[tuple[str, tuple[str, ...]]] = [
                ("custom", universe_override),
            ]
        else:
            universe_names: list[str] = (
                [universe] if isinstance(universe, str) else list(universe)
            )
            scan_groups = [
                (name, free_range_universe(excluded, universe=name))
                for name in universe_names
            ]

        all_passers: list[CandidateSnapshot] = []
        for uni_name, candidate_list in scan_groups:
            for tkr in candidate_list:
                snap = _scan_one(
                    tkr, "free_range", scan_fn, errors, enforce_price_band=True,
                )
                if snap is None:
                    continue
                snap.source_universe = uni_name
                all_passers.append(snap)
            universe_size += len(candidate_list)

        # GLOBAL ranking + cap across all universes — orchestrator rule 11:
        # hard cap `free_range_cap` candidates TOTAL, ranked by setup
        # quality (was per-universe, which allowed cap×3 with the default
        # three indexes). Padding still forbidden — if fewer pass, the note
        # says so; the scan does not invent setups.
        all_passers.sort(key=lambda s: s.score, reverse=True)
        if len(all_passers) < free_range_cap:
            notes.append(
                f"only {len(all_passers)} candidate(s) passed filters "
                f"across all universes (cap {free_range_cap}). Padding "
                "the slot count is forbidden — the scan does not "
                "invent setups."
            )
        free_range = all_passers[:free_range_cap]
        if free_range:
            # IVR is NOT auto-checked at scan time (no reliable free IV
            # source — see module docstring). The IVR>70 anti-pattern gate
            # fires at the kill-sheet layer from pasted broker data; until
            # then it is the user's manual check.
            notes.append(
                "⚠️ IVR not auto-checked — verify IV Rank < 70% "
                "(MarketChameleon) before kill-sheeting any candidate."
            )
    else:
        notes.append("Free-range Phase 3 skipped (baseline + user-submitted only)")

    return FreeRangeScan(
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        baseline=baseline,
        user_submitted=user_snaps,
        free_range=free_range,
        universe_size=universe_size,
        free_range_cap=free_range_cap,
        notes=notes,
        errors=errors,
    )
