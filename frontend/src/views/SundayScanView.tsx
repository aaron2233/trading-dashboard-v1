import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { VerdictBadge } from "../components/Verdict";
import { fromFocusSetup, fromSundayScan } from "../lib/verdict";
import type {
  FocusRecentSummary,
  FocusSetup,
  ScanResult,
  SundayScanResponse,
  SundayScanSummary,
} from "../api/types";

export function formatScanTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function killSheetLinkFor(setup: FocusSetup): string {
  const params = new URLSearchParams({
    ticker: setup.asset,
    direction: setup.direction,
    intent: "SWING",
    conviction: "high",
    focus: "true",
  });
  return `/kill-sheet?${params.toString()}`;
}

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

export function badgeClassForRecommendation(rec: SundayScanResponse["recommendation"]): string {
  if (rec === "trade") return "badge-bull";
  if (rec === "watch") return "badge-flag";
  return "badge-muted";
}

export function AssetCard({ title, scan }: { title: string; scan: ScanResult | null }) {
  if (scan === null) {
    return (
      <div className="panel">
        <div className="panel-header">{title}</div>
        <div className="panel-body text-text-muted text-sm">scan failed</div>
      </div>
    );
  }
  return (
    <div className="panel">
      <div className="panel-header flex items-center justify-between">
        <span>{title} — {scan.bar_date ?? "—"}</span>
        <span className="text-text-secondary text-xs">close ${fmt(scan.close)}</span>
      </div>
      <div className="panel-body space-y-3 text-sm">
        <div>
          <div className="label">MA Ribbon</div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
            <span><span className="text-text-secondary">10</span> ${fmt(scan.ma_ribbon.ma_10)}</span>
            <span><span className="text-text-secondary">20</span> ${fmt(scan.ma_ribbon.ma_20)}</span>
            <span><span className="text-text-secondary">50</span> ${fmt(scan.ma_ribbon.ma_50)}</span>
            <span><span className="text-text-secondary">200</span> ${fmt(scan.ma_ribbon.ma_200)}</span>
          </div>
          <div className="pt-1">
            <span className={`badge ${badgeClassForStack(scan.ma_ribbon.stack_state)}`}>
              {scan.ma_ribbon.stack_state ?? "—"}
            </span>
          </div>
        </div>
        <div>
          <div className="label">Stochastic</div>
          <div className="flex items-center gap-2 text-xs">
            <span>%K {fmt(scan.stochastic.k, 1)}</span>
            <span>%D {fmt(scan.stochastic.d, 1)}</span>
            <span className={`badge ${badgeClassForZone(scan.stochastic.zone)}`}>
              {scan.stochastic.zone ?? "—"}
            </span>
            <span className="badge badge-muted">{scan.stochastic.signal ?? "—"}</span>
          </div>
        </div>
        <div>
          <div className="label">SQN</div>
          <div className="flex items-center gap-2 text-xs">
            <span>{fmt(scan.sqn.sqn_value, 2)}</span>
            <span className={`badge ${badgeClassForRegime(scan.sqn.regime)}`}>
              {scan.sqn.regime ?? "—"}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SetupRow({ setup, isTop }: { setup: FocusSetup; isTop: boolean }) {
  const compsText = Object.entries(setup.components)
    .map(([k, v]) => `${k} ${v >= 0 ? "+" : ""}${v}`)
    .join(" · ");

  return (
    <div
      className={`panel-body border-t border-bg-border first:border-t-0 ${
        isTop ? "bg-signal-info/5" : ""
      }`}
    >
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <span className="font-mono text-text-secondary text-xs w-8">
            #{isTop ? "1" : ""}
          </span>
          <span className="font-semibold">
            {setup.asset} {setup.direction}
          </span>
          <VerdictBadge verdict={fromFocusSetup(setup)} />
        </div>
        <div className="text-right">
          <div className="text-base font-mono">{setup.score}</div>
          <div className="text-xs text-text-muted">{compsText}</div>
        </div>
      </div>
      {setup.blockers.length > 0 && (
        <ul className="mt-2 ml-11 text-xs text-signal-bear list-disc list-inside">
          {setup.blockers.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function SundayScanView() {
  const [data, setData] = useState<SundayScanResponse | null>(null);
  const [recent, setRecent] = useState<SundayScanSummary[]>([]);
  const [summary, setSummary] = useState<FocusRecentSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetch = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [result, recentList, summaryResult] = await Promise.all([
        api.focusSundayScan(),
        api.focusRecentScans(5).catch(() => [] as SundayScanSummary[]),
        api.focusSummary(4).catch(() => null),
      ]);
      setData(result);
      setRecent(recentList);
      setSummary(summaryResult);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetch();
  }, [fetch]);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <div className="page-header-row">
        <h2 className="page-title">Sunday Focus</h2>
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void fetch()}
          disabled={loading}
        >
          {loading ? "Scanning…" : "Refresh"}
        </button>
      </div>
      <p className="page-subtitle">
        SPY regime · QQQ + GLD reads · 4 ranked setups
      </p>

      {error && (
        <div className="panel p-3 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {data && (
        <>
          <div className="panel p-4">
            <div className="flex items-center justify-between gap-3 flex-wrap mb-2">
              <VerdictBadge verdict={fromSundayScan(data)} size="lg" />
              {data.recommendation === "trade" && data.setups.length > 0 && (
                <Link
                  to={killSheetLinkFor(data.setups[0])}
                  className="btn btn-primary"
                >
                  Pre-write kill sheet →
                </Link>
              )}
            </div>
            <div className="text-sm text-text-primary">{data.headline}</div>
            <div className="mt-2 text-xs text-text-muted">
              Saved {formatScanTime(data.scan_time_utc)}
            </div>
            {Object.keys(data.errors).length > 0 && (
              <div className="mt-3 text-xs text-signal-bear">
                Errors:{" "}
                {Object.entries(data.errors).map(([t, e]) => (
                  <span key={t} className="mr-3">
                    {t}: {e}
                  </span>
                ))}
              </div>
            )}
          </div>

          {summary && summary.scans_count > 0 && (
            <div className="panel p-3">
              <div className="flex items-center justify-between gap-3 flex-wrap text-sm">
                <span className="text-text-secondary">
                  Last {summary.weeks} weeks
                </span>
                <div className="flex items-center gap-4 flex-wrap text-xs">
                  <span>
                    <span className="text-text-muted">scans</span>{" "}
                    <span className="font-mono">{summary.scans_count}</span>
                  </span>
                  <span>
                    <span className="text-text-muted">trade recs</span>{" "}
                    <span className="font-mono">{summary.trade_recs}</span>
                  </span>
                  <span>
                    <span className="text-text-muted">followed</span>{" "}
                    <span className="font-mono text-signal-bull">
                      {summary.followed_count}
                    </span>
                    {summary.skipped_count > 0 && (
                      <>
                        {" "}
                        <span className="text-text-muted">skipped</span>{" "}
                        <span className="font-mono text-signal-flag">
                          {summary.skipped_count}
                        </span>
                      </>
                    )}
                  </span>
                  {summary.open_count > 0 && (
                    <span>
                      <span className="text-text-muted">open</span>{" "}
                      <span className="font-mono text-signal-info">
                        {summary.open_count}
                      </span>
                    </span>
                  )}
                  <span>
                    <span className="text-text-muted">realized</span>{" "}
                    <span
                      className={`font-mono font-semibold ${
                        summary.realized_pnl_usd >= 0
                          ? "text-signal-bull"
                          : "text-signal-bear"
                      }`}
                    >
                      {summary.realized_pnl_usd >= 0 ? "+" : "−"}$
                      {Math.abs(summary.realized_pnl_usd).toFixed(2)}
                    </span>
                  </span>
                </div>
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <AssetCard title="SPY (regime)" scan={data.spy} />
            <AssetCard title="QQQ" scan={data.qqq} />
            <AssetCard title="GLD" scan={data.gld} />
          </div>

          <div className="panel">
            <div className="panel-header">Ranked setups</div>
            {data.setups.length === 0 ? (
              <div className="panel-body text-text-muted text-sm">
                No setups available — all scans failed.
              </div>
            ) : (
              data.setups.map((s, i) => (
                <SetupRow
                  key={`${s.asset}-${s.direction}`}
                  setup={s}
                  isTop={i === 0}
                />
              ))
            )}
          </div>

          {recent.length > 1 && (
            <div className="panel">
              <div className="panel-header">Recent scans</div>
              <div className="panel-body space-y-1 text-sm">
                {recent.map((s) => (
                  <Link
                    key={s.date}
                    to={`/focus/${s.date}`}
                    className="flex items-center justify-between gap-3 flex-wrap py-1.5 px-2 -mx-2 hover:bg-bg-elevated transition"
                  >
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs text-text-secondary w-24">
                        {s.date}
                      </span>
                      <span className={`badge ${badgeClassForRecommendation(s.recommendation)}`}>
                        {s.recommendation}
                      </span>
                      {s.top_setup && (
                        <span className="text-text-secondary">
                          {s.top_setup.asset} {s.top_setup.direction}{" "}
                          <span className="text-text-muted">
                            (score {s.top_setup.score})
                          </span>
                        </span>
                      )}
                    </div>
                    <span className="text-xs text-text-muted truncate max-w-md">
                      {s.headline}
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
