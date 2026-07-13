import type { ActionState, IndicatorStatus } from "../api/types";

/** Icon legend — every glyph in the app maps to exactly one meaning.
 *
 * System 1 — regime-health status (indicator pills, history strip, panel
 * badges): how healthy a market-state indicator is.
 *   🟢 healthy · 🟡 caution · 🔴 broken · ⬜ unknown · ⚠ fetch error
 *
 * System 2 — action-gate verdicts (scan cards, lotto hero): what to DO
 * about a setup right now.
 *   🟢 enter now · 🟡 setup forming · 🟠 chase zone · ⚪ stale · ⛔ disqualified
 *
 * Shared marks used app-wide (inline, not mapped here):
 *   ⚠ warning/caveat · ✓ pass · ✗ fail · ⛔ blocked · → go/next ·
 *   ↻ refresh · ↗ external link · ▾/▴ expand/collapse
 *
 * New glyphs must join one of these systems or earn a line in this legend —
 * decorative one-off icons are how the icon set rotted the first time.
 */

export const STATUS_GLYPH: Record<IndicatorStatus, string> = {
  green: "🟢",
  amber: "🟡",
  red: "🔴",
  unknown: "⬜",
  error: "⚠",
};

export const STATUS_BADGE_CLASS: Record<IndicatorStatus, string> = {
  green: "badge-bull",
  amber: "badge-flag",
  red: "badge-bear",
  unknown: "badge-muted",
  error: "badge-muted",
};

export const STATUS_LABEL: Record<IndicatorStatus, string> = {
  green: "GREEN",
  amber: "AMBER",
  red: "RED",
  unknown: "UNKNOWN",
  error: "ERROR",
};

export const ACTION_GLYPH: Record<ActionState, string> = {
  enter_now: "🟢",
  setup_forming: "🟡",
  chase_zone: "🟠",
  stale: "⚪",
  disqualified: "⛔",
};
