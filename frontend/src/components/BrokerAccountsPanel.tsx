import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { BrokerAccountsResponse } from "../api/types";
import { fmtUsd, fmtUsdWhole } from "../lib/format";

/** Broker-truth account breakout. One row per REAL broker account (from the
 * user's local broker_accounts config + balance snapshots), so the sleeves
 * (main/lotto/...) always trace back to an account the broker actually sees.
 * Renders nothing when no broker_accounts block is configured. */

function fmtAsOf(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function BrokerAccountsPanel() {
  const [data, setData] = useState<BrokerAccountsResponse | null>(null);

  useEffect(() => {
    api.brokerAccounts().then(setData).catch(() => {
      // Offline — the stage banner already surfaces backend state; hide.
      setData(null);
    });
  }, []);

  if (!data || data.accounts.length === 0) return null;

  return (
    <section className="panel mb-4">
      <header className="panel-header flex items-center justify-between flex-wrap gap-2">
        <span className="font-bold uppercase tracking-widest text-xs">
          Accounts · Broker Truth
        </span>
        <span className="text-[10px] text-text-muted font-mono">
          local snapshots · never leaves this machine
        </span>
      </header>
      <div className="panel-body">
        <table className="w-full text-sm">
          <tbody>
            {data.accounts.map((a) => (
              <tr key={a.key} className="border-b border-bg-border last:border-0">
                <td className="py-1.5 pr-3">
                  <span className="text-text-primary font-semibold">{a.label}</span>
                  <span className="ml-2 font-mono text-xs text-text-muted">
                    {a.account_masked}
                  </span>
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-text-primary whitespace-nowrap">
                  {fmtUsd(a.total_value_usd)}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs text-text-secondary whitespace-nowrap hidden sm:table-cell">
                  {a.cash_usd !== null ? `cash ${fmtUsdWhole(a.cash_usd)}` : ""}
                </td>
                <td className="py-1.5 pr-3 text-xs text-text-secondary hidden md:table-cell">
                  {a.sleeves.length > 0 ? `sleeves: ${a.sleeves.join(" + ")}` : ""}
                </td>
                <td className="py-1.5 text-right whitespace-nowrap">
                  {a.error ? (
                    <span className="badge badge-muted text-[10px]" title={a.error}>
                      ⚠ NO DATA
                    </span>
                  ) : a.stale ? (
                    <span
                      className="badge badge-flag text-[10px]"
                      title={`snapshot age: ${Math.round(a.age_hours ?? 0)}h`}
                    >
                      ⚠ STALE · {fmtAsOf(a.as_of)}
                    </span>
                  ) : (
                    <span className="text-[10px] text-text-muted font-mono">
                      as of {fmtAsOf(a.as_of)}
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.unmapped_sleeves.length > 0 && (
          <p className="text-[11px] text-text-muted mt-2 font-mono">
            off-broker sleeves (config base):{" "}
            {data.unmapped_sleeves
              .map((s) => `${s.name} ${fmtUsdWhole(s.balance_usd)}`)
              .join(" · ")}
          </p>
        )}
      </div>
    </section>
  );
}
