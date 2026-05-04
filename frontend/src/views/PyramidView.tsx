import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
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

function GateBadge({ permitted }: { permitted: boolean }) {
  return (
    <span className={`badge ${permitted ? "badge-bull" : "badge-bear"}`}>
      {permitted ? "GREEN" : "RED"}
    </span>
  );
}

function CheckMark({ ok }: { ok: boolean }) {
  return (
    <span className={ok ? "text-signal-bull" : "text-signal-bear"}>
      {ok ? "✓" : "✗"}
    </span>
  );
}

function TrancheRow({ tranche }: { tranche: Tranche }) {
  const cost =
    tranche.cost_basis_per_unit !== null && tranche.quantity !== null
      ? tranche.vehicle === "leaps_call" || tranche.vehicle === "leaps_put"
        ? tranche.cost_basis_per_unit * tranche.quantity * 100
        : tranche.cost_basis_per_unit * tranche.quantity
      : null;
  return (
    <div className="text-xs grid grid-cols-7 gap-2 py-1">
      <span className="font-semibold">T{tranche.id}</span>
      <span
        className={`badge ${
          tranche.status === "filled"
            ? "badge-bull"
            : tranche.status === "skipped"
            ? "badge-muted"
            : "badge-info"
        }`}
      >
        {tranche.status}
      </span>
      <span>{tranche.vehicle ?? "—"}</span>
      <span>
        {tranche.cost_basis_per_unit !== null
          ? `$${tranche.cost_basis_per_unit}`
          : "—"}
      </span>
      <span>{tranche.quantity ?? "—"}</span>
      <span>
        {tranche.expiry ? `${tranche.expiry} (DTE)` : tranche.strike ? `K=$${tranche.strike}` : "—"}
      </span>
      <span>{cost !== null ? `$${cost.toFixed(0)}` : "—"}</span>
    </div>
  );
}

function EvaluationPanel({ ev }: { ev: PyramidEvaluation }) {
  return (
    <div className="border border-bg-border rounded p-3 bg-bg-panel space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <span className="text-text-secondary text-xs uppercase tracking-widest">Evaluation</span>
          <h3 className="text-lg font-semibold mt-1">
            {ev.ticker}{" "}
            <span className="text-text-muted">
              ({ev.direction.toUpperCase()})
            </span>
          </h3>
          <div className="text-xs text-text-muted">
            Bar: {ev.bar_date} · Close ${ev.close.toFixed(2)}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-text-muted">Gate</span>
          <GateBadge permitted={ev.gate.permitted} />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-3 text-xs">
        <div>
          <div className="text-text-muted uppercase text-[10px] mb-1">Regime</div>
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
          <div className="text-text-muted uppercase text-[10px] mb-1">MA Ribbon</div>
          <div>10: ${ev.ma_10?.toFixed(2) ?? "—"}</div>
          <div>20: ${ev.ma_20?.toFixed(2) ?? "—"}</div>
          <div>50: ${ev.ma_50?.toFixed(2) ?? "—"}</div>
          <div>200: ${ev.ma_200?.toFixed(2) ?? "—"}</div>
          <div className="mt-1 text-text-muted">Stack: {ev.ma_stack_state}</div>
        </div>

        <div>
          <div className="text-text-muted uppercase text-[10px] mb-1">Stoch / Structure</div>
          <div>
            %K {ev.stoch_k?.toFixed(1) ?? "—"} / %D {ev.stoch_d?.toFixed(1) ?? "—"}
          </div>
          <div>HH/HL: <CheckMark ok={ev.structure.higher_low_confirmed} /> / LH/LL: <CheckMark ok={ev.structure.lower_high_confirmed} /></div>
          <div>
            Pullback 20MA: <CheckMark ok={ev.structure.pullback_held_20ma} /> / 50MA: <CheckMark ok={ev.structure.pullback_held_50ma} />
          </div>
        </div>
      </div>

      <div>
        <div className="text-text-muted uppercase text-[10px] mb-1">Gate (5 conditions)</div>
        <div className="flex gap-3 text-xs">
          <span>SQN(100) <CheckMark ok={ev.gate.sqn_100_pass} /></span>
          <span>SQN(20) <CheckMark ok={ev.gate.sqn_20_pass} /></span>
          <span>MA <CheckMark ok={ev.gate.ma_stack_pass} /></span>
          <span>Pullback <CheckMark ok={ev.gate.pullback_pass} /></span>
          <span>Structure <CheckMark ok={ev.gate.structure_pass} /></span>
        </div>
        {ev.gate.blockers.length > 0 && (
          <ul className="mt-1 text-xs text-signal-bear list-disc list-inside">
            {ev.gate.blockers.map((b, i) => (
              <li key={i}>{b}</li>
            ))}
          </ul>
        )}
      </div>

      {(ev.t1 || ev.t2 || ev.t3) && (
        <div>
          <div className="text-text-muted uppercase text-[10px] mb-1">Tranche Triggers</div>
          {[ev.t1, ev.t2, ev.t3].map((t, i) =>
            t ? (
              <div key={i} className="text-xs mb-1">
                <span className="font-semibold">T{t.tranche_id}</span>{" "}
                {t.should_fire ? (
                  <span className="badge badge-bull">FIRE</span>
                ) : (
                  <span className="badge badge-muted">WAIT</span>
                )}
                {t.blockers.length > 0 && (
                  <ul className="ml-4 list-disc list-inside text-text-muted">
                    {t.blockers.map((b, j) => (
                      <li key={j}>{b}</li>
                    ))}
                  </ul>
                )}
              </div>
            ) : null,
          )}
        </div>
      )}

      <div>
        <div className="text-text-muted uppercase text-[10px] mb-1">Exits</div>
        <ul className="text-xs space-y-0.5">
          {ev.exits.map((d, i) => (
            <li key={i} className={severityClass(d.severity)}>
              [{d.action}] {d.reason}
            </li>
          ))}
        </ul>
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
    <section className="border border-bg-border rounded p-3 bg-bg-panel space-y-3">
      <h2 className="text-sm uppercase tracking-widest text-text-secondary">Planning</h2>
      <div className="grid grid-cols-5 gap-2 text-sm">
        <label className="flex flex-col">
          <span className="text-xs text-text-muted">Ticker</span>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="input"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-xs text-text-muted">Direction</span>
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
          <span className="text-xs text-text-muted">Benchmark</span>
          <input
            value={benchmark}
            onChange={(e) => setBenchmark(e.target.value.toUpperCase())}
            className="input"
          />
        </label>
        <label className="flex flex-col">
          <span className="text-xs text-text-muted">Allocation $</span>
          <input
            type="number"
            value={allocation}
            onChange={(e) => setAllocation(Number(e.target.value))}
            className="input"
          />
        </label>
        <div className="flex items-end gap-2">
          <button type="button" className="btn" onClick={() => void runEval()} disabled={loading}>
            {loading ? "…" : "Evaluate"}
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => void create()}
            disabled={creating}
            title="Save as a pending pyramid"
          >
            {creating ? "…" : "Create"}
          </button>
        </div>
      </div>
      {error && <div className="text-xs text-signal-bear">{error}</div>}
      {evaluation && <EvaluationPanel ev={evaluation} />}
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
    <section className="border border-bg-border rounded p-3 bg-bg-panel space-y-2">
      <header className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold">
            {pyramid.ticker}{" "}
            <span className="text-text-muted">({pyramid.direction.toUpperCase()})</span>
          </h3>
          <div className="text-xs text-text-muted">
            ID {pyramid.id} · {pyramid.status} · ${pyramid.total_allocation_usd.toLocaleString()} ·
            created {pyramid.created_date}
          </div>
        </div>
        <button type="button" className="btn text-xs" onClick={() => void refresh()} disabled={loading}>
          {loading ? "…" : "↻"}
        </button>
      </header>

      <div className="text-xs grid grid-cols-7 gap-2 text-text-muted font-semibold">
        <span>Tranche</span>
        <span>Status</span>
        <span>Vehicle</span>
        <span>Cost</span>
        <span>Qty</span>
        <span>Strike/Expiry</span>
        <span>Total $</span>
      </div>
      {pyramid.tranches.map((t) => (
        <TrancheRow key={t.id} tranche={t} />
      ))}

      {error && <div className="text-xs text-signal-bear">{error}</div>}
      {ev && <EvaluationPanel ev={ev} />}
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
    <div className="p-4 space-y-4">
      <header className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">Trend Pyramid</h1>
        <div className="flex items-center gap-2 text-xs">
          <label className="flex items-center gap-1 text-text-muted">
            <input
              type="checkbox"
              checked={showAll}
              onChange={(e) => setShowAll(e.target.checked)}
            />
            Show closed
          </label>
          <button type="button" className="btn" onClick={() => void refresh()} disabled={loading}>
            {loading ? "…" : "↻"}
          </button>
        </div>
      </header>

      <PlanningPanel onCreated={() => void refresh()} />

      {error && <div className="text-xs text-signal-bear">{error}</div>}
      {pyramids.length === 0 ? (
        <div className="text-sm text-text-muted">
          No {showAll ? "" : "active "}pyramids. Use the planning panel above to evaluate a setup
          and Create one.
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
