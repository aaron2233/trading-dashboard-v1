import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { TradingViewChart } from "../components/TradingViewChart";
import type {
  CryptoConfluence,
  CryptoInstrumentsResponse,
  CryptoSetup,
  CryptoTimeframeReadDTO,
} from "../api/types";


function fmtPrice(value: number | null): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1000) return `$${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
  if (value >= 1) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(6)}`;
}

function fmtPct(v: number | null): string {
  if (v === null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}${(v * 100).toFixed(2)}%`;
}

function fmtVolume(v: number | null): string {
  if (v === null) return "—";
  if (v >= 1_000_000) return `${(v / 1_000_000).toFixed(2)}M`;
  if (v >= 1_000) return `${(v / 1_000).toFixed(2)}K`;
  return v.toFixed(2);
}

function fmt(v: number | null, digits = 1): string {
  if (v === null || v === undefined) return "—";
  return v.toFixed(digits);
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

const CONFLUENCE_LABEL: Record<CryptoConfluence, string> = {
  high_conviction_long: "HIGH CONVICTION LONG",
  high_conviction_short: "HIGH CONVICTION SHORT",
  medium_conviction_long: "Medium conviction long",
  medium_conviction_short: "Medium conviction short",
  counter_weekly: "Counter-Weekly — half size",
  wait: "Wait — trigger pending",
  skip_chop: "Skip — Daily chop",
  skip_no_setup: "Skip — no setup",
};

function badgeClassForConfluence(c: CryptoConfluence): string {
  if (c.startsWith("high_conviction")) return "badge-bull";
  if (c.startsWith("medium_conviction")) return "badge-info";
  if (c === "counter_weekly") return "badge-flag";
  if (c === "wait") return "badge-info";
  return "badge-bear";
}

function changeClass(v: number | null): string {
  if (v === null) return "text-text-secondary";
  if (v > 0) return "text-signal-bull";
  if (v < 0) return "text-signal-bear";
  return "text-text-secondary";
}

function killSheetLink(setup: CryptoSetup): string {
  if (setup.direction === "none") return "/kill-sheet";
  const params = new URLSearchParams({
    ticker: setup.symbol,
    direction: setup.direction,
    intent: "SWING",
    trigger_tf: "2H",
    conviction: setup.confluence.startsWith("high_conviction") ? "high" : "medium",
  });
  return `/kill-sheet?${params.toString()}`;
}


function PairPicker({ onPick, instruments, common }: {
  onPick: (s: string) => void;
  instruments: CryptoInstrumentsResponse | null;
  common: string[];
}) {
  return (
    <div className="space-y-3">
      <div>
        <div className="label">Quick picks</div>
        <div className="flex flex-wrap gap-2">
          {common.map((sym) => (
            <button
              key={sym}
              type="button"
              className="btn text-xs"
              onClick={() => onPick(sym)}
            >
              {sym}
            </button>
          ))}
        </div>
      </div>
      {instruments && instruments.instruments.length > 0 && (
        <details className="text-xs">
          <summary className="cursor-pointer text-text-secondary">
            All supported pairs ({instruments.instruments.length})
          </summary>
          <div className="mt-2 max-h-40 overflow-y-auto flex flex-wrap gap-1">
            {instruments.instruments.map((i) => (
              <button
                key={i.instrument_name}
                type="button"
                className="btn text-[10px]"
                onClick={() => onPick(i.instrument_name)}
              >
                {i.instrument_name}
              </button>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}

function TickerHeader({ setup }: { setup: CryptoSetup }) {
  const t = setup.ticker;
  if (!t) {
    return (
      <div className="panel p-3 mb-4 border-signal-flag/40">
        <span className="text-sm text-signal-flag">
          ⚠ Live ticker unavailable. Indicator reads still ran on cached candlesticks.
        </span>
      </div>
    );
  }
  return (
    <div className="panel p-4 mb-4">
      <div className="flex items-baseline justify-between mb-2">
        <div>
          <div className="text-lg font-semibold font-mono">{setup.symbol}</div>
          <div className="text-xs text-text-secondary">
            scanned {new Date(setup.scan_time_utc).toLocaleString()}
          </div>
        </div>
        <div className="text-right">
          <div className="text-2xl font-mono">{fmtPrice(t.last_price)}</div>
          <div className={`text-sm ${changeClass(t.change_24h_pct)}`}>
            {fmtPct(t.change_24h_pct)} 24h
          </div>
        </div>
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs text-text-secondary">
        <div>
          <div className="label">Bid / Ask</div>
          <div className="font-mono text-text-primary">
            {fmtPrice(t.bid)} / {fmtPrice(t.ask)}
          </div>
        </div>
        <div>
          <div className="label">24h High / Low</div>
          <div className="font-mono text-text-primary">
            {fmtPrice(t.high_24h)} / {fmtPrice(t.low_24h)}
          </div>
        </div>
        <div>
          <div className="label">24h Volume</div>
          <div className="font-mono text-text-primary">{fmtVolume(t.volume_24h)}</div>
        </div>
        <div>
          <div className="label">Source</div>
          <div className="font-mono text-text-primary">Crypto.com</div>
        </div>
      </div>
    </div>
  );
}

function TimeframeRow({ read }: { read: CryptoTimeframeReadDTO }) {
  if (read.error) {
    return (
      <tr className="border-b border-bg-border/40">
        <td className="px-3 py-2 font-mono">{read.timeframe.toUpperCase()}</td>
        <td className="px-3 py-2 text-xs text-signal-bear" colSpan={5}>
          ⚠ {read.error}
        </td>
      </tr>
    );
  }
  return (
    <tr className="border-b border-bg-border/40">
      <td className="px-3 py-2 font-mono">{read.timeframe.toUpperCase()}</td>
      <td className="px-3 py-2">
        <span className={`badge ${badgeClassForStack(read.ma_stack_state)} text-xs`}>
          {read.ma_stack_state ?? "—"}
        </span>
      </td>
      <td className="px-3 py-2 text-xs font-mono">
        {fmt(read.stoch_k)}/{fmt(read.stoch_d)}
      </td>
      <td className="px-3 py-2 text-xs">{read.stoch_signal ?? "—"}</td>
      <td className="px-3 py-2">
        <span className={`badge ${badgeClassForRegime(read.sqn_regime)} text-xs`}>
          {read.sqn_regime ?? "—"}
        </span>
      </td>
      <td className="px-3 py-2 text-right text-xs text-text-secondary">
        {fmtPrice(read.close)}
      </td>
    </tr>
  );
}

function MultiTFGrid({ setup }: { setup: CryptoSetup }) {
  const order = ["1wk", "1d", "4h", "2h"];
  return (
    <div className="panel mb-4">
      <div className="panel-header">Multi-timeframe read</div>
      <table className="w-full text-sm">
        <thead className="text-xs text-text-secondary border-b border-bg-border">
          <tr>
            <th className="text-left px-3 py-2">TF</th>
            <th className="text-left px-3 py-2">MA stack</th>
            <th className="text-left px-3 py-2">Stoch K/D</th>
            <th className="text-left px-3 py-2">Stoch signal</th>
            <th className="text-left px-3 py-2">SQN(100)</th>
            <th className="text-right px-3 py-2">Close</th>
          </tr>
        </thead>
        <tbody>
          {order
            .filter((tf) => setup.reads[tf])
            .map((tf) => (
              <TimeframeRow key={tf} read={setup.reads[tf]} />
            ))}
        </tbody>
      </table>
    </div>
  );
}

function ConfluencePanel({ setup }: { setup: CryptoSetup }) {
  return (
    <div className="panel p-4 mb-4">
      <div className="flex items-baseline justify-between mb-2">
        <span className={`badge ${badgeClassForConfluence(setup.confluence)} text-sm`}>
          {CONFLUENCE_LABEL[setup.confluence]}
        </span>
        {setup.direction !== "none" && (
          <Link to={killSheetLink(setup)} className="btn btn-secondary text-xs">
            Pre-write kill sheet →
          </Link>
        )}
      </div>
      <p className="text-sm text-text-primary mb-2">{setup.why_now}</p>
      {setup.blockers.length > 0 && (
        <ul className="text-xs text-signal-flag space-y-0.5 mb-1">
          {setup.blockers.map((b, i) => (
            <li key={i}>⚠ {b}</li>
          ))}
        </ul>
      )}
      {setup.notes.length > 0 && (
        <ul className="text-xs text-text-secondary space-y-0.5">
          {setup.notes.map((n, i) => (
            <li key={i}>· {n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}


export function CryptoView() {
  const [symbol, setSymbol] = useState("BTC_USDT");
  const [pendingSymbol, setPendingSymbol] = useState("BTC_USDT");
  const [instruments, setInstruments] = useState<CryptoInstrumentsResponse | null>(null);
  const [setup, setSetup] = useState<CryptoSetup | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load instruments once on mount
  useEffect(() => {
    void api.cryptoInstruments().then(setInstruments).catch(() => setInstruments(null));
  }, []);

  const runScan = useCallback(async (sym: string) => {
    if (!sym || !sym.includes("_")) {
      setError("Crypto symbols use underscore form, e.g. BTC_USDT");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await api.cryptoScan(sym);
      setSetup(next);
      setSymbol(sym);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // Initial scan
  useEffect(() => {
    void runScan(symbol);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="mb-4">
        <h2 className="text-lg font-semibold">Crypto Analysis</h2>
        <p className="text-xs text-text-secondary mt-1">
          Multi-TF MA Ribbon + Stoch + SQN on Crypto.com candlesticks. Live
          ticker from public REST. Order book + execution data live with the
          brokerage UI — same anti-stale discipline as options input.
        </p>
      </div>

      <div className="panel p-4 mb-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void runScan(pendingSymbol.trim().toUpperCase());
          }}
          className="flex gap-2 mb-4"
        >
          <input
            className="input flex-1 font-mono"
            placeholder="BTC_USDT"
            value={pendingSymbol}
            onChange={(e) => setPendingSymbol(e.target.value.toUpperCase())}
          />
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Scanning…" : "Scan"}
          </button>
        </form>
        <PairPicker
          onPick={(s) => {
            setPendingSymbol(s);
            void runScan(s);
          }}
          instruments={instruments}
          common={instruments?.common ?? ["BTC_USDT", "ETH_USDT", "SOL_USDT"]}
        />
      </div>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {setup && (
        <>
          <TickerHeader setup={setup} />
          <ConfluencePanel setup={setup} />
          <div className="mb-4">
            <TradingViewChart
              ticker={setup.symbol}
              timeframe="4h"
              height={420}
              collapsedByDefault
            />
          </div>
          <MultiTFGrid setup={setup} />
        </>
      )}
    </div>
  );
}
