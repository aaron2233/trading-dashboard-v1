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
  skill?: string | null;  // routes skill-keyed gates (index-swing universe, weekly-trend asset block, DTE bands)
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
  // Phase B: present when kill sheet was AUTHORIZED + persisted. Pass back
  // to POST /api/v1/positions to satisfy the open-position gate.
  kill_sheet_id: string | null;
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
  // Greeks / IV at entry — snapshot, not refreshed
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
  iv: number | null;
  iv_rank: number | null;
  // Premium-level exit thresholds (separate from underlying-price target / invalidation)
  premium_stop: number | null;
  premium_target: number | null;
  // Phase B: kill sheet that authorized this position (null on legacy / bypass)
  kill_sheet_id: string | null;
  // Partial exits — each leg is one scale-out. Empty until first partial.
  partial_exits: PartialExit[];
}

export interface PartialExit {
  date: string;
  contracts_closed: number;
  pnl_usd: number | null;
  notes: string | null;
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
  // `direction` is the THESIS (long=bullish, short=bearish — matching the kill
  // sheet). For options the backend always STORES the contract as long (this
  // cash account only buys options); bearishness is carried by instrument=put.
  // Only long+call (bullish) and short+put (bearish) are valid options combos —
  // the API 422s a bearish CALL or bullish PUT (would be a sold/short option).
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
  // Greeks / IV at entry
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  vega?: number | null;
  iv?: number | null;
  iv_rank?: number | null;
  // Premium-level exit thresholds
  premium_stop?: number | null;
  premium_target?: number | null;
  // Phase B: authorization gate
  kill_sheet_id?: string | null;
  bypass_kill_sheet?: boolean;
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

export interface JournalExit {
  position_id: string;
  date: string;
  ticker: string;
  account_key: string;
  instrument: string;
  direction: string;
  contracts_closed: number | null;
  pnl_usd: number | null;
  notes: string | null;
  is_partial: boolean;
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

// ─── Action gate verdict (per-candidate buy/wait/skip) ────────────────

export type ActionState =
  | "enter_now"
  | "setup_forming"
  | "chase_zone"
  | "stale"
  | "disqualified";

export interface ActionVerdict {
  state: ActionState;
  direction: "long" | "short" | "none";
  skill: string;
  headline: string;
  suggested_entry_price: number | null;
  blockers: string[];
  advance_conditions: string[];
  rule_citations: string[];
}

// ─── Lotto strike suggestions ──────────────────────────────────────────

export type StrikeDirection = "call" | "put";

export interface StrikeCandidate {
  direction: StrikeDirection;
  strike: number;
  pct_otm: number;
  moneyness: string;          // "ATM", "1% OTM", ...
  distance_usd: number;
}

export interface StrikeSuggestionsResult {
  ticker: string;
  spot: number;
  bar_date: string;
  increment: number;
  calls: StrikeCandidate[];
  puts: StrikeCandidate[];
}

// ─── Regime Health ─────────────────────────────────────────────────────

export type IndicatorStatus = "green" | "amber" | "red" | "unknown" | "error";

export interface IndicatorReading {
  indicator_id: string;
  label: string;
  tier: number;
  status: IndicatorStatus;
  value: number | string | null;
  formatted_value: string;
  threshold_note: string;
  source: string;
  error: string | null;
  fetched_at: string;
}

export interface TierBundle {
  tier: number;
  label: string;
  readings: IndicatorReading[];
  error: string | null;
}

export interface PendingCapexUpdate {
  ticker: string;
  print_date: string;
}

export interface RegimeHealthSnapshot {
  snapshot_date: string;
  fetched_at: string;
  overall_status: IndicatorStatus;
  tiers: TierBundle[];
  overall_drivers: string[];
  pending_capex_updates: PendingCapexUpdate[];
}

export interface RegimeHealthHistoryResponse {
  snapshots: RegimeHealthSnapshot[];
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
  action_verdict: ActionVerdict | null;
  /** Which index this candidate was scanned from. Set on free_range phase
   * only; null for baseline + user-submitted. */
  source_universe?: FreeRangeUniverse | null;
}

export type FreeRangeUniverse =
  | "nasdaq_100"
  | "sp500_top_50"
  | "russell_2000_top_50"
  | "lotto_high_vol";

export interface FreeRangeScanRequest {
  user_tickers?: string[];
  free_range_cap?: number;
  /** Phase 3 candidate list(s). Server applies the cap PER universe and
   * tags each returned candidate with source_universe. Server default is
   * all three indexes. */
  universe?: FreeRangeUniverse[];
  /** When false, skip the Phase 3 sweep — fast read on baseline only. */
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

// ─── Weekly trend scan ────────────────────────────────────────────────

export type WeeklyConfluence =
  | "high_conviction_long"
  | "high_conviction_short"
  | "continuation_long"
  | "continuation_short"
  | "track_a_cross_long"
  | "track_a_cross_short"
  | "compression"
  | "chop"
  | "no_setup";

export interface TrackASignal {
  state: "cross_up" | "cross_down" | "above" | "below" | "none";
  ma_19: number | null;
  ma_39: number | null;
  asset_blocked: boolean;
}

/** Unified Buy / Wait / No-Go verdict shared across all scan types. */
export type ScanVerdict = "buy" | "wait" | "no_go";

/** Common fields exposed by every scan setup so a single TradeCard can render. */
export interface UnifiedSetupFields {
  ticker: string;
  bar_date: string | null;
  close: number | null;
  verdict: ScanVerdict;
  verdict_reason: string;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  suggested_dte: string | null;
  suggested_delta: string | null;
  suggested_strike: number | null;
  why_now: string;
  blockers: string[];
  sqn_100_regime?: string | null;
  sqn_20_regime?: string | null;
}

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
  action_verdict: ActionVerdict | null;
  track_a?: TrackASignal | null;
  // Unified scan-card fields
  verdict: ScanVerdict;
  verdict_reason: string;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  suggested_dte: string | null;
  suggested_delta: string | null;
  suggested_strike: number | null;
  source_universe: string | null;
}

export type WeeklyScanUniverseName =
  | "nasdaq_100"
  | "sp500_top_50"
  | "russell_2000_top_50";

export interface WeeklyScanRequest {
  tickers?: string[];
  universe?: WeeklyScanUniverseName[];
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

// ─── Index swing scan ────────────────────────────────────────────────

export type IndexSwingConfluence =
  | "breakout_high_conviction"
  | "breakout_standard"
  | "no_breakout"
  | "skip_bear_volatile"
  | "skip_low_volume"
  | "skip_macro_event"
  | "universe_violation";

export type IndexSwingTier = "primary" | "secondary" | "outside";

export interface SwingHighBreakout {
  swing_high_value: number;
  swing_high_date: string;
  swing_high_age_sessions: number;
  breakout_close: number;
  breakout_date: string;
  breakout_volume: number;
  avg_volume_30d: number;
  volume_ratio: number;
  base_range_atr_ratio: number | null;
  bar_close_in_upper_third: boolean;
  higher_lows_pattern: boolean;
  nearby_failed_breakouts: number;
  confluence_count: number;
}

export interface IndexSwingSetup {
  ticker: string;
  bar_date: string | null;
  close: number | null;
  in_universe: boolean;
  universe_tier: IndexSwingTier;
  sqn_20_regime: string | null;
  sqn_100_regime: string | null;
  confluence: IndexSwingConfluence;
  breakout: SwingHighBreakout | null;
  suggested_stop: number | null;
  suggested_target_2r: number | null;
  why_now: string;
  blockers: string[];
  // Unified scan-card fields
  verdict: ScanVerdict;
  verdict_reason: string;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  suggested_dte: string | null;
  suggested_delta: string | null;
  suggested_strike: number | null;
}

// ─── Lotto setup scan ────────────────────────────────────────────────

export interface LottoSetup {
  ticker: string;
  direction: "long" | "short";
  bar_date: string | null;
  close: number | null;
  daily_stack: string | null;
  daily_stoch_k: number | null;
  daily_stoch_d: number | null;
  sqn_100_regime: string | null;
  sqn_100_value: number | null;
  sqn_20_regime: string | null;
  sqn_20_value: number | null;
  h2_stack: string | null;
  h2_stoch_k: number | null;
  h2_stoch_d: number | null;
  h2_zone: string | null;
  h2_signal: string | null;
  why_now: string;
  blockers: string[];
  verdict: ScanVerdict;
  verdict_reason: string;
  entry_price: number | null;
  stop_price: number | null;
  target_price: number | null;
  suggested_dte: string | null;
  suggested_delta: string | null;
  suggested_strike: number | null;
  /** Which index this ticker was scanned from when universe scan ran. */
  source_universe?: FreeRangeUniverse | null;
}

export interface LottoScanRequest {
  tickers?: string[] | null;
  /** Defaults server-side to the curated lotto high-vol watchlist. */
  universe?: FreeRangeUniverse[];
}

export interface LottoScanResponse {
  scan_time_utc: string;
  setups: LottoSetup[];
  actionable_setups: LottoSetup[];
  errors: Record<string, string>;
}

export interface IndexSwingScanRequest {
  tickers?: string[] | null;
}

export interface IndexSwingScanResponse {
  scan_time_utc: string;
  setups: IndexSwingSetup[];
  actionable_setups: IndexSwingSetup[];
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

// ─── Options input (paste) ────────────────────────────────────────────

export type OptionsExtractionSource = "paste";

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


