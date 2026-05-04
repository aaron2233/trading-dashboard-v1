/**
 * Map dashboard ticker symbols to TradingView's symbol format.
 *
 * Stocks: pass through unchanged — TradingView's widget auto-resolves the
 * primary US exchange for unambiguous symbols (AAPL → NASDAQ:AAPL, SPY →
 * AMEX:SPY, etc.).
 *
 * Crypto: dashboard uses Crypto.com's underscore form (BTC_USDT). TradingView
 * uses the concatenated form prefixed with the exchange or generic CRYPTO.
 * We use the CRYPTO: prefix so the chart isn't tied to a specific exchange's
 * book — it'll show the aggregated index when available.
 */

export function isCryptoSymbol(ticker: string): boolean {
  return ticker.includes("_");
}

export function toTradingViewSymbol(ticker: string): string {
  const t = ticker.toUpperCase();
  if (!isCryptoSymbol(t)) return t;
  // BTC_USDT → CRYPTO:BTCUSDT (drop underscore, keep quote currency)
  return `CRYPTO:${t.replace("_", "")}`;
}

/** Dashboard timeframe → TradingView interval code. */
export function toTradingViewInterval(timeframe: string): string {
  switch (timeframe.toLowerCase()) {
    case "1wk":
    case "weekly":
    case "w":
      return "W";
    case "1d":
    case "daily":
    case "d":
      return "D";
    case "4h":
      return "240";
    case "2h":
      return "120";
    case "1h":
      return "60";
    case "30m":
      return "30";
    case "15m":
      return "15";
    default:
      return "D";
  }
}

/**
 * Build the TradingView Advanced Chart embed URL.
 *
 * Docs: https://www.tradingview.com/widget-docs/widgets/charts/advanced-chart/
 * Free, no auth, dark theme matches the dashboard.
 */
export function tradingViewEmbedUrl(
  ticker: string,
  timeframe: string = "1d",
): string {
  const symbol = toTradingViewSymbol(ticker);
  const interval = toTradingViewInterval(timeframe);
  const params = new URLSearchParams({
    symbol,
    interval,
    theme: "dark",
    style: "1",       // candles
    timezone: "exchange",
    withdateranges: "1",
    hide_side_toolbar: "0",
    allow_symbol_change: "1",
    studies: "MASimple@tv-basicstudies,MASimple@tv-basicstudies,MASimple@tv-basicstudies,Stochastic@tv-basicstudies",
    locale: "en",
  });
  return `https://s.tradingview.com/widgetembed/?${params.toString()}`;
}
