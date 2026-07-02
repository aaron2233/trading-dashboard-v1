import { useState } from "react";
import { api } from "../api/client";
import { TradeCard, type TradeCardBadge } from "../components/TradeCard";
import type {
  RegimeLeveredScanResponse,
  RegimeLeveredSetup,
} from "../api/types";

function killSheetLink(s: RegimeLeveredSetup): string {
  const params = new URLSearchParams({
    ticker: s.ticker,
    direction: "long",
    account: "main",
    intent: "POSITION",
    trigger_tf: "Weekly",
    skill: "regime-levered-trend",
    conviction: s.confluence === "core_entry" ? "high" : "medium",
    contract_type: "call",
  });
  if (s.stop_price != null) params.set("invalidation", String(s.stop_price));
  if (s.suggested_strike != null) params.set("strike", String(s.suggested_strike));
  if (s.why_now) params.set("trigger_desc", s.why_now);
  const noteParts: string[] = [
    "cohort: regime-levered-trend (forward-test)",
    "Layer 1 core · 365-540 DTE LEAPS · 0.75-0.90Δ",
    "Stop: 19WMA weekly close · cut −60% premium backstop",
  ];
  if (s.own_sqn_100 != null) {
    noteParts.push(`Own SQN(100) ${s.own_sqn_100.toFixed(2)}`);
  }
  params.set("notes", noteParts.join(" · "));
  return `/kill-sheet?${params.toString()}`;
}

function badgesFor(s: RegimeLeveredSetup): TradeCardBadge[] {
  const out: TradeCardBadge[] = [];
  if (s.confluence === "core_entry") {
    out.push({ label: "CORE ENTRY", tone: "bull" });
  } else if (s.confluence === "overbought_watch") {
    out.push({ label: "OVERBOUGHT — WATCH", tone: "flag" });
  } else if (s.confluence === "bull_no_trigger") {
    out.push({ label: "BULL · NO TRIGGER", tone: "info" });
  } else {
    out.push({ label: s.confluence.replace(/_/g, " ").toUpperCase(), tone: "muted" });
  }
  if (s.own_regime) {
    const tone = s.own_regime.includes("bull")
      ? "bull"
      : s.own_regime.includes("bear") ? "bear" : "info";
    const sqn = s.own_sqn_100 != null ? ` ${s.own_sqn_100.toFixed(2)}` : "";
    out.push({ label: `OWN SQN(100)${sqn}`, tone });
  }
  return out;
}

function detailsFor(s: RegimeLeveredSetup): { label: string; value: string }[] {
  const d: { label: string; value: string }[] = [];
  const w = s.weekly;
  if (!w) return d;
  if (w.ma_19 != null) {
    d.push({ label: "19WMA stop", value: `$${w.ma_19.toFixed(2)}` });
  }
  if (w.ma_20 != null) {
    d.push({ label: "20WMA", value: `$${w.ma_20.toFixed(2)}` });
  }
  if (w.stoch_k != null && w.stoch_k_prev != null) {
    d.push({
      label: "Weekly Stoch %K",
      value: `${w.stoch_k.toFixed(0)} (prev ${w.stoch_k_prev.toFixed(0)})${
        w.stoch_turned_up ? " ↑" : ""
      }`,
    });
  }
  d.push({ label: "Full Bull ribbon", value: w.full_bull ? "yes" : "no" });
  return d;
}

function StrategyPanel() {
  return (
    <details className="panel mb-4" open>
      <summary className="panel-header cursor-pointer">
        Regime-levered trend · Layer 1 core scan
      </summary>
      <div className="panel-body text-sm space-y-1 text-text-secondary">
        <p>
          Concentrated deep-delta LEAPS (365-540 DTE, 0.75-0.90Δ) on the
          strongest own-SQN(100) Bull trends. Entry: weekly Stoch reset-turn
          holding above the 20WMA in a rising Full Bull ribbon, gated by broad
          SQN(100) Bull. Stop: 19WMA weekly close. Max 2 concurrent slots.
        </p>
        <p>
          FORWARD-TEST cohort — synthetic-option backtest 2000-2026: 53
          trades, WR 45%, avg premium +39.8%, 34.7x vs SPY 8.2x, MaxDD −36%
          vs −55%. Deployment blocked in the main account while R1/R2
          recovery rules are active (dedicated sleeve only).
        </p>
      </div>
    </details>
  );
}

export function RegimeLeveredView() {
  const [data, setData] = useState<RegimeLeveredScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      const result = await api.regimeLeveredScan({});
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
          <h1 className="text-xl font-semibold">Regime-Levered Trend</h1>
          <p className="text-xs text-text-secondary mt-1">
            Deep-delta LEAPS core · weekly TF · max 2 slots · forward-test
            cohort
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

      <StrategyPanel />

      {error && (
        <div className="panel mb-4 border-signal-bear">
          <div className="panel-body text-sm text-signal-bear">
            Error: {error}
          </div>
        </div>
      )}

      {data && (
        <>
          <div
            className={`panel mb-4 ${
              data.layer1_live ? "border-signal-bull" : "border-signal-bear"
            }`}
          >
            <div className="panel-body text-sm">
              <span className="font-semibold">
                Broad SQN(100): {data.broad_sqn_100?.toFixed(2) ?? "n/a"} (
                {data.broad_regime?.replace(/_/g, " ") ?? "unknown"})
              </span>{" "}
              ·{" "}
              {data.layer1_live
                ? "Layer 1 OPEN to new entries"
                : "Layer 1 CLOSED — cash is the position"}
              <div className="text-xs text-text-secondary mt-1">
                {data.deployment_note}
              </div>
            </div>
          </div>

          <section className="mb-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider mb-2">
              Layer 2 · Rule-19 dip-buy (SPY/QQQ)
            </h2>
            <div className="space-y-1">
              {data.dip_buy_signals.map((d) => (
                <div
                  key={d.ticker}
                  className={`panel ${d.fired ? "border-signal-bull" : ""}`}
                >
                  <div className="panel-body text-xs flex items-center gap-2">
                    <span className="font-mono font-semibold">{d.ticker}</span>
                    <span
                      className={
                        d.fired ? "text-signal-bull font-semibold" : "text-text-secondary"
                      }
                    >
                      {d.fired ? "SIGNAL" : "—"}
                    </span>
                    <span className="text-text-secondary">{d.note}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <div className="text-xs text-text-secondary mb-3">
            Scan time: {new Date(data.scan_time_utc).toLocaleString()} ·{" "}
            {data.core_candidates.length} core candidate
            {data.core_candidates.length === 1 ? "" : "s"} /{" "}
            {data.setups.length} scanned
          </div>

          <div className="space-y-3">
            {data.setups.map((s) => (
              <TradeCard
                key={s.ticker}
                setup={s}
                strategy_label="Regime-levered · Layer 1"
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
