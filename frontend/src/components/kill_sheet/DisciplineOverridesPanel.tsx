import { useState } from "react";
import type { KillSheetRequest } from "../../api/types";

interface Props {
  form: KillSheetRequest;
  update: <K extends keyof KillSheetRequest>(key: K, value: KillSheetRequest[K]) => void;
  setAttestation: (key: string, value: boolean) => void;
}

export function DisciplineOverridesPanel({ form, update, setAttestation }: Props) {
  const [show, setShow] = useState(false);

  return (
    <>
      <div className="panel-header flex items-center justify-between">
        <span>Discipline overrides (optional)</span>
        <button type="button" className="btn text-xs" onClick={() => setShow(!show)}>
          {show ? "Hide" : "Show"}
        </button>
      </div>
      {show && (
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
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={!!form.attestation_user_inputs?.weekly_trend_track_a}
                onChange={(e) => setAttestation("weekly_trend_track_a", e.target.checked)}
              />
              <span>
                <span className="text-text-primary">Track A entry (19/39 weekly cross)</span>
                <span className="text-text-muted"> — flag for weekly-trend-trader Track A; activates the per-asset Track A blocked-tickers gate</span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={!!form.attestation_user_inputs?.weekly_trend_track_a_override_documented}
                onChange={(e) => setAttestation("weekly_trend_track_a_override_documented", e.target.checked)}
              />
              <span>
                <span className="text-text-primary">Track A asset override documented</span>
                <span className="text-text-muted"> — required if Track A entry on QQQ/GLD/SPY/AMZN/NFLX/AMD/TSLA (per-asset blocked list)</span>
              </span>
            </label>
          </div>
        </div>
      )}
    </>
  );
}
