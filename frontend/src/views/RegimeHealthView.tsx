import { HistoryStrip } from "../components/regime/HistoryStrip";
import { RegimeHealthPanel } from "../components/RegimeHealthPanel";

/** Dedicated /regime-health route — always-expanded panel + 14-day history
 * strip. v2 will swap the strip for per-indicator sparklines. */
export function RegimeHealthView() {
  return (
    <div className="max-w-4xl mx-auto px-4 py-6">
      <div className="page-header-row">
        <h2 className="page-title">Regime Health</h2>
      </div>
      <p className="page-subtitle text-sm">
        Leading-indicator monitor — surfaces macro cracks before SQN(100) flips
        structural regime. All 4 tiers wired (Tier 4 capex pending YAML config).
      </p>
      <RegimeHealthPanel alwaysExpanded />

      <section className="panel mb-4">
        <header className="panel-header">
          <span className="font-bold uppercase tracking-widest text-xs">
            14-day history
          </span>
        </header>
        <div className="panel-body">
          <HistoryStrip days={14} />
        </div>
      </section>

      <div className="text-xs text-text-muted mt-6 leading-relaxed">
        <p className="mb-2">
          ⚠ Threshold defaults are conservative-permissive starting values, not
          backtested against 2018/2020/2022 SPX drawdowns. Override via
          <code className="mx-1">~/.trading-dashboard/config.yaml</code>
          under <code>regime_health.thresholds</code> when you have a
          calibrated set.
        </p>
        <p className="mb-2">
          ⚠ FRED series IDs are pending verification until you register a free
          API key at fred.stlouisfed.org. Until then, Tier 2 indicators stay
          unknown — set <code>FRED_API_KEY</code> in your env to activate them.
        </p>
        <p>
          ⓘ Tier 4 capex calendar reads <code>regime_health.capex</code> from
          the same config file. Add <code>tickers</code>, <code>directions</code>,
          and <code>next_prints</code> blocks to populate.
        </p>
      </div>
    </div>
  );
}
