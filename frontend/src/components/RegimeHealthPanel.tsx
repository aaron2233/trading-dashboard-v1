import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { RegimeHealthSnapshot } from "../api/types";
import { STATUS_BADGE_CLASS, STATUS_LABEL } from "../lib/glyphs";
import { TierSection } from "./regime/TierSection";

const STORAGE_KEY = "regimeHealthPanel.collapsed";

function formatFetchedAt(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return iso;
    return d.toLocaleString(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      month: "short",
      day: "numeric",
    });
  } catch {
    return iso;
  }
}

interface RegimeHealthPanelProps {
  /** When true, panel renders without the collapsed-by-default behavior —
   * used on the dedicated /regime-health route. */
  alwaysExpanded?: boolean;
}

export function RegimeHealthPanel({ alwaysExpanded = false }: RegimeHealthPanelProps) {
  const [snapshot, setSnapshot] = useState<RegimeHealthSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Collapsed by default on Home — the header row (overall badge + stale
  // capex + drivers) is the summary; the four tier grids live one click away
  // here or fully expanded on /regime-health. Expanding persists.
  const [collapsed, setCollapsed] = useState(() => {
    if (alwaysExpanded) return false;
    if (typeof window === "undefined") return true;
    return window.localStorage.getItem(STORAGE_KEY) !== "0";
  });

  const fetchSnapshot = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.regimeHealth();
      setSnapshot(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    setError(null);
    try {
      const data = await api.regimeHealthRefresh();
      setSnapshot(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void fetchSnapshot();
  }, [fetchSnapshot]);

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, next ? "1" : "0");
    }
  };

  const overallStatus = snapshot?.overall_status ?? "unknown";
  const overallBadge = STATUS_BADGE_CLASS[overallStatus] ?? "badge-muted";
  const overallLabel = STATUS_LABEL[overallStatus] ?? "—";

  return (
    <section className="panel mb-4">
      <header className="panel-header flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="font-bold uppercase tracking-widest text-xs">
            Regime Health
          </span>
          <span className={`badge ${overallBadge} text-xs`}>{overallLabel}</span>
          {snapshot?.pending_capex_updates && snapshot.pending_capex_updates.length > 0 && (
            <span
              className="badge badge-flag text-xs"
              title={
                "Capex prints with stale direction values:\n" +
                snapshot.pending_capex_updates
                  .map((p) => `${p.ticker} — ${p.print_date}`)
                  .join("\n")
              }
            >
              ⚠ {snapshot.pending_capex_updates.length} stale
            </span>
          )}
          {snapshot?.overall_drivers && snapshot.overall_drivers.length > 0 && (
            <span className="text-[11px] text-text-secondary">
              · driven by{" "}
              <span className="text-text-primary font-mono">
                {snapshot.overall_drivers.slice(0, 3).join(", ")}
                {snapshot.overall_drivers.length > 3 ? "…" : ""}
              </span>
            </span>
          )}
          {snapshot && (
            <span className="text-[10px] text-text-muted font-mono">
              {formatFetchedAt(snapshot.fetched_at)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn btn-secondary text-xs"
            onClick={() => void refresh()}
            disabled={refreshing}
          >
            {refreshing ? "Refreshing…" : "↻ Refresh"}
          </button>
          {!alwaysExpanded && (
            <Link to="/regime-health" className="btn btn-secondary text-xs">
              Detail →
            </Link>
          )}
          {!alwaysExpanded && (
            <button
              type="button"
              className="btn btn-secondary text-xs"
              onClick={toggleCollapsed}
              aria-expanded={!collapsed}
            >
              {collapsed ? "Expand ▾" : "Collapse ▴"}
            </button>
          )}
        </div>
      </header>
      {!collapsed && (
        <div className="panel-body">
          {error && (
            <div className="text-sm text-signal-bear mb-3">
              Error fetching regime health: {error}
            </div>
          )}
          {loading && !snapshot && (
            <div className="text-sm text-text-secondary">Loading…</div>
          )}
          {snapshot && (
            <>
              {snapshot.tiers.map((bundle) => (
                <TierSection key={bundle.tier} bundle={bundle} />
              ))}
              <div className="text-[10px] text-text-muted mt-3 font-mono">
                snapshot date: {snapshot.snapshot_date}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
