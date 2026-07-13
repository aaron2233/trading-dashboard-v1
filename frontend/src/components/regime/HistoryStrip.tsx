import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { RegimeHealthSnapshot } from "../../api/types";
import { STATUS_BADGE_CLASS, STATUS_GLYPH } from "../../lib/glyphs";

/** Compact history strip on the /regime-health detail page — one row per day,
 * newest first, showing overall status + drivers. v2 will replace this with
 * per-indicator sparklines once the regime_health_series table is wired up. */
export function HistoryStrip({ days = 14 }: { days?: number }) {
  const [snapshots, setSnapshots] = useState<RegimeHealthSnapshot[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    api.regimeHealthHistory(days)
      .then((res) => setSnapshots(res.snapshots))
      .catch((err) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setLoading(false));
  }, [days]);

  if (loading && snapshots.length === 0) {
    return <div className="text-xs text-text-secondary">Loading history…</div>;
  }
  if (error) {
    return (
      <div className="text-xs text-signal-bear">
        History fetch failed: {error}
      </div>
    );
  }
  if (snapshots.length === 0) {
    return (
      <div className="text-xs text-text-muted">
        No prior snapshots yet — history accumulates one per day from this
        point forward.
      </div>
    );
  }
  return (
    <div className="space-y-1.5">
      {snapshots.map((s) => (
        <div
          key={s.snapshot_date}
          className="flex items-center gap-3 text-xs border-b border-bg-border pb-1.5 last:border-b-0"
        >
          <span className="font-mono text-text-secondary w-24">
            {s.snapshot_date}
          </span>
          <span
            className={`badge ${STATUS_BADGE_CLASS[s.overall_status] ?? "badge-muted"}`}
          >
            {STATUS_GLYPH[s.overall_status] ?? "⬜"}{" "}
            {s.overall_status.toUpperCase()}
          </span>
          {s.overall_drivers.length > 0 && (
            <span className="text-text-secondary truncate flex-1">
              · {s.overall_drivers.slice(0, 3).join(", ")}
              {s.overall_drivers.length > 3 ? "…" : ""}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}
