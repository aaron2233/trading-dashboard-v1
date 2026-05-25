import { VerdictHero } from "../Verdict";
import { fromKillSheetDevil } from "../../lib/verdict";
import type { KillSheetRequest, KillSheetResponse } from "../../api/types";

function aggregateClass(agg: string): string {
  if (agg.startsWith("KILL")) return "text-signal-bear";
  if (agg.startsWith("CONDITIONAL")) return "text-signal-flag";
  return "text-signal-bull";
}

interface Props {
  response: KillSheetResponse;
  form: KillSheetRequest;
  openPositionGate: React.ReactNode;
}

export function KillSheetResponsePanel({ response, form, openPositionGate }: Props) {
  const ks = response.kill_sheet as Record<string, unknown>;
  const status = ks?.status as string | undefined;
  const reason = ks?.rejection_reason as string | undefined;
  const att = ks?.discipline_attestation as Record<string, unknown> | undefined;

  const blocks = response.rule_violations.filter((v) => v.severity === "block");
  const warns = response.rule_violations.filter((v) => v.severity === "warn");

  return (
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

      {openPositionGate}

      {status === "REJECTED" && (
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
      )}

      {att && (() => {
        const flags: { label: string; ok: boolean }[] = [
          { label: "IV Rank > 70%", ok: !att.iv_rank_over_70 },
          { label: "DTE < 7", ok: !att.dte_under_7 },
          { label: "Daily MA chop", ok: !att.daily_chop },
          { label: "Fighting SQN regime", ok: !att.fighting_sqn_regime },
          { label: "Averaging down", ok: !att.averaging_down },
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

      {response.rules_blocked && blocks.length > 0 && (
        <div className="panel p-4 mb-4 border-signal-bear">
          <div className="font-semibold text-signal-bear mb-2">
            Account-rules block — kill sheet rendered for audit but trade is gated.
          </div>
          <ul className="text-sm space-y-1">
            {blocks.map((v, i) => (
              <li key={i} className="text-text-secondary">
                <span className="text-signal-bear">[{v.rule}]</span> {v.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {warns.length > 0 && (
        <div className="panel p-4 mb-4 border-signal-flag">
          <div className="font-semibold text-signal-flag mb-2">
            Account-rules advisory — review before sizing, trade is not gated.
          </div>
          <ul className="text-sm space-y-1">
            {warns.map((v, i) => (
              <li key={i} className="text-text-secondary">
                <span className="text-signal-flag">[{v.rule}]</span> {v.message}
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
  );
}
