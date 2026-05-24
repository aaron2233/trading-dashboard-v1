import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { StrikeSuggestionsPanel } from "../components/lotto/StrikeSuggestionsPanel";
import { TradeCard, type TradeCardBadge } from "../components/TradeCard";
import { VerdictHero } from "../components/Verdict";
import type { Verdict } from "../lib/verdict";
import type {
  CandidateSnapshot,
  FreeRangeUniverse,
  LottoCooldownReason,
  LottoScanResponse,
  LottoSetup,
  LottoState,
  LottoTradeSummary,
} from "../api/types";

const UNIVERSE_LABELS: Record<FreeRangeUniverse, string> = {
  nasdaq_100: "NASDAQ 100",
  sp500_top_50: "S&P 500 Top 50",
  russell_2000_top_50: "Russell 2000 Top 50",
};

/** Candidate is "lotto-actionable" when its tier tag includes Tier 2.
 * Score-floor is already enforced upstream by the free_range scanner. */
function isLottoActionable(c: CandidateSnapshot): boolean {
  return c.tier === "2" || c.tier === "1+2";
}


function fmtUsd(n: number | null | undefined, sign = false): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString(undefined, {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
    signDisplay: sign ? "exceptZero" : "auto",
  });
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  return `${(v * 100).toFixed(0)}%`;
}

function fmtHours(h: number | null): string {
  if (h === null) return "—";
  if (h < 1) return `${Math.round(h * 60)}m`;
  return `${h.toFixed(1)}h`;
}

function cooldownCopy(reason: LottoCooldownReason | null): { title: string; body: string } {
  if (reason === "post_big_win") {
    return {
      title: "24h cooldown — post-300%-winner",
      body: "Bank the win, reset the head, then trade. Per anti-greed protocol.",
    };
  }
  if (reason === "post_loss_streak") {
    return {
      title: "48h cooldown — post-3-loss-streak",
      body: "Review the 3 kill sheets — variance or process problem? Don't trade until you know.",
    };
  }
  return { title: "Cooldown active", body: "" };
}

function CooldownBanner({ state }: { state: LottoState }) {
  const cd = state.cooldown;
  if (!cd.active) {
    return (
      <div className="panel p-3 mb-4 border-signal-bull/40 bg-signal-bull/5">
        <span className="text-sm text-signal-bull">
          ✓ No cooldown active — anti-greed protocol clear.
        </span>
      </div>
    );
  }
  const { title, body } = cooldownCopy(cd.reason);
  return (
    <div className="panel stripe-bear p-4 mb-4 border-2 border-dashed border-signal-bear">
      <div className="flex items-baseline justify-between mb-1">
        <h3 className="text-sm font-semibold text-signal-bear">{title}</h3>
        <span className="text-xs text-text-secondary font-mono">
          {fmtHours(cd.hours_remaining)} remaining
        </span>
      </div>
      <p className="text-xs text-text-secondary mb-2">{body}</p>
      {cd.expires_at && (
        <p className="text-xs text-text-muted">
          Expires {new Date(cd.expires_at).toLocaleString()}
        </p>
      )}
      {cd.triggering_position_ids.length > 0 && (
        <p className="text-xs text-text-muted mt-1">
          Triggered by: {cd.triggering_position_ids.map((id) => (
            <span key={id} className="font-mono mr-2">{id.slice(0, 12)}…</span>
          ))}
        </p>
      )}
    </div>
  );
}

function SizeLockBanner({ state }: { state: LottoState }) {
  if (!state.size_lock_active) return null;
  return (
    <div className="panel stripe-warn p-3 mb-4 border-2 border-dashed border-signal-flag">
      <p className="text-sm text-signal-flag">
        ⚠ Size lock active — {state.size_lock_reason}
      </p>
    </div>
  );
}

function AccountHeader({ state }: { state: LottoState }) {
  const realizedClass =
    state.realized_pnl_usd > 0 ? "text-signal-bull"
    : state.realized_pnl_usd < 0 ? "text-signal-bear"
    : "text-text-secondary";
  const reserveClass =
    state.cash_reserve_status === "ok" ? "text-signal-bull" : "text-signal-bear";
  return (
    <div className="panel mb-4">
      <div className="panel-header flex items-baseline justify-between">
        <span>Lotto account</span>
        <span className="text-xs text-text-secondary">
          {state.closed_count_last_7d} trade{state.closed_count_last_7d === 1 ? "" : "s"} last 7 days
          (target tempo: 2-4/week)
        </span>
      </div>
      <div className="panel-body grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <div className="label">Account balance</div>
          <div className="font-mono text-lg">{fmtUsd(state.account_balance_usd)}</div>
          <div className="text-xs text-text-muted">
            base {fmtUsd(state.base_balance_usd)} + realized{" "}
            <span className={realizedClass}>{fmtUsd(state.realized_pnl_usd, true)}</span>
          </div>
        </div>
        <div>
          <div className="label">Open premium</div>
          <div className="font-mono text-lg">{fmtUsd(state.open_premium_usd)}</div>
          <div className="text-xs text-text-muted">
            capital tied up in open lottos
          </div>
        </div>
        <div>
          <div className="label">Cash available</div>
          <div className={`font-mono text-lg ${reserveClass}`}>
            {fmtUsd(state.cash_available_usd)}
          </div>
          <div className="text-xs text-text-muted">
            $200 floor — {state.cash_reserve_status === "ok" ? "ok" : "BELOW FLOOR"}
          </div>
        </div>
        <div>
          <div className="label">Growth ladder</div>
          <div className="text-xs leading-snug">{state.growth_ladder_stage}</div>
        </div>
      </div>
    </div>
  );
}

function returnClass(t: LottoTradeSummary): string {
  if (t.is_big_win) return "text-signal-bull font-semibold";
  if (t.is_loss) return "text-signal-bear";
  return "text-text-primary";
}

function RecentTrades({ trades }: { trades: LottoTradeSummary[] }) {
  if (trades.length === 0) {
    return (
      <div className="panel p-3 text-sm text-text-secondary">
        No closed lotto trades yet.
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-header">Recent lotto trades</div>
      <div className="panel-body p-0">
        <table className="w-full text-sm">
          <thead className="text-xs text-text-secondary border-b border-bg-border">
            <tr>
              <th className="text-left px-3 py-2">Closed</th>
              <th className="text-left px-3 py-2">Ticker</th>
              <th className="text-left px-3 py-2">Dir</th>
              <th className="text-right px-3 py-2">P&amp;L</th>
              <th className="text-right px-3 py-2">Return</th>
              <th className="text-left px-3 py-2">Flags</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.position_id} className="border-b border-bg-border/40">
                <td className="px-3 py-2 text-text-secondary text-xs">
                  {t.closed_date ? new Date(t.closed_date).toLocaleDateString() : "—"}
                </td>
                <td className="px-3 py-2 font-mono">{t.ticker}</td>
                <td className="px-3 py-2 text-text-secondary text-xs uppercase">{t.direction}</td>
                <td className="px-3 py-2 text-right font-mono">{fmtUsd(t.pnl_usd, true)}</td>
                <td className={`px-3 py-2 text-right font-mono ${returnClass(t)}`}>
                  {fmtPct(t.return_pct)}
                </td>
                <td className="px-3 py-2 text-xs">
                  {t.is_big_win && (
                    <span className="badge badge-bull mr-1">300%+ winner</span>
                  )}
                  {t.is_loss && <span className="badge badge-bear">loss</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function OpenLottoPositions({ ids }: { ids: string[] }) {
  if (ids.length === 0) return null;
  return (
    <div className="panel p-3 mb-4">
      <div className="text-sm font-semibold mb-1">
        {ids.length} open lotto position{ids.length === 1 ? "" : "s"}
      </div>
      <div className="text-xs text-text-secondary">
        Manage from{" "}
        <Link to="/positions" className="underline">Positions</Link>
        . Lotto-specific exit rules: 300%+ trim 75%, 50% drop hard stop.
      </div>
    </div>
  );
}


function deriveVerdict(
  state: LottoState | null,
  setups: CandidateSnapshot[] | null,
  scanLoading: boolean,
): Verdict {
  // Hard SKIP conditions first — these block trading regardless of setups
  if (state?.cooldown.active) {
    const reason = state.cooldown.reason === "post_big_win"
      ? "24h cooldown after a 300%+ winner"
      : "48h cooldown after 3 consecutive losses";
    const remain = state.cooldown.hours_remaining;
    return {
      kind: "skip",
      confidence: 1,
      rationale: `${reason}${remain !== null ? ` — ${remain.toFixed(1)}h remaining` : ""}. Bank the result, walk away.`,
    };
  }
  if (state?.cash_reserve_status === "below_floor") {
    return {
      kind: "skip",
      confidence: 1,
      rationale: `Cash $${state.cash_available_usd.toFixed(2)} below $200 floor. Close a position before opening a new lotto.`,
    };
  }

  // Still scanning — neutral state
  if (scanLoading || setups === null) {
    return {
      kind: "wait",
      confidence: 3,
      rationale: "Scanning QQQ + GLD baseline for actionable lotto setups.",
    };
  }

  const actionable = setups.filter(isLottoActionable);
  if (actionable.length === 0) {
    return {
      kind: "wait",
      confidence: 4,
      rationale: "QQQ + GLD show no Tier 2 confluence. Check back later or run a full free-range sweep.",
    };
  }

  // Drive the hero off action verdicts — count candidates by state so
  // the top-line call matches what's actually on the board, not just
  // "candidates exist".
  const counts = { enter_now: 0, setup_forming: 0, chase_zone: 0, stale: 0, disqualified: 0, no_verdict: 0 };
  for (const c of actionable) {
    const st = c.action_verdict?.state;
    if (st === "enter_now") counts.enter_now++;
    else if (st === "setup_forming") counts.setup_forming++;
    else if (st === "chase_zone") counts.chase_zone++;
    else if (st === "stale") counts.stale++;
    else if (st === "disqualified") counts.disqualified++;
    else counts.no_verdict++;
  }
  const sizeLockNote = state?.size_lock_active
    ? " ⚠ Size lock: do not increase size after the recent loss."
    : "";

  // 1+ ENTER_NOW → directional call with high confidence
  if (counts.enter_now > 0) {
    const enterCandidates = actionable.filter((c) => c.action_verdict?.state === "enter_now");
    const direction = enterCandidates[0].direction === "short" ? "short" : "long";
    const tickers = enterCandidates.map((c) => c.ticker).join(", ");
    return {
      kind: direction,
      confidence: state?.size_lock_active ? 6 : 8,
      rationale: `${counts.enter_now} ready to enter (${tickers}) — kill sheet at top.${sizeLockNote}`,
    };
  }

  // 1+ SETUP_FORMING → WAIT, trigger conditions still pending
  if (counts.setup_forming > 0) {
    const formingTickers = actionable
      .filter((c) => c.action_verdict?.state === "setup_forming")
      .map((c) => c.ticker)
      .join(", ");
    return {
      kind: "wait",
      confidence: 4,
      rationale: `${counts.setup_forming} setup${counts.setup_forming === 1 ? "" : "s"} forming (${formingTickers}) — waiting for 2H trigger. No clean entries yet.`,
    };
  }

  // No actionable verdicts at all — every candidate is stale/chase/disqualified
  if (counts.chase_zone + counts.stale + counts.disqualified > 0) {
    const parts: string[] = [];
    if (counts.chase_zone) parts.push(`${counts.chase_zone} chase`);
    if (counts.stale) parts.push(`${counts.stale} stale`);
    if (counts.disqualified) parts.push(`${counts.disqualified} disqualified`);
    return {
      kind: "skip",
      confidence: 2,
      rationale: `Stand down — 0 clean entries. ${actionable.length} candidate${actionable.length === 1 ? "" : "s"} flagged: ${parts.join(" · ")}. Check back after the next 2H candle.`,
    };
  }

  // Fallback (legacy data with no verdicts): WAIT, since we can't make
  // a confident call without verdicts wired up.
  return {
    kind: "wait",
    confidence: 3,
    rationale: `${actionable.length} candidate${actionable.length === 1 ? "" : "s"} surfaced; verdicts not computed (try Refresh).${sizeLockNote}`,
  };
}

function lottoBadges(s: LottoSetup): TradeCardBadge[] {
  const out: TradeCardBadge[] = [];
  if (s.sqn_100_regime) {
    const tone = s.sqn_100_regime.includes("bull") ? "bull"
      : s.sqn_100_regime.includes("bear") ? "bear" : "info";
    out.push({ label: `SQN(100) ${s.sqn_100_regime.replace(/_/g, " ")}`, tone });
  }
  if (s.sqn_20_regime) {
    const tone = s.sqn_20_regime.includes("bull") ? "bull"
      : s.sqn_20_regime.includes("bear") ? "bear" : "info";
    out.push({ label: `SQN(20) ${s.sqn_20_regime.replace(/_/g, " ")}`, tone });
  }
  if (s.daily_stack) {
    const tone = (s.daily_stack === "full_bull" || s.daily_stack === "bull_developing") ? "bull"
      : (s.daily_stack === "full_bear" || s.daily_stack === "bear_developing") ? "bear"
      : "muted";
    out.push({ label: `daily ${s.daily_stack.replace(/_/g, " ")}`, tone });
  }
  return out;
}

function lottoDetails(s: LottoSetup): { label: string; value: string }[] {
  return [
    { label: "Daily Stoch K/D", value:
      `${s.daily_stoch_k?.toFixed(1) ?? "—"} / ${s.daily_stoch_d?.toFixed(1) ?? "—"}` },
    { label: "2H Stoch K/D", value:
      `${s.h2_stoch_k?.toFixed(1) ?? "—"} / ${s.h2_stoch_d?.toFixed(1) ?? "—"}` },
    { label: "2H zone", value: s.h2_zone ?? "—" },
    { label: "2H signal", value: s.h2_signal ?? "—" },
  ];
}

// Higher = better setup for the given direction. Used to sort BUY setups
// inside each universe group so the strongest regime + stack alignment
// surfaces first instead of alphabetical-by-ticker.
function lottoSetupQuality(s: LottoSetup): number {
  const longBias = s.direction === "long" ? 1 : -1;
  const regimeWeight: Record<string, number> = {
    strong_bull: 5, bull: 4, neutral: 3, bear: 2, strong_bear: 1,
  };
  const stackWeight: Record<string, number> = {
    full_bull: 5, bull_developing: 4, compression: 3,
    bear_developing: 2, full_bear: 1,
  };
  const regime = s.sqn_100_regime ? (regimeWeight[s.sqn_100_regime] ?? 3) : 3;
  const stack = s.daily_stack ? (stackWeight[s.daily_stack] ?? 3) : 3;
  // Both regime and stack are signed by direction so long setups score
  // higher when the regime/stack is bullish, short setups score higher
  // when they're bearish. 2x regime weight because regime alignment is
  // the larger expectancy lever in the existing backtest data.
  const regimeContrib = (regime - 3) * longBias * 2;
  const stackContrib = (stack - 3) * longBias;
  return regimeContrib + stackContrib;
}

function LottoSetupScanSection({
  scan, loading, onScan,
}: {
  scan: LottoScanResponse | null;
  loading: boolean;
  onScan: () => void;
}) {
  // Surface BUYs only — by design, WAITs and NO_GOs are filtered out of
  // this section. The verdict banner above the page already handles the
  // "nothing to do right now" state.
  const surfaced = (scan?.setups ?? []).filter((s) => s.verdict === "buy");
  // Group by source_universe; fall back to "other" for setups scanned via
  // an explicit ticker list (no universe tag).
  const grouped = new Map<string, LottoSetup[]>();
  for (const s of surfaced) {
    const key = s.source_universe ?? "other";
    const bucket = grouped.get(key) ?? [];
    bucket.push(s);
    grouped.set(key, bucket);
  }
  // Sort each bucket by quality score (descending). Stable secondary sort
  // by ticker for deterministic ordering when scores tie.
  for (const bucket of grouped.values()) {
    bucket.sort((a, b) => {
      const qDiff = lottoSetupQuality(b) - lottoSetupQuality(a);
      if (qDiff !== 0) return qDiff;
      return a.ticker.localeCompare(b.ticker);
    });
  }
  const universeOrder: (FreeRangeUniverse | "other")[] = [
    "nasdaq_100", "sp500_top_50", "russell_2000_top_50", "other",
  ];

  const renderCard = (s: LottoSetup) => (
    <TradeCard
      key={`${s.source_universe ?? "x"}-${s.ticker}-${s.direction}`}
      setup={s}
      strategy_label="Lotto · 2H trigger"
      direction={s.direction}
      kill_sheet_href={
        s.verdict !== "no_go"
          ? `/kill-sheet?${(() => {
              const p = new URLSearchParams({
                ticker: s.ticker, direction: s.direction,
                account: "lotto", intent: "SCALP", trigger_tf: "2H",
                skill: "lotto-options", conviction: "speculative",
                contract_type: s.direction === "long" ? "call" : "put",
              });
              if (s.target_price != null) p.set("target", String(s.target_price));
              if (s.stop_price != null) p.set("invalidation", String(s.stop_price));
              if (s.suggested_strike != null) p.set("strike", String(s.suggested_strike));
              if (s.why_now) p.set("trigger_desc", s.why_now);
              // Notes — useful framework context so it doesn't have to be retyped
              const noteParts: string[] = [];
              if (s.daily_stack) noteParts.push(`Daily stack: ${s.daily_stack}`);
              if (s.sqn_100_regime) noteParts.push(`SQN(100) ${s.sqn_100_regime}`);
              if (s.sqn_20_value != null) noteParts.push(`SQN(20) ${s.sqn_20_value.toFixed(2)}`);
              if (s.h2_signal) noteParts.push(`2H ${s.h2_signal}`);
              if (s.suggested_dte) noteParts.push(`DTE: ${s.suggested_dte}`);
              if (s.suggested_delta) noteParts.push(`Delta: ${s.suggested_delta}`);
              if (noteParts.length) p.set("notes", noteParts.join(" · "));
              return p.toString();
            })()}`
          : null
      }
      badges={lottoBadges(s)}
      details={lottoDetails(s)}
    />
  );

  return (
    <section className="mb-6">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="text-base font-semibold">
          Lotto buys{" "}
          <span className="text-text-secondary font-normal text-sm">
            (NASDAQ 100 + S&P 500 Top 50 + Russell 2000 Top 50 · long & short ·
            sorted by regime + stack quality)
          </span>
        </h3>
        <button
          type="button"
          className="btn text-xs"
          onClick={onScan}
          disabled={loading}
        >
          {loading
            ? "Scanning ~200 tickers (1-2 min)…"
            : scan === null
            ? "Run lotto universe scan"
            : "Re-scan universe"}
        </button>
      </div>
      {loading && scan === null ? (
        <div className="panel p-3 text-sm text-text-secondary">
          Scanning the lotto universe (~200 tickers × daily + 2H reads).
          Typical run: 60-90 seconds.
        </div>
      ) : scan === null ? (
        <div className="panel p-3 text-sm text-text-secondary">
          Click "Run lotto universe scan" to surface today's actionable lotto
          BUYs across the universe.
        </div>
      ) : surfaced.length === 0 ? (
        <div className="panel p-3 text-sm text-text-secondary">
          No BUY setups across the universe right now — every name is either
          in chop, off-regime, or waiting on a 2H trigger.
        </div>
      ) : (
        <div className="space-y-4">
          {universeOrder.map((uni) => {
            const list = grouped.get(uni);
            if (!list || list.length === 0) return null;
            const label = uni === "other"
              ? "Custom"
              : UNIVERSE_LABELS[uni as FreeRangeUniverse];
            return (
              <div key={uni} className="space-y-2">
                <h4 className="text-xs font-semibold text-text-secondary uppercase tracking-wide">
                  {label}{" "}
                  <span className="font-normal normal-case">
                    ({list.length} setup{list.length === 1 ? "" : "s"})
                  </span>
                </h4>
                {list.map(renderCard)}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}


export function LottoView() {
  const [state, setState] = useState<LottoState | null>(null);
  const [setups, setSetups] = useState<CandidateSnapshot[] | null>(null);
  const [setupScan, setSetupScan] = useState<LottoScanResponse | null>(null);
  const [setupScanLoading, setSetupScanLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [scanLoading, setScanLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await api.lottoState();
      setState(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const runBaselineScan = useCallback(async () => {
    setScanLoading(true);
    try {
      // Baseline-only: skip the Nasdaq 100 sweep. ~3s vs ~30s.
      const result = await api.freeRangeScan({ enable_free_range: false });
      // Combine baseline + user-submitted (none for the auto-load) candidates
      setSetups([...result.baseline, ...result.user_submitted]);
    } catch (err) {
      // Don't blow up the page if the scan fails — verdict falls back to "scanning"
      // eslint-disable-next-line no-console
      console.error("Baseline lotto scan failed:", err);
    } finally {
      setScanLoading(false);
    }
  }, []);

  const runSetupScan = useCallback(async () => {
    setSetupScanLoading(true);
    try {
      const result = await api.lottoScan({});
      setSetupScan(result);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Lotto setup scan failed:", err);
    } finally {
      setSetupScanLoading(false);
    }
  }, []);

  useEffect(() => {
    // NOTE: the lotto setup scan is no longer auto-triggered on mount —
    // it now hits ~200 tickers (NASDAQ 100 + S&P 500 Top 50 + Russell
    // 2000 Top 50) and takes 60-90s. The user runs it explicitly via
    // the section's "Run lotto universe scan" button.
    void refresh();
    void runBaselineScan();
  }, [refresh, runBaselineScan]);

  const verdict = deriveVerdict(state, setups, scanLoading);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Lotto Dashboard</h2>
        <button
          type="button"
          className="btn text-xs"
          onClick={() => {
            void refresh();
            void runBaselineScan();
          }}
          disabled={loading || scanLoading}
        >
          {loading || scanLoading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      <p className="page-subtitle">
        $1K small-account playbook · anti-greed enforced
      </p>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      <div className="mb-4">
        <VerdictHero verdict={verdict} context="Today's lotto call" />
      </div>

      <LottoSetupScanSection
        scan={setupScan}
        loading={setupScanLoading}
        onScan={runSetupScan}
      />

      <StrikeSuggestionsPanel />

      {state && (
        <>
          <AccountHeader state={state} />
          <CooldownBanner state={state} />
          <SizeLockBanner state={state} />
          <OpenLottoPositions ids={state.open_position_ids} />
          <RecentTrades trades={state.recent_trades} />
        </>
      )}
    </div>
  );
}
