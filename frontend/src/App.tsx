import { Link, NavLink, Route, Routes } from "react-router-dom";
import { RegimeHeader } from "./components/RegimeHeader";
import { useDashboardState } from "./state/DashboardStateContext";
import { HomeView } from "./views/HomeView";
import { ScanView } from "./views/ScanView";
import { FreeRangeView } from "./views/FreeRangeView";
import { KillSheetView } from "./views/KillSheetView";
import { CryptoView } from "./views/CryptoView";
import { LottoView } from "./views/LottoView";
import { WeeklyTrendView } from "./views/WeeklyTrendView";
import { PositionsView } from "./views/PositionsView";
import { JournalView } from "./views/JournalView";
import { PyramidView } from "./views/PyramidView";
import { SundayScanRetroView } from "./views/SundayScanRetroView";
import { SundayScanView } from "./views/SundayScanView";
import { WeeklyReviewView } from "./views/WeeklyReviewView";

function navClass({ isActive }: { isActive: boolean }): string {
  return `px-3 py-1.5 rounded text-sm transition ${
    isActive
      ? "bg-signal-info/20 text-signal-info border border-signal-info/40"
      : "text-text-secondary hover:text-text-primary"
  }`;
}

function fmtUsd(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function StageBanner() {
  const { state, error } = useDashboardState();
  if (error || !state) {
    // Static fallback while loading or on fetch error — never silently
    // misrepresent stage based on stale cache.
    return (
      <span className="ml-auto text-xs text-signal-flag/80 px-3">
        Stage 1 — Discipline &gt; P&amp;L until $100K
      </span>
    );
  }
  const isStage1 = state.stage === "stage_1";
  const cls = isStage1 ? "text-signal-flag/80" : "text-signal-bull/80";
  const balance = fmtUsd(state.account_balance_usd);
  const threshold = fmtUsd(state.threshold_usd);
  const subtext = isStage1
    ? `${balance} of ${threshold} (Discipline > P&L)`
    : `${balance} — discipline floor maintained`;
  return (
    <span className={`ml-auto text-xs ${cls} px-3 flex items-center gap-2`}>
      <span className="font-semibold">
        {isStage1 ? "Stage 1" : "Stage 2"}
      </span>
      <span className="opacity-80">— {subtext}</span>
    </span>
  );
}

export function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <RegimeHeader />
      <nav className="px-4 py-2 border-b border-bg-border bg-bg-base flex items-center gap-1">
        <Link to="/" className="font-semibold text-text-primary mr-4">
          Trading Dashboard
        </Link>
        <NavLink to="/" end className={navClass}>Home</NavLink>
        <NavLink to="/scan" className={navClass}>Scan</NavLink>
        <NavLink to="/free-range" className={navClass}>Free-Range</NavLink>
        <NavLink to="/weekly" className={navClass}>Weekly</NavLink>
        <NavLink to="/crypto" className={navClass}>Crypto</NavLink>
        <NavLink to="/focus" className={navClass}>Focus</NavLink>
        <NavLink to="/kill-sheet" className={navClass}>Kill Sheet</NavLink>
        <NavLink to="/lotto" className={navClass}>Lotto</NavLink>
        <NavLink to="/pyramid" className={navClass}>Pyramid</NavLink>
        <NavLink to="/positions" className={navClass}>Positions</NavLink>
        <NavLink to="/journal" className={navClass}>Journal</NavLink>
        <NavLink to="/weekly-review" className={navClass}>Weekly Review</NavLink>
        <StageBanner />
      </nav>
      <main className="flex-1">
        <Routes>
          <Route path="/" element={<HomeView />} />
          <Route path="/scan" element={<ScanView />} />
          <Route path="/free-range" element={<FreeRangeView />} />
          <Route path="/weekly" element={<WeeklyTrendView />} />
          <Route path="/crypto" element={<CryptoView />} />
          <Route path="/focus" element={<SundayScanView />} />
          <Route path="/focus/:date" element={<SundayScanRetroView />} />
          <Route path="/kill-sheet" element={<KillSheetView />} />
          <Route path="/lotto" element={<LottoView />} />
          <Route path="/pyramid" element={<PyramidView />} />
          <Route path="/weekly-review" element={<WeeklyReviewView />} />
          <Route path="/positions" element={<PositionsView />} />
          <Route path="/journal" element={<JournalView />} />
        </Routes>
      </main>
    </div>
  );
}
