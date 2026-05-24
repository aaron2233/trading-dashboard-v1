import { useState } from "react";
import { api } from "../api/client";
import { ActionVerdictBanner } from "../components/ActionVerdictBanner";
import { TradingViewChart } from "../components/TradingViewChart";
import { VerdictHero } from "../components/Verdict";
import { fromRawIndicators } from "../lib/verdict";
import type { ActionVerdict, ScanResult } from "../api/types";

type SkillContext = "none" | "lotto" | "weekly";
type GateDirection = "long" | "short";

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

  // Opt-in action verdict — default to "none" so the page stays a
  // pure indicator readout. User picks a skill context to get the
  // buy/wait/skip call applied to that ticker.
  const [skillContext, setSkillContext] = useState<SkillContext>("none");
  const [gateDirection, setGateDirection] = useState<GateDirection>("long");
  const [verdict, setVerdict] = useState<ActionVerdict | null>(null);
  const [verdictLoading, setVerdictLoading] = useState(false);
  const [verdictError, setVerdictError] = useState<string | null>(null);

  async function handleScan(t: string) {
    if (!t) return;
    setLoading(true);
    setError(null);
    setVerdict(null);
    setVerdictError(null);
    try {
      const result = await api.scan(t);
      setData(result);
      // Auto-fetch verdict when a context is already selected
      if (skillContext !== "none") {
        void fetchVerdict(t, skillContext, gateDirection);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  async function fetchVerdict(
    t: string,
    skill: SkillContext,
    dir: GateDirection,
  ) {
    if (skill === "none") {
      setVerdict(null);
      return;
    }
    setVerdictLoading(true);
    setVerdictError(null);
    try {
      const v = await api.actionGateVerdict(t, skill, dir);
      setVerdict(v);
    } catch (err) {
      setVerdictError(err instanceof Error ? err.message : String(err));
      setVerdict(null);
    } finally {
      setVerdictLoading(false);
    }
  }

  function onContextChange(next: SkillContext) {
    setSkillContext(next);
    if (data && next !== "none") {
      void fetchVerdict(data.ticker, next, gateDirection);
    } else {
      setVerdict(null);
    }
  }

  function onDirectionChange(next: GateDirection) {
    setGateDirection(next);
    if (data && skillContext !== "none") {
      void fetchVerdict(data.ticker, skillContext, next);
    }
  }

  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Scan</h2>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void handleScan(ticker.trim().toUpperCase());
        }}
        className="flex gap-2 mb-2"
      >
        <input
          className="input flex-1"
          placeholder="Ticker — equity (SPY, AAPL) or crypto (BTC_USDT, ETH_USDT)"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          autoFocus
        />
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Scanning…" : "Scan"}
        </button>
      </form>
      <div className="text-xs text-text-secondary mb-4">
        Crypto pairs use Crypto.com underscore format (e.g. <code>BTC_USDT</code>).
        Equities go through yfinance. Same MA Ribbon + Stochastic + SQN stack
        either way.
      </div>

      {/* Opt-in action verdict — default "none" keeps ScanView as a
          pure indicator readout; pick a skill to get the buy/wait/skip call. */}
      <div className="flex items-center gap-3 mb-4 flex-wrap text-xs">
        <span className="text-text-secondary uppercase tracking-widest font-semibold">
          Evaluate for
        </span>
        <select
          className="input text-xs py-1"
          value={skillContext}
          onChange={(e) => onContextChange(e.target.value as SkillContext)}
        >
          <option value="none">— no verdict —</option>
          <option value="lotto">Lotto (Daily / 2H / 0-14 DTE)</option>
          <option value="weekly">Weekly trend (1wk / 120-180 DTE)</option>
        </select>
        {skillContext !== "none" && (
          <>
            <span className="text-text-secondary">direction</span>
            <div className="inline-flex border border-bg-border rounded overflow-hidden">
              {(["long", "short"] as GateDirection[]).map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`px-2 py-1 uppercase ${
                    gateDirection === d
                      ? "bg-signal-flag/20 text-signal-flag"
                      : "text-text-secondary hover:text-text-primary"
                  }`}
                  onClick={() => onDirectionChange(d)}
                >
                  {d}
                </button>
              ))}
            </div>
          </>
        )}
      </div>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {data && verdict && (
        <ActionVerdictBanner verdict={verdict} />
      )}
      {data && skillContext !== "none" && verdictLoading && !verdict && (
        <div className="text-xs text-text-secondary mb-2">Computing verdict…</div>
      )}
      {data && verdictError && (
        <div className="text-xs text-signal-bear mb-2">
          Verdict failed: {verdictError}
        </div>
      )}

      {data && (
        <>
        <div className="mb-4">
          <VerdictHero
            verdict={fromRawIndicators({
              maStackState: data.ma_ribbon.stack_state,
              stochZone: data.stochastic.zone,
              stochSignal: data.stochastic.signal,
              sqnRegime: data.sqn.regime,
            })}
            context={`${data.ticker} · ${data.timeframe}`}
          />
        </div>
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
