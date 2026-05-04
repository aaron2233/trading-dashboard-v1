import { Link } from "react-router-dom";
import { useDashboardState } from "../state/DashboardStateContext";

function UnreviewedWeeksCTA() {
  const { state } = useDashboardState();
  const weeks = state?.unreviewed_weeks ?? [];
  if (weeks.length === 0) return null;
  return (
    <section className="panel p-4 mb-6 border-signal-flag/40 bg-signal-flag/5">
      <div className="flex items-baseline justify-between mb-2">
        <h2 className="text-sm font-semibold text-signal-flag">
          {weeks.length} unreviewed week{weeks.length === 1 ? "" : "s"} pending
        </h2>
        <span className="text-xs text-text-secondary">
          Discipline rule: weekly review every Sunday after the prior week closes.
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

export function HomeView() {
  return (
    <div className="max-w-3xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-2">Trading Dashboard</h1>
      <p className="text-text-secondary mb-6">
        The trading dashboard that won&apos;t let you break your own rules.
      </p>

      <UnreviewedWeeksCTA />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Link to="/scan" className="panel p-4 hover:border-signal-info transition">
          <div className="text-sm font-semibold mb-1">Scan a ticker</div>
          <div className="text-text-secondary text-xs">
            MA Ribbon, Stochastic, SQN regime — full readout.
          </div>
        </Link>
        <Link to="/kill-sheet" className="panel p-4 hover:border-signal-info transition">
          <div className="text-sm font-semibold mb-1">Generate a kill sheet</div>
          <div className="text-text-secondary text-xs">
            Auto-fills indicators + sizing. Trade devil + account rules pre-flight.
          </div>
        </Link>
        <Link to="/positions" className="panel p-4 hover:border-signal-info transition">
          <div className="text-sm font-semibold mb-1">Manage positions</div>
          <div className="text-text-secondary text-xs">
            Open / close trades. Live alerts on DTE, target, invalidation, MA flip.
          </div>
        </Link>
        <Link to="/journal" className="panel p-4 hover:border-signal-info transition">
          <div className="text-sm font-semibold mb-1">Trade journal</div>
          <div className="text-text-secondary text-xs">
            Win rate, total P&amp;L, profit factor, expectancy, by-account breakdown.
          </div>
        </Link>
      </div>
      <div className="mt-8 text-text-muted text-xs">
        Backend: <code>{import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000"}</code>
      </div>
    </div>
  );
}
