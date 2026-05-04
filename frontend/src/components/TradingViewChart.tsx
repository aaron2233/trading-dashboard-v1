import { useMemo, useState } from "react";
import { tradingViewEmbedUrl } from "../lib/tradingview";

interface TradingViewChartProps {
  ticker: string;
  timeframe?: string;     // dashboard form: 1wk / 1d / 4h / 2h
  height?: number;
  /** When true, render a "Show chart" toggle and only mount the iframe on click. */
  collapsedByDefault?: boolean;
  title?: string;
}

/**
 * Embed TradingView's Advanced Chart widget.
 *
 * Lazy-mounted by default — TV widgets are heavy (loads their entire chart
 * library into the iframe) so views with many charts (per-row/per-card) should
 * leave them collapsed and let the user click to expand.
 *
 * Symbol resolution + interval mapping live in lib/tradingview.ts so the
 * widget URL stays consistent across views.
 */
export function TradingViewChart({
  ticker,
  timeframe = "1d",
  height = 480,
  collapsedByDefault = false,
  title,
}: TradingViewChartProps) {
  const [expanded, setExpanded] = useState(!collapsedByDefault);
  const url = useMemo(
    () => tradingViewEmbedUrl(ticker, timeframe),
    [ticker, timeframe],
  );

  const headerLabel = title ?? `Chart — ${ticker.toUpperCase()} (${timeframe.toUpperCase()})`;

  return (
    <div className="panel">
      <div className="panel-header flex items-center justify-between">
        <span>{headerLabel}</span>
        <div className="flex items-center gap-2">
          <a
            href={`https://www.tradingview.com/chart/?symbol=${encodeURIComponent(
              ticker.toUpperCase(),
            )}`}
            target="_blank"
            rel="noopener noreferrer"
            className="text-xs text-text-secondary underline"
          >
            open in TradingView ↗
          </a>
          {collapsedByDefault && (
            <button
              type="button"
              className="btn text-xs"
              onClick={() => setExpanded((v) => !v)}
            >
              {expanded ? "Hide" : "Show chart"}
            </button>
          )}
        </div>
      </div>
      {expanded ? (
        <div className="panel-body p-0" style={{ height }}>
          <iframe
            key={url}
            src={url}
            title={headerLabel}
            className="w-full h-full border-0"
            allow="clipboard-write"
          />
        </div>
      ) : (
        <div className="panel-body text-xs text-text-secondary">
          Click "Show chart" to load the TradingView embed (heavy iframe).
        </div>
      )}
    </div>
  );
}
