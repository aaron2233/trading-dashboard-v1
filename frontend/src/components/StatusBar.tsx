import { useEffect, useState } from "react";
import { useDashboardState } from "../state/DashboardStateContext";

/**
 * Bloomberg-style fixed footer status bar. Shows backend link state, live
 * clock, build identifier. Brutalist by design — monospace, uppercase,
 * hairline indicators. Stage + balance live in the nav StageBanner only:
 * one balance display, one source of truth.
 */
function nowIso(): string {
  // YYYY-MM-DD HH:MM:SS in local time. Compact, no timezone abbrev.
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

export function StatusBar() {
  const [clock, setClock] = useState(nowIso());
  const { state, error } = useDashboardState();

  useEffect(() => {
    const id = setInterval(() => setClock(nowIso()), 1000);
    return () => clearInterval(id);
  }, []);

  const link = error
    ? { cls: "status-cell--bad", label: "OFFLINE" }
    : state
    ? { cls: "status-cell--ok", label: "LIVE" }
    : { cls: "status-cell--warn", label: "SYNC" };

  return (
    <div className="status-bar" role="contentinfo">
      <span className={`status-cell ${link.cls}`}>
        <span className="status-dot" />
        <span>{link.label}</span>
      </span>
      <span className="status-cell">
        <span>UTC_LOCAL</span>
        <strong>{clock}</strong>
      </span>
      <span className="status-cell ml-auto">
        <span>BUILD</span>
        <strong>0.1.0</strong>
      </span>
    </div>
  );
}
