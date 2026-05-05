import type { TierBundle } from "../../api/types";
import { IndicatorPill } from "./IndicatorPill";

interface TierSectionProps {
  bundle: TierBundle;
}

const TIER_BLURB: Record<number, string> = {
  1: "Existing SQN/MA stack + new VIX & VVIX",
  2: "FRED leading indicators",
  3: "RSP/SPY ratio breadth",
  4: "AI capex calendar (manual)",
};

/** One tier's section — header + pill grid. Empty bundles still render
 * (Tier 3 + Tier 4 stubs in Sprint 1) so the panel shape is consistent. */
export function TierSection({ bundle }: TierSectionProps) {
  const blurb = TIER_BLURB[bundle.tier] ?? "";
  return (
    <div className="border-t border-bg-border pt-3 mt-3 first:border-t-0 first:pt-0 first:mt-0">
      <div className="flex items-baseline justify-between mb-2">
        <h4 className="text-[11px] uppercase tracking-widest font-semibold text-text-secondary">
          Tier {bundle.tier} — {bundle.label}
        </h4>
        {blurb && (
          <span className="text-[10px] text-text-muted font-mono">{blurb}</span>
        )}
      </div>
      {bundle.error && (
        <div className="text-xs text-signal-flag bg-signal-flag/10 border border-signal-flag/30 px-2 py-1 mb-2 rounded">
          {bundle.error}
        </div>
      )}
      {bundle.readings.length === 0 && !bundle.error ? (
        <div className="text-xs text-text-muted italic">
          No readings yet — Sprint 3 will fill this tier in.
        </div>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {bundle.readings.map((r) => (
            <IndicatorPill key={r.indicator_id} reading={r} />
          ))}
        </div>
      )}
    </div>
  );
}
