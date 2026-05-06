import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import {
  ActionVerdictBanner,
  ACTION_VERDICT_SORT_ORDER,
} from "../components/ActionVerdictBanner";
import { StrikeSuggestionsPanel } from "../components/lotto/StrikeSuggestionsPanel";
import { VerdictHero } from "../components/Verdict";
import type { Verdict } from "../lib/verdict";
import type {
  CandidateSnapshot,
  LottoCooldownReason,
  LottoState,
  LottoTradeSummary,
} from "../api/types";

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
      rationale: "QQQ + GLD show no Tier 2 confluence. Check back later or run the full Nasdaq 100 sweep.",
    };
  }

  // Direction is candidate-specific. Pick the single most-aligned candidate's
  // direction for the dashboard verdict; per-card verdicts handle the rest.
  const direction = actionable[0].direction === "short" ? "short" : "long";
  const sizeLockNote = state?.size_lock_active
    ? " ⚠ Size lock: do not increase size after the recent loss."
    : "";
  return {
    kind: direction,
    confidence: state?.size_lock_active ? 5 : 7,
    rationale: `${actionable.length} actionable setup${actionable.length === 1 ? "" : "s"} — pre-write a kill sheet from any candidate below.${sizeLockNote}`,
  };
}

function badgeClassForStack(stack: string | null): string {
  if (stack === "full_bull" || stack === "bull_developing") return "badge-bull";
  if (stack === "full_bear" || stack === "bear_developing") return "badge-bear";
  if (stack === "compression") return "badge-flag";
  return "badge-muted";
}

function badgeClassForDirection(direction: string): string {
  return direction === "long" ? "badge-bull" : "badge-bear";
}


function lottoKillSheetLink(c: CandidateSnapshot): string {
  const params = new URLSearchParams({
    ticker: c.ticker,
    direction: c.direction,
    account: "lotto",
    intent: "SCALP",
    trigger_tf: "2H",
    conviction: "high",
  });
  return `/kill-sheet?${params.toString()}`;
}

function ActionableCandidateCard({ candidate }: { candidate: CandidateSnapshot }) {
  const verdict = candidate.action_verdict;
  const isEnterNow = verdict?.state === "enter_now";
  return (
    <div className="panel p-3 border-signal-bull/30">
      {verdict && <ActionVerdictBanner verdict={verdict} />}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="font-mono font-semibold text-base">{candidate.ticker}</span>
          <span className={`badge ${badgeClassForDirection(candidate.direction)} text-xs`}>
            {candidate.direction.toUpperCase()}
          </span>
          <span className={`badge ${badgeClassForStack(candidate.ma_stack)} text-xs`}>
            {candidate.ma_stack ?? "—"}
          </span>
          {candidate.tier === "1+2" && (
            <span className="badge badge-info text-xs">also Tier 1</span>
          )}
        </div>
        <Link
          to={lottoKillSheetLink(candidate)}
          className={`btn text-xs ${isEnterNow ? "btn-primary" : "btn-secondary"}`}
        >
          Pre-write lotto kill sheet →
        </Link>
      </div>
      <p className="text-xs text-text-secondary mb-1">{candidate.why_now}</p>
      {candidate.notes.length > 0 && (
        <ul className="text-[11px] text-text-muted space-y-0.5">
          {candidate.notes.slice(0, 2).map((n, i) => (
            <li key={i}>· {n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ActionableSetupsSection({ setups, scanLoading, onFullScan, fullScanLoading }: {
  setups: CandidateSnapshot[] | null;
  scanLoading: boolean;
  onFullScan: () => void;
  fullScanLoading: boolean;
}) {
  // Sort by action verdict so ENTER_NOW lands first; candidates without a
  // computed verdict drop to the bottom (treat as disqualified for sort).
  const actionable = (setups ?? [])
    .filter(isLottoActionable)
    .slice()
    .sort((a, b) => {
      const aOrd = a.action_verdict
        ? ACTION_VERDICT_SORT_ORDER[a.action_verdict.state] ?? 99
        : 99;
      const bOrd = b.action_verdict
        ? ACTION_VERDICT_SORT_ORDER[b.action_verdict.state] ?? 99
        : 99;
      return aOrd - bOrd;
    });
  return (
    <section className="mb-6">
      <div className="flex items-baseline justify-between mb-2">
        <h3 className="text-sm font-semibold text-text-primary">
          Actionable setups{" "}
          <span className="text-text-secondary font-normal">
            (QQQ + GLD baseline)
          </span>
        </h3>
        <button
          type="button"
          className="btn text-xs"
          onClick={onFullScan}
          disabled={fullScanLoading}
        >
          {fullScanLoading ? "Sweeping Nasdaq 100…" : "Run full Nasdaq 100 scan"}
        </button>
      </div>
      {scanLoading && setups === null ? (
        <div className="panel p-3 text-sm text-text-secondary">
          Scanning QQQ + GLD…
        </div>
      ) : actionable.length === 0 ? (
        <div className="panel p-3 text-sm text-text-secondary">
          No Tier 2 confluence on the baseline. Try the full Nasdaq 100 sweep
          or check back after the next 2H candle.
        </div>
      ) : (
        <div className="space-y-2">
          {actionable.map((c) => (
            <ActionableCandidateCard key={`${c.phase}-${c.ticker}`} candidate={c} />
          ))}
        </div>
      )}
    </section>
  );
}

export function LottoView() {
  const [state, setState] = useState<LottoState | null>(null);
  const [setups, setSetups] = useState<CandidateSnapshot[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanLoading, setScanLoading] = useState(false);
  const [fullScanLoading, setFullScanLoading] = useState(false);
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

  const runFullScan = useCallback(async () => {
    setFullScanLoading(true);
    try {
      const result = await api.freeRangeScan({ enable_free_range: true });
      setSetups([
        ...result.baseline,
        ...result.user_submitted,
        ...result.free_range,
      ]);
    } catch (err) {
      // eslint-disable-next-line no-console
      console.error("Full Nasdaq 100 scan failed:", err);
    } finally {
      setFullScanLoading(false);
    }
  }, []);

  useEffect(() => {
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

      <ActionableSetupsSection
        setups={setups}
        scanLoading={scanLoading}
        onFullScan={runFullScan}
        fullScanLoading={fullScanLoading}
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
