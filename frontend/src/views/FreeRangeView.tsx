import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { Sparkline } from "../components/Sparkline";
import { VerdictBadge } from "../components/Verdict";
import { fromFreeRangeCandidate } from "../lib/verdict";
import type {
  CandidateSnapshot,
  FreeRangeScanResponse,
} from "../api/types";

function fmtPrice(value: number | null): string {
  if (value === null || value === undefined) return "—";
  return `$${value.toFixed(2)}`;
}

function badgeClassForRegime(regime: string | null): string {
  if (regime === "strong_bull" || regime === "bull") return "badge-bull";
  if (regime === "strong_bear" || regime === "bear") return "badge-bear";
  return "badge-info";
}

function badgeClassForTier(tier: string): string {
  if (tier === "1+2") return "badge-info";
  if (tier === "1") return "badge-info";
  if (tier === "2") return "badge-flag";
  return "badge-muted";
}

function combinedSqnRegime(s100: string | null, s20: string | null): string {
  // Show the "worse" of the two if they differ — otherwise just one.
  if (!s100 && !s20) return "—";
  if (!s20 || s100 === s20) return s100 ?? "—";
  if (!s100) return s20;
  return `${s100} / ${s20} 20d`;
}

function killSheetLink(s: CandidateSnapshot): string {
  const params = new URLSearchParams({
    ticker: s.ticker,
    direction: s.direction,
    intent: s.tier === "2" ? "SCALP" : "SWING",
    conviction: "medium",
  });
  return `/kill-sheet?${params.toString()}`;
}

function CandidateCard({ snap }: { snap: CandidateSnapshot }) {
  const verdict = fromFreeRangeCandidate(snap.tier, snap.direction);
  return (
    <div className="panel p-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-text-primary text-base">
            {snap.ticker}
          </span>
          {snap.is_etf && (
            <span className="badge badge-muted text-xs">ETF</span>
          )}
          <VerdictBadge verdict={verdict} />
          <span className={`badge ${badgeClassForTier(snap.tier)} text-xs`}>
            Tier {snap.tier}
          </span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <Sparkline ticker={snap.ticker} timeframe="1d" count={30} width={120} height={32} />
          <span className="text-text-secondary">{fmtPrice(snap.current_price)}</span>
          <span className="text-text-secondary">score {snap.score}</span>
        </div>
      </div>

      <p className="text-sm text-text-primary mb-2">{snap.why_now}</p>

      <div className="flex items-center gap-3 text-xs text-text-muted mb-2">
        <span>
          <span className="text-text-secondary">SQN</span>{" "}
          <span className={`badge ${badgeClassForRegime(snap.sqn_100_regime)} text-[10px]`}>
            {combinedSqnRegime(snap.sqn_100_regime, snap.sqn_20_regime)}
          </span>
        </span>
        <span>·</span>
        <span>
          stack <span className="font-mono">{snap.ma_stack ?? "—"}</span>
        </span>
        {snap.stoch_zone && (
          <>
            <span>·</span>
            <span>
              stoch <span className="font-mono">{snap.stoch_zone}</span>
            </span>
          </>
        )}
      </div>

      {snap.notes.length > 0 && (
        <ul className="text-xs text-text-secondary space-y-0.5 mb-2">
          {snap.notes.slice(0, 1).map((n, i) => (
            <li key={i}>· {n}</li>
          ))}
        </ul>
      )}

      <div className="flex justify-end">
        <Link to={killSheetLink(snap)} className="btn btn-primary text-xs">
          Pre-write kill sheet →
        </Link>
      </div>
    </div>
  );
}

function PhaseSection({
  title,
  subtitle,
  snaps,
  emptyMessage,
}: {
  title: string;
  subtitle: string;
  snaps: CandidateSnapshot[];
  emptyMessage: string;
}) {
  return (
    <section className="mb-6">
      <div className="mb-3">
        <h3 className="text-base font-semibold text-text-primary">{title}</h3>
        <p className="text-xs text-text-secondary">{subtitle}</p>
      </div>
      {snaps.length === 0 ? (
        <div className="panel p-3 text-sm text-text-secondary">{emptyMessage}</div>
      ) : (
        <div className="space-y-3">
          {snaps.map((s) => (
            <CandidateCard key={`${s.phase}-${s.ticker}`} snap={s} />
          ))}
        </div>
      )}
    </section>
  );
}

export function FreeRangeView() {
  const [userTickers, setUserTickers] = useState("");
  const [data, setData] = useState<FreeRangeScanResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function runScan() {
    setLoading(true);
    setError(null);
    try {
      const tickers = userTickers
        .split(/[,\s]+/)
        .map((t) => t.trim().toUpperCase())
        .filter(Boolean);
      const result = await api.freeRangeScan({ user_tickers: tickers });
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
        <h2 className="page-title">Free-Range Scan</h2>
      </div>
      <p className="page-subtitle">
        3-phase · baseline → user → free-range · cap 5 · per orchestrator rule 12
      </p>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void runScan();
        }}
        className="panel p-4 mb-6 space-y-3"
      >
        <div>
          <label className="label" htmlFor="user-tickers">
            User-submitted tickers (optional, comma or space separated)
          </label>
          <input
            id="user-tickers"
            className="input w-full"
            placeholder="AAPL NVDA TSLA"
            value={userTickers}
            onChange={(e) => setUserTickers(e.target.value)}
          />
          <p className="text-xs text-text-secondary mt-1">
            Bypass the $15-50 single-stock filter — analyzed against Tier 1/2 stack regardless of price.
          </p>
        </div>

        <div className="flex justify-end">
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Scanning…" : "Run scan"}
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
            Universe scanned: {data.universe_size} tickers · Free-range cap{" "}
            {data.free_range_cap} · Scanned at{" "}
            {new Date(data.scan_time_utc).toLocaleString()}
          </div>

          {data.notes.length > 0 && (
            <div className="panel p-3 mb-4 border-signal-flag/40">
              <ul className="text-sm text-signal-flag space-y-1">
                {data.notes.map((n, i) => (
                  <li key={i}>• {n}</li>
                ))}
              </ul>
            </div>
          )}

          <PhaseSection
            title="Phase 1 — Baseline"
            subtitle="QQQ + GLD always scanned per orchestrator default watchlist."
            snaps={data.baseline}
            emptyMessage="No baseline candidates passed the indicator floor."
          />
          <PhaseSection
            title="Phase 2 — User-submitted"
            subtitle="Analyzed against Tier 1/2 stack regardless of price."
            snaps={data.user_submitted}
            emptyMessage="No user-submitted tickers (or none passed the scan)."
          />
          <PhaseSection
            title="Phase 3 — Free-range top"
            subtitle="Nasdaq 100, $15-50 single-stock band, ranked by indicator score."
            snaps={data.free_range}
            emptyMessage="No additional candidates passed the filters. Don't force a trade."
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
