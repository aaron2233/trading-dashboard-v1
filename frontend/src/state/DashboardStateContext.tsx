import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api/client";
import type { DashboardState } from "../api/types";

interface DashboardStateContextValue {
  state: DashboardState | null;
  loading: boolean;
  error: string | null;
  refresh: () => Promise<void>;
}

const DashboardStateContext = createContext<DashboardStateContextValue | undefined>(undefined);

export function DashboardStateProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<DashboardState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const next = await api.dashboardState();
      setState(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <DashboardStateContext.Provider value={{ state, loading, error, refresh }}>
      {children}
    </DashboardStateContext.Provider>
  );
}

export function useDashboardState(): DashboardStateContextValue {
  const ctx = useContext(DashboardStateContext);
  if (!ctx) {
    throw new Error("useDashboardState must be used inside <DashboardStateProvider>");
  }
  return ctx;
}
