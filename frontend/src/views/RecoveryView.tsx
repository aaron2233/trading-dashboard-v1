import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { RecoveryStatus, RecoveryMilestone } from "../api/types";


function fmtUsd(v: number, signed = false): string {
  const sign = signed && v >= 0 ? "+" : "";
  const abs = Math.abs(v);
  return `${v < 0 ? "−" : sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}`;
}

function fmtPct(v: number): string {
  return `${v.toFixed(1)}%`;
}


function MilestoneRow({ m, currentBalance }: { m: RecoveryMilestone; currentBalance: number }) {
  // Width of progress bar = clamp(current/threshold, 0, 1) * 100
  const pct = Math.min(100, Math.max(0, (currentBalance / m.threshold) * 100));
  return (
    <div className="mb-3">
      <div className="flex justify-between items-baseline text-sm mb-1">
        <span className={m.hit ? "text-signal-bull font-semibold" : "text-text-secondary"}>
          {m.hit ? "✓ " : ""}{m.label}
        </span>
        <span className="font-mono">
          {fmtUsd(m.threshold)} {m.hit ? "(hit)" : `(${fmtPct(pct)})`}
        </span>
      </div>
      <div className="h-2 bg-bg-border/40 rounded overflow-hidden">
        <div
          className={m.hit ? "h-full bg-signal-bull" : "h-full bg-accent"}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}


function RuleBadge({ ok, label, detail }: { ok: boolean; label: string; detail: string }) {
  return (
    <div className={`panel p-3 ${ok ? "border-signal-bull/30" : "border-signal-bear/40"}`}>
      <div className="flex items-baseline justify-between">
        <span className="font-semibold text-sm">{label}</span>
        <span className={`text-xs font-mono ${ok ? "text-signal-bull" : "text-signal-bear"}`}>
          {ok ? "OK" : "AT LIMIT"}
        </span>
      </div>
      <div className="text-xs text-text-secondary mt-1">{detail}</div>
    </div>
  );
}


export function RecoveryView() {
  const [status, setStatus] = useState<RecoveryStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Edit form state
  const [editing, setEditing] = useState(false);
  const [editBalance, setEditBalance] = useState("");
  const [depositAmount, setDepositAmount] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const s = await api.recoveryStatus();
      setStatus(s);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  async function saveBalance() {
    if (!editBalance) return;
    const v = Number(editBalance);
    if (Number.isNaN(v)) return;
    try {
      const s = await api.recoveryConfigUpdate({ current_balance: v });
      setStatus(s);
      setEditing(false);
      setEditBalance("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function logDeposit() {
    if (!depositAmount) return;
    const v = Number(depositAmount);
    if (Number.isNaN(v) || v <= 0) return;
    try {
      const s = await api.recoveryConfigUpdate({ delta_deposit_usd: v });
      setStatus(s);
      setDepositAmount("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (loading && !status) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-6">
        <div className="text-text-secondary text-sm">Loading recovery status…</div>
      </div>
    );
  }
  if (error) {
    return (
      <div className="max-w-5xl mx-auto px-4 py-6">
        <div className="panel p-3 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      </div>
    );
  }
  if (!status) return null;

  const r1Description = `Lotto: ${fmtUsd(status.r1_lotto_cap_usd)} · Main: ${fmtUsd(status.r1_main_cap_usd)} max-loss per trade`;
  const r2Description = `${status.r2_entries_today}/${status.r2_max_daily_entries} entries today · ${status.r2_remaining_today} remaining`;
  const r3Description = `Standing premium stop at 40% of entry (−60% premium loss)`;
  const r2AtLimit = status.r2_remaining_today === 0;

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Recovery Plan</h2>
      </div>
      <p className="page-subtitle">
        2026 plan committed {status.plan_committed_at} · Primary target: YTD breakeven · Plan doc:{" "}
        <span className="font-mono text-xs">~/Documents/Trading Recovery Plan 2026.md</span>
      </p>

      {/* Top-line balance */}
      <div className="panel p-4 mb-4">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Current balance</div>
            <div className="text-xl font-mono">{fmtUsd(status.current_balance)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">YTD realized P&L</div>
            <div className={`text-xl font-mono ${status.ytd_realized_pnl < 0 ? "text-signal-bear" : "text-signal-bull"}`}>
              {fmtUsd(status.ytd_realized_pnl, true)}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">Breakeven target</div>
            <div className="text-xl font-mono">{fmtUsd(status.year_breakeven_target)}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-wider text-text-muted">$ needed from today</div>
            <div className="text-xl font-mono">{fmtUsd(status.pnl_from_today_needed)}</div>
          </div>
        </div>
        {status.deposits_total > 0 && (
          <div className="text-xs text-text-muted mt-3">
            Capital adds logged this year: {fmtUsd(status.deposits_total)} · Year start: {fmtUsd(status.year_start_balance)}
          </div>
        )}
      </div>

      {/* Update controls */}
      <div className="panel p-3 mb-4">
        <div className="flex flex-wrap gap-4 items-end">
          <div className="flex-1 min-w-[200px]">
            <label className="label text-xs">Update current balance</label>
            {editing ? (
              <div className="flex gap-2">
                <input
                  className="input w-32 font-mono text-sm"
                  type="number"
                  step="0.01"
                  placeholder={String(status.current_balance)}
                  value={editBalance}
                  onChange={(e) => setEditBalance(e.target.value)}
                />
                <button type="button" className="btn btn-primary text-xs" onClick={() => void saveBalance()}>
                  Save
                </button>
                <button type="button" className="btn text-xs" onClick={() => { setEditing(false); setEditBalance(""); }}>
                  Cancel
                </button>
              </div>
            ) : (
              <button type="button" className="btn text-xs" onClick={() => setEditing(true)}>
                Edit balance
              </button>
            )}
          </div>
          <div className="flex-1 min-w-[200px]">
            <label className="label text-xs">Log a capital add (deposit)</label>
            <div className="flex gap-2">
              <input
                className="input w-32 font-mono text-sm"
                type="number"
                step="0.01"
                placeholder="amount"
                value={depositAmount}
                onChange={(e) => setDepositAmount(e.target.value)}
              />
              <button type="button" className="btn text-xs" onClick={() => void logDeposit()}>
                Log deposit
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Milestones */}
      <div className="panel p-4 mb-4">
        <div className="panel-header mb-3">Milestones (hit in order)</div>
        <div>
          {status.milestones.map((m) => (
            <MilestoneRow key={m.name} m={m} currentBalance={status.current_balance} />
          ))}
        </div>
        <div className="text-xs text-text-muted mt-2">
          Working toward: <span className="text-text-secondary">{status.milestone_status.next?.label ?? "all hit"}</span>
          {status.milestone_status.last_hit && (
            <> · Last hit: <span className="text-signal-bull">{status.milestone_status.last_hit.label}</span></>
          )}
        </div>
      </div>

      {/* Rules */}
      <div className="panel p-4 mb-4">
        <div className="panel-header mb-3">Three hard rules (no override)</div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <RuleBadge ok={true} label="R1 — Dollar size caps" detail={r1Description} />
          <RuleBadge ok={!r2AtLimit} label="R2 — Max 2 entries / day" detail={r2Description} />
          <RuleBadge ok={true} label="R3 — Standing −60% stop" detail={r3Description} />
        </div>
        <div className="text-xs text-text-muted mt-3">
          Per-trade R1/R3 evaluated at journal time. Recent positions' violations surface as warnings on the position's response and on the discipline scorecard. R2 is real-time — count above shows today.
        </div>
      </div>

      {/* Recent rule notes */}
      <div className="panel p-3 text-xs text-text-secondary">
        <strong className="text-text-primary">Reminder:</strong> the dashboard never blocks an entry once the broker has filled —
        positions are journaled regardless and the discipline scorecard handles retrospective scoring. R1/R2/R3 are surfaced
        loudly here and on every position to keep behavior visible.
      </div>
    </div>
  );
}
