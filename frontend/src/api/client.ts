import type {
  DashboardState,
  DisciplineScoreDTO,
  DisciplineStatsDTO,
  FreeRangeScanRequest,
  FreeRangeScanResponse,
  JournalBreakdown,
  JournalExit,
  JournalStats,
  KillSheetRequest,
  KillSheetResponse,
  LottoState,
  OpenPositionRequest,
  ParsedOptionsResponse,
  WeeklyScanRequest,
  WeeklyScanResponse,
  IndexSwingScanRequest,
  IndexSwingScanResponse,
  LottoScanRequest,
  LottoScanResponse,
  ActionVerdict,
  Position,
  PositionAlert,
  RegimeHealthHistoryResponse,
  RegimeHealthSnapshot,
  ScanResult,
  StrikeSuggestionsResult,
  WeeklyReviewDTO,
} from "./types";

const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => request<{ status: string; version: string }>("/api/v1/health"),

  dashboardState: () => request<DashboardState>("/api/v1/dashboard/state"),

  lottoState: () => request<LottoState>("/api/v1/lotto/state"),

  lottoStrikes: (ticker: string, direction: "call" | "put" | "both" = "both") =>
    request<StrikeSuggestionsResult>(
      `/api/v1/lotto/strikes/${encodeURIComponent(ticker)}?direction=${direction}`,
    ),

  weeklyScan: (req: WeeklyScanRequest) =>
    request<WeeklyScanResponse>("/api/v1/weekly/scan", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  indexSwingScan: (req: IndexSwingScanRequest = {}) =>
    request<IndexSwingScanResponse>("/api/v1/index-swing/scan", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  lottoScan: (req: LottoScanRequest = {}) =>
    request<LottoScanResponse>("/api/v1/lotto/scan", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  regimeHealth: () =>
    request<RegimeHealthSnapshot>("/api/v1/regime-health/snapshot"),

  regimeHealthRefresh: () =>
    request<RegimeHealthSnapshot>("/api/v1/regime-health/refresh", {
      method: "POST",
    }),

  regimeHealthHistory: (days = 30) =>
    request<RegimeHealthHistoryResponse>(
      `/api/v1/regime-health/history?days=${days}`,
    ),

  actionGateVerdict: (
    ticker: string,
    skill: "lotto" | "weekly" | "focus",
    direction: "long" | "short",
  ) =>
    request<ActionVerdict>(
      `/api/v1/action-gate/verdict/${encodeURIComponent(ticker)}` +
        `?skill=${skill}&direction=${direction}`,
    ),

  scan: (ticker: string, timeframe = "1d") =>
    request<ScanResult>(
      `/api/v1/scan/${encodeURIComponent(ticker)}?timeframe=${timeframe}`,
    ),

  scanMulti: (ticker: string) =>
    request<Record<string, ScanResult | { error: string }>>(
      `/api/v1/scan/${encodeURIComponent(ticker)}/multi`,
    ),

  killSheet: (req: KillSheetRequest) =>
    request<KillSheetResponse>("/api/v1/kill_sheet", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  positions: (status: "open" | "closed" | "all" = "open", account?: string) => {
    const params = new URLSearchParams({ status });
    if (account) params.set("account", account);
    return request<Position[]>(`/api/v1/positions?${params}`);
  },

  openPosition: (req: OpenPositionRequest) =>
    request<Position>("/api/v1/positions", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  closePosition: (
    id: string,
    pnl?: number | null,
    notes?: string | null,
    contracts?: number | null,
  ) =>
    request<Position>(`/api/v1/positions/${id}/close`, {
      method: "POST",
      body: JSON.stringify({ pnl, notes, contracts }),
    }),

  positionAlerts: () =>
    request<PositionAlert[]>("/api/v1/positions/alerts"),

  journalStats: (account?: string) =>
    request<JournalStats>(
      `/api/v1/journal/stats${account ? `?account=${account}` : ""}`,
    ),

  journalBreakdown: () =>
    request<JournalBreakdown>("/api/v1/journal/breakdown"),

  journalRecent: (limit = 10) =>
    request<Position[]>(`/api/v1/journal/recent?limit=${limit}`),

  journalExits: (limit = 20) =>
    request<JournalExit[]>(`/api/v1/journal/exits?limit=${limit}`),

  disciplineScore: (positionId: string, scoreLegacy = false) =>
    request<DisciplineScoreDTO>(
      `/api/v1/discipline/score/${encodeURIComponent(positionId)}` +
      (scoreLegacy ? "?score_legacy=true" : ""),
    ),

  updateDisciplineScore: (positionId: string, body: {
    notes?: string | null;
    profitable_violation_resolution?: string | null;
    score_legacy?: boolean;
  }) =>
    request<DisciplineScoreDTO>(
      `/api/v1/discipline/score/${encodeURIComponent(positionId)}`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  disciplineStats: (range: "week" | "month" | "all" = "all") =>
    request<DisciplineStatsDTO>(`/api/v1/discipline/stats?range=${range}`),

  disciplineScores: (limit = 20) =>
    request<DisciplineScoreDTO[]>(`/api/v1/discipline/scores?limit=${limit}`),

  weeklyReview: (weekOf?: string, recompute = false) => {
    const params = new URLSearchParams();
    if (weekOf) params.set("week_of", weekOf);
    if (recompute) params.set("recompute", "true");
    const qs = params.toString();
    return request<WeeklyReviewDTO>(
      `/api/v1/discipline/weekly-review${qs ? `?${qs}` : ""}`,
    );
  },

  setWeeklyLockdown: (weekStart: string, behavior: string) =>
    request<WeeklyReviewDTO>(
      `/api/v1/discipline/weekly-review/${encodeURIComponent(weekStart)}/lockdown`,
      { method: "POST", body: JSON.stringify({ behavior }) },
    ),

  freeRangeScan: (req: FreeRangeScanRequest = {}) =>
    request<FreeRangeScanResponse>("/api/v1/free-range-scan", {
      method: "POST",
      body: JSON.stringify(req),
    }),

  extractOptionsText: (text: string, ticker?: string) =>
    request<ParsedOptionsResponse>("/api/v1/options/extract/text", {
      method: "POST",
      body: JSON.stringify({ text, ticker }),
    }),
};
