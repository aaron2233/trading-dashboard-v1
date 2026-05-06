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

    # Apex options block
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
    shares: int | None = None
    entry_price: float | None = None
    target: float | None = None
    invalidation: float | None = None
    notes: str | None = None

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
    shares: int | None = None
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


# ─── Focus / Sunday scan ──────────────────────────────────────────────────────


class FocusSetup(BaseModel):
    asset: Literal["QQQ", "GLD"]
    direction: Literal["long", "short"]
    score: int
    status: Literal["fires", "watch", "blocked"]
    components: dict[str, int]
    blockers: list[str]
    action_verdict: dict | None = None


class SundayScanResponse(BaseModel):
    scan_time_utc: str
    spy: ScanResult | None = None
    qqq: ScanResult | None = None
    gld: ScanResult | None = None
    setups: list[FocusSetup]
    recommendation: Literal["trade", "watch", "cash"]
    headline: str
    errors: dict[str, str] = Field(default_factory=dict)


class FocusTopSetupSummary(BaseModel):
    asset: str
    direction: str
    score: int
    status: str


class SundayScanSummaryResponse(BaseModel):
    date: str
    scan_time_utc: str
    recommendation: Literal["trade", "watch", "cash"]
    headline: str
    top_setup: FocusTopSetupSummary | None = None


class MatchedPositionResponse(BaseModel):
    id: str
    ticker: str
    direction: str
    instrument: str
    entry_date: str
    status: str
    pnl_usd: float | None = None
    max_loss_usd: float
    contracts: int | None = None
    strike: float | None = None
    expiry: str | None = None


class FocusRecentSummaryResponse(BaseModel):
    weeks: int
    scans_count: int
    trade_recs: int
    watch_recs: int
    cash_recs: int
    followed_count: int
    skipped_count: int
    realized_pnl_usd: float
    open_count: int


class FocusOutcomeResponse(BaseModel):
    scan_date: str
    recommendation: Literal["trade", "watch", "cash"]
    top_setup: FocusTopSetupSummary | None = None
    window_days: int
    followed: bool
    matched: list[MatchedPositionResponse]
    realized_pnl_usd: float
    open_count: int
    closed_count: int
    aggregate_status: Literal[
        "skipped",
        "no_recommendation",
        "open",
        "closed_winner",
        "closed_loser",
        "mixed",
    ]


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


class FreeRangeScanRequest(BaseModel):
    """User-supplied parameters for the 3-phase free-range scan.

    `user_tickers` are explicit additions — they bypass the price-band filter
    (the user named them, surface the read regardless). Empty list is fine.

    `enable_free_range=False` skips Phase 3 (Nasdaq 100 sweep) — returns
    baseline + user-submitted only. Used by views that just need the QQQ+GLD
    baseline read fast (~3s vs ~30s for the full scan).
    """
    user_tickers: list[str] = Field(default_factory=list)
    free_range_cap: int = Field(default=5, ge=1, le=10)
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


class FreeRangeScanResponse(BaseModel):
    scan_time_utc: str
    baseline: list[CandidateSnapshotResponse]
    user_submitted: list[CandidateSnapshotResponse]
    free_range: list[CandidateSnapshotResponse]
    universe_size: int
    free_range_cap: int
    notes: list[str] = Field(default_factory=list)
    errors: dict[str, str] = Field(default_factory=dict)


# ─── Options input (paste / screenshot) ──────────────────────────────────────


class OptionsTextRequest(BaseModel):
    """Pasted brokerage text → ParsedOptions extraction."""
    text: str
    ticker: str | None = None  # informational, currently unused by the parser


class ParsedOptionsResponse(BaseModel):
    """Mirrors options_input.ParsedOptions plus an `extraction_source` tag.

    `extraction_source` is "paste" for text input, "screenshot" for image
    upload. The frontend uses this to display per-field provenance badges.
    """
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
    extraction_source: Literal["paste", "screenshot"]


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
    """Sunday-scan input — list of tickers + benchmark for SQN regime read."""
    tickers: list[str]
    benchmark: str = "SPY"
    top_n: int = Field(default=3, ge=1, le=10)


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
        "compression", "chop", "no_setup",
    ]
    direction: Literal["long", "short", "none"]
    rank_score: int
    why_now: str
    blockers: list[str] = Field(default_factory=list)
    action_verdict: dict | None = None


class WeeklyScanResponse(BaseModel):
    scan_time_utc: str
    benchmark: str
    benchmark_regime: str | None = None
    setups: list[WeeklySetupResponse] = Field(default_factory=list)
    top_setups: list[WeeklySetupResponse] = Field(default_factory=list)
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
