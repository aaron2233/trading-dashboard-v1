import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { TradingViewChart } from "../components/TradingViewChart";
import { VerdictBadge } from "../components/Verdict";
import { fromWeeklyConfluence } from "../lib/verdict";
import type { WeeklyScanResponse, WeeklySetup } from "../api/types";


function fmtPrice(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(2)}`;
}

function fmt(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

function badgeClassForStack(stack: string | null): string {
  if (stack === "full_bull" || stack === "bull_developing") return "badge-bull";
  if (stack === "full_bear" || stack === "bear_developing") return "badge-bear";
  if (stack === "compression") return "badge-flag";
  return "badge-muted";
}

function badgeClassForRegime(regime: string | null): string {
  if (regime === "strong_bull" || regime === "bull") return "badge-bull";
  if (regime === "strong_bear" || regime === "bear") return "badge-bear";
  return "badge-info";
}

function killSheetLink(s: WeeklySetup): string {
  if (s.direction === "none") return "/kill-sheet";
  const params = new URLSearchParams({
    ticker: s.ticker,
    direction: s.direction,
    account: "weekly",
    intent: "TREND CAPTURE",
    trigger_tf: "Weekly",
    conviction: "high",
  });
  return `/kill-sheet?${params.toString()}`;
}


function ChecklistPanel() {
  return (
    <details className="panel mb-4" open>
      <summary className="panel-header cursor-pointer">
        Sunday-scan workflow checklist
      </summary>
      <div className="panel-body text-sm space-y-2">
        <ol className="list-decimal pl-6 space-y-1 text-text-primary">
          <li>
            <strong>Regime read.</strong> SQN(100) on benchmark — read pulled
            below by the scan automatically.
          </li>
          <li>
            <strong>Scan watchlist on weekly chart.</strong> MA stack +
            Stochastic cross + regime alignment. The table below classifies each
            ticker into a confluence rating.
          </li>
          <li>
            <strong>Rank setups.</strong> Top 3 surfaced separately. Skill
            order: regime alignment &gt; Stoch location &gt; MA clarity.
          </li>
          <li>
            <strong>Pre-write kill sheets</strong> for top 1-3 — click the
            "Kill sheet" link on each row.
          </li>
          <li>
            <strong>Set TradingView alerts</strong> for weekly close triggers.
          </li>
          <li>
            <strong>Execute Friday/Monday</strong> when the weekly candle
            confirms — never mid-week on a hunch.
          </li>
        </ol>
        <p className="text-xs text-text-secondary mt-2">
          Penny stocks (close &lt; $5) auto-suggest "shares" vehicle — illiquid
          option chains aren't worth the spread.
        </p>
      </div>
    </details>
  );
}

function TopSetupCard({ setup, rank, onSelect }: {
  setup: WeeklySetup;
  rank: number;
  onSelect?: (ticker: string) => void;
}) {
  const verdict = fromWeeklyConfluence(setup.confluence, setup.direction);
  return (
    <div className="panel">
      <div className="panel-body">
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="text-text-muted text-xs font-mono">#{rank}</span>
            <span className="font-mono font-semibold text-base">{setup.ticker}</span>
            <span className="text-xs text-text-secondary">
              {fmtPrice(setup.close)} · bar {setup.bar_date ?? "—"}
            </span>
            {setup.is_penny_stock && (
              <span className="badge badge-flag text-xs">PENNY</span>
            )}
          </div>
          <VerdictBadge verdict={verdict} size="lg" />
        </div>

        {setup.why_now && (
          <p className="text-sm text-text-primary mb-2">{setup.why_now}</p>
        )}

        {setup.blockers.length > 0 && (
          <ul className="text-xs text-signal-flag mb-3 space-y-0.5">
            {setup.blockers.map((b, i) => (
              <li key={i}>⚠ {b}</li>
            ))}
          </ul>
        )}

        <div className="flex items-center justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-2 text-xs text-text-muted">
            <span>
              <span className="text-text-secondary">SQN</span>{" "}
              <span className={`badge ${badgeClassForRegime(setup.sqn_100_regime)} text-[10px]`}>
                {setup.sqn_100_regime ?? "—"}
              </span>
            </span>
            <span>·</span>
            <span>
              <span className="text-text-secondary">stack</span>{" "}
              <span className={`badge ${badgeClassForStack(setup.ma_stack_state)} text-[10px]`}>
                {setup.ma_stack_state ?? "—"}
              </span>
            </span>
            <span>·</span>
            <span>stoch <span className="font-mono">{fmt(setup.stoch_k)}/{fmt(setup.stoch_d)}</span></span>
            <span>·</span>
            <span>vehicle {setup.suggested_vehicle}</span>
          </div>
          <div className="flex items-center gap-2">
            {onSelect && (
              <button
                type="button"
                className="btn text-xs"
                onClick={() => onSelect(setup.ticker)}
              >
                Chart
              </button>
            )}
            {setup.direction !== "none" ? (
              <Link to={killSheetLink(setup)} className="btn btn-primary text-xs">
                Kill sheet →
              </Link>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function ThinSetupRow({ setup, onSelect }: {
  setup: WeeklySetup;
  onSelect?: (ticker: string) => void;
}) {
  const verdict = fromWeeklyConfluence(setup.confluence, setup.direction);
  return (
    <tr className="border-b border-bg-border/40">
      <td className="px-3 py-2">
        <span className="font-mono font-semibold">{setup.ticker}</span>
      </td>
      <td className="px-3 py-2">
        <VerdictBadge verdict={verdict} size="sm" />
      </td>
      <td className="px-3 py-2">
        <span className={`badge ${badgeClassForRegime(setup.sqn_100_regime)} text-[10px]`}>
          {setup.sqn_100_regime ?? "—"}
        </span>
      </td>
      <td className="px-3 py-2 text-right text-xs text-text-muted font-mono">
        {setup.rank_score}
      </td>
      <td className="px-3 py-2 text-right">
        <div className="flex items-center justify-end gap-2">
          {onSelect && (
            <button
              type="button"
              className="btn text-xs"
              onClick={() => onSelect(setup.ticker)}
            >
              Chart
            </button>
          )}
          {setup.direction !== "none" ? (
            <Link to={killSheetLink(setup)} className="btn text-xs">
              Kill sheet →
            </Link>
          ) : (
            <span className="text-xs text-text-muted">—</span>
          )}
        </div>
      </td>
    </tr>
  );
}

function TopSetupsSection({ setups, onSelect }: {
  setups: WeeklySetup[];
  onSelect?: (ticker: string) => void;
}) {
  if (setups.length === 0) {
    return (
      <section className="mb-6">
        <h3 className="text-base font-semibold text-text-primary mb-2">
          Top setups
        </h3>
        <div className="panel p-3 text-sm text-text-secondary">
          No actionable setups — chop / compression / no Stoch trigger across the
          watchlist.
        </div>
      </section>
    );
  }
  return (
    <section className="mb-6">
      <h3 className="text-base font-semibold text-text-primary mb-2">
        Top setups{" "}
        <span className="text-text-secondary font-normal text-sm">
          (ranked, top {Math.min(setups.length, 3)})
        </span>
      </h3>
      <div className="space-y-3">
        {setups.slice(0, 3).map((s, i) => (
          <TopSetupCard key={s.ticker} setup={s} rank={i + 1} onSelect={onSelect} />
        ))}
      </div>
    </section>
  );
}

function AllScannedTable({ setups, onSelect }: {
  setups: WeeklySetup[];
  onSelect?: (ticker: string) => void;
}) {
  if (setups.length === 0) {
    return (
      <section className="mb-6">
        <h3 className="text-base font-semibold text-text-primary mb-2">All scanned</h3>
        <div className="panel p-3 text-sm text-text-secondary">No tickers scanned.</div>
      </section>
    );
  }
  return (
    <section className="mb-6">
      <h3 className="text-base font-semibold text-text-primary mb-2">All scanned</h3>
      <div className="panel">
        <table className="w-full text-sm">
          <thead className="text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
            <tr>
              <th className="text-left px-3 py-2">Ticker</th>
              <th className="text-left px-3 py-2">Verdict</th>
              <th className="text-left px-3 py-2">SQN(100)</th>
              <th className="text-right px-3 py-2">Score</th>
              <th className="text-right px-3 py-2">Action</th>
            </tr>
          </thead>
          <tbody>
            {setups.map((s) => (
              <ThinSetupRow key={s.ticker} setup={s} onSelect={onSelect} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}


export function WeeklyTrendView() {
  const [tickerInput, setTickerInput] = useState("AAPL NVDA META MSFT GOOGL TSLA");
  const [benchmark, setBenchmark] = useState("SPY");
  const [data, setData] = useState<WeeklyScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartTicker, setChartTicker] = useState<string | null>(null);

  async function runScan() {
    const tickers = tickerInput
      .split(/[,\s]+/)
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);
    if (tickers.length === 0) return;
    setLoading(true);
    setError(null);
    try {
      const result = await api.weeklyScan({ tickers, benchmark });
      setData(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  const errorEntries = data ? Object.entries(data.errors) : [];

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Weekly Trend</h2>
      </div>
      <p className="page-subtitle">
        Position trading on the weekly TF · 120-180+ DTE · one chart, one decision
      </p>

      <ChecklistPanel />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void runScan();
        }}
        className="panel p-4 mb-6 space-y-3"
      >
        <div>
          <label className="label" htmlFor="weekly-tickers">
            Watchlist (comma or space separated)
          </label>
          <input
            id="weekly-tickers"
            className="input w-full font-mono"
            placeholder="AAPL NVDA META MSFT"
            value={tickerInput}
            onChange={(e) => setTickerInput(e.target.value)}
          />
        </div>
        <div className="flex items-end gap-3">
          <div className="flex-1 max-w-xs">
            <label className="label" htmlFor="weekly-benchmark">
              Benchmark (SQN regime read)
            </label>
            <input
              id="weekly-benchmark"
              className="input w-full font-mono"
              value={benchmark}
              onChange={(e) => setBenchmark(e.target.value.toUpperCase())}
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Scanning…" : "Run weekly scan"}
          </button>
        </div>
      </form>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {data && (
        <>
          <div className="panel p-3 mb-4 text-xs text-text-secondary">
            Scanned {data.setups.length} ticker{data.setups.length === 1 ? "" : "s"}{" "}
            against {data.benchmark}{" "}
            (regime{" "}
            <span className={`badge ${badgeClassForRegime(data.benchmark_regime)} text-xs`}>
              {data.benchmark_regime ?? "—"}
            </span>
            ) at {new Date(data.scan_time_utc).toLocaleString()}
          </div>

          <TopSetupsSection setups={data.top_setups} onSelect={setChartTicker} />

          {chartTicker && (
            <div className="mb-6">
              <TradingViewChart
                ticker={chartTicker}
                timeframe="1wk"
                height={460}
                title={`Weekly chart — ${chartTicker}`}
              />
            </div>
          )}

          <AllScannedTable setups={data.setups} onSelect={setChartTicker} />

          {errorEntries.length > 0 && (
            <details className="panel p-3 mt-4">
              <summary className="text-xs text-text-secondary cursor-pointer">
                Tickers excluded ({errorEntries.length})
              </summary>
              <ul className="text-xs text-text-secondary mt-2 space-y-0.5">
                {errorEntries.map(([t, msg]) => (
                  <li key={t}>
                    <span className="font-mono">{t}</span> — {msg}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
