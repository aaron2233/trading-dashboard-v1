import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api/client";
import { TradingViewChart } from "../components/TradingViewChart";
import { VerdictHero } from "../components/Verdict";
import { fromKillSheetDevil } from "../lib/verdict";
import type {
  KillSheetRequest,
  KillSheetResponse,
  OpenPositionRequest,
  OptionsExtractionSource,
  ParsedOptionsResponse,
} from "../api/types";

const ACCOUNTS = ["main", "lotto", "weekly"];
const INTENTS = ["SCALP", "SWING", "TREND CAPTURE", "POSITION"] as const;
const CONVICTIONS = ["high", "medium", "speculative", "default"] as const;
const DIRECTIONS = ["long", "short"] as const;

function aggregateClass(agg: string): string {
  if (agg.startsWith("KILL")) return "text-signal-bear";
  if (agg.startsWith("CONDITIONAL")) return "text-signal-flag";
  return "text-signal-bull";
}

/**
 * Phase B authorization gate UI. Visible only when:
 *   - kill sheet is AUTHORIZED (not REJECTED)
 *   - entry_authorized=true on the discipline §8 attestation
 *   - response.kill_sheet_id is present (sheet was persisted server-side)
 *
 * "Open Position" reads the original position-form fields from URL params
 * (passed in by PositionsView's "Generate kill sheet" button) and POSTs to
 * /api/v1/positions with kill_sheet_id attached. On success, redirects.
 */
function OpenPositionGate({
  response,
  searchParams,
  form,
}: {
  response: KillSheetResponse;
  searchParams: URLSearchParams;
  form: KillSheetRequest;
}) {
  const navigate = useNavigate();
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);

  const ks = response.kill_sheet as Record<string, unknown>;
  const status = (ks?.status as string | undefined) ?? "AUTHORIZED";
  const attestation = ks?.discipline_attestation as
    | { entry_authorized?: boolean }
    | undefined;
  const entryAuthorized =
    attestation === undefined ? true : attestation?.entry_authorized === true;

  // The position-form data only round-trips if PositionsView opened this
  // view (i.e. the position-relevant URL params are present). For ad-hoc
  // kill sheets generated from a scan/focus deep-link, there's no position
  // payload to submit.
  const hasPositionPayload = !!searchParams.get("instrument");

  if (status !== "AUTHORIZED" || !response.kill_sheet_id) return null;
  if (!hasPositionPayload) return null;

  async function handleOpenPosition() {
    if (!response.kill_sheet_id) return;
    setOpening(true);
    setOpenError(null);
    try {
      const num = (k: string): number | null => {
        const v = searchParams.get(k);
        if (v === null || v === "") return null;
        const n = Number(v);
        return Number.isFinite(n) ? n : null;
      };
      const str = (k: string): string | null => {
        const v = searchParams.get(k);
        return v === null || v === "" ? null : v;
      };

      const instrument = (str("instrument") ?? "call") as
        OpenPositionRequest["instrument"];
      const direction = (str("direction") ?? "long") as
        OpenPositionRequest["direction"];

      const payload: OpenPositionRequest = {
        ticker: form.ticker,
        direction,
        instrument,
        account: str("account") ?? "main",
        strike: num("strike"),
        expiry: str("expiry"),
        premium: num("premium"),
        contracts: num("contracts"),
        shares: num("shares"),
        entry_price: num("entry_price"),
        target: num("target"),
        invalidation: num("invalidation"),
        notes: str("notes"),
        skill: str("skill"),
        tier: num("tier"),
        delta: num("delta"),
        gamma: num("gamma"),
        theta: num("theta"),
        vega: num("vega"),
        iv: num("iv"),
        iv_rank: num("iv_rank"),
        premium_stop: num("premium_stop"),
        premium_target: num("premium_target"),
        kill_sheet_id: response.kill_sheet_id,
      };
      await api.openPosition(payload);
      navigate("/positions");
    } catch (err) {
      setOpenError(err instanceof Error ? err.message : String(err));
    } finally {
      setOpening(false);
    }
  }

  if (!entryAuthorized) {
    return (
      <div className="panel stripe-warn p-4 mb-4 border-2 border-dashed border-signal-flag">
        <div className="text-sm font-bold text-signal-flag uppercase tracking-widest mb-1">
          ⚠ Pass §8 attestation to record this position
        </div>
        <div className="text-xs text-text-secondary">
          Authorize entry by completing the Discipline §8 checks below, then
          re-submit the kill sheet. The position cannot be recorded until
          attestation passes.
        </div>
      </div>
    );
  }

  return (
    <div className="panel p-4 mb-4 border-2 border-signal-bull">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-xs uppercase tracking-widest text-signal-bull max-w-md">
          ✓ Authorization granted · sheet persisted as
          <span className="font-mono ml-2 text-text-primary">
            {response.kill_sheet_id}
          </span>
        </div>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleOpenPosition}
          disabled={opening}
        >
          {opening ? "Recording…" : "→ Open Position"}
        </button>
      </div>
      {openError && (
        <div className="mt-3 text-sm text-signal-bear">
          ⛔ {openError}
        </div>
      )}
    </div>
  );
}

function readInitialForm(params: URLSearchParams): KillSheetRequest {
  const direction = params.get("direction");
  const intent = params.get("intent");
  const conviction = params.get("conviction");
  const account = params.get("account");
  const ticker = params.get("ticker") ?? "";
  const focus = params.get("focus") === "true";

  // Numeric params — pre-filled from the position-open form.
  const numParam = (key: string): number | null => {
    const v = params.get(key);
    if (v === null || v === "") return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  };

  const contractParam = params.get("contract_type");
  const contract_type =
    contractParam === "call" || contractParam === "put" ? contractParam : null;

  return {
    ticker: ticker.toUpperCase(),
    direction: DIRECTIONS.includes(direction as (typeof DIRECTIONS)[number])
      ? (direction as "long" | "short")
      : "long",
    account: account && ACCOUNTS.includes(account) ? account : "main",
    intent: INTENTS.includes(intent as (typeof INTENTS)[number])
      ? (intent as KillSheetRequest["intent"])
      : "SWING",
    conviction: CONVICTIONS.includes(conviction as (typeof CONVICTIONS)[number])
      ? (conviction as KillSheetRequest["conviction"])
      : "high",
    focus,
    // Pre-fillable from the position form
    strike: numParam("strike"),
    expiry: params.get("expiry") || null,
    premium: numParam("premium"),
    contract_type,
    delta: numParam("delta"),
    iv_rank: numParam("iv_rank"),
    target: numParam("target"),
    invalidation: numParam("invalidation"),
    notes: params.get("notes") || null,
  };
}

type FieldSourceMap = Partial<Record<keyof KillSheetRequest, OptionsExtractionSource>>;

const OPTIONS_FIELDS: (keyof KillSheetRequest)[] = [
  "strike", "premium", "expiry", "contract_type",
  "delta", "iv_rank", "oi", "spread",
];

function applyParsedToForm(
  form: KillSheetRequest,
  parsed: ParsedOptionsResponse,
): { next: KillSheetRequest; sources: FieldSourceMap } {
  const next = { ...form };
  const sources: FieldSourceMap = {};
  const tag = parsed.extraction_source;
  if (parsed.strike !== null) { next.strike = parsed.strike; sources.strike = tag; }
  if (parsed.premium !== null) { next.premium = parsed.premium; sources.premium = tag; }
  if (parsed.expiry !== null) { next.expiry = parsed.expiry; sources.expiry = tag; }
  if (parsed.contract_type !== null) {
    next.contract_type = parsed.contract_type;
    sources.contract_type = tag;
  }
  if (parsed.delta !== null) { next.delta = parsed.delta; sources.delta = tag; }
  if (parsed.iv_rank !== null) { next.iv_rank = parsed.iv_rank; sources.iv_rank = tag; }
  if (parsed.open_interest !== null) {
    next.oi = parsed.open_interest;
    sources.oi = tag;
  }
  if (parsed.bid_ask_spread !== null) {
    next.spread = parsed.bid_ask_spread;
    sources.spread = tag;
  }
  return { next, sources };
}

export function KillSheetView() {
  const [searchParams] = useSearchParams();
  const [form, setForm] = useState<KillSheetRequest>(() => readInitialForm(searchParams));
  const [response, setResponse] = useState<KillSheetResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showOptions, setShowOptions] = useState(false);
  const [showDiscipline, setShowDiscipline] = useState(false);

  // Options-input state (paste only — screenshot extraction removed to
  // avoid the Anthropic API spend; manual entry covers the same fields).
  const [pasteText, setPasteText] = useState("");
  const [extractLoading, setExtractLoading] = useState(false);
  const [extractError, setExtractError] = useState<string | null>(null);
  const [extractWarnings, setExtractWarnings] = useState<string[]>([]);
  const [fieldSources, setFieldSources] = useState<FieldSourceMap>({});

  function clearFieldSource(key: keyof KillSheetRequest) {
    setFieldSources((prev) => {
      if (!(key in prev)) return prev;
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  async function handlePasteExtract() {
    if (!pasteText.trim()) return;
    setExtractLoading(true);
    setExtractError(null);
    try {
      const parsed = await api.extractOptionsText(pasteText, form.ticker || undefined);
      const { next, sources } = applyParsedToForm(form, parsed);
      setForm(next);
      setFieldSources((prev) => ({ ...prev, ...sources }));
      setExtractWarnings(parsed.warnings);
      setShowOptions(true);
    } catch (err) {
      setExtractError(err instanceof Error ? err.message : String(err));
    } finally {
      setExtractLoading(false);
    }
  }

  function setAttestation(key: string, value: boolean) {
    setForm((prev) => ({
      ...prev,
      attestation_user_inputs: {
        ...(prev.attestation_user_inputs ?? {}),
        [key]: value,
      },
    }));
  }
  // If the URL changes (e.g. user clicks a different focus deep-link), reseed.
  useEffect(() => {
    setForm(readInitialForm(searchParams));
    setResponse(null);
    setError(null);
    // Auto-open the options panel when a deep-link brings options data
    // (typically from the position-open form's "Generate kill sheet" button).
    const hasOptionsData =
      ["strike", "premium", "expiry", "contract_type", "delta", "iv_rank"]
        .some((k) => searchParams.get(k));
    if (hasOptionsData) setShowOptions(true);
  }, [searchParams]);

  function update<K extends keyof KillSheetRequest>(key: K, value: KillSheetRequest[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
    // Manual edit clears any "from paste" badge for that field.
    if (OPTIONS_FIELDS.includes(key)) {
      clearFieldSource(key);
    }
  }

  function sourceBadge(key: keyof KillSheetRequest) {
    const src = fieldSources[key];
    if (!src) return null;
    return (
      <span className="badge badge-info text-[10px] ml-2">from paste</span>
    );
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.ticker) return;
    setLoading(true);
    setError(null);
    try {
      const payload: KillSheetRequest = { ...form, ticker: form.ticker.toUpperCase() };
      const res = await api.killSheet(payload);
      setResponse(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setResponse(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Kill Sheet</h2>
      </div>

      {form.focus && (
        <div className="panel p-3 mb-4 border-signal-info/40 bg-signal-info/5 flex items-center justify-between gap-3 flex-wrap">
          <span className="text-signal-info text-sm">
            qqq-gld-focus mode {form.ticker ? `— ${form.ticker} ${form.direction}` : ""}.
            Account-rule + DTE-band + $200 risk-cap gates apply on submit.
          </span>
          <button
            type="button"
            className="btn text-xs"
            onClick={() => update("focus", false)}
            title="Generate without focus gates"
          >
            disable focus mode
          </button>
        </div>
      )}

      <form onSubmit={handleSubmit} className="panel mb-4">
        <div className="panel-header">Input</div>
        <div className="panel-body grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="label">Ticker *</label>
            <input
              className="input w-full"
              value={form.ticker}
              onChange={(e) => update("ticker", e.target.value)}
              placeholder="SPY"
              required
            />
          </div>
          <div>
            <label className="label">Direction</label>
            <select
              className="input w-full"
              value={form.direction}
              onChange={(e) => update("direction", e.target.value as "long" | "short")}
            >
              <option value="long">long</option>
              <option value="short">short</option>
            </select>
          </div>
          <div>
            <label className="label">Account</label>
            <select
              className="input w-full"
              value={form.account}
              onChange={(e) => update("account", e.target.value)}
            >
              {ACCOUNTS.map((a) => <option key={a} value={a}>{a}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Intent</label>
            <select
              className="input w-full"
              value={form.intent}
              onChange={(e) => update("intent", e.target.value as KillSheetRequest["intent"])}
            >
              {INTENTS.map((i) => <option key={i} value={i}>{i}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Conviction</label>
            <select
              className="input w-full"
              value={form.conviction}
              onChange={(e) => update("conviction", e.target.value as KillSheetRequest["conviction"])}
            >
              {CONVICTIONS.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </div>
          <div>
            <label className="label">Target ($)</label>
            <input
              className="input w-full"
              type="number"
              step="0.01"
              value={form.target ?? ""}
              onChange={(e) => update("target", e.target.value === "" ? null : Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">Invalidation ($)</label>
            <input
              className="input w-full"
              type="number"
              step="0.01"
              value={form.invalidation ?? ""}
              onChange={(e) => update("invalidation", e.target.value === "" ? null : Number(e.target.value))}
            />
          </div>
          <div className="md:col-span-2">
            <label className="label">Notes</label>
            <input
              className="input w-full"
              value={form.notes ?? ""}
              onChange={(e) => update("notes", e.target.value || null)}
              placeholder="(optional)"
            />
          </div>
        </div>

        <div className="panel-header">Options input — paste from brokerage</div>
        <div className="panel-body space-y-3">
          <p className="text-xs text-text-secondary">
            Brokerage data is fresher than any web feed. Paste options chain
            text from your platform — extracted fields prefill the form below
            with a "from paste" tag. Manual edits clear the tag.
          </p>
          <div>
            <label className="label">Paste options snapshot</label>
            <textarea
              className="input w-full font-mono text-xs"
              rows={5}
              placeholder={"Strike: 480\nPremium: 4.55\nIV Rank: 35\nOI: 12,500\nExpiry: 2026-06-19\nType: call"}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
            />
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                className="btn btn-secondary text-xs"
                disabled={extractLoading || !pasteText.trim()}
                onClick={handlePasteExtract}
              >
                {extractLoading ? "Extracting…" : "Extract from paste"}
              </button>
              {pasteText && (
                <button
                  type="button"
                  className="btn text-xs"
                  onClick={() => setPasteText("")}
                >
                  Clear
                </button>
              )}
            </div>
          </div>
          {extractError && (
            <div className="text-sm text-signal-bear">{extractError}</div>
          )}
          {extractWarnings.length > 0 && (
            <ul className="text-xs text-signal-flag space-y-0.5">
              {extractWarnings.map((w, i) => (
                <li key={i}>⚠ {w}</li>
              ))}
            </ul>
          )}
        </div>

        <div className="panel-header flex items-center justify-between">
          <span>Apex options (optional)</span>
          <button type="button" className="btn text-xs" onClick={() => setShowOptions(!showOptions)}>
            {showOptions ? "hide" : "show"}
          </button>
        </div>
        {showOptions && (
          <div className="panel-body grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <label className="label">Strike{sourceBadge("strike")}</label>
              <input className="input w-full" type="number" step="0.01"
                value={form.strike ?? ""}
                onChange={(e) => update("strike", e.target.value === "" ? null : Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label">Premium ($/share){sourceBadge("premium")}</label>
              <input className="input w-full" type="number" step="0.01"
                value={form.premium ?? ""}
                onChange={(e) => update("premium", e.target.value === "" ? null : Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label">Expiry (YYYY-MM-DD){sourceBadge("expiry")}</label>
              <input className="input w-full"
                value={form.expiry ?? ""}
                onChange={(e) => update("expiry", e.target.value || null)}
                placeholder="2026-06-19"
              />
            </div>
            <div>
              <label className="label">Type{sourceBadge("contract_type")}</label>
              <select className="input w-full"
                value={form.contract_type ?? ""}
                onChange={(e) => update("contract_type", (e.target.value || null) as KillSheetRequest["contract_type"])}
              >
                <option value="">infer from direction</option>
                <option value="call">call</option>
                <option value="put">put</option>
              </select>
            </div>
            <div>
              <label className="label">Delta{sourceBadge("delta")}</label>
              <input className="input w-full" type="number" step="0.01"
                value={form.delta ?? ""}
                onChange={(e) => update("delta", e.target.value === "" ? null : Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label">IV Rank (%){sourceBadge("iv_rank")}</label>
              <input className="input w-full" type="number" step="0.1"
                value={form.iv_rank ?? ""}
                onChange={(e) => update("iv_rank", e.target.value === "" ? null : Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label">Open Interest{sourceBadge("oi")}</label>
              <input className="input w-full" type="number"
                value={form.oi ?? ""}
                onChange={(e) => update("oi", e.target.value === "" ? null : Number(e.target.value))}
              />
            </div>
            <div>
              <label className="label">Spread ($){sourceBadge("spread")}</label>
              <input className="input w-full" type="number" step="0.01"
                value={form.spread ?? ""}
                onChange={(e) => update("spread", e.target.value === "" ? null : Number(e.target.value))}
              />
            </div>
          </div>
        )}

        <div className="panel-header flex items-center justify-between">
          <span>Discipline overrides (optional)</span>
          <button type="button" className="btn text-xs" onClick={() => setShowDiscipline(!showDiscipline)}>
            {showDiscipline ? "Hide" : "Show"}
          </button>
        </div>
        {showDiscipline && (
          <div className="panel-body grid grid-cols-1 gap-3 border-t border-bg-border">
            <p className="text-xs text-text-muted">
              Section 8 attestation. Use only when an anti-pattern is auto-flagged
              and you have a documented thesis to override. Friction is the point —
              the kill sheet won't authorize entry until each fired flag has its
              corresponding attestation.
            </p>

            <div>
              <label className="label">Divergence thesis</label>
              <textarea
                className="input w-full"
                rows={2}
                value={form.divergence_thesis ?? ""}
                onChange={(e) => update("divergence_thesis", e.target.value || null)}
                placeholder="Required to override SQN(100) regime gate (e.g. 'VIX spike post-Powell, bottom forming')"
              />
            </div>

            <div>
              <label className="label">Counter-Weekly thesis</label>
              <textarea
                className="input w-full"
                rows={2}
                value={form.counter_weekly_thesis ?? ""}
                onChange={(e) => update("counter_weekly_thesis", e.target.value || null)}
                placeholder="Required when Weekly opposes (otherwise rule 11 fails on score)"
              />
            </div>

            <div className="space-y-1.5">
              <label className="label">Attestation booleans</label>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={!!form.attestation_user_inputs?.explicit_post_earnings_crush_thesis}
                  onChange={(e) => setAttestation("explicit_post_earnings_crush_thesis", e.target.checked)}
                />
                <span>
                  <span className="text-text-primary">Post-earnings IV crush thesis</span>
                  <span className="text-text-muted"> — required if IV Rank &gt; 70%</span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={!!form.attestation_user_inputs?.explicit_0dte_framing}
                  onChange={(e) => setAttestation("explicit_0dte_framing", e.target.checked)}
                />
                <span>
                  <span className="text-text-primary">Explicit 0DTE framing</span>
                  <span className="text-text-muted"> — required if DTE &lt; 7</span>
                </span>
              </label>
              <label className="flex items-start gap-2 text-sm">
                <input
                  type="checkbox"
                  className="mt-0.5"
                  checked={!!form.attestation_user_inputs?.new_signal_for_average_down}
                  onChange={(e) => setAttestation("new_signal_for_average_down", e.target.checked)}
                />
                <span>
                  <span className="text-text-primary">New signal for averaging down</span>
                  <span className="text-text-muted"> — required if open position same ticker+direction</span>
                </span>
              </label>
            </div>
          </div>
        )}

        <div className="panel-body border-t border-bg-border flex items-center justify-end gap-2">
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Generating…" : "Generate"}
          </button>
        </div>
      </form>

      {form.ticker && (
        <div className="mb-4">
          <TradingViewChart
            ticker={form.ticker}
            timeframe={form.trigger_tf === "Weekly" ? "1wk"
                       : form.trigger_tf === "4H" ? "4h"
                       : form.trigger_tf === "2H" ? "2h"
                       : "1d"}
            height={420}
            collapsedByDefault
          />
        </div>
      )}

      {error && (
        <div className="panel p-3 mb-4 border-signal-bear/50">
          <span className="text-signal-bear text-sm">{error}</span>
        </div>
      )}

      {response && (
        <>
          <div className="mb-4">
            <VerdictHero
              verdict={fromKillSheetDevil(
                response.devil,
                form.direction ?? "long",
                response.rules_blocked,
              )}
              context={`${form.ticker || "Trade"} · ${form.direction ?? "long"}`}
            />
          </div>

          <OpenPositionGate
            response={response}
            searchParams={searchParams}
            form={form}
          />
          {(() => {
            const ks = response.kill_sheet as Record<string, unknown>;
            const status = ks?.status as string | undefined;
            const reason = ks?.rejection_reason as string | undefined;
            if (status === "REJECTED") {
              return (
                <div className="panel stripe-bear p-4 mb-4 border-2 border-dashed border-signal-bear">
                  <div className="font-semibold text-signal-bear mb-2">
                    ⛔ KILL SHEET REJECTED — {reason}
                  </div>
                  <div className="text-sm text-text-secondary">
                    Document a divergence thesis and re-submit to override the
                    regime gate. Structure / sizing / exit blocks are suppressed
                    until the thesis is recorded.
                  </div>
                </div>
              );
            }
            return null;
          })()}

          {(() => {
            const ks = response.kill_sheet as Record<string, unknown>;
            const att = ks?.discipline_attestation as Record<string, unknown> | undefined;
            if (!att) return null;
            const flags: { label: string; ok: boolean }[] = [
              { label: "IV Rank > 70%", ok: !att.iv_rank_over_70 },
              { label: "DTE < 7", ok: !att.dte_under_7 },
              { label: "Daily MA chop", ok: !att.daily_chop },
              { label: "Fighting SQN regime", ok: !att.fighting_sqn_regime },
              { label: "Averaging down", ok: !att.averaging_down },
              { label: "Doubling pyramid direction", ok: !att.doubling_pyramid_direction },
              { label: "Spreads/margin", ok: !att.spreads_or_margin },
            ];
            const authorized = att.entry_authorized as boolean;
            return (
              <div className="panel mb-4">
                <div className="panel-header flex items-center justify-between">
                  <span>Discipline Attestation (§8)</span>
                  <span className={`badge ${authorized ? "badge-bull" : "badge-bear"}`}>
                    {authorized ? "ENTRY AUTHORIZED" : "ENTRY NOT AUTHORIZED"}
                  </span>
                </div>
                <div className="panel-body grid grid-cols-2 gap-2 text-sm">
                  {flags.map((f, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <span className={f.ok ? "text-signal-bull" : "text-signal-bear"}>
                        {f.ok ? "✓" : "✗"}
                      </span>
                      <span className="text-text-secondary">{f.label}</span>
                    </div>
                  ))}
                </div>
                {!authorized && (
                  <div className="panel-body text-xs text-text-muted border-t border-bg-border">
                    To authorize entry: pass attestation_user_inputs with the
                    appropriate booleans on the next kill-sheet request, or
                    document a divergence thesis if the regime is fighting.
                  </div>
                )}
              </div>
            );
          })()}

          {response.rules_blocked && (
            <div className="panel p-4 mb-4 border-signal-bear">
              <div className="font-semibold text-signal-bear mb-2">
                Account-rules block — kill sheet rendered for audit but trade is gated.
              </div>
              <ul className="text-sm space-y-1">
                {response.rule_violations.map((v, i) => (
                  <li key={i} className="text-text-secondary">
                    <span className="text-signal-bear">[{v.rule}]</span> {v.message}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {response.devil && (
            <div className="panel mb-4">
              <div className="panel-header flex items-center justify-between">
                <span>Trade Devil</span>
                <span className={`font-semibold ${aggregateClass(response.devil.aggregate)}`}>
                  {response.devil.aggregate}
                  <span className="text-text-muted ml-2 text-xs">
                    {response.devil.kills}K · {response.devil.flags}F · {response.devil.passes}P
                  </span>
                </span>
              </div>
              <div className="panel-body space-y-2">
                {response.devil.results.map((r) => (
                  <div key={r.category} className="flex gap-3 text-sm">
                    <span className={`badge ${
                      r.verdict === "KILL" ? "badge-bear"
                      : r.verdict === "FLAG" ? "badge-flag"
                      : "badge-bull"
                    }`}>
                      {r.verdict}
                    </span>
                    <span className="font-semibold w-48 flex-shrink-0">{r.category}</span>
                    <span className="text-text-secondary">{r.reason}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="panel">
            <div className="panel-header">Kill Sheet</div>
            <pre className="panel-body text-xs leading-relaxed overflow-x-auto whitespace-pre">
              {response.rendered_text}
            </pre>
          </div>
        </>
      )}
    </div>
  );
}
