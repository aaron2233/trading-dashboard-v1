import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { Sparkline } from "../components/Sparkline";
import type { OpenPositionRequest, Position, PositionAlert } from "../api/types";

const ACCOUNTS = ["main", "lotto", "weekly"];

const SEVERITY_BADGE: Record<string, string> = {
  action: "badge-bear",
  warn: "badge-flag",
  info: "badge-info",
};

function fmtUsd(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-US", { style: "currency", currency: "USD",
    minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function emptyOpenForm(): OpenPositionRequest {
  return {
    ticker: "",
    direction: "long",
    instrument: "call",
    account: "main",
  };
}

export function PositionsView() {
  const [openPositions, setOpenPositions] = useState<Position[]>([]);
  const [closedPositions, setClosedPositions] = useState<Position[]>([]);
  const [alerts, setAlerts] = useState<PositionAlert[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<OpenPositionRequest>(emptyOpenForm());
  const [closingId, setClosingId] = useState<string | null>(null);
  const [closePnl, setClosePnl] = useState<string>("");
  const [closeNotes, setCloseNotes] = useState<string>("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [open, closed, alertList] = await Promise.all([
        api.positions("open"),
        api.positions("closed"),
        api.positionAlerts().catch(() => [] as PositionAlert[]),
      ]);
      setOpenPositions(open);
      setClosedPositions(closed);
      setAlerts(alertList);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  function update<K extends keyof OpenPositionRequest>(key: K, value: OpenPositionRequest[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  async function handleOpen(e: React.FormEvent) {
    e.preventDefault();
    if (!form.ticker) return;
    setError(null);
    try {
      await api.openPosition({ ...form, ticker: form.ticker.toUpperCase() });
      setForm(emptyOpenForm());
      setShowForm(false);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleClose(id: string) {
    setError(null);
    try {
      const pnl = closePnl === "" ? null : Number(closePnl);
      const notes = closeNotes === "" ? null : closeNotes;
      await api.closePosition(id, pnl, notes);
      setClosingId(null);
      setClosePnl("");
      setCloseNotes("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold">Positions</h2>
        <div className="flex gap-2">
          <button className="btn" onClick={() => void refresh()} disabled={loading}>
            {loading ? "…" : "↻ Refresh"}
          </button>
          <button className="btn btn-primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? "Cancel" : "Open new"}
          </button>
        </div>
      </div>

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {showForm && (
        <form onSubmit={handleOpen} className="panel mb-4">
          <div className="panel-header">Open new position</div>
          <div className="panel-body grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="label">Ticker *</label>
              <input className="input w-full" required
                value={form.ticker} onChange={(e) => update("ticker", e.target.value)} />
            </div>
            <div>
              <label className="label">Direction</label>
              <select className="input w-full" value={form.direction}
                onChange={(e) => update("direction", e.target.value as "long" | "short")}>
                <option value="long">long</option>
                <option value="short">short</option>
              </select>
            </div>
            <div>
              <label className="label">Account</label>
              <select className="input w-full" value={form.account}
                onChange={(e) => update("account", e.target.value)}>
                {ACCOUNTS.map((a) => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Instrument</label>
              <select className="input w-full" value={form.instrument}
                onChange={(e) => update("instrument", e.target.value as OpenPositionRequest["instrument"])}>
                <option value="call">call</option>
                <option value="put">put</option>
                <option value="shares">shares</option>
              </select>
            </div>
            {form.instrument !== "shares" && (
              <>
                <div>
                  <label className="label">Strike</label>
                  <input className="input w-full" type="number" step="0.01"
                    value={form.strike ?? ""}
                    onChange={(e) => update("strike", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
                <div>
                  <label className="label">Expiry (YYYY-MM-DD)</label>
                  <input className="input w-full" placeholder="2026-06-19"
                    value={form.expiry ?? ""}
                    onChange={(e) => update("expiry", e.target.value || null)} />
                </div>
                <div>
                  <label className="label">Premium ($/share)</label>
                  <input className="input w-full" type="number" step="0.01"
                    value={form.premium ?? ""}
                    onChange={(e) => update("premium", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
                <div>
                  <label className="label">Contracts</label>
                  <input className="input w-full" type="number"
                    value={form.contracts ?? ""}
                    onChange={(e) => update("contracts", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
              </>
            )}
            {form.instrument === "shares" && (
              <>
                <div>
                  <label className="label">Shares</label>
                  <input className="input w-full" type="number"
                    value={form.shares ?? ""}
                    onChange={(e) => update("shares", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
                <div>
                  <label className="label">Entry price</label>
                  <input className="input w-full" type="number" step="0.01"
                    value={form.entry_price ?? ""}
                    onChange={(e) => update("entry_price", e.target.value === "" ? null : Number(e.target.value))} />
                </div>
              </>
            )}
            <div>
              <label className="label">Target ($)</label>
              <input className="input w-full" type="number" step="0.01"
                value={form.target ?? ""}
                onChange={(e) => update("target", e.target.value === "" ? null : Number(e.target.value))} />
            </div>
            <div>
              <label className="label">Invalidation ($)</label>
              <input className="input w-full" type="number" step="0.01"
                value={form.invalidation ?? ""}
                onChange={(e) => update("invalidation", e.target.value === "" ? null : Number(e.target.value))} />
            </div>
            <div className="md:col-span-2">
              <label className="label">Notes</label>
              <input className="input w-full"
                value={form.notes ?? ""}
                onChange={(e) => update("notes", e.target.value || null)} />
            </div>
          </div>
          <div className="panel-body border-t border-bg-border flex justify-end gap-2">
            <button type="button" className="btn" onClick={() => setShowForm(false)}>Cancel</button>
            <button type="submit" className="btn btn-primary">Open</button>
          </div>
        </form>
      )}

      {alerts.length > 0 && (
        <div className="panel mb-4">
          <div className="panel-header">Alerts ({alerts.length})</div>
          <div className="panel-body space-y-2">
            {alerts.map((a, i) => (
              <div key={i} className="flex gap-3 text-sm items-center">
                <span className={`badge ${SEVERITY_BADGE[a.severity] ?? "badge-muted"}`}>
                  {a.severity.toUpperCase()}
                </span>
                <span className="font-semibold">{a.ticker}</span>
                <span className="muted text-text-muted">[{a.rule}]</span>
                <span className="text-text-secondary">{a.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="panel mb-4">
        <div className="panel-header">Open ({openPositions.length})</div>
        {openPositions.length === 0 ? (
          <div className="panel-body text-text-muted text-sm">No open positions.</div>
        ) : (
          <div className="panel-body overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-text-secondary text-xs uppercase tracking-wider">
                <tr><th className="text-left p-1">ID</th>
                  <th className="text-left p-1">Ticker</th>
                  <th className="text-left p-1">Trend</th>
                  <th className="text-left p-1">Acct</th>
                  <th className="text-left p-1">Inst</th>
                  <th className="text-right p-1">Strike</th>
                  <th className="text-left p-1">Expiry</th>
                  <th className="text-right p-1">Cost</th>
                  <th className="text-right p-1">Max Loss</th>
                  <th className="text-right p-1">Target</th>
                  <th className="text-right p-1">Invalid.</th>
                  <th className="text-right p-1"></th>
                </tr>
              </thead>
              <tbody>
                {openPositions.map((p) => (
                  <tr key={p.id} className="border-t border-bg-border align-top">
                    <td className="p-1 font-mono text-text-muted">{p.id}</td>
                    <td className="p-1 font-semibold">{p.ticker}</td>
                    <td className="p-1">
                      <Sparkline ticker={p.ticker} timeframe="1d" count={30} width={120} height={28} />
                    </td>
                    <td className="p-1">{p.account_key}</td>
                    <td className="p-1">{p.instrument}</td>
                    <td className="p-1 text-right">{p.strike !== null ? `$${p.strike}` : "—"}</td>
                    <td className="p-1">{p.expiry ?? "—"}</td>
                    <td className="p-1 text-right">{fmtUsd(p.total_cost_usd)}</td>
                    <td className="p-1 text-right">{fmtUsd(p.max_loss_usd)}</td>
                    <td className="p-1 text-right">{fmtUsd(p.target_price)}</td>
                    <td className="p-1 text-right">{fmtUsd(p.invalidation_price)}</td>
                    <td className="p-1 text-right">
                      {closingId === p.id ? (
                        <div className="flex flex-col gap-1 items-end">
                          <input className="input w-24" type="number" step="0.01" placeholder="P&L"
                            value={closePnl} onChange={(e) => setClosePnl(e.target.value)} />
                          <input className="input w-32" placeholder="notes"
                            value={closeNotes} onChange={(e) => setCloseNotes(e.target.value)} />
                          <div className="flex gap-1">
                            <button className="btn text-xs"
                              onClick={() => { setClosingId(null); setClosePnl(""); setCloseNotes(""); }}>
                              Cancel
                            </button>
                            <button className="btn btn-primary text-xs"
                              onClick={() => void handleClose(p.id)}>
                              Confirm
                            </button>
                          </div>
                        </div>
                      ) : (
                        <button className="btn text-xs"
                          onClick={() => { setClosingId(p.id); setClosePnl(""); setCloseNotes(""); }}>
                          Close
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="panel">
        <div className="panel-header">Closed ({closedPositions.length})</div>
        {closedPositions.length === 0 ? (
          <div className="panel-body text-text-muted text-sm">No closed positions yet.</div>
        ) : (
          <div className="panel-body overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-text-secondary text-xs uppercase tracking-wider">
                <tr><th className="text-left p-1">Closed</th>
                  <th className="text-left p-1">Ticker</th>
                  <th className="text-left p-1">Acct</th>
                  <th className="text-left p-1">Inst</th>
                  <th className="text-right p-1">P&L</th>
                  <th className="text-left p-1">Notes</th>
                </tr>
              </thead>
              <tbody>
                {closedPositions.map((p) => (
                  <tr key={p.id} className="border-t border-bg-border">
                    <td className="p-1 text-text-muted">{(p.closed_date ?? "").slice(0, 19).replace("T", " ")}</td>
                    <td className="p-1 font-semibold">{p.ticker}</td>
                    <td className="p-1">{p.account_key}</td>
                    <td className="p-1">{p.instrument}</td>
                    <td className={`p-1 text-right font-semibold ${
                      (p.pnl_usd ?? 0) > 0 ? "text-signal-bull" :
                      (p.pnl_usd ?? 0) < 0 ? "text-signal-bear" : "text-text-secondary"
                    }`}>{fmtUsd(p.pnl_usd)}</td>
                    <td className="p-1 text-text-secondary text-xs">{p.notes ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
