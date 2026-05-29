import { useState } from "react";
import { api } from "../api/client";
import { TradeCard, type TradeCardBadge } from "../components/TradeCard";
import type {
  IndexSwingScanResponse,
  IndexSwingSetup,
} from "../api/types";

function killSheetLink(s: IndexSwingSetup): string {
  const params = new URLSearchParams({
    ticker: s.ticker,
    direction: "long",
    account: "main",
    intent: "SWING",
    trigger_tf: "2H",
    skill: "index-swing",
    conviction:
      s.confluence === "breakout_high_conviction" ? "high" : "medium",
    contract_type: "call",
  });
  // Index-swing setup carries explicit suggested stop / 2R target.
  const target = s.target_price ?? s.suggested_target_2r;
  const stop = s.stop_price ?? s.suggested_stop;
  if (target != null) params.set("target", String(target));
  if (stop != null) params.set("invalidation", String(stop));
  if (s.suggested_strike != null) params.set("strike", String(s.suggested_strike));
  if (s.why_now) params.set("trigger_desc", s.why_now);
  const noteParts: string[] = [];
  if (s.breakout) {
    noteParts.push(
      `Breakout above $${s.breakout.swing_high_value.toFixed(2)} ` +
      `(${s.breakout.swing_high_age_sessions} bars old) · ` +
      `vol ${s.breakout.volume_ratio.toFixed(2)}× · ` +
      `confluence ${s.breakout.confluence_count}/5`,
    );
  }
  if (s.sqn_100_regime) noteParts.push(`SQN(100) ${s.sqn_100_regime}`);
  if (s.sqn_20_regime) noteParts.push(`SQN(20) ${s.sqn_20_regime}`);
  if (s.suggested_dte) noteParts.push(`DTE: ${s.suggested_dte}`);
  if (s.suggested_delta) noteParts.push(`Delta: ${s.suggested_delta}`);
  if (noteParts.length) params.set("notes", noteParts.join(" · "));
  return `/kill-sheet?${params.toString()}`;
}

function badgesFor(s: IndexSwingSetup): TradeCardBadge[] {
  const out: TradeCardBadge[] = [];
  if (s.universe_tier === "primary") {
    out.push({ label: "PRIMARY", tone: "bull" });
  } else if (s.universe_tier === "secondary") {
    out.push({ label: "SECONDARY", tone: "info" });
  } else {
    out.push({ label: "OUT-OF-UNIVERSE", tone: "bear" });
  }
  if (s.sqn_100_regime) {
    const tone = s.sqn_100_regime.includes("bull")
      ? "bull"
      : s.sqn_100_regime.includes("bear") ? "bear" : "info";
    out.push({
      label: `SQN(100) ${s.sqn_100_regime.replace(/_/g, " ")}`,
      tone,
    });
  }
  if (s.sqn_20_regime) {
    const tone = s.sqn_20_regime.includes("bull")
      ? "bull"
      : s.sqn_20_regime.includes("bear") ? "bear" : "info";
    out.push({
      label: `SQN(20) ${s.sqn_20_regime.replace(/_/g, " ")}`,
      tone,
    });
  }
  return out;
}

function detailsFor(s: IndexSwingSetup): { label: string; value: string }[] {
  const d: { label: string; value: string }[] = [];
  if (s.breakout) {
    d.push({
      label: "Prior swing high",
      value: `$${s.breakout.swing_high_value.toFixed(2)} (${
        s.breakout.swing_high_age_sessions
      }d ago)`,
    });
    d.push({
      label: "Volume",
      value: `${s.breakout.volume_ratio.toFixed(2)}× avg`,
    });
    d.push({
      label: "Confluence",
      value: `${s.breakout.confluence_count}/5 quality filters`,
    });
    d.push({
      label: "Failed prior breakouts",
      value: `${s.breakout.nearby_failed_breakouts}`,
    });
  }
  return d;
}

function ChecklistPanel() {
  return (
    <details className="panel mb-4" open>
      <summary className="panel-header cursor-pointer">
        Index-swing strategy
      </summary>
      <div className="panel-body text-sm space-y-1 text-text-secondary">
        <p>
          Long-only daily breakouts above the prior 5-bar swing high on QQQ /
          IWM / SPY. 30-60 DTE long calls, 2% structural stop, 2R take-profit
          with optional trail. 15-60 day hold; never below 21 DTE.
        </p>
        <p>
          Hard skip: SQN(100) Strong Bear, OR SQN(100) Bear with SQN(20) &lt;
          −1.9. Backtest 1999-2022: 370 trades, WR 52.4%, expectancy +0.88R,
          PF 2.09, max equity DD −8.3R.
        </p>
      </div>
    </details>
  );
}

export function IndexSwingView() {
  const [data, setData] = useState<IndexSwingScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.indexSwingScan({});
      setData(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="container mx-auto p-4 max-w-5xl">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-semibold">Index Swing</h1>
          <p className="text-xs text-text-secondary mt-1">
            QQQ / IWM / SPY · long-only · 30-60 DTE · 2% stop / 2R target
          </p>
        </div>
        <button
          type="button"
          onClick={runScan}
          disabled={loading}
          className="btn btn-primary"
        >
          {loading ? "Scanning…" : "Run scan"}
        </button>
      </div>

      <ChecklistPanel />

      {error && (
        <div className="panel mb-4 border-signal-bear">
          <div className="panel-body text-sm text-signal-bear">
            Error: {error}
          </div>
        </div>
      )}

      {data && (
        <>
          <div className="text-xs text-text-secondary mb-3">
            Scan time: {new Date(data.scan_time_utc).toLocaleString()} ·{" "}
            {data.actionable_setups.length} actionable / {data.setups.length} total
          </div>

          <div className="space-y-3">
            {data.setups.map((s) => (
              <TradeCard
                key={s.ticker}
                setup={s}
                strategy_label="Index swing"
                kill_sheet_href={killSheetLink(s)}
                badges={badgesFor(s)}
                details={detailsFor(s)}
              />
            ))}
          </div>

          {Object.keys(data.errors).length > 0 && (
            <section className="mt-6">
              <h2 className="text-sm font-semibold uppercase tracking-wider text-signal-flag mb-2">
                Errors
              </h2>
              <ul className="text-xs text-signal-flag space-y-1">
                {Object.entries(data.errors).map(([ticker, msg]) => (
                  <li key={ticker}>
                    <span className="font-mono">{ticker}:</span> {msg}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </div>
  );
}
