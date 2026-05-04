import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useDashboardState } from "../state/DashboardStateContext";
import type {
  DisciplineScoreDTO,
  DisciplineStatsDTO,
  JournalBreakdown,
  JournalStats,
  Position,
} from "../api/types";


// Stage-aware default — discipline first in stage 1 (account < $100K), P&L
// first in stage 2. Per ~/.claude/skills/user/discipline/SKILL.md. Pulls
// from live account balance via the dashboard-state context; falls back to
// discipline if state hasn't loaded yet (stage 1 is the safe default).
type Tab = "discipline" | "pnl";


function fmtUsd(n: number | null | undefined, sign = false): string {
  if (n === null || n === undefined) return "—";
  const opts: Intl.NumberFormatOptions = {
    style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2,
    signDisplay: sign ? "exceptZero" : "auto",
  };
  return n.toLocaleString("en-US", opts);
}

function fmtPct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

function StatBlock({ stats, title }: { stats: JournalStats; title?: string }) {
  if (stats.total_trades_closed === 0) {
    return (
      <div className="panel">
        <div className="panel-header">{title ?? stats.label}</div>
        <div className="panel-body text-text-muted text-sm">No closed positions yet.</div>
      </div>
    );
  }
  const pnlClass = stats.total_pnl_usd > 0 ? "text-signal-bull"
    : stats.total_pnl_usd < 0 ? "text-signal-bear" : "text-text-secondary";
  const pf = stats.profit_factor;
  const pfStr = pf === null ? "n/a"
    : pf === Infinity || pf > 1e9 ? "∞ (all wins)"
    : pf.toFixed(2);
  return (
    <div className="panel">
      <div className="panel-header flex items-center justify-between">
        <span>{title ?? stats.label}</span>
        <span className="text-text-muted">
          {stats.total_trades_closed} closed · {stats.open_trades} open
        </span>
      </div>
      <div className="panel-body grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div>
          <div className="label">Win rate</div>
          <div className="text-lg font-semibold">{fmtPct(stats.win_rate)}</div>
          <div className="text-text-muted text-xs">
            {stats.wins}W · {stats.losses}L · {stats.breakevens}BE
          </div>
        </div>
        <div>
          <div className="label">Total P&amp;L</div>
          <div className={`text-lg font-semibold ${pnlClass}`}>
            {fmtUsd(stats.total_pnl_usd, true)}
          </div>
          <div className="text-text-muted text-xs">
            expectancy {fmtUsd(stats.expectancy_usd, true)}/trade
          </div>
        </div>
        <div>
          <div className="label">Profit factor</div>
          <div className="text-lg font-semibold">{pfStr}</div>
          <div className="text-text-muted text-xs">
            avg win {fmtUsd(stats.avg_win_usd)} · avg loss {fmtUsd(stats.avg_loss_usd)}
          </div>
        </div>
        <div>
          <div className="label">Capital deployed</div>
          <div className="text-lg font-semibold">{fmtUsd(stats.total_cost_invested_usd)}</div>
          <div className="text-text-muted text-xs">
            largest win {fmtUsd(stats.largest_win_usd)}
          </div>
        </div>
      </div>
    </div>
  );
}


function DisciplineMetric({
  label, value, emphasis,
}: {
  label: string;
  value: string;
  emphasis?: "bear" | "bull" | "muted";
}) {
  const valueClass = emphasis === "bear"
    ? "text-signal-bear font-semibold"
    : emphasis === "bull"
    ? "text-signal-bull font-semibold"
    : emphasis === "muted"
    ? "text-text-muted"
    : "text-text-primary font-semibold";
  return (
    <div>
      <div className="label">{label}</div>
      <div className={`text-lg ${valueClass}`}>{value}</div>
    </div>
  );
}


function DisciplineStatsBlock({ stats }: { stats: DisciplineStatsDTO }) {
  if (stats.trades_scored === 0) {
    return (
      <div className="panel">
        <div className="panel-header">Discipline ({stats.label})</div>
        <div className="panel-body text-text-muted text-sm">
          No scored trades yet. Trades are auto-scored when closed.
        </div>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-header flex items-center justify-between">
        <span>Discipline ({stats.label})</span>
        <span className={`badge ${
          stats.drift_trend === "improving" ? "badge-bull"
          : stats.drift_trend === "drifting" ? "badge-bear"
          : "badge-info"
        }`}>{stats.drift_trend}</span>
      </div>
      <div className="panel-body grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <DisciplineMetric label="Trades scored" value={String(stats.trades_scored)} />
        <DisciplineMetric label="Avg score" value={fmtPct(stats.avg_discipline_score)}
                          emphasis={stats.avg_discipline_score >= 0.9 ? "bull" : undefined} />
        <DisciplineMetric label="100% adherence" value={String(stats.full_adherence_count)} />
        <DisciplineMetric
          label="Profitable violations"
          value={String(stats.profitable_violation_count)}
          emphasis={stats.profitable_violation_count > 0 ? "bear" : "muted"}
        />
        {stats.most_violated_rule_text && (
          <div className="md:col-span-4 text-text-muted text-xs">
            Most violated: <span className="text-text-secondary">{stats.most_violated_rule_text}</span>
          </div>
        )}
      </div>
    </div>
  );
}


function ScoredTradesTable({ scores }: { scores: DisciplineScoreDTO[] }) {
  if (scores.length === 0) {
    return (
      <div className="panel mt-4">
        <div className="panel-header">Recent scored trades</div>
        <div className="panel-body text-text-muted text-sm">No scored trades yet.</div>
      </div>
    );
  }
  return (
    <div className="panel mt-4">
      <div className="panel-header">Recent scored trades</div>
      <div className="panel-body overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-text-secondary text-xs uppercase tracking-wider">
            <tr>
              <th className="text-left p-1">Closed</th>
              <th className="text-left p-1">Ticker</th>
              <th className="text-left p-1">Dir</th>
              <th className="text-right p-1">Score</th>
              <th className="text-right p-1">P&amp;L</th>
              <th className="text-left p-1">Flags</th>
              <th className="text-left p-1">Violated</th>
            </tr>
          </thead>
          <tbody>
            {scores.map((s) => (
              <tr key={s.position_id} className="border-t border-bg-border">
                <td className="p-1 text-text-muted">
                  {s.closed_at.slice(0, 10)}
                </td>
                <td className="p-1 font-semibold">{s.ticker}</td>
                <td className="p-1">{s.direction}</td>
                <td className={`p-1 text-right font-semibold ${
                  s.score === 1 ? "text-signal-bull"
                  : s.score >= 0.9 ? "text-text-primary"
                  : "text-signal-bear"
                }`}>
                  {fmtPct(s.score)}
                </td>
                <td className={`p-1 text-right ${
                  (s.pnl_usd ?? 0) > 0 ? "text-signal-bull"
                  : (s.pnl_usd ?? 0) < 0 ? "text-signal-bear"
                  : "text-text-secondary"
                }`}>
                  {fmtUsd(s.pnl_usd, true)}
                </td>
                <td className="p-1">
                  {s.profitable_violation && (
                    <span className="badge badge-bear" title="Profitable violation — highest-risk pattern">
                      ⚠ profitable-violation
                    </span>
                  )}
                  {s.full_adherence && (
                    <span className="badge badge-bull">100%</span>
                  )}
                </td>
                <td className="p-1 text-text-muted text-xs">
                  {s.violated_rule_ids.length === 0
                    ? "—"
                    : s.violated_rule_ids.slice(0, 3).join(", ") +
                      (s.violated_rule_ids.length > 3
                        ? `, +${s.violated_rule_ids.length - 3}`
                        : "")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


function tabClass(active: boolean): string {
  return active
    ? "px-3 py-1.5 rounded text-sm bg-signal-info/20 text-signal-info border border-signal-info/40"
    : "px-3 py-1.5 rounded text-sm text-text-secondary hover:text-text-primary border border-transparent";
}


export function JournalView() {
  const { state: dashState } = useDashboardState();
  const [tab, setTab] = useState<Tab>("discipline");

  // Track whether the user has manually selected a tab — once they do, we
  // never override their choice on dashboard-state updates.
  const userPickedTab = useRef(false);
  useEffect(() => {
    if (userPickedTab.current || !dashState) return;
    setTab(dashState.stage === "stage_2" ? "pnl" : "discipline");
  }, [dashState]);

  function selectTab(next: Tab) {
    userPickedTab.current = true;
    setTab(next);
  }

  // P&L data
  const [breakdown, setBreakdown] = useState<JournalBreakdown | null>(null);
  const [recent, setRecent] = useState<Position[]>([]);
  // Discipline data
  const [disciplineStats, setDisciplineStats] = useState<DisciplineStatsDTO | null>(null);
  const [disciplineScores, setDisciplineScores] = useState<DisciplineScoreDTO[]>([]);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [bd, rec, ds, dscores] = await Promise.all([
        api.journalBreakdown(),
        api.journalRecent(20),
        api.disciplineStats("all"),
        api.disciplineScores(20),
      ]);
      setBreakdown(bd);
      setRecent(rec);
      setDisciplineStats(ds);
      setDisciplineScores(dscores);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold">Journal</h2>
          <div className="flex gap-1">
            <button type="button" className={tabClass(tab === "discipline")}
                    onClick={() => selectTab("discipline")}>
              Discipline
            </button>
            <button type="button" className={tabClass(tab === "pnl")}
                    onClick={() => selectTab("pnl")}>
              P&amp;L
            </button>
          </div>
        </div>
        <button className="btn" onClick={() => void refresh()} disabled={loading}>
          {loading ? "…" : "↻ Refresh"}
        </button>
      </div>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {tab === "discipline" && (
        <>
          {disciplineStats && <DisciplineStatsBlock stats={disciplineStats} />}
          <ScoredTradesTable scores={disciplineScores} />
          <div className="text-xs text-text-muted mt-3">
            <Link to="/weekly-review" className="text-signal-info hover:underline">
              See weekly review →
            </Link>
            <span className="mx-2">·</span>
            Trades auto-score when closed via the dashboard. Use{" "}
            <code className="text-text-secondary">python -m discipline score &lt;id&gt;</code>{" "}
            to score a position manually.
          </div>
        </>
      )}

      {tab === "pnl" && breakdown && (
        <>
          <div className="mb-4">
            <StatBlock stats={breakdown.overall} title="All accounts" />
          </div>

          {Object.keys(breakdown.by_account).length > 0 && (
            <>
              <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-2 mt-6">
                By account
              </h3>
              <div className="space-y-3 mb-4">
                {Object.entries(breakdown.by_account).map(([key, stats]) => (
                  <StatBlock key={key} stats={stats} title={key} />
                ))}
              </div>
            </>
          )}

          {Object.keys(breakdown.by_instrument).length > 0 && (
            <>
              <h3 className="text-text-secondary text-xs uppercase tracking-wider mb-2 mt-6">
                By instrument
              </h3>
              <div className="space-y-3 mb-4">
                {Object.entries(breakdown.by_instrument).map(([key, stats]) => (
                  <StatBlock key={key} stats={stats} title={key} />
                ))}
              </div>
            </>
          )}

          <div className="panel mt-4">
            <div className="panel-header">Recent closes</div>
            {recent.length === 0 ? (
              <div className="panel-body text-text-muted text-sm">No closed positions yet.</div>
            ) : (
              <div className="panel-body overflow-x-auto">
                <table className="w-full text-sm">
                  <thead className="text-text-secondary text-xs uppercase tracking-wider">
                    <tr>
                      <th className="text-left p-1">Closed</th>
                      <th className="text-left p-1">Ticker</th>
                      <th className="text-left p-1">Acct</th>
                      <th className="text-left p-1">Inst</th>
                      <th className="text-right p-1">P&amp;L</th>
                      <th className="text-right p-1">Cost</th>
                      <th className="text-left p-1">Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((p) => (
                      <tr key={p.id} className="border-t border-bg-border">
                        <td className="p-1 text-text-muted">
                          {(p.closed_date ?? "").slice(0, 19).replace("T", " ")}
                        </td>
                        <td className="p-1 font-semibold">{p.ticker}</td>
                        <td className="p-1">{p.account_key}</td>
                        <td className="p-1">{p.instrument}</td>
                        <td className={`p-1 text-right font-semibold ${
                          (p.pnl_usd ?? 0) > 0 ? "text-signal-bull" :
                          (p.pnl_usd ?? 0) < 0 ? "text-signal-bear" : "text-text-secondary"
                        }`}>{fmtUsd(p.pnl_usd, true)}</td>
                        <td className="p-1 text-right text-text-muted">{fmtUsd(p.total_cost_usd)}</td>
                        <td className="p-1 text-text-secondary text-xs">{p.notes ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
