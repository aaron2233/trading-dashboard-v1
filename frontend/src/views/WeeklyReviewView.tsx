import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import type { DisciplineStatsDTO, WeeklyReviewDTO } from "../api/types";

function driftClass(trend: string | null | undefined): string {
  switch (trend) {
    case "improving":
      return "badge-bull";
    case "drifting":
      return "badge-bear";
    default:
      return "badge-info";
  }
}

function fmtPct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

export function WeeklyReviewView() {
  const [searchParams] = useSearchParams();
  // Optional ?week_of= param — HomeView CTA links here for an unreviewed
  // week. Backend resolves to the Sunday of the containing week.
  const weekOfParam = searchParams.get("week_of") ?? undefined;

  const [review, setReview] = useState<WeeklyReviewDTO | null>(null);
  const [stats, setStats] = useState<DisciplineStatsDTO | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lockdownDraft, setLockdownDraft] = useState("");
  const [savingLockdown, setSavingLockdown] = useState(false);
  const [recomputing, setRecomputing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [r, s] = await Promise.all([
        api.weeklyReview(weekOfParam),
        api.disciplineStats("all"),
      ]);
      setReview(r);
      setStats(s);
      setLockdownDraft(r.lockdown_behavior ?? "");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [weekOfParam]);

  useEffect(() => {
    void load();
  }, [load]);

  const recompute = useCallback(async () => {
    setRecomputing(true);
    setError(null);
    try {
      setReview(await api.weeklyReview(weekOfParam, true));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRecomputing(false);
    }
  }, []);

  const saveLockdown = useCallback(async () => {
    if (!review || !lockdownDraft.trim()) return;
    setSavingLockdown(true);
    setError(null);
    try {
      const updated = await api.setWeeklyLockdown(review.week_start, lockdownDraft.trim());
      setReview(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingLockdown(false);
    }
  }, [lockdownDraft, review]);

  return (
    <div className="max-w-4xl mx-auto px-4 py-6 space-y-4">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold">Weekly Review</h1>
          {review && (
            <div className="text-xs text-text-muted">
              Week of {review.week_start} → {review.week_end}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button type="button" className="btn" onClick={() => void recompute()} disabled={recomputing}>
            {recomputing ? "…" : "Recompute"}
          </button>
          <button type="button" className="btn" onClick={() => void load()} disabled={loading}>
            {loading ? "…" : "↻"}
          </button>
        </div>
      </header>

      {error && (
        <div className="panel p-3 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {review && (
        <section className="panel">
          <div className="panel-header">Aggregate metrics — this week</div>
          <div className="panel-body grid grid-cols-2 gap-4 text-sm">
            <Metric label="Trades scored" value={String(review.trades_scored)} />
            <Metric label="Average discipline score"
                    value={fmtPct(review.avg_discipline_score)} />
            <Metric label="100% adherence" value={String(review.full_adherence_count)} />
            <Metric label="Any violation" value={String(review.any_violation_count)} />
            <Metric
              label="Profitable violations (red)"
              value={String(review.profitable_violation_count)}
              emphasis={review.profitable_violation_count > 0 ? "bear" : "muted"}
            />
            <Metric label="P&amp;L this week" value={`$${review.pnl_usd.toLocaleString()}`} />
            <div className="col-span-2 flex items-center gap-2 text-text-secondary">
              <span>Drift trend (vs prior 4 weeks):</span>
              <span className={`badge ${driftClass(review.drift_trend)}`}>
                {review.drift_trend}
              </span>
              <span className="ml-4 text-text-muted">
                Most-violated rule: {review.most_violated_rule ?? "—"}
              </span>
            </div>
          </div>
        </section>
      )}

      {review && (
        <section className="panel">
          <div className="panel-header">Lockdown behavior — for next week</div>
          <div className="panel-body space-y-2">
            <textarea
              className="input w-full"
              rows={3}
              value={lockdownDraft}
              onChange={(e) => setLockdownDraft(e.target.value)}
              placeholder="One specific behavior to lock down next week"
            />
            <div className="flex justify-end">
              <button
                type="button"
                className="btn btn-primary"
                disabled={savingLockdown || !lockdownDraft.trim()}
                onClick={() => void saveLockdown()}
              >
                {savingLockdown ? "Saving…" : "Save lockdown"}
              </button>
            </div>
            {review.lockdown_behavior && (
              <div className="text-xs text-text-muted">
                Saved: <em>{review.lockdown_behavior}</em>
              </div>
            )}
          </div>
        </section>
      )}

      {stats && (
        <section className="panel">
          <div className="panel-header">All-time discipline stats</div>
          <div className="panel-body grid grid-cols-2 gap-4 text-sm">
            <Metric label="Trades scored" value={String(stats.trades_scored)} />
            <Metric label="Average score" value={fmtPct(stats.avg_discipline_score)} />
            <Metric label="100% adherence" value={String(stats.full_adherence_count)} />
            <Metric
              label="Profitable violations"
              value={String(stats.profitable_violation_count)}
              emphasis={stats.profitable_violation_count > 0 ? "bear" : "muted"}
            />
            {stats.most_violated_rule_text && (
              <div className="col-span-2 text-text-muted">
                Most violated: <span className="text-text-primary">{stats.most_violated_rule_text}</span>
              </div>
            )}
          </div>
        </section>
      )}

      <section className="panel">
        <div className="panel-header">Stage reminder</div>
        <div className="panel-body text-sm text-text-secondary">
          Stage 1 (account &lt; $100K): discipline score is the primary KPI. A
          flat-P&amp;L week with 100% adherence is a winning week. A profitable
          week with sub-90% adherence is a warning — variance masks process
          drift.
        </div>
      </section>
    </div>
  );
}

function Metric({
  label, value, emphasis,
}: {
  label: string;
  value: string;
  emphasis?: "bear" | "muted";
}) {
  const valueClass = emphasis === "bear"
    ? "text-signal-bear font-semibold"
    : emphasis === "muted"
    ? "text-text-muted"
    : "text-text-primary font-semibold";
  return (
    <div>
      <div className="text-xs text-text-muted uppercase tracking-widest">{label}</div>
      <div className={`text-lg ${valueClass}`}>{value}</div>
    </div>
  );
}
