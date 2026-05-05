import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { RegimeHeader } from "./components/RegimeHeader";
import { StatusBar } from "./components/StatusBar";
import { ThemeToggle } from "./components/ThemeToggle";
import { useDashboardState } from "./state/DashboardStateContext";
import { HomeView } from "./views/HomeView";
import { ScanView } from "./views/ScanView";
import { KillSheetView } from "./views/KillSheetView";
import { LottoView } from "./views/LottoView";
import { WeeklyTrendView } from "./views/WeeklyTrendView";
import { PositionsView } from "./views/PositionsView";
import { JournalView } from "./views/JournalView";
import { PyramidView } from "./views/PyramidView";
import { SundayScanRetroView } from "./views/SundayScanRetroView";
import { SundayScanView } from "./views/SundayScanView";
import { WeeklyReviewView } from "./views/WeeklyReviewView";

type NavItem =
  | { kind: "link"; to: string; label: string }
  | { kind: "divider" };

interface NavGroupDef {
  label: string;
  items: NavItem[];
}

// Single Scan dropdown groups every "find me a setup" tool. General-purpose
// scanners come first; the divider separates them from playbook-specific
// dashboards (Lotto, Pyramid) which also output setups but only for their
// own account/strategy. Kill sheet, Positions, Journal, Weekly review live
// at the top level — each is one click away.
const SCAN_GROUP: NavGroupDef = {
  label: "Scan",
  items: [
    { kind: "link", to: "/scan", label: "Scan ticker" },
    { kind: "link", to: "/weekly", label: "Weekly trend" },
    { kind: "link", to: "/focus", label: "Sunday focus" },
    { kind: "divider" },
    { kind: "link", to: "/lotto", label: "Lotto · $1K playbook" },
    { kind: "link", to: "/pyramid", label: "Pyramid · trend tranches" },
  ],
};

const TOP_LEVEL_LINKS: { to: string; label: string }[] = [
  { to: "/kill-sheet", label: "Kill Sheet" },
  { to: "/positions", label: "Positions" },
  { to: "/journal", label: "Journal" },
  { to: "/weekly-review", label: "Weekly Review" },
];

function navItemClass({ isActive }: { isActive: boolean }): string {
  return `block px-3 py-1.5 text-[11px] uppercase font-semibold tracking-wider transition ${
    isActive
      ? "bg-signal-flag/15 text-signal-flag"
      : "text-text-secondary hover:text-signal-flag hover:bg-bg-elevated"
  }`;
}

function topLevelClass(active: boolean): string {
  return `px-3 py-1.5 text-[11px] uppercase font-semibold tracking-wider transition flex items-center gap-1.5 border ${
    active
      ? "border-signal-flag text-signal-flag bg-signal-flag/10"
      : "border-transparent text-text-secondary hover:text-signal-flag hover:border-bg-border"
  }`;
}

function NavGroup({ group }: { group: NavGroupDef }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const location = useLocation();
  const linkTos = group.items.flatMap((i) => (i.kind === "link" ? [i.to] : []));
  const isActive = linkTos.some((to) => location.pathname.startsWith(to));

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  // Auto-close on route change
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        className={topLevelClass(isActive)}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <span>{group.label}</span>
        <span className="text-[10px] opacity-70">▾</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute left-0 mt-1 z-20 min-w-[14rem] bg-bg-base border-2 border-signal-flag/50 py-1"
        >
          {group.items.map((item, i) =>
            item.kind === "divider" ? (
              <div
                key={`d-${i}`}
                className="my-1 mx-2 border-t border-bg-border"
                role="separator"
              />
            ) : (
              <NavLink
                key={item.to}
                to={item.to}
                className={navItemClass}
                role="menuitem"
              >
                {item.label}
              </NavLink>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function fmtUsd(n: number): string {
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

// Brand wordmark — single-color amber for visual consistency with the
// rest of the app's primary-accent treatment.
function BrandMark() {
  return (
    <Link to="/" className="brand-mark mr-3" aria-label="Home">
      <span className="brand-prefix">//&nbsp;</span>
      <span className="text-signal-flag">TRADING-DASHBOARD</span>
      <span className="brand-suffix">::&nbsp;V0.1</span>
    </Link>
  );
}

function StageBanner() {
  const { state, error } = useDashboardState();
  if (error || !state) {
    return (
      <span className="ml-auto sticker text-signal-flag">
        STAGE_1 · DISCIPLINE &gt; P&amp;L
      </span>
    );
  }
  const isStage1 = state.stage === "stage_1";
  const cls = isStage1 ? "text-signal-flag" : "text-signal-bull";
  const balance = fmtUsd(state.account_balance_usd);
  const threshold = fmtUsd(state.threshold_usd);
  const subtext = isStage1
    ? `${balance} / ${threshold}`
    : `${balance} · floor`;
  return (
    <span className={`ml-auto sticker ${cls}`}>
      <span>{isStage1 ? "STAGE_1" : "STAGE_2"}</span>
      <span className="opacity-80">· {subtext}</span>
    </span>
  );
}

export function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <RegimeHeader />
      <nav className="px-4 py-2 border-b border-bg-border bg-bg-base flex items-center gap-2">
        <BrandMark />
        <NavLink
          to="/"
          end
          className={({ isActive }) => topLevelClass(isActive)}
        >
          Home
        </NavLink>
        <NavGroup group={SCAN_GROUP} />
        {TOP_LEVEL_LINKS.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            className={({ isActive }) => topLevelClass(isActive)}
          >
            {l.label}
          </NavLink>
        ))}
        <StageBanner />
        <ThemeToggle />
      </nav>
      <main className="flex-1 app-main">
        <Routes>
          <Route path="/" element={<HomeView />} />
          <Route path="/scan" element={<ScanView />} />
          <Route path="/weekly" element={<WeeklyTrendView />} />
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
      <StatusBar />
    </div>
  );
}
