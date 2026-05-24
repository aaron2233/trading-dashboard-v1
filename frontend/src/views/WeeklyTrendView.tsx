import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { TradingViewChart } from "../components/TradingViewChart";
import { TradeCard, type TradeCardBadge } from "../components/TradeCard";
import { VerdictBadge } from "../components/Verdict";
import { fromWeeklyConfluence } from "../lib/verdict";
import { ActionVerdictBanner } from "../components/ActionVerdictBanner";
import type {
  WeeklyScanResponse,
  WeeklyScanUniverseName,
  WeeklySetup,
} from "../api/types";

type ScanMode = "tickers" | "universe";

const UNIVERSE_LABELS: Record<WeeklyScanUniverseName, string> = {
  nasdaq_100: "NASDAQ 100",
  sp500_top_50: "S&P 500 Top 50",
  russell_2000_top_50: "Russell 2000 Top 50",
};


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
    contract_type: s.direction === "long" ? "call" : "put",
  });
  if (s.target_price != null) params.set("target", String(s.target_price));
  if (s.stop_price != null) params.set("invalidation", String(s.stop_price));
  if (s.suggested_strike != null) params.set("strike", String(s.suggested_strike));
  if (s.why_now) params.set("trigger_desc", s.why_now);
  const noteParts: string[] = [];
  if (s.ma_stack_state) noteParts.push(`Weekly stack: ${s.ma_stack_state}`);
  if (s.sqn_100_regime) noteParts.push(`SQN(100) ${s.sqn_100_regime}`);
  if (s.stoch_signal) noteParts.push(`Weekly stoch: ${s.stoch_signal}`);
  if (s.confluence) noteParts.push(`Confluence: ${s.confluence}`);
  if (s.suggested_dte) noteParts.push(`DTE: ${s.suggested_dte}`);
  if (s.suggested_delta) noteParts.push(`Delta: ${s.suggested_delta}`);
  if (noteParts.length) params.set("notes", noteParts.join(" · "));
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
        {setup.action_verdict && <ActionVerdictBanner verdict={setup.action_verdict} />}
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

function weeklyBadges(setup: WeeklySetup): TradeCardBadge[] {
  const out: TradeCardBadge[] = [];
  if (setup.sqn_100_regime) {
    const tone = setup.sqn_100_regime.includes("bull") ? "bull"
      : setup.sqn_100_regime.includes("bear") ? "bear" : "info";
    out.push({ label: `SQN(100) ${setup.sqn_100_regime.replace(/_/g, " ")}`, tone });
  }
  if (setup.ma_stack_state) {
    const tone = (setup.ma_stack_state === "full_bull" || setup.ma_stack_state === "bull_developing") ? "bull"
      : (setup.ma_stack_state === "full_bear" || setup.ma_stack_state === "bear_developing") ? "bear"
      : "muted";
    out.push({ label: `${setup.ma_stack_state.replace(/_/g, " ")}`, tone });
  }
  if (setup.is_penny_stock) out.push({ label: "PENNY · SHARES", tone: "flag" });
  if (setup.track_a && setup.track_a.state !== "none") {
    const tone = setup.track_a.state === "cross_up" ? "bull"
      : setup.track_a.state === "cross_down" ? "bear" : "info";
    out.push({ label: `19/39 ${setup.track_a.state.replace(/_/g, " ")}`, tone });
  }
  return out;
}

function weeklyDetails(setup: WeeklySetup): { label: string; value: string }[] {
  return [
    { label: "Stack", value: setup.ma_stack_state ?? "—" },
    { label: "Stoch K/D", value:
      `${setup.stoch_k?.toFixed(1) ?? "—"} / ${setup.stoch_d?.toFixed(1) ?? "—"}` },
    { label: "Stoch zone", value: setup.stoch_zone ?? "—" },
    { label: "Rank score", value: `${setup.rank_score}` },
  ];
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
        {setups.slice(0, 3).map((s) => (
          <div key={s.ticker}>
            <TradeCard
              setup={s}
              strategy_label={
                s.confluence?.startsWith("track_a")
                  ? "Weekly trend · Track A (19/39)"
                  : "Weekly trend · Track B (ribbon)"
              }
              kill_sheet_href={s.direction !== "none" ? killSheetLink(s) : null}
              direction={s.direction === "short" ? "short" : "long"}
              badges={weeklyBadges(s)}
              details={weeklyDetails(s)}
            />
            {onSelect && (
              <div className="mt-1 text-right">
                <button
                  type="button"
                  className="btn text-xs"
                  onClick={() => onSelect(s.ticker)}
                >
                  Chart
                </button>
              </div>
            )}
          </div>
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
      <h3 className="text-base font-semibold text-text-primary mb-1">All scanned</h3>
      <p className="text-xs text-text-secondary mb-2">
        <strong>Score</strong> is a composite setup-quality rank, scale roughly{" "}
        <span className="font-mono">−20 to 110</span>: confluence base (0–70:
        high-conviction 70, continuation 50, compression 20, no-setup 10, chop 0)
        + SQN(100) regime alignment (with-trend +30, counter-trend −20) + MA
        clarity (full stack +10, developing +5). Higher = stronger setup;
        70+ generally clears for kill sheet.
      </p>
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
  const [mode, setMode] = useState<ScanMode>("tickers");
  const [tickerInput, setTickerInput] = useState("AAPL NVDA META MSFT GOOGL TSLA");
  const [benchmark, setBenchmark] = useState("SPY");
  const [universes, setUniverses] = useState<WeeklyScanUniverseName[]>([
    "nasdaq_100", "sp500_top_50", "russell_2000_top_50",
  ]);
  const [data, setData] = useState<WeeklyScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chartTicker, setChartTicker] = useState<string | null>(null);

  function toggleUniverse(name: WeeklyScanUniverseName) {
    setUniverses((prev) =>
      prev.includes(name) ? prev.filter((n) => n !== name) : [...prev, name]
    );
  }

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      if (mode === "tickers") {
        const tickers = tickerInput
          .split(/[,\s]+/)
          .map((t) => t.trim().toUpperCase())
          .filter(Boolean);
        if (tickers.length === 0) {
          setError("Enter at least one ticker");
          return;
        }
        const result = await api.weeklyScan({ tickers, benchmark });
        setData(result);
      } else {
        if (universes.length === 0) {
          setError("Select at least one universe");
          return;
        }
        const result = await api.weeklyScan({ universe: universes, benchmark });
        setData(result);
      }
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
        <div className="flex gap-2 text-xs">
          <button
            type="button"
            className={`btn text-xs ${mode === "tickers" ? "btn-primary" : ""}`}
            onClick={() => setMode("tickers")}
          >
            Per-ticker
          </button>
          <button
            type="button"
            className={`btn text-xs ${mode === "universe" ? "btn-primary" : ""}`}
            onClick={() => setMode("universe")}
          >
            Universe sweep
          </button>
        </div>

        {mode === "tickers" ? (
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
        ) : (
          <div>
            <div className="label mb-1">Universes</div>
            <div className="flex flex-wrap gap-3 text-sm">
              {(Object.keys(UNIVERSE_LABELS) as WeeklyScanUniverseName[]).map((u) => (
                <label key={u} className="flex items-center gap-1.5">
                  <input
                    type="checkbox"
                    checked={universes.includes(u)}
                    onChange={() => toggleUniverse(u)}
                  />
                  <span>{UNIVERSE_LABELS[u]}</span>
                </label>
              ))}
            </div>
            <p className="text-xs text-text-muted mt-1">
              ~60–120s for all three (~200 names; Track A pulls 5y weekly bars per
              ticker).
            </p>
          </div>
        )}

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
