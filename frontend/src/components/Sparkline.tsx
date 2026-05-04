import { useEffect, useMemo, useRef, useState } from "react";
import { createChart, LineSeries, type IChartApi } from "lightweight-charts";
import { api } from "../api/client";


interface SparklineProps {
  ticker: string;
  timeframe?: string;     // 1d / 1wk / 4h / 2h
  count?: number;
  width?: number;
  height?: number;
  /** Color override. Defaults to neutral; we recolor green/red based on net change. */
  color?: string;
}

/**
 * Compact close-only mini-chart (Lightweight Charts v5).
 *
 * Fetches /api/v1/sparkline/{ticker}?timeframe&count and renders a single
 * line series. Auto-greens / auto-reds based on net price change over the
 * window unless a `color` is passed. Disposes the chart on unmount.
 *
 * No axis labels, no grid — meant to embed inside a card or table cell.
 */
export function Sparkline({
  ticker,
  timeframe = "1d",
  count = 30,
  width = 160,
  height = 40,
  color,
}: SparklineProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [data, setData] = useState<{ time: string; value: number }[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Fetch
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);
    fetch(
      `${import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000"}/api/v1/sparkline/${encodeURIComponent(ticker)}?timeframe=${timeframe}&count=${count}`,
    )
      .then(async (res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then((body) => {
        if (cancelled) return;
        const points = body.dates.map((d: string, i: number) => ({
          time: d,
          value: body.closes[i],
        }));
        setData(points);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [ticker, timeframe, count]);

  // Pick line color from net change
  const lineColor = useMemo(() => {
    if (color) return color;
    if (!data || data.length < 2) return "#94a3b8"; // slate-400
    const first = data[0].value;
    const last = data[data.length - 1].value;
    if (last > first) return "#22c55e";  // green-500
    if (last < first) return "#ef4444";  // red-500
    return "#94a3b8";
  }, [data, color]);

  // Mount chart
  useEffect(() => {
    if (!containerRef.current || !data || data.length === 0) return;
    const chart = createChart(containerRef.current, {
      width,
      height,
      layout: {
        background: { color: "transparent" } as never,
        textColor: "#64748b",
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      timeScale: { visible: false },
      rightPriceScale: { visible: false },
      leftPriceScale: { visible: false },
      handleScroll: false,
      handleScale: false,
    });
    chartRef.current = chart;
    const series = chart.addSeries(LineSeries, {
      color: lineColor,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
    });
    series.setData(data);
    chart.timeScale().fitContent();

    return () => {
      chart.remove();
      chartRef.current = null;
    };
  }, [data, width, height, lineColor]);

  if (error) {
    return (
      <div
        style={{ width, height }}
        className="flex items-center justify-center text-[10px] text-text-muted"
        title={error}
      >
        no chart
      </div>
    );
  }
  if (!data) {
    return <div style={{ width, height }} className="bg-bg-border/30 animate-pulse rounded" />;
  }
  return <div ref={containerRef} style={{ width, height }} />;
}
