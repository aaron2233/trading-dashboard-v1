import { Link } from "react-router-dom";
import type { ScanVerdict, UnifiedSetupFields } from "../api/types";

/**
 * Unified scan card. Renders the same layout for weekly-trend, lotto, and
 * index-swing setups: verdict pill (Buy / Wait / No-Go), entry/stop/target
 * prices, suggested options data, regime badges, and the why-now blurb.
 *
 * Detail rows and badges are passed in from the caller — the card itself
 * doesn't know strategy specifics, so the UI stays consistent.
 */

export interface TradeCardBadge {
  label: string;
  tone?: "bull" | "bear" | "info" | "muted" | "flag";
}

export interface TradeCardOptions {
  /** "QQQ 480C 2026-06-19" once filled. Null = placeholder. */
  contract?: string | null;
  iv_rank?: number | null;
  premium?: number | null;
  contracts?: number | null;
}

export interface TradeCardProps {
  setup: UnifiedSetupFields;
  /** Strategy label shown in upper-right (e.g. "Weekly trend · Track A"). */
  strategy_label: string;
  /** Optional pre-fill link to /kill-sheet?... — null means no link. */
  kill_sheet_href?: string | null;
  /** Strategy-specific badges (regime, tier, asset class, etc.). */
  badges?: TradeCardBadge[];
  /** Strategy-specific detail rows (key/value pairs below the prices). */
  details?: { label: string; value: string }[];
  /** Optional options data — placeholder for now, real fills later. */
  options?: TradeCardOptions | null;
  /** Optional direction — defaults to "long" for the kill-sheet pre-fill. */
  direction?: "long" | "short";
}

function fmtPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1) return `$${value.toFixed(4)}`;
  if (value < 100) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(2)}`;
}

function badgeTone(tone: TradeCardBadge["tone"]): string {
  switch (tone) {
    case "bull": return "badge-bull";
    case "bear": return "badge-bear";
    case "flag": return "badge-flag";
    case "info": return "badge-info";
    default: return "badge-muted";
  }
}

function VerdictPill({ verdict }: { verdict: ScanVerdict }) {
  const map: Record<ScanVerdict, { label: string; classes: string }> = {
    buy: {
      label: "BUY",
      classes:
        "bg-signal-bull/15 text-signal-bull border-signal-bull/40",
    },
    wait: {
      label: "WAIT",
      classes:
        "bg-signal-flag/15 text-signal-flag border-signal-flag/40",
    },
    no_go: {
      label: "NO-GO",
      classes:
        "bg-signal-bear/15 text-signal-bear border-signal-bear/40",
    },
  };
  const cfg = map[verdict];
  return (
    <span
      className={
        "inline-flex items-center px-3 py-1 rounded-md border text-xs font-bold tracking-wider " +
        cfg.classes
      }
    >
      {cfg.label}
    </span>
  );
}

function PriceRow({
  label, value, accent,
}: { label: string; value: string; accent?: "bull" | "bear" | "neutral" }) {
  const accentCls =
    accent === "bull" ? "text-signal-bull"
      : accent === "bear" ? "text-signal-bear"
        : "text-text-primary";
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-xs uppercase tracking-wider text-text-secondary">
        {label}
      </span>
      <span className={`font-mono font-semibold text-sm ${accentCls}`}>
        {value}
      </span>
    </div>
  );
}

function OptionsBlock({
  options, suggested_dte, suggested_delta, suggested_strike, direction,
}: {
  options: TradeCardOptions | null | undefined;
  suggested_dte: string | null;
  suggested_delta: string | null;
  suggested_strike: number | null;
  direction: "long" | "short";
}) {
  const hasReal =
    options &&
    (options.contract ||
      options.premium !== null && options.premium !== undefined);

  return (
    <div className="mt-3 pt-3 border-t border-bg-border">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs uppercase tracking-wider text-text-secondary">
          Options data
        </span>
        {!hasReal && (
          <span className="text-[10px] text-text-muted uppercase tracking-wider">
            manual entry
          </span>
        )}
      </div>
      {hasReal ? (
        <div className="space-y-1 text-sm">
          {options?.contract && (
            <div className="font-mono text-text-primary">{options.contract}</div>
          )}
          <div className="flex gap-4 text-xs text-text-secondary">
            {options?.iv_rank !== null && options?.iv_rank !== undefined && (
              <span>IVR {options.iv_rank.toFixed(0)}%</span>
            )}
            {options?.premium !== null && options?.premium !== undefined && (
              <span>Prem ${options.premium.toFixed(2)}</span>
            )}
            {options?.contracts !== null && options?.contracts !== undefined && (
              <span>{options.contracts}× contract</span>
            )}
          </div>
        </div>
      ) : (
        <div className="text-xs text-text-secondary space-y-1">
          {suggested_strike != null && (
            <div className="text-sm">
              Suggested strike:{" "}
              <span className="text-text-primary font-mono font-semibold">
                ${suggested_strike.toFixed(2)} {direction === "long" ? "call" : "put"}
              </span>
            </div>
          )}
          <div>
            DTE target:{" "}
            <span className="text-text-primary font-mono">
              {suggested_dte ?? "—"}
            </span>
          </div>
          <div>
            Delta target:{" "}
            <span className="text-text-primary font-mono">
              {suggested_delta ?? "—"}
            </span>
          </div>
          <div className="text-text-muted italic">
            Strike auto-derived from spot + HV at the mid-band delta. Verify on
            broker chain — final strike / premium / IV / OI / spread are yours
            to fill.
          </div>
        </div>
      )}
    </div>
  );
}

export function TradeCard({
  setup,
  strategy_label,
  kill_sheet_href,
  badges = [],
  details = [],
  options = null,
  direction = "long",
}: TradeCardProps) {
  const verdictBorder =
    setup.verdict === "buy" ? "border-l-signal-bull"
      : setup.verdict === "wait" ? "border-l-signal-flag"
        : "border-l-signal-bear";

  return (
    <div
      className={`panel border-l-4 ${verdictBorder}`}
      data-verdict={setup.verdict}
      data-ticker={setup.ticker}
    >
      <div className="panel-body">
        {/* Header row */}
        <div className="flex items-start justify-between gap-3 mb-3 flex-wrap">
          <div className="flex items-baseline gap-3 flex-wrap">
            <span className="font-mono font-semibold text-lg">{setup.ticker}</span>
            <span className="text-xs text-text-secondary">
              {fmtPrice(setup.close)} · bar {setup.bar_date ?? "—"}
            </span>
            <span className="text-[10px] text-text-muted uppercase tracking-wider">
              {strategy_label}
              {direction === "short" ? " · SHORT" : ""}
            </span>
          </div>
          <VerdictPill verdict={setup.verdict} />
        </div>

        {/* Verdict reason */}
        {setup.verdict_reason && (
          <p className="text-sm text-text-primary mb-3">{setup.verdict_reason}</p>
        )}

        {/* Badges */}
        {badges.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-3">
            {badges.map((b, i) => (
              <span key={i} className={`badge text-xs ${badgeTone(b.tone)}`}>
                {b.label}
              </span>
            ))}
          </div>
        )}

        {/* Entry / Stop / Target */}
        {(setup.entry_price !== null || setup.stop_price !== null ||
          setup.target_price !== null) && (
          <div className="bg-bg-elevated/50 rounded p-3 space-y-0.5">
            <PriceRow
              label="Entry"
              value={fmtPrice(setup.entry_price)}
              accent="neutral"
            />
            <PriceRow
              label="Stop"
              value={fmtPrice(setup.stop_price)}
              accent="bear"
            />
            <PriceRow
              label="Target"
              value={fmtPrice(setup.target_price)}
              accent="bull"
            />
          </div>
        )}

        {/* Strategy-specific details */}
        {details.length > 0 && (
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
            {details.map((d, i) => (
              <div key={i} className="flex justify-between border-b border-bg-border/40 py-0.5">
                <span className="text-text-secondary">{d.label}</span>
                <span className="font-mono text-text-primary">{d.value}</span>
              </div>
            ))}
          </div>
        )}

        {/* Options (placeholder until manually filled) */}
        {setup.verdict !== "no_go" && (
          <OptionsBlock
            options={options}
            suggested_dte={setup.suggested_dte}
            suggested_delta={setup.suggested_delta}
            suggested_strike={setup.suggested_strike ?? null}
            direction={direction === "short" ? "short" : "long"}
          />
        )}

        {/* Why now */}
        {setup.why_now && setup.why_now !== setup.verdict_reason && (
          <p className="text-[11px] text-text-muted mt-3 italic">
            {setup.why_now}
          </p>
        )}

        {/* Blockers */}
        {setup.blockers.length > 0 && (
          <ul className="text-xs text-signal-flag mt-3 space-y-0.5">
            {setup.blockers.map((b, i) => (
              <li key={i}>⚠ {b}</li>
            ))}
          </ul>
        )}

        {/* Kill sheet link */}
        {kill_sheet_href && setup.verdict !== "no_go" && (
          <div className="mt-3 pt-3 border-t border-bg-border">
            <Link
              to={kill_sheet_href}
              className="text-xs text-signal-flag hover:underline"
            >
              → Pre-fill kill sheet
            </Link>
          </div>
        )}
      </div>
    </div>
  );
}
