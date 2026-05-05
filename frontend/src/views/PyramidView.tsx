import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { VerdictBadge } from "../components/Verdict";
import { fromPyramidGate, fromTrancheTrigger } from "../lib/verdict";
import type {
  Pyramid,
  PyramidEvaluation,
  Tranche,
} from "../api/types";

type Direction = "long" | "short";

function severityClass(s: "info" | "warn" | "action"): string {
  switch (s) {
    case "action":
      return "text-signal-bear";
    case "warn":
      return "text-signal-flag";
    default:
      return "text-text-muted";
  }
}

function CheckMark({ ok }: { ok: boolean }) {
  return (
    <span className={ok ? "text-signal-bull" : "text-signal-bear"}>
      {ok ? "✓" : "✗"}
    </span>
  );
}

function tranchePosCost(t: Tranche): number | null {
  if (t.cost_basis_per_unit === null || t.quantity === null) return null;
  const isLeaps = t.vehicle === "leaps_call" || t.vehicle === "leaps_put";
  return t.cost_basis_per_unit * t.quantity * (isLeaps ? 100 : 1);
}

function trancheStatusBadge(status: Tranche["status"]): string {
  if (status === "filled") return "badge-bull";
  if (status === "skipped") return "badge-muted";
  return "badge-info";
}

function TrancheTable({ tranches }: { tranches: Tranche[] }) {
  return (
    <table className="w-full text-xs">
      <thead className="text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
        <tr>
          <th className="text-left px-3 py-2">Tranche</th>
          <th className="text-left px-3 py-2">Status</th>
          <th className="text-left px-3 py-2">Vehicle</th>
          <th className="text-right px-3 py-2">Cost</th>
          <th className="text-right px-3 py-2">Qty</th>
          <th className="text-left px-3 py-2">Strike / Expiry</th>
          <th className="text-right px-3 py-2">Total $</th>
        </tr>
      </thead>
      <tbody>
        {tranches.map((t) => {
          const cost = tranchePosCost(t);
          return (
            <tr key={t.id} className="border-b border-bg-border/40">
              <td className="px-3 py-2 font-semibold">T{t.id}</td>
              <td className="px-3 py-2">
                <span className={`badge ${trancheStatusBadge(t.status)}`}>
                  {t.status}
                </span>
              </td>
              <td className="px-3 py-2 text-text-secondary">{t.vehicle ?? "—"}</td>
              <td className="px-3 py-2 text-right font-mono">
                {t.cost_basis_per_unit !== null ? `$${t.cost_basis_per_unit}` : "—"}
              </td>
              <td className="px-3 py-2 text-right font-mono">{t.quantity ?? "—"}</td>
              <td className="px-3 py-2 text-text-secondary">
                {t.expiry ? `${t.expiry}` : t.strike ? `K=$${t.strike}` : "—"}
              </td>
              <td className="px-3 py-2 text-right font-mono">
                {cost !== null ? `$${cost.toFixed(0)}` : "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function EvaluationPanel({ ev }: { ev: PyramidEvaluation }) {
  const direction: Direction = ev.direction === "short" ? "short" : "long";
  return (
    <div className="panel">
      <div className="panel-header flex items-center justify-between">
        <span>
          Evaluation — {ev.ticker}{" "}
          <span className="text-text-muted">({ev.direction.toUpperCase()})</span>
        </span>
        <div className="flex items-center gap-2 normal-case">
          <span className="text-text-muted text-[10px]">Gate</span>
          <VerdictBadge verdict={fromPyramidGate(ev.gate)} />
        </div>
      </div>
      <div className="panel-body space-y-3">
        <div className="text-xs text-text-muted">
          Bar {ev.bar_date} · Close ${ev.close.toFixed(2)}
        </div>

        {/* Tranche triggers — primary action signal, always visible */}
        {(ev.t1 || ev.t2 || ev.t3) && (
          <div>
            <div className="label">Tranche triggers</div>
            <div className="space-y-1">
              {[ev.t1, ev.t2, ev.t3].map((t, i) =>
                t ? (
                  <div key={i} className="flex items-start gap-3">
                    <span className="font-semibold text-sm w-8">T{t.tranche_id}</span>
                    <VerdictBadge
                      verdict={fromTrancheTrigger(t, direction)}
                      size="sm"
                    />
                    {t.blockers.length > 0 && (
                      <span className="text-xs text-text-muted flex-1">
                        {t.blockers[0]}
                        {t.blockers.length > 1 ? ` (+${t.blockers.length - 1} more)` : ""}
                      </span>
                    )}
                  </div>
                ) : null,
              )}
            </div>
          </div>
        )}

        {/* Exits — surface only when there's something to act on */}
        {ev.exits.length > 0 && (
          <div>
            <div className="label">Exit directives</div>
            <ul className="text-xs space-y-1">
              {ev.exits.map((d, i) => (
                <li key={i} className={severityClass(d.severity)}>
                  <span className="font-semibold uppercase">{d.action}</span> · {d.reason}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Detailed indicators — collapsed by default */}
        <details className="border-t border-bg-border pt-2">
          <summary className="cursor-pointer text-xs text-text-secondary hover:text-text-primary">
            Indicator detail
          </summary>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs mt-3">
            <div>
              <div className="label">Regime</div>
              <div>
                SQN(100):{" "}
                <span className="font-mono">
                  {ev.sqn_100_value !== null ? ev.sqn_100_value.toFixed(2) : "—"}
                </span>{" "}
                <span className="text-text-muted">{ev.sqn_100_regime}</span>
              </div>
              <div>
                SQN(20):{" "}
                <span className="font-mono">
                  {ev.sqn_20_value !== null ? ev.sqn_20_value.toFixed(2) : "—"}
                </span>{" "}
                <span className="text-text-muted">{ev.sqn_20_regime}</span>
              </div>
              <div className="text-text-muted">{ev.sqn_diagnostic ?? "—"}</div>
            </div>

            <div>
              <div className="label">MA Ribbon</div>
              <div>10: ${ev.ma_10?.toFixed(2) ?? "—"}</div>
              <div>20: ${ev.ma_20?.toFixed(2) ?? "—"}</div>
              <div>50: ${ev.ma_50?.toFixed(2) ?? "—"}</div>
              <div>200: ${ev.ma_200?.toFixed(2) ?? "—"}</div>
              <div className="mt-1 text-text-muted">Stack: {ev.ma_stack_state}</div>
            </div>

            <div>
              <div className="label">Stoch / Structure</div>
              <div>
                %K {ev.stoch_k?.toFixed(1) ?? "—"} / %D {ev.stoch_d?.toFixed(1) ?? "—"}
              </div>
              <div>
                HH/HL: <CheckMark ok={ev.structure.higher_low_confirmed} /> /
                LH/LL: <CheckMark ok={ev.structure.lower_high_confirmed} />
              </div>
              <div>
                Pullback 20MA: <CheckMark ok={ev.structure.pullback_held_20ma} /> /
                50MA: <CheckMark ok={ev.structure.pullback_held_50ma} />
              </div>
            </div>
          </div>
        </details>

        {/* Gate condition breakdown — collapsed by default */}
        <details className="border-t border-bg-border pt-2">
          <summary className="cursor-pointer text-xs text-text-secondary hover:text-text-primary">
            Gate breakdown (5 conditions)
          </summary>
          <div className="flex flex-wrap gap-3 text-xs mt-3">
            <span>SQN(100) <CheckMark ok={ev.gate.sqn_100_pass} /></span>
            <span>SQN(20) <CheckMark ok={ev.gate.sqn_20_pass} /></span>
            <span>MA <CheckMark ok={ev.gate.ma_stack_pass} /></span>
            <span>Pullback <CheckMark ok={ev.gate.pullback_pass} /></span>
            <span>Structure <CheckMark ok={ev.gate.structure_pass} /></span>
          </div>
          {ev.gate.blockers.length > 0 && (
            <ul className="mt-2 text-xs text-signal-bear list-disc list-inside">
              {ev.gate.blockers.map((b, i) => (
                <li key={i}>{b}</li>
              ))}
            </ul>
          )}
        </details>
      </div>
    </div>
  );
}

function PlanningPanel({ onCreated }: { onCreated: () => void }) {
  const [ticker, setTicker] = useState("SPY");
  const [direction, setDirection] = useState<Direction>("long");
  const [benchmark, setBenchmark] = useState("SPY");
  const [allocation, setAllocation] = useState<number>(5000);
  const [evaluation, setEvaluation] = useState<PyramidEvaluation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const runEval = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const ev = await api.pyramidEvaluatePlanning(ticker, direction, benchmark);
      setEvaluation(ev);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [ticker, direction, benchmark]);

  const create = useCallback(async () => {
    setCreating(true);
    setError(null);
    try {
      await api.createPyramid({
        ticker,
        direction,
        total_allocation_usd: allocation,
        benchmark,
      });
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }, [ticker, direction, benchmark, allocation, onCreated]);

  return (
    <section className="panel">
      <div className="panel-header">Planning</div>
      <div className="panel-body space-y-3">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
          <label className="flex flex-col">
            <span className="label">Ticker</span>
            <input
              value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              className="input"
            />
          </label>
          <label className="flex flex-col">
            <span className="label">Direction</span>
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value as Direction)}
              className="input"
            >
              <option value="long">long</option>
              <option value="short">short</option>
            </select>
          </label>
          <label className="flex flex-col">
            <span className="label">Benchmark</span>
            <input
              value={benchmark}
              onChange={(e) => setBenchmark(e.target.value.toUpperCase())}
              className="input"
            />
          </label>
          <label className="flex flex-col">
            <span className="label">Allocation $</span>
            <input
              type="number"
              value={allocation}
              onChange={(e) => setAllocation(Number(e.target.value))}
              className="input"
            />
          </label>
        </div>
        <div className="flex items-center justify-end gap-2">
          <button
            type="button"
            className="btn"
            onClick={() => void runEval()}
            disabled={loading}
          >
            {loading ? "Evaluating…" : "Evaluate"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void create()}
            disabled={creating}
            title="Save as a pending pyramid"
          >
            {creating ? "Creating…" : "Create"}
          </button>
        </div>
        {error && <div className="text-xs text-signal-bear">{error}</div>}
        {evaluation && <EvaluationPanel ev={evaluation} />}
      </div>
    </section>
  );
}

function PyramidCard({ pyramid }: { pyramid: Pyramid }) {
  const [ev, setEv] = useState<PyramidEvaluation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setEv(await api.pyramidEvaluation(pyramid.id));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [pyramid.id]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="panel">
      <div className="panel-header flex items-center justify-between">
        <span>
          {pyramid.ticker}{" "}
          <span className="text-text-muted normal-case">
            ({pyramid.direction.toUpperCase()})
          </span>
        </span>
        <button
          type="button"
          className="btn text-xs"
          onClick={() => void refresh()}
          disabled={loading}
        >
          {loading ? "…" : "↻"}
        </button>
      </div>
      <div className="panel-body space-y-3">
        <div className="text-xs text-text-muted">
          ID {pyramid.id} · {pyramid.status} · $
          {pyramid.total_allocation_usd.toLocaleString()} · created{" "}
          {pyramid.created_date}
        </div>
        <TrancheTable tranches={pyramid.tranches} />
        {error && <div className="text-xs text-signal-bear">{error}</div>}
        {ev && <EvaluationPanel ev={ev} />}
      </div>
    </section>
  );
}

export function PyramidView() {
  const [pyramids, setPyramids] = useState<Pyramid[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPyramids(await api.pyramids(showAll ? "all" : "active"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [showAll]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <header className="page-header-row">
        <h1 className="page-title">Trend Pyramid</h1>
        <div className="flex items-center gap-3 text-xs">
          <label className="flex items-center gap-1.5 text-text-secondary uppercase tracking-wider">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            Show closed
          </label>
          <button
            type="button"
            className="btn"
            onClick={() => void refresh()}
            disabled={loading}
          >
            {loading ? "…" : "Refresh"}
          </button>
        </div>
      </header>

      <PlanningPanel onCreated={() => void refresh()} />

      {error && <div className="text-xs text-signal-bear">{error}</div>}
      {pyramids.length === 0 ? (
        <div className="panel p-3 text-sm text-text-secondary">
          No {showAll ? "" : "active "}pyramids. Use the planning panel above to
          evaluate a setup, then Create.
        </div>
      ) : (
        <div className="space-y-3">
          {pyramids.map((p) => (
            <PyramidCard key={p.id} pyramid={p} />
          ))}
        </div>
      )}
    </div>
  );
}
