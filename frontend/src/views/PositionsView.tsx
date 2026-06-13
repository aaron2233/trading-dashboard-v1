import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { useDashboardState } from "../state/DashboardStateContext";
import { Sparkline } from "../components/Sparkline";
import type { OpenPositionRequest, Position, PositionAlert } from "../api/types";

const ACCOUNTS = ["main", "lotto", "weekly", "portfolio"];

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

function directionBadge(direction: string): string {
  if (direction === "long") return "badge-bull";
  if (direction === "short") return "badge-bear";
  return "badge-muted";
}

function emptyOpenForm(): OpenPositionRequest {
  return {
    ticker: "",
    direction: "long",
    instrument: "call",
    account: "main",
  };
}

type FormUpdate = <K extends keyof OpenPositionRequest>(
  key: K, value: OpenPositionRequest[K]
) => void;

type FormSetter = (
  next: OpenPositionRequest | ((prev: OpenPositionRequest) => OpenPositionRequest)
) => void;

interface AdvancedOptionsFieldsProps {
  form: OpenPositionRequest;
  update: FormUpdate;
  setForm: FormSetter;
}

/**
 * Advanced options-trade inputs: Greeks at entry (delta/gamma/theta/vega),
 * IV / IV-rank, premium-level stop and take-profit thresholds.
 *
 * The "Auto-fill from delta" button derives Target / Invalidation
 * (underlying-price levels) from the premium-level thresholds plus delta
 * and entry underlying price. First-order linearization — gamma drift
 * makes it less accurate as the trade moves.
 *
 * Conventions:
 *   - delta: signed. +0.5 for an ATM call, -0.5 for an ATM put.
 *   - iv: decimal (0.45 = 45%). The label says "IV %" so user enters 45.
 *     We store it as 0.45 — convert at input boundary.
 *   - iv_rank: 0-100 percentile.
 */
function AdvancedOptionsFields({ form, update, setForm }: AdvancedOptionsFieldsProps) {
  // Per-field "in-progress" string so partial inputs like "0." or "-" or ".5"
  // survive between keystrokes. React controlled-input + Number() round-trip
  // collapses "0." → 0 → "0", which blocks the user from typing decimals.
  // We render from the draft if present; commit the parsed number to form
  // state on every keystroke that parses cleanly so auto-fill still works.
  const [drafts, setDrafts] = useState<Record<string, string>>({});

  const num = (v: number | null | undefined): string =>
    v === null || v === undefined ? "" : String(v);

  /** Display value: prefer the in-progress draft if there is one. */
  const display = (key: keyof OpenPositionRequest): string => {
    if (key in drafts) return drafts[key];
    return num(form[key] as number | null | undefined);
  };

  function setNum<K extends keyof OpenPositionRequest>(key: K, raw: string) {
    setDrafts((prev) => ({ ...prev, [key as string]: raw }));
    if (raw === "") {
      update(key, null as OpenPositionRequest[K]);
      return;
    }
    const v = Number(raw);
    // Only commit when the string parses cleanly to a finite number.
    // Partial inputs like "0.", "-", "." are kept in the draft only.
    if (Number.isFinite(v)) {
      update(key, v as OpenPositionRequest[K]);
    }
  }

  /** Drop the draft entry on blur so the field re-formats from form state. */
  function clearDraft(key: keyof OpenPositionRequest) {
    setDrafts((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key as string];
      return next;
    });
  }

  function autoFillFromDelta() {
    const entry = form.entry_price;
    const prem = form.premium;
    const d = form.delta;
    if (!entry || !prem || !d || d === 0) return;
    const next: Partial<OpenPositionRequest> = {};
    if (form.premium_target !== null && form.premium_target !== undefined) {
      next.target = round2(entry + (form.premium_target - prem) / d);
    }
    if (form.premium_stop !== null && form.premium_stop !== undefined) {
      next.invalidation = round2(entry + (form.premium_stop - prem) / d);
    }
    if (Object.keys(next).length === 0) return;
    setForm((prev) => ({ ...prev, ...next }));
  }

  function applyDefault65Stop() {
    if (form.premium && form.premium > 0) {
      update("premium_stop", round2(form.premium * 0.35));
    }
  }

  const canAutofill =
    form.delta !== null && form.delta !== undefined && form.delta !== 0 &&
    form.entry_price !== null && form.entry_price !== undefined &&
    form.premium !== null && form.premium !== undefined &&
    (form.premium_stop !== null || form.premium_target !== null);

  return (
    <details className="panel-dashed p-3" open>
      <summary className="cursor-pointer text-[10px] uppercase tracking-widest text-text-muted mb-2">
        ▮ Advanced · Greeks · IV · premium thresholds
      </summary>

      <div className="mt-2 mb-3">
        <label className="label">Underlying price at entry ($)</label>
        <input
          className="input w-full md:w-1/3"
          type="number"
          step="0.01"
          value={display("entry_price")}
          onChange={(e) => setNum("entry_price", e.target.value)}
          onBlur={() => clearDraft("entry_price")}
          placeholder="needed for delta-based auto-fill"
        />
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <div>
          <label className="label">Δ Delta (signed)</label>
          <input
            className="input w-full"
            type="number"
            step="0.001"
            value={display("delta")}
            onChange={(e) => setNum("delta", e.target.value)}
            onBlur={() => clearDraft("delta")}
            placeholder="+0.5 / −0.5"
          />
        </div>
        <div>
          <label className="label">Γ Gamma</label>
          <input
            className="input w-full"
            type="number"
            step="0.0001"
            value={display("gamma")}
            onChange={(e) => setNum("gamma", e.target.value)}
            onBlur={() => clearDraft("gamma")}
          />
        </div>
        <div>
          <label className="label">Θ Theta ($/day)</label>
          <input
            className="input w-full"
            type="number"
            step="0.01"
            value={display("theta")}
            onChange={(e) => setNum("theta", e.target.value)}
            onBlur={() => clearDraft("theta")}
          />
        </div>
        <div>
          <label className="label">ν Vega</label>
          <input
            className="input w-full"
            type="number"
            step="0.01"
            value={display("vega")}
            onChange={(e) => setNum("vega", e.target.value)}
            onBlur={() => clearDraft("vega")}
          />
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-3">
        <div>
          <label className="label">IV (%)</label>
          <input
            className="input w-full"
            type="number"
            step="0.1"
            // Stored as decimal; display ×100. Convert on read/write.
            value={
              "iv" in drafts
                ? drafts["iv"]
                : (form.iv === null || form.iv === undefined ? "" : (form.iv * 100).toFixed(1))
            }
            onChange={(e) => {
              const raw = e.target.value;
              setDrafts((prev) => ({ ...prev, iv: raw }));
              if (raw === "") {
                update("iv", null);
                return;
              }
              const pct = Number(raw);
              if (Number.isFinite(pct)) {
                update("iv", pct / 100);
              }
            }}
            onBlur={() => clearDraft("iv")}
            placeholder="e.g. 45"
          />
        </div>
        <div>
          <label className="label">IV rank (0-100)</label>
          <input
            className="input w-full"
            type="number"
            step="1"
            value={display("iv_rank")}
            onChange={(e) => setNum("iv_rank", e.target.value)}
            onBlur={() => clearDraft("iv_rank")}
          />
        </div>
        <div>
          <label className="label flex items-center justify-between">
            <span>Premium stop ($/share)</span>
            {form.premium && form.premium > 0 && (
              <button
                type="button"
                onClick={applyDefault65Stop}
                className="text-[9px] text-signal-info hover:text-signal-flag"
                title="−65% midpoint of CLAUDE.md cut rule"
              >
                use −65%
              </button>
            )}
          </label>
          <input
            className="input w-full"
            type="number"
            step="0.01"
            value={display("premium_stop")}
            onChange={(e) => setNum("premium_stop", e.target.value)}
            onBlur={() => clearDraft("premium_stop")}
          />
        </div>
        <div>
          <label className="label">Premium target ($/share)</label>
          <input
            className="input w-full"
            type="number"
            step="0.01"
            value={display("premium_target")}
            onChange={(e) => setNum("premium_target", e.target.value)}
            onBlur={() => clearDraft("premium_target")}
          />
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 flex-wrap border-t border-bg-border pt-3">
        <div className="text-[10px] text-text-muted leading-relaxed max-w-md">
          ⚠ Auto-fill uses first-order delta · gamma drift &amp; theta decay
          will move the actual exit. Treat the derived Target / Invalidation
          as a starting point, not a prediction.
        </div>
        <button
          type="button"
          onClick={autoFillFromDelta}
          disabled={!canAutofill}
          className="btn"
          title={canAutofill
            ? "Compute Target/Invalidation from delta + entry price + premium thresholds"
            : "Need delta, underlying price, premium, and at least one premium threshold"}
        >
          ↳ Auto-fill Target / Invalidation
        </button>
      </div>
    </details>
  );
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/**
 * Premium-based stop / take-profit reference levels.
 *
 * The Target / Invalidation form fields are *underlying* prices, but the
 * discipline rule (-60/-70% max loss per CLAUDE.md) is on premium %.
 * Computing one from the other requires delta, which we don't capture.
 *
 * So this is purely advisory — it shows the premium values at common cut
 * and take-profit thresholds. The user eyeballs them while watching the
 * trade and translates to underlying levels with a chart or their own
 * delta read. No magic, no fake math.
 */
function PremiumLevelsHint({ premium }: { premium: number }) {
  const fmt = (v: number) => `$${v.toFixed(2)}`;
  const stops = [
    { label: "−50%", value: premium * 0.5 },
    { label: "−60%", value: premium * 0.4, emphasized: true },
    { label: "−70%", value: premium * 0.3, emphasized: true },
  ];
  const targets = [
    { label: "+100%", value: premium * 2 },
    { label: "+200%", value: premium * 3 },
    { label: "+300%", value: premium * 4 },
  ];
  return (
    <div className="panel-dashed p-3 mt-1 text-xs">
      <div className="text-[10px] uppercase tracking-widest text-text-muted mb-2">
        ▮ Premium reference · advisory only · convert to underlying with delta
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="marker-chip" style={{ background: "#ff3030", color: "#0a0a0a" }}>
              Stop
            </span>
            <span className="text-text-muted">premium-cut levels</span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono">
            {stops.map((s) => (
              <span
                key={s.label}
                className={s.emphasized ? "text-signal-bear font-bold" : "text-text-secondary"}
              >
                {s.label} → {fmt(s.value)}
              </span>
            ))}
          </div>
        </div>
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="marker-chip" style={{ background: "#00ff66", color: "#0a0a0a" }}>
              TP
            </span>
            <span className="text-text-muted">take-profit levels</span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono">
            {targets.map((t) => (
              <span key={t.label} className="text-signal-bull">
                {t.label} → {fmt(t.value)}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

interface PositionRowFragmentProps {
  position: Position;
  expanded: boolean;
  onToggle: () => void;
  closingId: string | null;
  setClosingId: (id: string | null) => void;
  closePnl: string;
  setClosePnl: (s: string) => void;
  closeNotes: string;
  setCloseNotes: (s: string) => void;
  closeContracts: string;
  setCloseContracts: (s: string) => void;
  onConfirmClose: () => void;
}

function PositionRowFragment({
  position: p,
  expanded,
  onToggle,
  closingId,
  setClosingId,
  closePnl,
  setClosePnl,
  closeNotes,
  setCloseNotes,
  closeContracts,
  setCloseContracts,
  onConfirmClose,
}: PositionRowFragmentProps) {
  const isClosing = closingId === p.id;
  const isOptions = p.instrument === "call" || p.instrument === "put";
  const remainingContracts = p.contracts ?? 0;
  return (
    <>
      <tr className="border-b border-bg-border/40">
        <td className="px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="font-semibold">{p.ticker}</span>
            <span className={`badge ${directionBadge(p.direction)} text-[10px]`}>
              {p.direction.toUpperCase()}
            </span>
          </div>
        </td>
        <td className="px-3 py-2">
          <Sparkline ticker={p.ticker} timeframe="1d" count={30} width={100} height={26} />
        </td>
        <td className="px-3 py-2 text-xs text-text-secondary">
          {p.account_key} · {p.instrument}
        </td>
        <td className="px-3 py-2 text-right font-mono text-xs">
          {fmtUsd(p.total_cost_usd)}
        </td>
        <td className="px-3 py-2 text-right font-mono text-xs">
          {fmtUsd(p.target_price)}
        </td>
        <td className="px-3 py-2 text-right">
          <button type="button" className="btn text-xs" onClick={onToggle}>
            {expanded ? "Collapse" : "Detail"}
          </button>
        </td>
      </tr>
      {expanded && (
        <tr className="bg-bg-elevated/30">
          <td colSpan={6} className="px-3 py-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs mb-3">
              <Detail label="ID" value={<span className="font-mono">{p.id}</span>} />
              <Detail label="Strike" value={p.strike !== null ? `$${p.strike}` : "—"} />
              <Detail label="Expiry" value={p.expiry ?? "—"} />
              <Detail label="Max loss" value={fmtUsd(p.max_loss_usd)} />
              <Detail label="Invalidation" value={fmtUsd(p.invalidation_price)} />
              <Detail label="Entry date" value={p.entry_date} />
              {p.notes && (
                <div className="md:col-span-2">
                  <div className="text-[10px] uppercase tracking-wider text-text-muted">Notes</div>
                  <div className="text-text-secondary">{p.notes}</div>
                </div>
              )}
            </div>

            <GreeksDetail position={p} />

            {p.partial_exits && p.partial_exits.length > 0 && (
              <div className="border-t border-bg-border pt-3 mb-3 text-xs">
                <div className="text-[10px] uppercase tracking-wider text-text-muted mb-1">
                  Partial exits ({p.partial_exits.length})
                </div>
                <div className="space-y-1">
                  {p.partial_exits.map((leg, i) => (
                    <div key={i} className="flex items-center gap-3 font-mono">
                      <span className="text-text-secondary">{leg.date.slice(0, 10)}</span>
                      <span>−{leg.contracts_closed}c</span>
                      <span className={leg.pnl_usd !== null && leg.pnl_usd >= 0 ? "text-green-400" : "text-red-400"}>
                        {leg.pnl_usd !== null ? fmtUsd(leg.pnl_usd) : "—"}
                      </span>
                      {leg.notes && <span className="text-text-muted">{leg.notes}</span>}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex items-center justify-end gap-2 border-t border-bg-border pt-3 flex-wrap">
              {isClosing ? (
                <>
                  {isOptions && remainingContracts > 1 && (
                    <label className="flex items-center gap-1 text-xs">
                      <span className="text-text-muted">Close</span>
                      <input
                        className="input w-16"
                        type="number"
                        min="1"
                        max={remainingContracts}
                        step="1"
                        value={closeContracts}
                        onChange={(e) => setCloseContracts(e.target.value)}
                      />
                      <span className="text-text-muted">/ {remainingContracts}</span>
                    </label>
                  )}
                  <input
                    className="input w-24"
                    type="number"
                    step="0.01"
                    placeholder="P&L"
                    value={closePnl}
                    onChange={(e) => setClosePnl(e.target.value)}
                  />
                  <input
                    className="input w-48"
                    placeholder="Close notes"
                    value={closeNotes}
                    onChange={(e) => setCloseNotes(e.target.value)}
                  />
                  <button
                    type="button"
                    className="btn text-xs"
                    onClick={() => {
                      setClosingId(null);
                      setClosePnl("");
                      setCloseNotes("");
                      setCloseContracts("");
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary text-xs"
                    onClick={onConfirmClose}
                  >
                    {isOptions && Number(closeContracts) > 0 && Number(closeContracts) < remainingContracts
                      ? `Close ${closeContracts} of ${remainingContracts}`
                      : "Confirm close"}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="btn btn-primary text-xs"
                  onClick={() => {
                    setClosingId(p.id);
                    setClosePnl("");
                    setCloseNotes("");
                    setCloseContracts(isOptions ? String(remainingContracts) : "");
                  }}
                >
                  Close position
                </button>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function Detail({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-text-muted">{label}</div>
      <div className="text-text-secondary">{value}</div>
    </div>
  );
}

function fmtNum(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined) return "—";
  return n.toFixed(digits);
}

/**
 * Greeks / IV / premium-threshold readout for the expanded position row.
 * Shows nothing if every field is null (legacy positions without Greeks).
 */
function GreeksDetail({ position: p }: { position: Position }) {
  const hasAnyGreek =
    p.delta !== null || p.gamma !== null || p.theta !== null || p.vega !== null
    || p.iv !== null || p.iv_rank !== null
    || p.premium_stop !== null || p.premium_target !== null;
  if (!hasAnyGreek) return null;
  return (
    <div className="border-t border-bg-border pt-3 mb-3">
      <div className="text-[10px] uppercase tracking-widest text-text-muted mb-2">
        ▮ Entry Greeks · IV · premium thresholds
      </div>
      <div className="grid grid-cols-3 md:grid-cols-6 gap-3 text-xs">
        <Detail label="Δ Delta" value={<span className="font-mono">{fmtNum(p.delta, 3)}</span>} />
        <Detail label="Γ Gamma" value={<span className="font-mono">{fmtNum(p.gamma, 4)}</span>} />
        <Detail label="Θ Theta" value={<span className="font-mono">{fmtNum(p.theta, 2)}</span>} />
        <Detail label="ν Vega" value={<span className="font-mono">{fmtNum(p.vega, 2)}</span>} />
        <Detail
          label="IV"
          value={
            <span className="font-mono">
              {p.iv === null ? "—" : `${(p.iv * 100).toFixed(1)}%`}
            </span>
          }
        />
        <Detail
          label="IV rank"
          value={<span className="font-mono">{fmtNum(p.iv_rank, 0)}</span>}
        />
        <Detail
          label="Premium stop"
          value={
            <span className="font-mono text-signal-bear">
              {p.premium_stop === null ? "—" : `$${p.premium_stop.toFixed(2)}`}
            </span>
          }
        />
        <Detail
          label="Premium target"
          value={
            <span className="font-mono text-signal-bull">
              {p.premium_target === null ? "—" : `$${p.premium_target.toFixed(2)}`}
            </span>
          }
        />
      </div>
    </div>
  );
}

export function PositionsView() {
  // Refresh the shared dashboard banner (balance / stage / unreviewed weeks)
  // after a trade — otherwise the StatusBar shows app-load values all session.
  const { refresh: refreshDashboard } = useDashboardState();
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
  const [closeContracts, setCloseContracts] = useState<string>("");
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // Home-card deep link: /positions?open=1 auto-pops the open-position form.
  // Strip the param after consuming it so refreshing doesn't re-trigger.
  useEffect(() => {
    if (searchParams.get("open") === "1") {
      setShowForm(true);
      const next = new URLSearchParams(searchParams);
      next.delete("open");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  function toggleExpand(id: string) {
    setExpandedId((prev) => (prev === id ? null : id));
    if (closingId !== id) {
      setClosingId(null);
      setClosePnl("");
      setCloseNotes("");
      setCloseContracts("");
    }
  }

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

  // Phase B: positions open only through the kill-sheet view. Enter-key
  // submission on this form routes to handleGenerateKillSheet rather than
  // POST /positions directly.
  function handleFormSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.ticker) return;
    // Long-only cash account: a bullish (long) thesis must be a CALL and a
    // bearish (short) thesis a PUT. Block the contradictory options combos
    // here so the user gets immediate feedback instead of a 422 at open time.
    // (Mirrors the API guard in src/api/routes/positions.py.)
    if (
      (form.instrument === "call" || form.instrument === "put") &&
      (form.instrument === "call") !== (form.direction === "long")
    ) {
      setError(
        "Cash account is long-only: pair a long (bullish) thesis with a CALL " +
          "and a short (bearish) thesis with a PUT. A bearish CALL or bullish " +
          "PUT would be a sold/short option.",
      );
      return;
    }
    setError(null);
    handleGenerateKillSheet();
  }

  async function handleClose(id: string) {
    setError(null);
    try {
      const pnl = closePnl === "" ? null : Number(closePnl);
      const notes = closeNotes === "" ? null : closeNotes;
      const contracts = closeContracts === "" ? null : Number(closeContracts);
      await api.closePosition(id, pnl, notes, contracts);
      setClosingId(null);
      setClosePnl("");
      setCloseNotes("");
      setCloseContracts("");
      await refresh();
      await refreshDashboard();  // realized P&L changed → update banner
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function handleGenerateKillSheet() {
    // Carry every position-form field into the kill-sheet view via URL
    // params. After kill-sheet AUTHORIZED, the kill-sheet view reads these
    // back to POST /api/v1/positions with kill_sheet_id attached.
    const params = new URLSearchParams();
    const set = (k: string, v: unknown) => {
      if (v === null || v === undefined || v === "") return;
      params.set(k, String(v));
    };
    set("ticker", form.ticker);
    set("direction", form.direction);
    set("account", form.account);
    set("instrument", form.instrument);
    if (form.instrument === "call" || form.instrument === "put") {
      set("contract_type", form.instrument);
    }
    set("strike", form.strike);
    set("expiry", form.expiry);
    set("premium", form.premium);
    set("contracts", form.contracts);
    set("shares", form.shares);
    set("entry_price", form.entry_price);
    set("delta", form.delta);
    set("gamma", form.gamma);
    set("theta", form.theta);
    set("vega", form.vega);
    set("iv", form.iv);
    set("iv_rank", form.iv_rank);
    set("premium_stop", form.premium_stop);
    set("premium_target", form.premium_target);
    set("target", form.target);
    set("invalidation", form.invalidation);
    set("notes", form.notes);
    set("skill", form.skill);
    set("tier", form.tier);
    navigate(`/kill-sheet?${params.toString()}`);
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Positions</h2>
        <div className="flex gap-2">
          <button className="btn" onClick={() => void refresh()} disabled={loading}>
            {loading ? "…" : "Refresh"}
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
        <form onSubmit={handleFormSubmit} className="panel mb-4">
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
                {form.premium !== null && form.premium !== undefined && form.premium > 0 && (
                  <div className="md:col-span-3">
                    <PremiumLevelsHint premium={form.premium} />
                  </div>
                )}
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
            {form.instrument !== "shares" && (
              <div className="md:col-span-3">
                <AdvancedOptionsFields form={form} update={update} setForm={setForm} />
              </div>
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
          <div className="panel-body border-t border-bg-border flex flex-wrap items-center justify-between gap-3">
            <div className="text-[10px] uppercase tracking-widest text-text-muted leading-relaxed max-w-md">
              ▮ Phase B · positions open only through an AUTHORIZED kill sheet ·
              discipline + devil gates run before the record is created
            </div>
            <div className="flex gap-2">
              <button type="button" className="btn" onClick={() => setShowForm(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleGenerateKillSheet}
                disabled={!form.ticker}
                title="Open the kill sheet — position records on AUTHORIZED + §8 attestation"
              >
                Generate kill sheet →
              </button>
            </div>
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
          <table className="w-full text-sm">
            <thead className="text-[10px] uppercase tracking-wider text-text-muted border-b border-bg-border">
              <tr>
                <th className="text-left px-3 py-2">Ticker</th>
                <th className="text-left px-3 py-2">Trend</th>
                <th className="text-left px-3 py-2">Acct / Inst</th>
                <th className="text-right px-3 py-2">Cost</th>
                <th className="text-right px-3 py-2">Target</th>
                <th className="text-right px-3 py-2"></th>
              </tr>
            </thead>
            <tbody>
              {openPositions.map((p) => {
                const expanded = expandedId === p.id;
                return (
                  <PositionRowFragment
                    key={p.id}
                    position={p}
                    expanded={expanded}
                    onToggle={() => toggleExpand(p.id)}
                    closingId={closingId}
                    setClosingId={setClosingId}
                    closePnl={closePnl}
                    setClosePnl={setClosePnl}
                    closeNotes={closeNotes}
                    setCloseNotes={setCloseNotes}
                    closeContracts={closeContracts}
                    setCloseContracts={setCloseContracts}
                    onConfirmClose={() => void handleClose(p.id)}
                  />
                );
              })}
            </tbody>
          </table>
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
