import { useLocation, useNavigate } from "react-router-dom";
import { JournalView } from "./JournalView";
import { PositionsView } from "./PositionsView";

/** Book = Positions + Journal as tabs in one nav destination. The tab is the
 * route (/positions, /journal) so every existing deep link — Home cards,
 * Lotto links, kill-sheet redirects — keeps working unchanged. */

function tabClass(active: boolean): string {
  return active
    ? "px-3 py-1.5 text-[11px] uppercase font-semibold tracking-wider bg-signal-flag/10 text-signal-flag border border-signal-flag"
    : "px-3 py-1.5 text-[11px] uppercase font-semibold tracking-wider text-text-secondary hover:text-signal-flag border border-transparent";
}

export function BookView() {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = location.pathname.startsWith("/journal") ? "journal" : "positions";

  return (
    <div>
      <div className="max-w-5xl mx-auto px-4 pt-4 -mb-2">
        <div className="flex gap-1">
          <button
            type="button"
            className={tabClass(tab === "positions")}
            onClick={() => navigate("/positions")}
          >
            Positions
          </button>
          <button
            type="button"
            className={tabClass(tab === "journal")}
            onClick={() => navigate("/journal")}
          >
            Journal
          </button>
        </div>
      </div>
      {tab === "positions" ? <PositionsView /> : <JournalView />}
    </div>
  );
}
