import { RegimeHealthPanel } from "../components/RegimeHealthPanel";

/** Dedicated /regime-health route — always-expanded panel + room for the
 * Sprint 3 history sparklines below. */
export function RegimeHealthView() {
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Regime Health</h2>
      </div>
      <p className="page-subtitle text-sm">
        Leading-indicator monitor — surfaces macro cracks before SQN(100) flips
        structural regime. Tier 1+2 ship in Sprint 1; breadth + capex calendar +
        history sparklines arrive in Sprint 3.
      </p>
      <RegimeHealthPanel alwaysExpanded />
      <div className="text-xs text-text-muted mt-6 leading-relaxed">
        <p className="mb-2">
          ⚠ Threshold defaults are conservative-permissive starting values, not
          backtested against 2018/2020/2022 SPX drawdowns. Override via
          <code className="mx-1">~/.trading-dashboard/config.yaml</code>
          under <code>regime_health.thresholds</code> when you have a
          calibrated set.
        </p>
        <p>
          ⚠ FRED series IDs are pending verification until you register a free
          API key at fred.stlouisfed.org. Until then, Tier 2 indicators stay
          unknown — set <code>FRED_API_KEY</code> in your env to activate them.
        </p>
      </div>
    </div>
  );
}
