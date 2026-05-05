import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { ScanResult } from "../api/types";

const BENCHMARKS = ["SPY", "QQQ", "IWM"] as const;

function regimeBadgeClass(regime: string | null | undefined): string {
  switch (regime) {
    case "strong_bull":
    case "bull":
      return "badge-bull";
    case "strong_bear":
    case "bear":
      return "badge-bear";
    case "neutral":
      return "badge-info";
    default:
      return "badge-muted";
  }
}

function diagnosticBadgeClass(diagnostic: string | null | undefined): string {
  switch (diagnostic) {
    case "confluence_bullish":
    case "healthy_trend":
    case "early_bull_signal":
    case "trend_forming":
      return "badge-bull";
    case "confluence_bearish":
    case "early_bear_signal":
    case "counter_trend_bounce":
      return "badge-bear";
    case "confluence_chase_warning":
      return "badge-flag";
    case "buy_the_dip":
    case "confluence_capitulation_watch":
      return "badge-info";
    default:
      return "badge-muted";
  }
}

function diagnosticLabel(diagnostic: string | null | undefined): string {
  if (!diagnostic) return "";
  // Convert snake_case → human-readable. Compact for header use.
  const map: Record<string, string> = {
    confluence_bullish: "confluence",
    confluence_bearish: "confluence",
    confluence_chase_warning: "chase ⚠",
    confluence_capitulation_watch: "capitulation ↻",
    healthy_trend: "healthy",
    normal_pullback: "pullback",
    buy_the_dip: "buy-the-dip",
    early_bull_signal: "early bull",
    early_bear_signal: "early bear",
    trend_forming: "forming",
    true_chop: "chop",
    bear_weakening: "weakening",
    counter_trend_bounce: "counter-trend",
    uncategorized: "—",
  };
  return map[diagnostic] ?? diagnostic;
}

export function RegimeHeader() {
  const [data, setData] = useState<Record<string, ScanResult | { error: string }>>({});
  const [loading, setLoading] = useState(false);
  const [lastFetch, setLastFetch] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    const next: Record<string, ScanResult | { error: string }> = {};
    await Promise.all(
      BENCHMARKS.map(async (t) => {
        try {
          next[t] = await api.scan(t);
        } catch (err) {
          next[t] = { error: err instanceof Error ? err.message : String(err) };
        }
      }),
    );
    setData(next);
    setLastFetch(new Date());
    setLoading(false);
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <header className="border-b border-bg-border bg-bg-panel px-4 py-3">
      <div className="flex items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <span className="text-text-secondary text-xs uppercase tracking-widest">Regime</span>
          <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
            {BENCHMARKS.map((t) => {
              const row = data[t];
              if (!row || "error" in row) {
                return (
                  <span key={t} className="text-text-muted text-xs">
                    {t} —
                  </span>
                );
              }
              const sqn100 = row.sqn.sqn_value;
              const sqn20 = row.sqn.sqn_20_value;
              const regime100 = row.sqn.regime;
              const regime20 = row.sqn.regime_20;
              const diag = row.sqn.diagnostic;
              const stack = row.ma_ribbon.stack_state;
              const divergent = regime20 && regime20 !== regime100;
              const flaggedDiag =
                diag === "confluence_chase_warning" ||
                diag === "confluence_capitulation_watch";
              const tooltipLines = [
                `SQN(100) ${regime100 ?? "—"}${sqn100 !== null ? ` (${sqn100.toFixed(2)})` : ""}`,
                `SQN(20) ${regime20 ?? "—"}${sqn20 !== null && sqn20 !== undefined ? ` (${sqn20.toFixed(2)})` : ""}`,
                `MA stack ${stack ?? "—"}`,
                diag ? `Diagnostic ${diag}` : "",
              ]
                .filter(Boolean)
                .join("\n");
              return (
                <span
                  key={t}
                  className="flex items-center gap-1.5"
                  title={tooltipLines}
                >
                  <span className="text-text-secondary text-xs font-mono">{t}</span>
                  <span className={`badge ${regimeBadgeClass(regime100)}`}>
                    {regime100 ?? "—"}
                  </span>
                  {divergent && (
                    <span
                      className={`badge ${regimeBadgeClass(regime20)} text-[10px]`}
                      title="SQN(20) tactical divergence"
                    >
                      20d {regime20}
                    </span>
                  )}
                  {flaggedDiag && (
                    <span className={`badge ${diagnosticBadgeClass(diag)} text-[10px]`}>
                      {diagnosticLabel(diag)}
                    </span>
                  )}
                </span>
              );
            })}
          </div>
        </div>
        <div className="flex items-center gap-3 text-xs text-text-muted">
          {lastFetch && <span>{lastFetch.toLocaleTimeString()}</span>}
          <button
            type="button"
            className="btn"
            onClick={() => void refresh()}
            disabled={loading}
            title="Refresh regime data"
          >
            {loading ? "…" : "↻"}
          </button>
        </div>
      </div>
    </header>
  );
}
