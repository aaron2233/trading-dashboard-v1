import { Link } from "react-router-dom";
import { useDashboardState } from "../state/DashboardStateContext";

function UnreviewedWeeksCTA() {
  const { state } = useDashboardState();
  const weeks = state?.unreviewed_weeks ?? [];
  if (weeks.length === 0) return null;
  return (
    <section className="panel stripe-warn p-4 mb-6 border-2 border-dashed border-signal-flag">
      <div className="flex items-baseline justify-between mb-2 flex-wrap gap-2">
        <h2 className="text-sm font-bold text-signal-flag uppercase tracking-widest">
          ⚠ {weeks.length} unreviewed week{weeks.length === 1 ? "" : "s"} pending
        </h2>
        <span className="text-[10px] uppercase tracking-widest text-text-muted">
          Discipline rule · weekly review every sunday
        </span>
      </div>
      <ul className="space-y-1.5">
        {weeks.slice(0, 5).map((w) => (
          <li key={w.week_start} className="flex items-center justify-between text-sm">
            <span className="text-text-primary">
              Week of <span className="font-mono">{w.week_start}</span>
              <span className="text-text-secondary text-xs ml-2">
                ({w.closed_trade_count} closed trade{w.closed_trade_count === 1 ? "" : "s"})
              </span>
            </span>
            <Link
              to={`/weekly-review?week_of=${w.week_start}`}
              className="btn btn-secondary text-xs"
            >
              Run review →
            </Link>
          </li>
        ))}
      </ul>
      {weeks.length > 5 && (
        <p className="text-xs text-text-secondary mt-2">
          Showing 5 of {weeks.length} unreviewed weeks. Older weeks accessible via{" "}
          <Link to="/weekly-review" className="underline">Weekly Review</Link>.
        </p>
      )}
    </section>
  );
}

interface QuickCardProps {
  to: string;
  index: number;
  title: string;
  desc: string;
  rotate?: "left" | "right";
}

function QuickCard({ to, index, title, desc, rotate }: QuickCardProps) {
  const tilt =
    rotate === "left"
      ? "-rotate-[0.4deg]"
      : rotate === "right"
      ? "rotate-[0.4deg]"
      : "";
  return (
    <Link
      to={to}
      className={`panel p-4 transition hover:border-signal-flag hover:border-dashed group block ${tilt}`}
    >
      <div className="flex items-baseline gap-2 mb-2">
        <span className="marker-chip">{String(index).padStart(2, "0")}</span>
        <span className="text-[10px] uppercase tracking-widest text-text-muted font-mono group-hover:text-signal-flag transition">
          {to}
        </span>
      </div>
      <div
        className="font-display text-text-primary mb-1 group-hover:text-signal-flag transition"
        style={{ fontSize: "1.6rem", lineHeight: "1.05", letterSpacing: "0.04em" }}
      >
        {title}
      </div>
      <div className="text-text-secondary text-xs leading-relaxed">{desc}</div>
    </Link>
  );
}

export function HomeView() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <div className="page-header-row">
        <h1 className="page-title">Trading Dashboard</h1>
      </div>
      <p className="page-subtitle">
        ╞══ The dashboard that won&apos;t let you break your own rules ══╡
      </p>

      <UnreviewedWeeksCTA />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <QuickCard
          to="/scan"
          index={1}
          title="Scan a ticker"
          desc="MA Ribbon · Stochastic · SQN regime — full readout."
          rotate="left"
        />
        <QuickCard
          to="/positions?open=1"
          index={2}
          title="Open new position"
          desc="Routes through kill sheet — discipline + devil gates run before the trade is recorded."
          rotate="right"
        />
        <QuickCard
          to="/positions"
          index={3}
          title="Manage positions"
          desc="Open / close · live alerts on DTE, target, invalidation, MA flip."
          rotate="right"
        />
        <QuickCard
          to="/journal"
          index={4}
          title="Trade journal"
          desc="Win rate · total P&L · profit factor · expectancy · by-account breakdown."
          rotate="left"
        />
      </div>
    </div>
  );
}
