"""Pydantic request/response schemas for the Trading Dashboard API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ─── Scan ─────────────────────────────────────────────────────────────────────


class MaRibbonReading(BaseModel):
    ma_10: float | None = None
    ma_20: float | None = None
    ma_50: float | None = None
    ma_200: float | None = None
    stack_state: str | None = None


class StochasticReading(BaseModel):
    k: float | None = None
    d: float | None = None
    zone: str | None = None
    signal: str | None = None


class SqnReading(BaseModel):
    sqn_value: float | None = None
    regime: str | None = None
    # Tactical 20-day window (added Tier 1 — 2026-05-02). Optional so
    # legacy persisted scans without these fields still deserialize.
    sqn_20_value: float | None = None
    regime_20: str | None = None
    diagnostic: str | None = None


class ScanResult(BaseModel):
    ticker: str
    timeframe: str = "1d"
    bar_date: str | None = None
    close: float | None = None
    ma_ribbon: MaRibbonReading
    stochastic: StochasticReading
    sqn: SqnReading


# ─── Kill sheet ───────────────────────────────────────────────────────────────


class KillSheetRequest(BaseModel):
    ticker: str
    direction: Literal["long", "short"]
    account: str = "main"
    intent: Literal["SCALP", "SWING", "TREND CAPTURE", "POSITION"] = "SWING"
    trigger_tf: Literal["2H", "4H", "Daily", "Weekly"] = "Daily"
    conviction: Literal["high", "medium", "speculative", "default"] = "high"

    target: float | None = None
    invalidation: float | None = None
    trigger_desc: str | None = None
    notes: str | None = None

    # Options block
    strike: float | None = None
    premium: float | None = None
    expiry: str | None = None
    contract_type: Literal["call", "put"] | None = None
    delta: float | None = None
    iv_rank: float | None = None
    oi: int | None = None
    spread: float | None = None

    skip_devil: bool = False
    force_devil: bool = False
    skip_rules: bool = False
    bypass_rules: bool = False
    include_multi_tf: bool = True
    period: str | None = None
    focus: bool = False

    # Discipline-layer extensions (Tier 3 closure, 2026-05-02).
    # When supplied, override the regime gate and feed §8 attestation.
    divergence_thesis: str | None = None
    counter_weekly_thesis: str | None = None
    # User-attested booleans clearing each conditional anti-pattern. Keys must
    # match DisciplineAttestation field names; unknown keys are ignored.
    attestation_user_inputs: dict[str, bool] = Field(default_factory=dict)

    # Skill tag — drives skill-specific gates (e.g. weekly-trend asset
    # allowlist) and downstream cohort tagging. Caller passes the skill name
    # as a string; builder resolves to SkillConfig if needed.
    skill: str | None = None


class RuleViolationResponse(BaseModel):
    rule: str
    severity: str
    message: str
    current_value: float
    limit: float


class DevilCategoryResult(BaseModel):
    category: str
    verdict: Literal["KILL", "FLAG", "PASS"]
    reason: str


class DevilReportResponse(BaseModel):
    aggregate: str
    kills: int
    flags: int
    passes: int
    triggered_by_risk_threshold: bool
    results: list[DevilCategoryResult]


class KillSheetResponse(BaseModel):
    kill_sheet: dict[str, Any]
    rendered_text: str
    rule_violations: list[RuleViolationResponse]
    rules_blocked: bool
    devil: DevilReportResponse | None = None
    # Phase B: clients pass this back to POST /api/v1/positions to satisfy
    # the authorization gate. Only populated when the kill sheet was
    # AUTHORIZED (REJECTED kill sheets aren't persisted).
    kill_sheet_id: str | None = None


# ─── Positions ────────────────────────────────────────────────────────────────


class OpenPositionRequest(BaseModel):
    ticker: str
    direction: Literal["long", "short"] = "long"
    instrument: Literal["call", "put", "shares"] = "call"
    account: str = "main"

    strike: float | None = None
    expiry: str | None = None
    premium: float | None = None
    contracts: int | None = None
    shares: float | None = None
    entry_price: float | None = None
    target: float | None = None
    invalidation: float | None = None
    notes: str | None = None

    # Set True to log a genuine second lot identical to an already-open position
    # (the store rejects identical-open contracts by default as double-submits).
    allow_duplicate: bool = False

    # Skill / tier tagging — drives orchestrator rule 11 portfolio scope.
    # Nullable; legacy positions stay None.
    skill: str | None = None
    tier: int | None = None

    # Greeks / IV at entry (snapshot, not updated as the trade ages)
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    iv_rank: float | None = None

    # Premium-level exit thresholds — discipline cut rule lives here
    premium_stop: float | None = None
    premium_target: float | None = None

    # Phase B authorization gate. Pass kill_sheet_id from the AUTHORIZED
    # KillSheetResponse to satisfy the gate. To bypass (legacy / emergency
    # logging only), set bypass_kill_sheet=True and supply a reason in
    # notes — the bypass is recorded for audit.
    kill_sheet_id: str | None = None
    bypass_kill_sheet: bool = False


class ClosePositionRequest(BaseModel):
    pnl: float | None = None
    notes: str | None = None
    # Optional partial-close: if provided and less than remaining contracts,
    # closes only that many contracts and leaves the position open. Omit (or
    # set to remaining) for a full close.
    contracts: int | None = None


class PositionResponse(BaseModel):
    id: str
    ticker: str
    direction: str
    instrument: str
    account_key: str
    status: str
    entry_date: str
    entry_underlying_price: float | None = None
    contracts: int | None = None
    shares: float | None = None
    strike: float | None = None
    expiry: str | None = None
    premium_paid_per_contract: float | None = None
    total_cost_usd: float
    max_loss_usd: float
    target_price: float | None = None
    invalidation_price: float | None = None
    closed_date: str | None = None
    pnl_usd: float | None = None
    notes: str | None = None
    skill: str | None = None
    tier: int | None = None

    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    iv: float | None = None
    iv_rank: float | None = None
    premium_stop: float | None = None
    premium_target: float | None = None
    kill_sheet_id: str | None = None
    partial_exits: list[dict] = Field(default_factory=list)


class AlertResponse(BaseModel):
    position_id: str
    ticker: str
    severity: Literal["action", "warn", "info"]
    rule: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


# ─── Journal ──────────────────────────────────────────────────────────────────


class JournalStatsResponse(BaseModel):
    label: str
    total_trades_closed: int
    open_trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: float
    total_pnl_usd: float
    avg_win_usd: float
    avg_loss_usd: float
    largest_win_usd: float
    largest_loss_usd: float
    profit_factor: float | None
    expectancy_usd: float
    total_cost_invested_usd: float
    total_max_loss_taken_usd: float


class JournalReportResponse(BaseModel):
    overall: JournalStatsResponse
    by_account: dict[str, JournalStatsResponse]
    by_instrument: dict[str, JournalStatsResponse]
    by_direction: dict[str, JournalStatsResponse]


class JournalExitResponse(BaseModel):
    """One exit event: either a partial-close leg or a fully-closed
    position's terminal event. The unit is the exit decision, not the
    position."""
    position_id: str
    date: str
    ticker: str
    account_key: str
    instrument: str
    direction: str
    contracts_closed: int | None = None
    pnl_usd: float | None = None
    notes: str | None = None
    is_partial: bool = False


# ─── Discipline ───────────────────────────────────────────────────────────────


class RuleResultResponse(BaseModel):
    rule_id: str
    score: Literal["Y", "N", "N/A"]
    auto_evaluated: bool
    note: str | None = None


class DisciplineScoreResponse(BaseModel):
    position_id: str
    kill_sheet_id: str | None = None
    closed_at: str
    rules: list[RuleResultResponse]
    pnl_usd: float | None = None

    ticker: str = ""
    direction: str = ""
    instrument: str = ""
    entry_at: str | None = None

    score_numerator: int
    score_denominator: int
    score: float
    profitable_violation: bool
    counterfactual_loss_usd: float | None = None
    full_adherence: bool
    violated_rule_ids: list[str] = Field(default_factory=list)

    notes: str = ""
    profitable_violation_resolution: str | None = None
    scored_at: str = ""


class DisciplineScoreOverridesRequest(BaseModel):
    """Body for POST /discipline/score/{position_id} — overrides + notes."""
    notes: str | None = None
    profitable_violation_resolution: str | None = None
    rule_overrides: dict[str, RuleResultResponse] = Field(default_factory=dict)
    score_legacy: bool = False


class DisciplineStatsResponse(BaseModel):
    label: str
    trades_scored: int
    avg_discipline_score: float
    full_adherence_count: int
    any_violation_count: int
    profitable_violation_count: int
    most_violated_rule: str | None = None
    most_violated_rule_text: str | None = None
    drift_trend: Literal["improving", "flat", "drifting"]


class WeeklyReviewResponse(BaseModel):
    week_start: str
    week_end: str
    trades_scored: int
    avg_discipline_score: float
    full_adherence_count: int
    any_violation_count: int
    profitable_violation_count: int
    most_violated_rule: str | None = None
    drift_trend: Literal["improving", "flat", "drifting"]
    pnl_usd: float
    lockdown_behavior: str | None = None


class LockdownRequest(BaseModel):
    behavior: str


# ─── Free-range scan ──────────────────────────────────────────────────────────


FreeRangeUniverseName = Literal[
    "nasdaq_100", "sp500_top_50", "russell_2000_top_50", "lotto_high_vol",
]


class FreeRangeScanRequest(BaseModel):
    """User-supplied parameters for the 3-phase free-range scan.

    `user_tickers` are explicit additions — they bypass the price-band filter
    (the user named them, surface the read regardless). Empty list is fine.

    `universe` picks the Phase 3 candidate list(s). Pass a list of names to
    scan multiple indexes; `free_range_cap` is applied PER index so each
    index gets its own top-N. Default = all three (NASDAQ 100 + S&P 500
    Top 50 + Russell 2000 Top 50). Each returned free-range candidate
    carries a `source_universe` field for grouped display.

    `enable_free_range=False` skips Phase 3 entirely — returns baseline +
    user-submitted only. Used by views that just need the QQQ+GLD baseline
    read fast (~3s vs ~30s per index for the full scan).
    """
    user_tickers: list[str] = Field(default_factory=list)
    free_range_cap: int = Field(default=5, ge=1, le=10)
    universe: list[FreeRangeUniverseName] = Field(
        default_factory=lambda: [
            "nasdaq_100", "sp500_top_50", "russell_2000_top_50",
        ],
        min_length=1,
    )
    enable_free_range: bool = True


class CandidateSnapshotResponse(BaseModel):
    ticker: str
    phase: Literal["baseline", "user", "free_range"]
    tier: str
    direction: Literal["long", "short"]
    is_etf: bool
    current_price: float | None = None
    ma_stack: str | None = None
    stoch_zone: str | None = None
    stoch_signal: str | None = None
    sqn_100_regime: str | None = None
    sqn_20_regime: str | None = None
    score: int
    why_now: str
    notes: list[str] = Field(default_factory=list)
    action_verdict: dict | None = None
    # Which index this candidate was scanned from. Set on phase="free_range"
    # candidates only; None for baseline + user-submitted.
    source_universe: str | None = None


class FreeRangeScanResponse(BaseModel):
    scan_time_utc: str
    baseline: list[CandidateSnapshotResponse]
    user_submitted: list[CandidateSnapshotResponse]
    free_range: list[CandidateSnapshotResponse]
    universe_size: int
    free_range_cap: int
    notes: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


# ─── Options input (paste) ────────────────────────────────────────────────────


class OptionsTextRequest(BaseModel):
    """Pasted brokerage text → ParsedOptions extraction."""
    text: str
    ticker: str | None = None  # informational, currently unused by the parser


class ParsedOptionsResponse(BaseModel):
    """Mirrors options_input.ParsedOptions plus an `extraction_source` tag."""
    strike: float | None = None
    premium: float | None = None
    expiry: str | None = None
    contract_type: Literal["call", "put"] | None = None
    delta: float | None = None
    iv_rank: float | None = None
    open_interest: int | None = None
    bid_ask_spread: float | None = None
    bid: float | None = None
    ask: float | None = None
    source_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    extraction_source: Literal["paste"]


# ─── Dashboard state ─────────────────────────────────────────────────────────


class UnreviewedWeekResponse(BaseModel):
    week_start: str
    week_end: str
    closed_trade_count: int


class DashboardStateResponse(BaseModel):
    """Aggregate state for dynamic stage banner + HomeView CTA."""
    stage: Literal["stage_1", "stage_2"]
    stage_reminder: str
    account_balance_usd: float
    threshold_usd: int
    progress_to_threshold: float    # 0.0–1.0; >1.0 once stage 2 reached
    realized_pnl_usd: float
    base_balance_usd: float
    unreviewed_weeks: list[UnreviewedWeekResponse] = Field(default_factory=list)


# ─── Lotto state ─────────────────────────────────────────────────────────────


class LottoTradeSummaryResponse(BaseModel):
    position_id: str
    ticker: str
    direction: str
    closed_date: str | None = None
    pnl_usd: float | None = None
    return_pct: float | None = None
    is_big_win: bool
    is_loss: bool


class LottoCooldownResponse(BaseModel):
    active: bool
    reason: Literal["post_big_win", "post_loss_streak"] | None = None
    triggered_at: str | None = None
    expires_at: str | None = None
    hours_remaining: float | None = None
    triggering_position_ids: list[str] = Field(default_factory=list)


class LottoStateResponse(BaseModel):
    account_balance_usd: float
    base_balance_usd: float
    realized_pnl_usd: float
    open_premium_usd: float
    cash_available_usd: float
    cash_reserve_status: Literal["ok", "below_floor"]
    growth_ladder_stage: str
    cooldown: LottoCooldownResponse
    size_lock_active: bool
    size_lock_reason: str | None = None
    closed_count_last_7d: int
    recent_trades: list[LottoTradeSummaryResponse] = Field(default_factory=list)
    open_position_ids: list[str] = Field(default_factory=list)


# ─── Weekly trend scan ────────────────────────────────────────────────────────


class WeeklyScanRequest(BaseModel):
    """Sunday-scan input. Either explicit `tickers` OR a `universe` sweep
    (one of "nasdaq_100" / "sp500_top_50" / "russell_2000_top_50"). When
    both are provided, `tickers` wins. Pass at least one or the request
    rejects with 400."""
    tickers: list[str] | None = None
    universe: list[FreeRangeUniverseName] | None = None
    benchmark: str = "SPY"
    top_n: int = Field(default=3, ge=1, le=10)


class TrackASignalResponse(BaseModel):
    state: Literal["cross_up", "cross_down", "above", "below", "none"]
    ma_19: float | None = None
    ma_39: float | None = None
    asset_blocked: bool


# Unified verdict + entry/stop block included in EVERY scan setup response.
Verdict = Literal["buy", "wait", "no_go"]


class WeeklySetupResponse(BaseModel):
    ticker: str
    bar_date: str | None = None
    close: float | None = None
    is_penny_stock: bool
    suggested_vehicle: Literal["shares", "options"]
    ma_stack_state: str | None = None
    stoch_k: float | None = None
    stoch_d: float | None = None
    stoch_zone: str | None = None
    stoch_signal: str | None = None
    sqn_100_regime: str | None = None
    confluence: Literal[
        "high_conviction_long", "high_conviction_short",
        "continuation_long", "continuation_short",
        "track_a_cross_long", "track_a_cross_short",
        "compression", "chop", "no_setup",
    ]
    direction: Literal["long", "short", "none"]
    rank_score: int
    why_now: str
    blockers: list[str] = Field(default_factory=list)
    action_verdict: dict | None = None
    track_a: TrackASignalResponse | None = None
    # Unified scan-card fields
    verdict: Verdict = "wait"
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    suggested_dte: str | None = None
    suggested_delta: str | None = None
    suggested_strike: float | None = None
    source_universe: str | None = None


class WeeklyScanResponse(BaseModel):
    scan_time_utc: str
    benchmark: str
    benchmark_regime: str | None = None
    setups: list[WeeklySetupResponse] = Field(default_factory=list)
    top_setups: list[WeeklySetupResponse] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


# ─── Index swing scan ─────────────────────────────────────────────────────────


class SwingHighBreakoutResponse(BaseModel):
    swing_high_value: float
    swing_high_date: str
    swing_high_age_sessions: int
    breakout_close: float
    breakout_date: str
    breakout_volume: float
    avg_volume_30d: float
    volume_ratio: float
    base_range_atr_ratio: float | None = None
    bar_close_in_upper_third: bool
    higher_lows_pattern: bool
    nearby_failed_breakouts: int
    confluence_count: int


class IndexSwingSetupResponse(BaseModel):
    ticker: str
    bar_date: str | None = None
    close: float | None = None
    in_universe: bool
    universe_tier: Literal["primary", "secondary", "outside"]
    sqn_20_regime: str | None = None
    sqn_100_regime: str | None = None
    confluence: Literal[
        "breakout_high_conviction", "breakout_standard", "no_breakout",
        "skip_bear_volatile", "skip_low_volume", "skip_macro_event",
        "universe_violation",
    ]
    breakout: SwingHighBreakoutResponse | None = None
    suggested_stop: float | None = None
    suggested_target_2r: float | None = None
    why_now: str
    blockers: list[str] = Field(default_factory=list)
    # Unified scan-card fields
    verdict: Verdict = "wait"
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    suggested_dte: str | None = "30-60 DTE"
    suggested_delta: str | None = "0.50-0.65 (ATM/slight ITM)"
    suggested_strike: float | None = None


class LottoSetupResponse(BaseModel):
    ticker: str
    direction: Literal["long", "short"]
    bar_date: str | None = None
    close: float | None = None
    daily_stack: str | None = None
    daily_stoch_k: float | None = None
    daily_stoch_d: float | None = None
    sqn_100_regime: str | None = None
    sqn_100_value: float | None = None
    sqn_20_regime: str | None = None
    sqn_20_value: float | None = None
    h2_stack: str | None = None
    h2_stoch_k: float | None = None
    h2_stoch_d: float | None = None
    h2_zone: str | None = None
    h2_signal: str | None = None
    why_now: str
    blockers: list[str] = Field(default_factory=list)
    verdict: Verdict = "wait"
    verdict_reason: str = ""
    entry_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    suggested_dte: str | None = "5-14 DTE"
    suggested_delta: str | None = "0.10-0.25 (deep OTM lotto)"
    suggested_strike: float | None = None
    source_universe: str | None = None


class LottoScanRequest(BaseModel):
    """Lotto setup scan target.

    `tickers` (explicit list, wins if both given) — scans exactly those names.
    `universe` (list of FreeRangeUniverseName) — scans every ticker in the
        listed indexes; each result is tagged with its source_universe so the
        UI can group by index. Default = the curated lotto high-vol watchlist
        ("lotto_high_vol", 25 names, ~15-25s) — in-band-only singles since the
        2026-07-17 rotation (scripts/lotto_universe_review.py) — the cohort the
        2026-05-16 backtests scored profitable for lotto (PF 1.39-1.48;
        broad NDX-100 / broad-ETF universes scored PF 0.75-0.89 = skip, and
        most broad-index names are hard-blocked by the $10-50 band anyway).
        The broad indexes remain available by passing them explicitly.
    Pass `tickers=[]` and `universe=[]` to fall back to the QQQ + GLD
    legacy baseline.
    """
    tickers: list[str] | None = None
    universe: list[FreeRangeUniverseName] = Field(
        default_factory=lambda: ["lotto_high_vol"],
    )


class LottoScanResponse(BaseModel):
    scan_time_utc: str
    setups: list[LottoSetupResponse] = Field(default_factory=list)
    actionable_setups: list[LottoSetupResponse] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


class IndexSwingScanRequest(BaseModel):
    """Optional ticker override; default is the hard-locked QQQ/IWM/SPY universe."""
    tickers: list[str] | None = None


class IndexSwingScanResponse(BaseModel):
    scan_time_utc: str
    setups: list[IndexSwingSetupResponse] = Field(default_factory=list)
    actionable_setups: list[IndexSwingSetupResponse] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


# ─── Sparkline ───────────────────────────────────────────────────────────────


class SparklineResponse(BaseModel):
    """Compact close-only price series for inline mini-charts.

    Lightweight by design — used in tables/cards where a full ScanResult is
    overkill. `dates` and `closes` are zip-aligned.
    """
    ticker: str
    timeframe: str
    dates: list[str] = Field(default_factory=list)
    closes: list[float] = Field(default_factory=list)


# ─── Health ───────────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str
    version: str
