import type { IndicatorReading } from "../../api/types";
import { STATUS_BADGE_CLASS, STATUS_GLYPH } from "../../lib/glyphs";

interface IndicatorPillProps {
  reading: IndicatorReading;
}

/** Compact pill for one indicator. Hover (title attr) shows the threshold
 * rule + the source. Click → no-op for now; Sprint 3 wires history detail. */
export function IndicatorPill({ reading }: IndicatorPillProps) {
  const badgeCls = STATUS_BADGE_CLASS[reading.status] ?? "badge-muted";
  const glyph = STATUS_GLYPH[reading.status] ?? "⬜";
  const title = [
    reading.threshold_note,
    reading.source ? `source: ${reading.source}` : "",
    reading.error ? `error: ${reading.error}` : "",
  ]
    .filter(Boolean)
    .join("\n");

  return (
    <span
      className={`badge ${badgeCls} inline-flex items-center gap-1.5 text-xs`}
      title={title}
    >
      <span aria-hidden="true">{glyph}</span>
      <span className="font-semibold">{reading.label}</span>
      <span className="opacity-80">·</span>
      <span className="font-mono">{reading.formatted_value}</span>
    </span>
  );
}
