import type { ActionState, ActionVerdict } from "../api/types";
import { ACTION_GLYPH } from "../lib/glyphs";

const VERDICT_STYLE: Record<
  ActionState,
  { container: string; pill: string; label: string }
> = {
  enter_now: {
    container: "border-l-4 border-signal-bull bg-signal-bull/10",
    pill: "badge-bull", label: "ENTER NOW",
  },
  setup_forming: {
    container: "border-l-4 border-signal-flag bg-signal-flag/10",
    pill: "badge-flag", label: "SETUP FORMING",
  },
  chase_zone: {
    container: "border-l-4 border-signal-bear bg-signal-bear/10",
    pill: "badge-bear", label: "CHASE ZONE",
  },
  stale: {
    container: "border-l-4 border-text-muted bg-bg-elevated",
    pill: "badge-muted", label: "STALE",
  },
  disqualified: {
    container: "border-l-4 border-text-muted bg-bg-elevated",
    pill: "badge-muted", label: "DISQUALIFIED",
  },
};

export const ACTION_VERDICT_SORT_ORDER: Record<ActionState, number> = {
  enter_now: 0,
  setup_forming: 1,
  chase_zone: 2,
  stale: 3,
  disqualified: 4,
};

interface ActionVerdictBannerProps {
  verdict: ActionVerdict;
  /** Compact = no advance_conditions / blockers list. Used in space-tight rows. */
  compact?: boolean;
}

export function ActionVerdictBanner({ verdict, compact = false }: ActionVerdictBannerProps) {
  const style = VERDICT_STYLE[verdict.state];
  const conditions = verdict.advance_conditions ?? [];
  const blockers = verdict.blockers ?? [];
  return (
    <div className={`px-3 py-2 mb-2 rounded-r ${style.container}`}>
      <div className="flex items-center gap-2 flex-wrap">
        <span aria-hidden="true">{ACTION_GLYPH[verdict.state]}</span>
        <span className={`badge ${style.pill} text-[10px] uppercase tracking-widest`}>
          {style.label}
        </span>
        <span className="font-mono text-sm font-semibold">{verdict.headline}</span>
      </div>
      {!compact && conditions.length > 0 && (
        <ul className="text-[11px] text-text-secondary mt-1 space-y-0.5">
          {conditions.slice(0, 2).map((c, i) => (
            <li key={i}>→ {c}</li>
          ))}
        </ul>
      )}
      {!compact && blockers.length > 0 && (
        <ul className="text-[11px] text-text-secondary mt-1 space-y-0.5">
          {blockers.slice(0, 2).map((b, i) => (
            <li key={i}>· {b}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
