import {
  VERDICT_BADGE_CLASS,
  VERDICT_BORDER_CLASS,
  VERDICT_LABEL,
  VERDICT_RING_CLASS,
  type Verdict,
} from "../lib/verdict";

type Size = "sm" | "md" | "lg";

interface VerdictBadgeProps {
  verdict: Verdict;
  size?: Size;
  showConfidence?: boolean;
}

const SIZE_CLASSES: Record<Size, { wrap: string; conf: string }> = {
  sm: { wrap: "text-[10px] px-1.5 py-0.5", conf: "text-[9px]" },
  md: { wrap: "text-[11px] px-2 py-0.5", conf: "text-[10px]" },
  lg: { wrap: "text-sm px-3 py-1", conf: "text-xs" },
};

/**
 * Confidence bar — 10-cell terminal indicator. Uses currentColor so it
 * inherits the parent verdict color.
 */
function ConfBar({ confidence }: { confidence: number }) {
  const cells = Array.from({ length: 10 }, (_, i) => i < confidence);
  return (
    <span className="conf-bar" aria-label={`confidence ${confidence}/10`}>
      {cells.map((filled, i) => (
        <span
          key={i}
          className={`conf-bar-cell ${filled ? "filled" : ""}`}
        />
      ))}
    </span>
  );
}

/**
 * Compact verdict badge — drop-in replacement for ad-hoc badge spans.
 * Shows ■ KIND · N/10 with optional inline confidence bar at lg size.
 */
export function VerdictBadge({ verdict, size = "md", showConfidence = true }: VerdictBadgeProps) {
  const sz = SIZE_CLASSES[size];
  return (
    <span
      className={`inline-flex items-center gap-1.5 ${VERDICT_BADGE_CLASS[verdict.kind]} ${sz.wrap}`}
    >
      <span aria-hidden="true">■</span>
      <span>{VERDICT_LABEL[verdict.kind]}</span>
      {showConfidence ? (
        <span className={`opacity-80 ${sz.conf}`}>{verdict.confidence}/10</span>
      ) : null}
    </span>
  );
}

interface VerdictHeroProps {
  verdict: Verdict;
  /** Override the default rationale stored on the verdict. */
  rationale?: string;
  /** Optional small label above the verdict (e.g. "Today's call"). */
  context?: string;
}

/**
 * Hero verdict — primary call-to-action banner. Use one per view at most.
 * Phosphor-glow label, confidence bar + N/10, optional one-line rationale.
 */
export function VerdictHero({ verdict, rationale, context }: VerdictHeroProps) {
  const text = rationale ?? verdict.rationale;
  return (
    <div
      className={`panel frame-brackets border-2 ${VERDICT_BORDER_CLASS[verdict.kind]} ${VERDICT_RING_CLASS[verdict.kind]} flex items-center gap-5 px-5 py-4`}
    >
      <div className="flex flex-col items-start min-w-[7rem]">
        <span className="text-[10px] opacity-70 uppercase mb-0.5" style={{ letterSpacing: "0.22em" }}>
          ▮ Verdict
        </span>
        <span
          className="font-display phosphor leading-none"
          style={{ fontSize: "3.75rem", letterSpacing: "0.04em" }}
        >
          {VERDICT_LABEL[verdict.kind]}
        </span>
        <div className="flex items-center gap-2 mt-1">
          <ConfBar confidence={verdict.confidence} />
          <span className="text-xs font-bold tabular">
            {verdict.confidence}/10
          </span>
        </div>
      </div>
      <div className="flex-1 min-w-0 border-l-2 border-bg-rule pl-5">
        {context ? (
          <div className="text-[10px] uppercase text-text-muted mb-1" style={{ letterSpacing: "0.22em" }}>
            {context}
          </div>
        ) : null}
        {text ? (
          <div className="text-sm text-text-secondary leading-snug">{text}</div>
        ) : (
          <div className="text-sm text-text-muted">—</div>
        )}
      </div>
    </div>
  );
}

/**
 * Inline verdict — for table cells. Badge + confidence on one line,
 * optional rationale on a second muted line.
 */
export function VerdictInline({ verdict, rationale }: { verdict: Verdict; rationale?: string }) {
  const text = rationale ?? verdict.rationale;
  return (
    <div className="flex flex-col gap-0.5">
      <VerdictBadge verdict={verdict} size="md" />
      {text ? (
        <span className="text-[11px] text-text-muted leading-tight">{text}</span>
      ) : null}
    </div>
  );
}
