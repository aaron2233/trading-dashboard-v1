import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { TradingViewChart } from "../components/TradingViewChart";
import type {
  WeeklyConfluence,
  WeeklyScanResponse,
  WeeklySetup,
} from "../api/types";


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

const CONFLUENCE_LABEL: Record<WeeklyConfluence, string> = {
  high_conviction_long: "HIGH CONVICTION LONG",
  high_conviction_short: "HIGH CONVICTION SHORT",
  continuation_long: "Continuation long",
  continuation_short: "Continuation short",
  compression: "Compression — wait",
  chop: "Chop — sit out",
  no_setup: "No setup",
};

function badgeClassForConfluence(c: WeeklyConfluence): string {
  if (c.startsWith("high_conviction")) return "badge-bull";
  if (c.startsWith("continuation")) return "badge-info";
  if (c === "compression") return "badge-flag";
  if (c === "chop") return "badge-bear";
  return "badge-muted";
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

function SetupRow({ setup, onSelect }: {
  setup: WeeklySetup;
  onSelect?: (ticker: string) => void;
}) {
  return (
    <tr className="border-b border-bg-border/40 align-top">
      <td className="px-3 py-2">
        <div className="flex items-center gap-2">
          <span className="font-mono font-semibold">{setup.ticker}</span>
          {setup.is_penny_stock && (
            <span className="badge badge-flag text-xs">PENNY</span>
          )}
        </div>
        <div className="text-xs text-text-secondary">
          {fmtPrice(setup.close)} · bar {setup.bar_date ?? "—"}
        </div>
      </td>
      <td className="px-3 py-2">
        <span className={`badge ${badgeClassForConfluence(setup.confluence)} text-xs`}>
          {CONFLUENCE_LABEL[setup.confluence]}
        </span>
        <div className="text-xs text-text-secondary mt-1">{setup.why_now}</div>
        {setup.blockers.length > 0 && (
          <ul className="text-xs text-signal-flag mt-1 space-y-0.5">
            {setup.blockers.map((b, i) => (
              <li key={i}>⚠ {b}</li>
            ))}
          </ul>
        )}
      </td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          <span className={`badge ${badgeClassForStack(setup.ma_stack_state)} text-xs`}>
            {setup.ma_stack_state ?? "—"}
          </span>
          <span className="badge badge-info text-xs">
            stoch {fmt(setup.stoch_k)}/{fmt(setup.stoch_d)}
          </span>
        </div>
      </td>
      <td className="px-3 py-2 text-xs">
        <span className={`badge ${badgeClassForRegime(setup.sqn_100_regime)} text-xs`}>
          {setup.sqn_100_regime ?? "—"}
        </span>
      </td>
      <td className="px-3 py-2 text-right">
        <div className="text-xs text-text-secondary">score {setup.rank_score}</div>
        <div className="text-xs text-text-muted">
          vehicle: {setup.suggested_vehicle}
        </div>
      </td>
      <td className="px-3 py-2 text-right space-y-1">
        {onSelect && (
          <button
            type="button"
            className="btn text-xs block ml-auto"
            onClick={() => onSelect(setup.ticker)}
          >
            Chart
          </button>
        )}
        {setup.direction !== "none" ? (
          <Link to={killSheetLink(setup)} className="btn btn-secondary text-xs block">
            Kill sheet →
          </Link>
        ) : (
          <span className="text-xs text-text-muted">—</span>
        )}
      </td>
    </tr>
  );
}

function ResultsTable({ title, setups, emptyMessage, onSelect }: {
  title: string;
  setups: WeeklySetup[];
  emptyMessage: string;
  onSelect?: (ticker: string) => void;
}) {
  return (
    <section className="mb-6">
      <h3 className="text-base font-semibold text-text-primary mb-2">{title}</h3>
      {setups.length === 0 ? (
        <div className="panel p-3 text-sm text-text-secondary">{emptyMessage}</div>
      ) : (
        <div className="panel">
          <table className="w-full text-sm">
            <thead className="text-xs text-text-secondary border-b border-bg-border">
              <tr>
                <th className="text-left px-3 py-2">Ticker</th>
                <th className="text-left px-3 py-2">Confluence</th>
                <th className="text-left px-3 py-2">Indicators</th>
                <th className="text-left px-3 py-2">SQN(100)</th>
                <th className="text-right px-3 py-2">Score</th>
                <th className="text-right px-3 py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {setups.map((s) => (
                <SetupRow key={s.ticker} setup={s} onSelect={onSelect} />
              ))}
            </tbody>
          </table>
        </div>
      )}
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
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Weekly Trend Scan</h2>
        <p className="text-xs text-text-secondary mt-1">
          Position trading on the weekly TF. One chart, one decision, hold for
          weeks to months. Per{" "}
          <code>~/.claude/skills/user/weekly-trend-trader/SKILL.md</code>.
        </p>
      </div>

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

          <ResultsTable
            title="Top setups (ranked)"
            setups={data.top_setups}
            emptyMessage="No actionable setups — chop / compression / no Stoch trigger across the watchlist."
            onSelect={setChartTicker}
          />

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

          <ResultsTable
            title="All scanned"
            setups={data.setups}
            emptyMessage="No tickers scanned."
            onSelect={setChartTicker}
          />

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
