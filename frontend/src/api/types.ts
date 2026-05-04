// Mirrors the Pydantic models in src/api/models.py — keep in sync.

export interface MaRibbonReading {
  ma_10: number | null;
  ma_20: number | null;
  ma_50: number | null;
  ma_200: number | null;
  stack_state: string | null;
}

export interface StochasticReading {
  k: number | null;
  d: number | null;
  zone: string | null;
  signal: string | null;
}

export interface SqnReading {
  sqn_value: number | null;
  regime: string | null;
  // Tactical 20-day window (Tier 1 addition).
  sqn_20_value: number | null;
  regime_20: string | null;
  diagnostic: string | null;
}

export interface ScanResult {
  ticker: string;
  timeframe: string;
  bar_date: string | null;
  close: number | null;
  ma_ribbon: MaRibbonReading;
  stochastic: StochasticReading;
  sqn: SqnReading;
}

export interface KillSheetRequest {
  ticker: string;
  direction: "long" | "short";
  account?: string;
  intent?: "SCALP" | "SWING" | "TREND CAPTURE" | "POSITION";
  trigger_tf?: "2H" | "4H" | "Daily" | "Weekly";
  conviction?: "high" | "medium" | "speculative" | "default";
  target?: number | null;
  invalidation?: number | null;
  trigger_desc?: string | null;
  notes?: string | null;
  strike?: number | null;
  premium?: number | null;
  expiry?: string | null;
  contract_type?: "call" | "put" | null;
  delta?: number | null;
  iv_rank?: number | null;
  oi?: number | null;
  spread?: number | null;
  skip_devil?: boolean;
  force_devil?: boolean;
  skip_rules?: boolean;
  bypass_rules?: boolean;
  include_multi_tf?: boolean;
  focus?: boolean;
  // Discipline-layer extensions
  divergence_thesis?: string | null;
  counter_weekly_thesis?: string | null;
  attestation_user_inputs?: Record<string, boolean>;
}

export interface RuleViolation {
  rule: string;
  severity: string;
  message: string;
  current_value: number;
  limit: number;
}

export interface DevilCategoryResult {
  category: string;
  verdict: "KILL" | "FLAG" | "PASS";
  reason: string;
}

export interface DevilReport {
  aggregate: string;
  kills: number;
  flags: number;
  passes: number;
  triggered_by_risk_threshold: boolean;
  results: DevilCategoryResult[];
}

export interface KillSheetResponse {
  // Loosely typed because the kill sheet model has many optional fields.
  kill_sheet: Record<string, unknown>;
  rendered_text: string;
  rule_violations: RuleViolation[];
  rules_blocked: boolean;
  devil: DevilReport | null;
}

export interface Position {
  id: string;
  ticker: string;
  direction: string;
  instrument: string;
  account_key: string;
  status: string;
  entry_date: string;
  contracts: number | null;
  shares: number | null;
  strike: number | null;
  expiry: string | null;
  premium_paid_per_contract: number | null;
  total_cost_usd: number;
  max_loss_usd: number;
  target_price: number | null;
  invalidation_price: number | null;
  closed_date: string | null;
  pnl_usd: number | null;
  notes: string | null;
  skill: string | null;
  tier: number | null;
}

export interface PositionAlert {
  position_id: string;
  ticker: string;
  severity: "action" | "warn" | "info";
  rule: string;
  message: string;
  details: Record<string, unknown>;
}

export interface OpenPositionRequest {
  ticker: string;
  direction?: "long" | "short";
  instrument?: "call" | "put" | "shares";
  account?: string;
  strike?: number | null;
  expiry?: string | null;
  premium?: number | null;
  contracts?: number | null;
  shares?: number | null;
  entry_price?: number | null;
  target?: number | null;
  invalidation?: number | null;
  notes?: string | null;
  skill?: string | null;
  tier?: number | null;
}

export interface JournalStats {
  label: string;
  total_trades_closed: number;
  open_trades: number;
  wins: number;
  losses: number;
  breakevens: number;
  win_rate: number;
  total_pnl_usd: number;
  avg_win_usd: number;
  avg_loss_usd: number;
  largest_win_usd: number;
  largest_loss_usd: number;
  profit_factor: number | null;
  expectancy_usd: number;
  total_cost_invested_usd: number;
  total_max_loss_taken_usd: number;
}

export interface JournalBreakdown {
  overall: JournalStats;
  by_account: Record<string, JournalStats>;
  by_instrument: Record<string, JournalStats>;
  by_direction: Record<string, JournalStats>;
}

export interface FocusSetup {
  asset: "QQQ" | "GLD";
  direction: "long" | "short";
  score: number;
  status: "fires" | "watch" | "blocked";
  components: Record<string, number>;
  blockers: string[];
}

export interface SundayScanResponse {
  scan_time_utc: string;
  spy: ScanResult | null;
  qqq: ScanResult | null;
  gld: ScanResult | null;
  setups: FocusSetup[];
  recommendation: "trade" | "watch" | "cash";
  headline: string;
  errors: Record<string, string>;
}

export interface FocusTopSetupSummary {
  asset: string;
  direction: string;
  score: number;
  status: string;
}

export interface SundayScanSummary {
  date: string;
  scan_time_utc: string;
  recommendation: "trade" | "watch" | "cash";
  headline: string;
  top_setup: FocusTopSetupSummary | null;
}

export interface MatchedPosition {
  id: string;
  ticker: string;
  direction: string;
  instrument: string;
  entry_date: string;
  status: string;
  pnl_usd: number | null;
  max_loss_usd: number;
  contracts: number | null;
  strike: number | null;
  expiry: string | null;
}

export type FocusOutcomeAggregate =
  | "skipped"
  | "no_recommendation"
  | "open"
  | "closed_winner"
  | "closed_loser"
  | "mixed";

export interface FocusOutcome {
  scan_date: string;
  recommendation: "trade" | "watch" | "cash";
  top_setup: FocusTopSetupSummary | null;
  window_days: number;
  followed: boolean;
  matched: MatchedPosition[];
  realized_pnl_usd: number;
  open_count: number;
  closed_count: number;
  aggregate_status: FocusOutcomeAggregate;
}

export interface FocusRecentSummary {
  weeks: number;
  scans_count: number;
  trade_recs: number;
  watch_recs: number;
  cash_recs: number;
  followed_count: number;
  skipped_count: number;
  realized_pnl_usd: number;
  open_count: number;
}

// ── Discipline ──────────────────────────────────────────────────────────────

export type RuleVerdict = "Y" | "N" | "N/A";
export type DriftTrend = "improving" | "flat" | "drifting";

export interface RuleResultDTO {
  rule_id: string;
  score: RuleVerdict;
  auto_evaluated: boolean;
  note: string | null;
}

export interface DisciplineScoreDTO {
  position_id: string;
  kill_sheet_id: string | null;
  closed_at: string;
  rules: RuleResultDTO[];
  pnl_usd: number | null;
  ticker: string;
  direction: string;
  instrument: string;
  entry_at: string | null;
  score_numerator: number;
  score_denominator: number;
  score: number;
  profitable_violation: boolean;
  counterfactual_loss_usd: number | null;
  full_adherence: boolean;
  violated_rule_ids: string[];
  notes: string;
  profitable_violation_resolution: string | null;
  scored_at: string;
}

export interface DisciplineStatsDTO {
  label: string;
  trades_scored: number;
  avg_discipline_score: number;
  full_adherence_count: number;
  any_violation_count: number;
  profitable_violation_count: number;
  most_violated_rule: string | null;
  most_violated_rule_text: string | null;
  drift_trend: DriftTrend;
}

export interface WeeklyReviewDTO {
  week_start: string;
  week_end: string;
  trades_scored: number;
  avg_discipline_score: number;
  full_adherence_count: number;
  any_violation_count: number;
  profitable_violation_count: number;
  most_violated_rule: string | null;
  drift_trend: DriftTrend;
  pnl_usd: number;
  lockdown_behavior: string | null;
}

// ── Pyramid ─────────────────────────────────────────────────────────────────

export type PyramidStatus = "pending" | "active" | "completed" | "stopped_out";
export type TrancheStatus = "pending" | "filled" | "skipped";
export type Vehicle = "shares" | "leaps_call" | "leaps_put" | "barbell" | "etf";

export interface Tranche {
  id: number;
  target_pct: number;
  status: TrancheStatus;
  filled_date: string | null;
  vehicle: Vehicle | null;
  cost_basis_per_unit: number | null;
  quantity: number | null;
  strike: number | null;
  expiry: string | null;
  notes: string | null;
}

export interface Pyramid {
  id: string;
  ticker: string;
  direction: "long" | "short";
  benchmark: string;
  total_allocation_usd: number;
  horizon: string;
  status: PyramidStatus;
  created_date: string;
  closed_date: string | null;
  tranches: Tranche[];
  aggregate_pnl_usd: number | null;
  notes: string | null;
}

export interface CreatePyramidRequest {
  ticker: string;
  direction: "long" | "short";
  total_allocation_usd: number;
  benchmark?: string;
  horizon?: string;
  notes?: string | null;
}

export interface FillTrancheRequest {
  tranche: 1 | 2 | 3;
  vehicle: Vehicle;
  cost_basis_per_unit: number;
  quantity: number;
  strike?: number | null;
  expiry?: string | null;
  notes?: string | null;
}

export interface ClosePyramidRequest {
  completed?: boolean;
  pnl?: number | null;
  notes?: string | null;
}

export interface StructureReadDTO {
  recent_swing_high: number | null;
  recent_swing_high_date: string | null;
  recent_swing_low: number | null;
  recent_swing_low_date: string | null;
  prior_swing_high: number | null;
  prior_swing_low: number | null;
  pullback_held_20ma: boolean;
  pullback_held_50ma: boolean;
  rally_rejected_at_20ma: boolean;
  rally_rejected_at_50ma: boolean;
  higher_low_confirmed: boolean;
  lower_high_confirmed: boolean;
}

export interface GateResult {
  permitted: boolean;
  direction: string;
  sqn_100_pass: boolean;
  sqn_20_pass: boolean;
  ma_stack_pass: boolean;
  pullback_pass: boolean;
  structure_pass: boolean;
  blockers: string[];
  reasons: Record<string, string>;
}

export interface TrancheTriggerResult {
  tranche_id: number;
  should_fire: boolean;
  blockers: string[];
  reasons: Record<string, string>;
}

export interface ExitDirective {
  action: string;
  reason: string;
  severity: "info" | "warn" | "action";
  affected_tranches: number[];
  // LEAPS roll/close economic context — null for non-LEAPS directives
  cost_basis_per_unit?: number | null;
  quantity?: number | null;
  current_exposure_usd?: number | null;
  strike?: number | null;
  expiry?: string | null;
  dte?: number | null;
}

export interface PyramidEvaluation {
  ticker: string;
  direction: string;
  bar_date: string;
  close: number;
  sqn_100_value: number | null;
  sqn_100_regime: string | null;
  sqn_20_value: number | null;
  sqn_20_regime: string | null;
  sqn_diagnostic: string | null;
  ma_10: number | null;
  ma_20: number | null;
  ma_50: number | null;
  ma_200: number | null;
  ma_stack_state: string | null;
  stoch_k: number | null;
  stoch_d: number | null;
  structure: StructureReadDTO;
  gate: GateResult;
  t1: TrancheTriggerResult | null;
  t2: TrancheTriggerResult | null;
  t3: TrancheTriggerResult | null;
  exits: ExitDirective[];
  next_tranche: number | null;
}

// ─── Free-range scan ───────────────────────────────────────────────────

export type FreeRangePhase = "baseline" | "user" | "free_range";
export type FreeRangeDirection = "long" | "short";

export interface CandidateSnapshot {
  ticker: string;
  phase: FreeRangePhase;
  tier: string;
  direction: FreeRangeDirection;
  is_etf: boolean;
  current_price: number | null;
  ma_stack: string | null;
  stoch_zone: string | null;
  stoch_signal: string | null;
  sqn_100_regime: string | null;
  sqn_20_regime: string | null;
  score: number;
  why_now: string;
  notes: string[];
}

export interface FreeRangeScanRequest {
  user_tickers?: string[];
  free_range_cap?: number;
  /** When false, skip the Nasdaq 100 sweep — fast read on baseline only. */
  enable_free_range?: boolean;
}

export interface FreeRangeScanResponse {
  scan_time_utc: string;
  baseline: CandidateSnapshot[];
  user_submitted: CandidateSnapshot[];
  free_range: CandidateSnapshot[];
  universe_size: number;
  free_range_cap: number;
  notes: string[];
  errors: Record<string, string>;
}

// ─── Crypto ───────────────────────────────────────────────────────────

export type CryptoConfluence =
  | "high_conviction_long"
  | "high_conviction_short"
  | "medium_conviction_long"
  | "medium_conviction_short"
  | "counter_weekly"
  | "wait"
  | "skip_chop"
  | "skip_no_setup";

export type CryptoDirection = "long" | "short" | "none";

export interface CryptoInstrument {
  instrument_name: string;
  base_ccy: string;
  quote_ccy: string;
}

export interface CryptoInstrumentsResponse {
  common: string[];
  instruments: CryptoInstrument[];
}

export interface CryptoTickerDTO {
  instrument_name: string;
  last_price: number | null;
  bid: number | null;
  ask: number | null;
  change_24h_pct: number | null;
  high_24h: number | null;
  low_24h: number | null;
  volume_24h: number | null;
  source_timestamp_ms: number | null;
}

export interface CryptoTimeframeReadDTO {
  timeframe: string;
  error: string | null;
  bar_date: string | null;
  close: number | null;
  ma_stack_state: string | null;
  stoch_k: number | null;
  stoch_d: number | null;
  stoch_zone: string | null;
  stoch_signal: string | null;
  sqn_regime: string | null;
  sqn_value: number | null;
}

export interface CryptoSetup {
  symbol: string;
  scan_time_utc: string;
  ticker: CryptoTickerDTO | null;
  reads: Record<string, CryptoTimeframeReadDTO>;
  confluence: CryptoConfluence;
  direction: CryptoDirection;
  why_now: string;
  blockers: string[];
  notes: string[];
}

// ─── Weekly trend scan ────────────────────────────────────────────────

export type WeeklyConfluence =
  | "high_conviction_long"
  | "high_conviction_short"
  | "continuation_long"
  | "continuation_short"
  | "compression"
  | "chop"
  | "no_setup";

export type WeeklyDirection = "long" | "short" | "none";
export type WeeklyVehicle = "shares" | "options";

export interface WeeklySetup {
  ticker: string;
  bar_date: string | null;
  close: number | null;
  is_penny_stock: boolean;
  suggested_vehicle: WeeklyVehicle;
  ma_stack_state: string | null;
  stoch_k: number | null;
  stoch_d: number | null;
  stoch_zone: string | null;
  stoch_signal: string | null;
  sqn_100_regime: string | null;
  confluence: WeeklyConfluence;
  direction: WeeklyDirection;
  rank_score: number;
  why_now: string;
  blockers: string[];
}

export interface WeeklyScanRequest {
  tickers: string[];
  benchmark?: string;
  top_n?: number;
}

export interface WeeklyScanResponse {
  scan_time_utc: string;
  benchmark: string;
  benchmark_regime: string | null;
  setups: WeeklySetup[];
  top_setups: WeeklySetup[];
  errors: Record<string, string>;
}

// ─── Lotto state ──────────────────────────────────────────────────────

export type LottoCooldownReason = "post_big_win" | "post_loss_streak";
export type LottoCashReserveStatus = "ok" | "below_floor";

export interface LottoTradeSummary {
  position_id: string;
  ticker: string;
  direction: string;
  closed_date: string | null;
  pnl_usd: number | null;
  return_pct: number | null;
  is_big_win: boolean;
  is_loss: boolean;
}

export interface LottoCooldownDTO {
  active: boolean;
  reason: LottoCooldownReason | null;
  triggered_at: string | null;
  expires_at: string | null;
  hours_remaining: number | null;
  triggering_position_ids: string[];
}

export interface LottoState {
  account_balance_usd: number;
  base_balance_usd: number;
  realized_pnl_usd: number;
  open_premium_usd: number;
  cash_available_usd: number;
  cash_reserve_status: LottoCashReserveStatus;
  growth_ladder_stage: string;
  cooldown: LottoCooldownDTO;
  size_lock_active: boolean;
  size_lock_reason: string | null;
  closed_count_last_7d: number;
  recent_trades: LottoTradeSummary[];
  open_position_ids: string[];
}

// ─── Dashboard state ──────────────────────────────────────────────────

export type Stage = "stage_1" | "stage_2";

export interface UnreviewedWeek {
  week_start: string;
  week_end: string;
  closed_trade_count: number;
}

export interface DashboardState {
  stage: Stage;
  stage_reminder: string;
  account_balance_usd: number;
  threshold_usd: number;
  progress_to_threshold: number;
  realized_pnl_usd: number;
  base_balance_usd: number;
  unreviewed_weeks: UnreviewedWeek[];
}

// ─── Options input (paste / screenshot) ───────────────────────────────

export type OptionsExtractionSource = "paste" | "screenshot";

export interface ParsedOptionsResponse {
  strike: number | null;
  premium: number | null;
  expiry: string | null;
  contract_type: "call" | "put" | null;
  delta: number | null;
  iv_rank: number | null;
  open_interest: number | null;
  bid_ask_spread: number | null;
  bid: number | null;
  ask: number | null;
  source_fields: string[];
  warnings: string[];
  extraction_source: OptionsExtractionSource;
}
