import { useEffect, useState } from "react";
import { useDashboardState } from "../state/DashboardStateContext";

/**
 * Bloomberg-style fixed footer status bar. Shows live clock, dashboard
 * stage, account balance read, build identifier. Brutalist by design —
 * monospace, uppercase, hairline indicators.
 */
function nowIso(): string {
  // YYYY-MM-DD HH:MM:SS in local time. Compact, no timezone abbrev.
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(
    d.getHours(),
  )}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function fmtUsd(n: number): string {
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function StatusBar() {
  const [clock, setClock] = useState(nowIso());
  const { state, error } = useDashboardState();

  useEffect(() => {
    const id = setInterval(() => setClock(nowIso()), 1000);
    return () => clearInterval(id);
  }, []);

  const stage = state?.stage === "stage_2" ? "STAGE_2" : "STAGE_1";
  const balance = state ? fmtUsd(state.account_balance_usd) : "—";
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
      <span className="status-cell">
        <span>STAGE</span>
        <strong>{stage}</strong>
      </span>
      <span className="status-cell">
        <span>BAL</span>
        <strong>{balance}</strong>
      </span>
      <span className="status-cell ml-auto">
        <span>BUILD</span>
        <strong>0.1.0</strong>
      </span>
    </div>
  );
}
