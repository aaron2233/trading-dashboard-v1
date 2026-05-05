// Unified verdict primitive for the dashboard. Single source of truth so every
// view shows the same vocabulary: LONG / SHORT / WAIT / SKIP + confidence 1–10.
//
// Each domain enum (WeeklyConfluence, CryptoConfluence, SundayScan
// recommendation, etc.) is mapped into this shape via a `from*` helper. Keep
// these mappings here — never inline new ones in views.

import type {
  CryptoConfluence,
  CryptoDirection,
  DevilReport,
  FocusSetup,
  FreeRangeDirection,
  GateResult,
  SundayScanResponse,
  TrancheTriggerResult,
  WeeklyConfluence,
  WeeklyDirection,
} from "../api/types";

export type VerdictKind = "long" | "short" | "wait" | "skip";

export interface Verdict {
  kind: VerdictKind;
  confidence: number; // 1–10
  rationale?: string; // optional one-line context
}

export const VERDICT_LABEL: Record<VerdictKind, string> = {
  long: "LONG",
  short: "SHORT",
  wait: "WAIT",
  skip: "SKIP",
};

export const VERDICT_BADGE_CLASS: Record<VerdictKind, string> = {
  long: "badge-bull",
  short: "badge-bear",
  wait: "badge-flag",
  skip: "badge-muted",
};

export const VERDICT_BORDER_CLASS: Record<VerdictKind, string> = {
  long: "border-signal-bull/40",
  short: "border-signal-bear/40",
  wait: "border-signal-flag/40",
  skip: "border-text-muted/40",
};

export const VERDICT_RING_CLASS: Record<VerdictKind, string> = {
  long: "text-signal-bull",
  short: "text-signal-bear",
  wait: "text-signal-flag",
  skip: "text-text-muted",
};

const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));

// ─── Weekly trend scanner ────────────────────────────────────────────────

const WEEKLY_CONF: Record<WeeklyConfluence, number> = {
  high_conviction_long: 9,
  high_conviction_short: 9,
  continuation_long: 6,
  continuation_short: 6,
  compression: 3,
  chop: 2,
  no_setup: 1,
};

export function fromWeeklyConfluence(
  c: WeeklyConfluence,
  direction: WeeklyDirection,
  rationale?: string,
): Verdict {
  const confidence = WEEKLY_CONF[c];
  if (c === "compression") return { kind: "wait", confidence, rationale };
  if (c === "chop" || c === "no_setup") return { kind: "skip", confidence, rationale };
  if (direction === "long") return { kind: "long", confidence, rationale };
  if (direction === "short") return { kind: "short", confidence, rationale };
  return { kind: "wait", confidence, rationale };
}

// ─── Crypto scanner ──────────────────────────────────────────────────────

const CRYPTO_CONF: Record<CryptoConfluence, number> = {
  high_conviction_long: 9,
  high_conviction_short: 9,
  medium_conviction_long: 7,
  medium_conviction_short: 7,
  counter_weekly: 4,
  wait: 3,
  skip_chop: 2,
  skip_no_setup: 1,
};

export function fromCryptoConfluence(
  c: CryptoConfluence,
  direction: CryptoDirection,
  rationale?: string,
): Verdict {
  const confidence = CRYPTO_CONF[c];
  if (c === "wait") return { kind: "wait", confidence, rationale };
  if (c === "skip_chop" || c === "skip_no_setup") return { kind: "skip", confidence, rationale };
  if (direction === "long") return { kind: "long", confidence, rationale };
  if (direction === "short") return { kind: "short", confidence, rationale };
  return { kind: "wait", confidence, rationale };
}

// ─── Sunday scan (focus) ─────────────────────────────────────────────────

const FOCUS_STATUS_CONF: Record<FocusSetup["status"], number> = {
  fires: 8,
  watch: 5,
  blocked: 2,
};

export function fromSundayScan(resp: SundayScanResponse): Verdict {
  // Top setup drives direction; recommendation drives kind/confidence floor.
  const topByScore = [...resp.setups].sort((a, b) => b.score - a.score)[0];
  const top = topByScore ?? null;
  const rationale = resp.headline || top?.asset;

  if (resp.recommendation === "cash") {
    return { kind: "skip", confidence: 1, rationale };
  }
  if (resp.recommendation === "watch") {
    return { kind: "wait", confidence: 4, rationale };
  }
  // recommendation === "trade"
  if (!top) return { kind: "wait", confidence: 4, rationale };
  const conf = FOCUS_STATUS_CONF[top.status];
  if (top.direction === "long") return { kind: "long", confidence: conf, rationale };
  if (top.direction === "short") return { kind: "short", confidence: conf, rationale };
  return { kind: "wait", confidence: conf, rationale };
}

export function fromFocusSetup(setup: FocusSetup): Verdict {
  const conf = FOCUS_STATUS_CONF[setup.status];
  if (setup.status === "blocked") {
    return { kind: "skip", confidence: conf, rationale: setup.blockers[0] };
  }
  if (setup.status === "watch") {
    return { kind: "wait", confidence: conf };
  }
  return {
    kind: setup.direction === "long" ? "long" : "short",
    confidence: conf,
  };
}

// ─── Pyramid gate / tranche ──────────────────────────────────────────────

export function fromPyramidGate(gate: GateResult): Verdict {
  if (!gate.permitted) {
    return {
      kind: "skip",
      confidence: 1,
      rationale: gate.blockers[0] ?? "Gate not permitted",
    };
  }
  // Permitted gate is a strong setup — 5 conditions passing.
  const passes = [
    gate.sqn_100_pass,
    gate.sqn_20_pass,
    gate.ma_stack_pass,
    gate.pullback_pass,
    gate.structure_pass,
  ].filter(Boolean).length;
  const confidence = clamp(4 + passes, 5, 9);
  if (gate.direction === "long") return { kind: "long", confidence };
  if (gate.direction === "short") return { kind: "short", confidence };
  return { kind: "wait", confidence };
}

export function fromTrancheTrigger(
  t: TrancheTriggerResult,
  direction: "long" | "short",
): Verdict {
  if (t.should_fire) {
    return { kind: direction, confidence: 8 };
  }
  return {
    kind: "wait",
    confidence: 3,
    rationale: t.blockers[0] ?? "Trigger conditions not met",
  };
}

// ─── KillSheet (devil aggregate + user-chosen direction) ─────────────────

export function fromKillSheetDevil(
  devil: DevilReport | null,
  direction: "long" | "short",
  rulesBlocked: boolean,
): Verdict {
  if (rulesBlocked) {
    return { kind: "skip", confidence: 1, rationale: "Rules blocked entry" };
  }
  if (!devil) {
    // No devil report run. Assume the user-chosen direction at modest
    // confidence; not a green-light but not a kill either.
    return { kind: direction, confidence: 5 };
  }
  const agg = devil.aggregate.toUpperCase();
  if (agg.startsWith("KILL")) {
    return { kind: "skip", confidence: 1, rationale: "Devil verdict: KILL" };
  }
  if (agg.startsWith("CONDITIONAL") || devil.flags > 0) {
    return {
      kind: "wait",
      confidence: 4,
      rationale: `${devil.flags} flag${devil.flags === 1 ? "" : "s"} — resolve before entry`,
    };
  }
  // PASS / clean
  const confidence = clamp(7 + Math.min(devil.passes, 2), 7, 9);
  return { kind: direction, confidence };
}

// ─── Free-range candidate (tier + direction) ─────────────────────────────

const TIER_CONF: Record<string, number> = {
  "1": 8,
  "2": 6,
  "1+2": 7,
};

export function fromFreeRangeCandidate(
  tier: string,
  direction: FreeRangeDirection,
  rationale?: string,
): Verdict {
  const confidence = TIER_CONF[tier] ?? 5;
  return { kind: direction === "long" ? "long" : "short", confidence, rationale };
}

// ─── Lotto state (derived from cooldown + actionable count) ──────────────

export interface LottoVerdictInputs {
  cooldownActive: boolean;
  cooldownReason: string | null;
  sizeLockActive: boolean;
  actionableCount: number;
}

export function fromLottoState(s: LottoVerdictInputs): Verdict {
  if (s.cooldownActive) {
    return {
      kind: "skip",
      confidence: 1,
      rationale: s.cooldownReason ?? "Anti-greed cooldown active",
    };
  }
  if (s.actionableCount === 0) {
    return { kind: "wait", confidence: 4, rationale: "No actionable setups" };
  }
  // Active candidates — direction varies per candidate, so the dashboard-level
  // verdict is "scan-ready, take the most aligned setup". Not directional.
  return {
    kind: "long", // Convention: lotto dashboard verdict = "GO" → bullish-styled
    confidence: s.sizeLockActive ? 5 : 7,
    rationale: s.sizeLockActive
      ? "Size lock — half-size only"
      : `${s.actionableCount} actionable setup${s.actionableCount === 1 ? "" : "s"}`,
  };
}

// ─── ScanView raw indicators → verdict ───────────────────────────────────
// Used when there's no domain enum yet, only raw indicator readings.

export interface RawIndicatorVerdictInputs {
  maStackState: string | null; // "full_bull" | "full_bear" | "rising" | "falling" | "tangled" | ...
  stochZone: string | null; // "oversold" | "overbought" | "neutral" | ...
  stochSignal: string | null; // "turning_up" | "turning_down" | "none" | ...
  sqnRegime: string | null; // "Strong Bull" | "Bull" | "Neutral" | ...
}

export function fromRawIndicators(i: RawIndicatorVerdictInputs): Verdict {
  const stack = (i.maStackState ?? "").toLowerCase();
  const zone = (i.stochZone ?? "").toLowerCase();
  const signal = (i.stochSignal ?? "").toLowerCase();
  const regime = (i.sqnRegime ?? "").toLowerCase();

  const stackBull = stack === "full_bull" || stack === "rising";
  const stackBear = stack === "full_bear" || stack === "falling";
  const stackTangled = stack === "tangled" || stack === "" || stack === "chop";

  if (stackTangled) {
    return { kind: "skip", confidence: 2, rationale: "MA stack tangled — no trend" };
  }

  const regimeAlignedLong = regime.includes("bull");
  const regimeAlignedShort = regime.includes("bear");

  if (stackBull) {
    let conf = 5;
    if (signal === "turning_up") conf += 2;
    if (zone === "oversold") conf += 1;
    if (regimeAlignedLong) conf += 1;
    if (regimeAlignedShort) conf -= 2; // counter-regime penalty
    return {
      kind: "long",
      confidence: clamp(conf, 1, 10),
      rationale: regimeAlignedShort ? "Counter-regime — reduce size" : undefined,
    };
  }
  if (stackBear) {
    let conf = 5;
    if (signal === "turning_down") conf += 2;
    if (zone === "overbought") conf += 1;
    if (regimeAlignedShort) conf += 1;
    if (regimeAlignedLong) conf -= 2;
    return {
      kind: "short",
      confidence: clamp(conf, 1, 10),
      rationale: regimeAlignedLong ? "Counter-regime — reduce size" : undefined,
    };
  }
  return { kind: "wait", confidence: 3, rationale: "No clear trigger" };
}

// ─── Display helpers ─────────────────────────────────────────────────────

export function verdictLabel(v: Verdict): string {
  return VERDICT_LABEL[v.kind];
}

export function verdictBadgeClass(v: Verdict): string {
  return VERDICT_BADGE_CLASS[v.kind];
}
