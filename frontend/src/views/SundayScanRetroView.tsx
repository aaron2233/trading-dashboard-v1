import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../api/client";
import type {
  FocusOutcome,
  FocusOutcomeAggregate,
  SundayScanResponse,
} from "../api/types";
import {
  AssetCard,
  SetupRow,
  badgeClassForRecommendation,
  formatScanTime,
} from "./SundayScanView";

const AGGREGATE_LABEL: Record<FocusOutcomeAggregate, string> = {
  skipped: "Skipped — recommended but not taken",
  no_recommendation: "Cash week — no recommendation",
  open: "Followed — position still open",
  closed_winner: "Followed — closed for a win",
  closed_loser: "Followed — closed at a loss",
  mixed: "Followed — partially open, partially closed",
};

function aggregateClass(agg: FocusOutcomeAggregate): string {
  if (agg === "closed_winner") return "badge-bull";
  if (agg === "closed_loser") return "badge-bear";
  if (agg === "skipped") return "badge-flag";
  if (agg === "open" || agg === "mixed") return "badge-info";
  return "badge-muted";
}

function fmtMoney(value: number): string {
  const sign = value >= 0 ? "+" : "−";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

export function SundayScanRetroView() {
  const { date = "" } = useParams<{ date: string }>();
  const [data, setData] = useState<SundayScanResponse | null>(null);
  const [outcome, setOutcome] = useState<FocusOutcome | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([
      api.focusSundayScanByDate(date),
      api.focusOutcome(date).catch(() => null), // outcome is best-effort
    ])
      .then(([scan, out]) => {
        if (cancelled) return;
        setData(scan);
        setOutcome(out);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : String(err));
          setData(null);
          setOutcome(null);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [date]);

  return (
    <div className="max-w-5xl mx-auto px-4 py-6 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div>
          <Link to="/focus" className="text-text-secondary text-sm hover:text-text-primary">
            ← Back to current scan
          </Link>
          <h2 className="text-lg font-semibold mt-1">Saved scan — {date}</h2>
        </div>
      </div>

      {loading && (
        <div className="panel p-4 text-text-muted text-sm">Loading…</div>
      )}

      {error && (
        <div className="panel p-3 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {data && !loading && (
        <>
          <div className="panel p-4">
            <div className="flex items-center gap-3 flex-wrap">
              <span className={`badge ${badgeClassForRecommendation(data.recommendation)} text-sm`}>
                {data.recommendation.toUpperCase()}
              </span>
              <span className="text-text-primary">{data.headline}</span>
            </div>
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

          {outcome && outcome.aggregate_status !== "no_recommendation" && (
            <div className="panel">
              <div className="panel-header flex items-center justify-between">
                <span>Outcome</span>
                <span className={`badge ${aggregateClass(outcome.aggregate_status)} text-xs`}>
                  {AGGREGATE_LABEL[outcome.aggregate_status]}
                </span>
              </div>
              <div className="panel-body space-y-3">
                <div className="flex items-center justify-between gap-3 flex-wrap text-sm">
                  <div className="text-text-secondary">
                    Recommended:{" "}
                    {outcome.top_setup ? (
                      <span className="text-text-primary font-semibold">
                        {outcome.top_setup.asset} {outcome.top_setup.direction}
                        <span className="text-text-muted ml-2">
                          (score {outcome.top_setup.score})
                        </span>
                      </span>
                    ) : (
                      <span className="text-text-muted">none</span>
                    )}
                  </div>
                  <div className="text-text-secondary text-xs">
                    Window {outcome.window_days}d ·{" "}
                    {outcome.matched.length} matched ·{" "}
                    {outcome.open_count} open · {outcome.closed_count} closed
                  </div>
                </div>

                {outcome.closed_count > 0 && (
                  <div className="text-sm">
                    Realized P&amp;L:{" "}
                    <span
                      className={`font-mono font-semibold ${
                        outcome.realized_pnl_usd >= 0
                          ? "text-signal-bull"
                          : "text-signal-bear"
                      }`}
                    >
                      {fmtMoney(outcome.realized_pnl_usd)}
                    </span>
                  </div>
                )}

                {outcome.matched.length > 0 ? (
                  <div className="space-y-1 text-xs">
                    {outcome.matched.map((m) => (
                      <div
                        key={m.id}
                        className="flex items-center justify-between gap-3 flex-wrap"
                      >
                        <span className="text-text-secondary">
                          {m.entry_date.slice(0, 10)} ·{" "}
                          <span className="text-text-primary">
                            {m.ticker} {m.direction} {m.instrument}
                          </span>
                          {m.strike !== null && m.expiry && (
                            <span className="text-text-muted">
                              {" "}
                              ${m.strike} {m.expiry}
                            </span>
                          )}
                        </span>
                        <span className="font-mono">
                          {m.status === "open" ? (
                            <span className="text-text-muted">open · risk ${m.max_loss_usd.toFixed(0)}</span>
                          ) : m.pnl_usd !== null ? (
                            <span className={m.pnl_usd >= 0 ? "text-signal-bull" : "text-signal-bear"}>
                              {fmtMoney(m.pnl_usd)}
                            </span>
                          ) : (
                            <span className="text-text-muted">closed</span>
                          )}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : outcome.top_setup ? (
                  <div className="text-text-muted text-xs">
                    No positions opened in the {outcome.window_days}-day window
                    matching {outcome.top_setup.asset} {outcome.top_setup.direction}.
                  </div>
                ) : null}
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
                No setups recorded for this scan.
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
        </>
      )}
    </div>
  );
}
