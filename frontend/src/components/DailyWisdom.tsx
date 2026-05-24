import { useMemo } from "react";
import { WISDOM, type WisdomEntry } from "../data/wisdom";

function pickForToday(): WisdomEntry {
  const now = new Date();
  // Date-seeded hash so the same calendar day always returns the same entry.
  // Using local date components keeps it stable across timezones for one user.
  const seed =
    now.getFullYear() * 10000 + (now.getMonth() + 1) * 100 + now.getDate();
  return WISDOM[seed % WISDOM.length];
}

export function DailyWisdom() {
  const entry = useMemo(pickForToday, []);
  const label = entry.kind === "quote" ? "Quote of the day" : "Macro fact of the day";

  return (
    <section className="panel p-4 mb-6 border border-bg-border bg-bg-elevated/40">
      <div className="flex items-baseline justify-between mb-2 flex-wrap gap-2">
        <span className="text-[10px] uppercase tracking-widest text-text-muted font-semibold">
          {label}
        </span>
        <span className="text-[10px] uppercase tracking-widest text-text-muted font-mono">
          {entry.category}
        </span>
      </div>
      <blockquote className="text-sm text-text-primary leading-relaxed mb-2 italic">
        {entry.kind === "quote" ? `"${entry.text}"` : entry.text}
      </blockquote>
      <cite className="text-xs text-text-secondary not-italic block">
        — <span className="text-text-primary font-semibold">{entry.attribution}</span>
        <span className="text-text-muted">, {entry.source}</span>
      </cite>
    </section>
  );
}
