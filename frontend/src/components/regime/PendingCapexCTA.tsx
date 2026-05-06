import { useEffect, useState } from "react";
import { api } from "../../api/client";
import type { PendingCapexUpdate } from "../../api/types";

/** "⚠ N capex prints pending direction update" CTA on HomeView.
 * Mirrors the UnreviewedWeeksCTA visual pattern. Renders nothing when
 * there are zero pending updates. */
export function PendingCapexCTA() {
  const [pending, setPending] = useState<PendingCapexUpdate[]>([]);

  useEffect(() => {
    let cancelled = false;
    api.regimeHealth()
      .then((snap) => {
        if (!cancelled) setPending(snap.pending_capex_updates ?? []);
      })
      .catch(() => {
        if (!cancelled) setPending([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (pending.length === 0) return null;

  return (
    <section className="panel stripe-warn p-4 mb-6 border-2 border-dashed border-signal-flag">
      <div className="flex items-baseline justify-between mb-2 flex-wrap gap-2">
        <h2 className="text-sm font-bold text-signal-flag uppercase tracking-widest">
          ⚠ {pending.length} capex print{pending.length === 1 ? "" : "s"} pending direction update
        </h2>
        <span className="text-[10px] uppercase tracking-widest text-text-muted">
          Tier 4 paperwork · edit ~/.trading-dashboard/config.yaml
        </span>
      </div>
      <ul className="space-y-1.5">
        {pending.slice(0, 5).map((p) => (
          <li key={p.ticker} className="flex items-center justify-between text-sm">
            <span className="text-text-primary">
              <span className="font-mono font-semibold">{p.ticker}</span>
              <span className="text-text-secondary text-xs ml-2">
                printed <span className="font-mono">{p.print_date}</span>
              </span>
            </span>
            <span className="text-xs text-text-muted">
              flip <code>directions.{p.ticker}</code> to{" "}
              <code>raised</code>/<code>held</code>/<code>cut</code>
            </span>
          </li>
        ))}
      </ul>
      {pending.length > 5 && (
        <p className="text-xs text-text-secondary mt-2">
          Showing 5 of {pending.length} — full list in the regime-health panel.
        </p>
      )}
    </section>
  );
}
