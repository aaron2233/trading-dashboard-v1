import { useEffect, useRef, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { fmtUsdWhole } from "./lib/format";
import { RegimeHeader } from "./components/RegimeHeader";
import { StatusBar } from "./components/StatusBar";
import { ThemeToggle } from "./components/ThemeToggle";
import { useDashboardState } from "./state/DashboardStateContext";
import { HomeView } from "./views/HomeView";
import { ScanView } from "./views/ScanView";
import { KillSheetView } from "./views/KillSheetView";
import { LottoView } from "./views/LottoView";
import { WeeklyTrendView } from "./views/WeeklyTrendView";
import { IndexSwingView } from "./views/IndexSwingView";
import { BookView } from "./views/BookView";
import { CoreView } from "./views/CoreView";
import { RegimeHealthView } from "./views/RegimeHealthView";
import { WeeklyReviewView } from "./views/WeeklyReviewView";

type NavItem =
  | { kind: "link"; to: string; label: string }
  | { kind: "divider" };

interface NavGroupDef {
  label: string;
  items: NavItem[];
}

// The three strategies of the book — one scan entry per strategy, ordered by
// horizon (short → long). Nothing else scans: utility readouts live under
// the Regime group, per the 3-strategy consolidation.
const SCAN_GROUP: NavGroupDef = {
  label: "Scan",
  items: [
    { kind: "link", to: "/lotto", label: "Lotto · $1K playbook" },
    { kind: "link", to: "/index-swing", label: "Index swing" },
    { kind: "link", to: "/core", label: "Core · QQQM" },
  ],
};

// Regime lens + diagnostics: weekly-trend is the bias lens (no entries) and
// Scan ticker is the single-ticker unified-stack readout.
const REGIME_GROUP: NavGroupDef = {
  label: "Regime",
  items: [
    { kind: "link", to: "/regime-health", label: "Regime health" },
    { kind: "link", to: "/weekly", label: "Weekly trend · lens" },
    { kind: "link", to: "/scan", label: "Scan ticker" },
  ],
};

const TOP_LEVEL_LINKS: { to: string; label: string }[] = [
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

// Book (Positions + Journal as tabs) — active for either tab and for the
// kill-sheet flow launched from it. Kill Sheet left the nav 2026-07-12: it's
// a gate inside the open-position flow, not a standalone destination (the
// /kill-sheet route still works for deep links and scan-card pre-fills).
function BookNavLink() {
  const location = useLocation();
  const active = ["/positions", "/journal", "/kill-sheet"].some((p) =>
    location.pathname.startsWith(p),
  );
  return (
    <NavLink to="/positions" className={() => topLevelClass(active)}>
      Book
    </NavLink>
  );
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
  const balance = fmtUsdWhole(state.account_balance_usd);
  const threshold = fmtUsdWhole(state.threshold_usd);
  const subtext = isStage1
    ? `${balance} / ${threshold}`
    : `${balance} · floor`;
  const pnl = state.realized_pnl_usd;
  const pnlCls = pnl >= 0 ? "text-signal-bull" : "text-signal-bear";
  const pnlText = `${pnl >= 0 ? "+" : "−"}${fmtUsdWhole(Math.abs(pnl))}`;
  return (
    <span
      className={`ml-auto sticker ${cls}`}
      title="Options book only — broker balance anchor + realized P&L since anchor date. Other accounts/sleeves: Home → Accounts panel."
    >
      <span>{isStage1 ? "STAGE_1" : "STAGE_2"}</span>
      <span className="opacity-80">· BOOK {subtext} ·</span>
      <span className={pnlCls}>{pnlText}</span>
    </span>
  );
}

export function App() {
  // Disable mouse-wheel value-stepping on focused <input type="number">.
  // Chromium/Firefox both interpret wheel-while-focused as a stepper input;
  // that hijacks page scroll AND silently mutates the field. We blur the
  // input on wheel so the page scrolls and the value stays put.
  useEffect(() => {
    const handler = () => {
      const active = document.activeElement;
      if (
        active instanceof HTMLInputElement &&
        active.type === "number"
      ) {
        active.blur();
      }
    };
    document.addEventListener("wheel", handler);
    return () => document.removeEventListener("wheel", handler);
  }, []);

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
        <NavGroup group={REGIME_GROUP} />
        <BookNavLink />
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
          <Route path="/index-swing" element={<IndexSwingView />} />
          <Route path="/kill-sheet" element={<KillSheetView />} />
          <Route path="/lotto" element={<LottoView />} />
          <Route path="/core" element={<CoreView />} />
          <Route path="/regime-health" element={<RegimeHealthView />} />
          <Route path="/weekly-review" element={<WeeklyReviewView />} />
          <Route path="/positions" element={<BookView />} />
          <Route path="/journal" element={<BookView />} />
        </Routes>
      </main>
      <StatusBar />
    </div>
  );
}
