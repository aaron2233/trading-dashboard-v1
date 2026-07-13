import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { fmtUsd, fmtUsdWhole } from "../lib/format";
import type { CoreStateResponse } from "../api/types";

/** QQQM core strategy view. The two-state signal (weekly close > 40WMA AND
 * SQN(100) >= +0.7) is computed by the daily monitor job — this view renders
 * its latest output plus the journal's core-sleeve position. It never
 * recomputes the signal: one computation source. */

const ACTION_TAG_LABEL: Record<string, string> = {
  S: "SIGNAL",
  E: "ENTRY",
  R: "ROLL",
  P: "PROVISIONAL",
  W: "TRACK A",
  D: "DIP · INFO",
};

const KILL_SHEET_PREFILL =
  "/kill-sheet?ticker=QQQM&account=beatmarket&skill=qqqm-core" +
  "&direction=long&intent=POSITION&conviction=high";

function fmtPctVs(close: number, ref: number): string {
  return `${((close / ref - 1) * 100).toFixed(1)}%`;
}

export function CoreView() {
  const [state, setState] = useState<CoreStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.coreState().then(setState).catch((err) =>
      setError(err instanceof Error ? err.message : String(err)),
    );
  }, []);

  const monitor = state?.monitor ?? null;
  const signal = monitor?.signal ?? null;

  return (
    <div className="max-w-3xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">QQQM Core</h2>
      </div>
      <p className="page-subtitle">
        One deep-ITM LEAPS · weekly 40WMA + SQN(100) two-state engine
      </p>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {state && !monitor && (
        <section className="panel p-4 mb-4">
          <div className="text-sm text-text-secondary">
            Monitor output unavailable: {state.monitor_error}
          </div>
          <div className="text-xs text-text-muted mt-1">
            The signal is computed by the daily qqqm-core monitor job
            (weekdays pre-open). Run it manually to populate this view.
          </div>
        </section>
      )}

      {signal && (
        <section
          className={`panel mb-4 border-l-4 ${
            signal.on ? "border-l-signal-bull" : "border-l-text-muted"
          }`}
        >
          <div className="panel-body">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`badge ${signal.on ? "badge-bull" : "badge-muted"} text-sm font-bold`}>
                SIGNAL {signal.on ? "ON" : "OFF"}
              </span>
              <span className="text-sm text-text-primary">
                since <span className="font-mono">{signal.since}</span>
              </span>
              {state?.monitor_stale && (
                <span className="badge badge-flag text-[10px]" title={`generated ${monitor?.generated}`}>
                  ⚠ STALE READ
                </span>
              )}
            </div>
            <div className="text-xs text-text-secondary font-mono mt-2">
              completed wk {signal.completed_week}: QQQ {signal.close.toFixed(2)} vs
              40WMA {signal.ma40.toFixed(2)} · SQN(100) {signal.sqn100 >= 0 ? "+" : ""}
              {signal.sqn100.toFixed(2)}
            </div>
            {signal.provisional_on !== null && signal.provisional_on !== signal.on && (
              <div className="text-xs text-signal-flag mt-1">
                ⚠ Week-to-date would flip the signal {signal.provisional_on ? "ON" : "OFF"} —
                heads-up only, the signal acts on completed Friday closes.
              </div>
            )}
          </div>
        </section>
      )}

      {state && (
        <section className="panel mb-4">
          <header className="panel-header">Core position</header>
          <div className="panel-body">
            {state.positions.length > 0 ? (
              <table className="w-full text-sm">
                <tbody>
                  {state.positions.map((p) => (
                    <tr key={p.id} className="border-b border-bg-border last:border-0">
                      <td className="py-1.5 pr-3 font-mono font-semibold">{p.ticker}</td>
                      <td className="py-1.5 pr-3 font-mono">
                        {p.strike !== null ? `$${p.strike}C` : "—"} · {p.expiry ?? "—"}
                      </td>
                      <td className="py-1.5 pr-3 font-mono text-right">
                        {p.total_cost_usd !== null ? fmtUsd(p.total_cost_usd) : "—"}
                      </td>
                      <td className="py-1.5 text-right whitespace-nowrap">
                        {p.roll_status === "roll_now" ? (
                          <span className="badge badge-bear text-[10px]">
                            ⛔ {p.dte} DTE — ROLL NOW (≤60 floor)
                          </span>
                        ) : p.roll_status === "roll_window" ? (
                          <span className="badge badge-flag text-[10px]">
                            ⚠ {p.dte} DTE — plan the roll
                          </span>
                        ) : (
                          <span className="text-xs text-text-muted font-mono">
                            {p.dte !== null ? `${p.dte} DTE` : ""}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <>
                <div className="text-sm text-text-primary mb-1">
                  Core unbought
                  {monitor && (
                    <span className="text-text-secondary"> — {monitor.core_state_note}</span>
                  )}
                </div>
                {state.sleeve && (
                  <div className="text-xs text-text-secondary font-mono mb-2">
                    sleeve {fmtUsdWhole(state.sleeve.balance_usd)} · premium target{" "}
                    {fmtUsdWhole(state.sleeve.premium_target_usd)} (
                    {Math.round(
                      (state.sleeve.premium_target_usd / state.sleeve.balance_usd) * 100,
                    )}
                    % of sleeve) · D0.75–0.85 · ≥365 DTE
                  </div>
                )}
                {signal?.on && (
                  <Link to={KILL_SHEET_PREFILL} className="btn btn-primary text-xs">
                    Pre-fill kill sheet →
                  </Link>
                )}
              </>
            )}
          </div>
        </section>
      )}

      {monitor && monitor.actions.length > 0 && (
        <section className="panel mb-4">
          <header className="panel-header">Today&apos;s actions</header>
          <div className="panel-body space-y-2">
            {monitor.actions.map((a, i) => (
              <div key={i} className="text-sm">
                <span className="badge badge-flag text-[10px] mr-2">
                  {ACTION_TAG_LABEL[a.tag] ?? a.tag}
                </span>
                <span className="text-text-secondary">{a.detail}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {monitor && (
        <section className="panel mb-4">
          <header className="panel-header">Regime / levels</header>
          <div className="panel-body font-mono text-xs space-y-1">
            {Object.entries(monitor.levels).map(([ticker, lvl]) =>
              lvl ? (
                <div key={ticker}>
                  <span className="font-semibold text-text-primary">{ticker}</span>{" "}
                  {lvl.close.toFixed(2)} · SQN100 {lvl.sqn100 >= 0 ? "+" : ""}
                  {lvl.sqn100.toFixed(2)} ({lvl.regime}) · SQN20{" "}
                  {lvl.sqn20 >= 0 ? "+" : ""}
                  {lvl.sqn20.toFixed(2)} · vs 200DMA {fmtPctVs(lvl.close, lvl.ma200)} ·
                  Stoch {lvl.k.toFixed(0)}
                </div>
              ) : (
                <div key={ticker} className="text-text-muted">{ticker}: no data</div>
              ),
            )}
          </div>
        </section>
      )}

      {monitor && (
        <p className="text-[11px] text-text-muted font-mono">
          as of close {monitor.as_of_close ?? "—"} · generated {monitor.generated} ·
          position-blind monitor — signal state, not fills · acts on completed
          Friday closes · verify live quotes before trading
        </p>
      )}
    </div>
  );
}
