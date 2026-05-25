import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type {
  StrikeCandidate,
  StrikeDirection,
  StrikeSuggestionsResult,
} from "../../api/types";

type DirectionFilter = "call" | "put" | "both";

function killSheetLink(
  ticker: string,
  direction: StrikeDirection,
  strike: number,
): string {
  // Pre-fills the kill-sheet form. account=lotto + intent=SCALP +
  // trigger_tf=2H matches the lotto playbook defaults the LottoView's
  // ActionableCandidateCard uses for full setups; here the user has
  // already chosen the strike via the panel.
  const params = new URLSearchParams({
    ticker,
    direction: direction === "call" ? "long" : "short",
    contract_type: direction,
    strike: String(strike),
    account: "lotto",
    intent: "SCALP",
    trigger_tf: "2H",
    conviction: "high",
  });
  return `/kill-sheet?${params.toString()}`;
}

function StrikeRow({
  ticker,
  candidate,
}: {
  ticker: string;
  candidate: StrikeCandidate;
}) {
  const sign = candidate.distance_usd >= 0 ? "+" : "−";
  return (
    <Link
      to={killSheetLink(ticker, candidate.direction, candidate.strike)}
      className="flex items-center justify-between text-xs px-2 py-1.5 rounded hover:bg-bg-elevated transition group"
    >
      <span className="flex items-center gap-2">
        <span className="font-mono font-semibold">${candidate.strike.toFixed(2)}</span>
        <span className="text-text-secondary">{candidate.moneyness}</span>
      </span>
      <span className="flex items-center gap-2 text-text-muted">
        <span className="font-mono">
          {sign}${Math.abs(candidate.distance_usd).toFixed(2)}
        </span>
        <span className="text-signal-flag opacity-0 group-hover:opacity-100 transition">
          → kill sheet
        </span>
      </span>
    </Link>
  );
}

function StrikeColumn({
  title,
  ticker,
  candidates,
}: {
  title: string;
  ticker: string;
  candidates: StrikeCandidate[];
}) {
  if (candidates.length === 0) return null;
  return (
    <div className="flex-1 min-w-[200px]">
      <h4 className="text-[11px] uppercase tracking-widest font-semibold text-text-secondary mb-1">
        {title}
      </h4>
      <div className="border border-bg-border rounded">
        {candidates.map((c) => (
          <StrikeRow key={c.strike} ticker={ticker} candidate={c} />
        ))}
      </div>
    </div>
  );
}

export function StrikeSuggestionsPanel() {
  const [ticker, setTicker] = useState("QQQ");
  const [pending, setPending] = useState("QQQ");
  const [direction, setDirection] = useState<DirectionFilter>("both");
  const [result, setResult] = useState<StrikeSuggestionsResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchStrikes = useCallback(
    async (t: string, dir: DirectionFilter) => {
      const trimmed = t.trim().toUpperCase();
      if (!trimmed) return;
      setLoading(true);
      setError(null);
      try {
        const data = await api.lottoStrikes(trimmed, dir);
        setResult(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        setResult(null);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setTicker(pending.toUpperCase());
    void fetchStrikes(pending, direction);
  };

  const onDirectionChange = (next: DirectionFilter) => {
    setDirection(next);
    if (result) {
      void fetchStrikes(ticker, next);
    }
  };

  return (
    <section className="panel mb-6">
      <header className="panel-header flex items-center justify-between flex-wrap gap-2">
        <span className="font-bold uppercase tracking-widest text-xs">
          Strike Suggestions
        </span>
        <span className="text-[10px] text-text-muted">
          Pick a strike → routes to kill sheet with ticker + direction +
          strike pre-filled
        </span>
      </header>
      <div className="panel-body">
        <form onSubmit={onSubmit} className="flex items-end gap-2 flex-wrap mb-3">
          <label className="block">
            <span className="label">Ticker</span>
            <input
              className="input w-32"
              value={pending}
              onChange={(e) => setPending(e.target.value)}
              placeholder="QQQ"
            />
          </label>
          <div>
            <span className="label block">Direction</span>
            <div className="inline-flex border border-bg-border rounded overflow-hidden">
              {(["both", "call", "put"] as DirectionFilter[]).map((d) => (
                <button
                  key={d}
                  type="button"
                  className={`px-2 py-1 text-xs uppercase ${
                    direction === d
                      ? "bg-signal-flag/20 text-signal-flag"
                      : "text-text-secondary hover:text-text-primary"
                  }`}
                  onClick={() => onDirectionChange(d)}
                >
                  {d}
                </button>
              ))}
            </div>
          </div>
          <button
            type="submit"
            className="btn btn-primary text-xs"
            disabled={loading}
          >
            {loading ? "Fetching…" : "Suggest strikes"}
          </button>
        </form>

        {error && (
          <div className="text-xs text-signal-bear mb-2">{error}</div>
        )}

        {result && (
          <>
            <div className="text-xs text-text-secondary mb-2">
              <span className="font-mono font-semibold text-text-primary">
                {result.ticker}
              </span>{" "}
              spot{" "}
              <span className="font-mono">${result.spot.toFixed(2)}</span>{" "}
              <span className="text-text-muted">
                · close on {result.bar_date} · ${result.increment} grid
              </span>
            </div>
            <div className="flex gap-3 flex-wrap">
              <StrikeColumn
                title="Calls"
                ticker={result.ticker}
                candidates={result.calls}
              />
              <StrikeColumn
                title="Puts"
                ticker={result.ticker}
                candidates={result.puts}
              />
            </div>
            <p className="text-[10px] text-text-muted mt-2 leading-relaxed">
              Strikes only — premium / IV / delta come from your broker chain
              via paste at kill-sheet time. ⚠ $1 grid is the
              default; verify against the actual chain for tickers that use
              wider increments.
            </p>
          </>
        )}
      </div>
    </section>
  );
}
