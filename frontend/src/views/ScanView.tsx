import { useState } from "react";
import { api } from "../api/client";
import { TradingViewChart } from "../components/TradingViewChart";
import type { ScanResult } from "../api/types";

function fmt(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

function badgeClassForStack(stack: string | null): string {
  if (stack === "full_bull" || stack === "bull_developing") return "badge-bull";
  if (stack === "full_bear" || stack === "bear_developing") return "badge-bear";
  if (stack === "compression") return "badge-flag";
  return "badge-muted";
}

function badgeClassForZone(zone: string | null): string {
  if (zone === "oversold") return "badge-bull";
  if (zone === "overbought") return "badge-bear";
  return "badge-info";
}

function badgeClassForRegime(regime: string | null): string {
  if (regime === "strong_bull" || regime === "bull") return "badge-bull";
  if (regime === "strong_bear" || regime === "bear") return "badge-bear";
  return "badge-info";
}

export function ScanView() {
  const [ticker, setTicker] = useState("");
  const [data, setData] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleScan(t: string) {
    if (!t) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.scan(t);
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <h2 className="text-lg font-semibold mb-4">Scan</h2>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleScan(ticker.trim().toUpperCase());
        }}
        className="flex gap-2 mb-4"
      >
        <input
          className="input flex-1"
          placeholder="Ticker (e.g. SPY, AAPL, GLD)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          autoFocus
        />
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Scanning…" : "Scan"}
        </button>
      </form>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {data && (
        <>
        <div className="mb-4">
          <TradingViewChart
            ticker={data.ticker}
            timeframe={data.timeframe}
            height={420}
          />
        </div>
        <div className="panel">
          <div className="panel-header flex items-center justify-between">
            <span>{data.ticker} — {data.timeframe} bar {data.bar_date}</span>
            <span className="text-text-secondary">close ${fmt(data.close)}</span>
          </div>
          <div className="panel-body grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <div className="label">MA Ribbon</div>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-text-secondary">10</span><span>${fmt(data.ma_ribbon.ma_10)}</span></div>
                <div className="flex justify-between"><span className="text-text-secondary">20</span><span>${fmt(data.ma_ribbon.ma_20)}</span></div>
                <div className="flex justify-between"><span className="text-text-secondary">50</span><span>${fmt(data.ma_ribbon.ma_50)}</span></div>
                <div className="flex justify-between"><span className="text-text-secondary">200</span><span>${fmt(data.ma_ribbon.ma_200)}</span></div>
                <div className="pt-2">
                  <span className={`badge ${badgeClassForStack(data.ma_ribbon.stack_state)}`}>
                    {data.ma_ribbon.stack_state ?? "—"}
                  </span>
                </div>
              </div>
            </div>
            <div>
              <div className="label">Stochastic 14/7/7</div>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between"><span className="text-text-secondary">%K</span><span>{fmt(data.stochastic.k, 1)}</span></div>
                <div className="flex justify-between"><span className="text-text-secondary">%D</span><span>{fmt(data.stochastic.d, 1)}</span></div>
                <div className="pt-2 flex flex-wrap gap-2">
                  <span className={`badge ${badgeClassForZone(data.stochastic.zone)}`}>
                    {data.stochastic.zone ?? "—"}
                  </span>
                  <span className="badge badge-muted">{data.stochastic.signal ?? "—"}</span>
                </div>
              </div>
            </div>
            <div>
              <div className="label">SQN Regime (100d)</div>
              <div className="space-y-1 text-sm">
                <div className="flex justify-between">
                  <span className="text-text-secondary">value</span>
                  <span>{fmt(data.sqn.sqn_value, 2)}</span>
                </div>
                <div className="pt-2">
                  <span className={`badge ${badgeClassForRegime(data.sqn.regime)}`}>
                    {data.sqn.regime ?? "—"}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>
        </>
      )}
    </div>
  );
}
