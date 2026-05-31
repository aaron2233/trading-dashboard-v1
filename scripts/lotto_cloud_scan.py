"""Standalone cloud lotto scan → structured result, rendered to Markdown or JSON.

Designed to run in a scheduled cloud environment with NO local
~/.trading-dashboard files. Config falls back to the baked-in defaults in
config/loader.py (load_config() merges an absent config.yaml onto defaults),
so the LOTTO account ($1K, $150/trade cap, -70% cut) is available with no
config file.

All scan / kill-sheet / formatting logic lives here ("fat script, thin
agent"). `run_scan()` computes one structured result dict (the contract);
Markdown and JSON are both renderers of it, so they never drift and the
expensive kill-sheet gate runs once per trade.

STDOUT CONTRACT — default (Markdown). The legacy routine keys off these:
  - Normal, ≥1 actionable trade → full Markdown starting "# Lotto Scan".
  - "market closed or no fresh 2H bar ... skipping"  (single line)
  - "no actionable setups this window"               (single line)
  - "DATA FETCH FAILED ..."                          (single line)

STDOUT CONTRACT — `--json`. One JSON object; consumers key off `status`:
  - status: "ok" | "skipped" | "data_failed" | "no_setups"
  - always: generated_at (ISO), timestamp_label, trigger_tf, dte_band,
            universe, universe_count, trades (list)
  - status "ok" also: window_close, actionable_count, and one entry per trade
            in `trades` (structured: numeric spot/target/stop, strike object
            or ladder, why_now, options target/cut pcts).
  - non-"ok" statuses carry a human `message` and an empty `trades` list.
Nothing is written to disk in either mode.

Reuses, verbatim:
  - lotto.scan_lotto_watchlist          (src/lotto/scanner.py:157)
  - kill_sheet.builder.build_standard   (src/kill_sheet/builder.py:190)
  - scan.scan_ticker / populate_trigger_bar (src/scan.py)
  - lotto.suggest_strikes               (src/lotto/strikes.py:88)
  - config.load_config                  (src/config/loader.py:182)

Run locally from repo root:
    PYTHONPATH=src .venv/bin/python scripts/lotto_cloud_scan.py
    PYTHONPATH=src .venv/bin/python scripts/lotto_cloud_scan.py --json
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

# Make src/ importable when run as a bare script in any environment.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

warnings.filterwarnings("ignore")  # silence yfinance / pandas chatter on stdout

from config import load_config  # noqa: E402
from kill_sheet.builder import build_standard  # noqa: E402
from lotto import scan_lotto_watchlist, suggest_strikes  # noqa: E402
from scan import populate_trigger_bar, scan_ticker  # noqa: E402

PT = ZoneInfo("America/Los_Angeles")
ET = ZoneInfo("America/New_York")
EXCHANGE_TZ = ET  # yfinance returns naive timestamps in US/Eastern (exchange time)

# NASDAQ-100 top 50 by market cap (yfinance fast_info snapshot 2026-05-28;
# ANSS excluded — no cap returned, mid-acquisition). Trimmed from the full
# 100 to halve the per-run yfinance load and cut datacenter rate-limit /
# timeout risk on the cloud routine. Re-rank with scripts/ rank logic if the
# membership drifts materially.
NASDAQ_50 = [
    "AAPL", "ADBE", "ADI", "ADP", "AMAT", "AMD", "AMGN", "AMZN", "ARM", "ASML",
    "AVGO", "AZN", "BKNG", "CDNS", "CEG", "CMCSA", "COST", "CRWD", "CSCO", "CSX",
    "FTNT", "GILD", "GOOG", "GOOGL", "HON", "INTC", "INTU", "ISRG", "KLAC", "LIN",
    "LRCX", "MAR", "MELI", "META", "MNST", "MRVL", "MSFT", "MU", "NFLX", "NVDA",
    "PANW", "PDD", "PEP", "QCOM", "SBUX", "SNPS", "TMUS", "TSLA", "TXN", "VRTX",
]
GUARD_TICKER = "QQQ"  # liquid proxy used only to read the latest 2H bar timestamp
LOTTO_TARGET_PCT = 200  # lotto standard: +200% premium target (skill spec)
UNIVERSE_LABEL = "NASDAQ top 50"


# ─── Fresh-bar / trading-session guard ──────────────────────────────────────
def latest_2h_bar_time() -> datetime | None:
    """Latest 2H bar timestamp (tz-aware, exchange tz) for the guard ticker,
    or None if bars can't be fetched."""
    try:
        from scan import load_bars
        bars = load_bars(GUARD_TICKER, interval="2h")
    except Exception:
        return None
    if bars.empty:
        return None
    ts = bars.index[-1]
    py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
    if py.tzinfo is None:
        py = py.replace(tzinfo=EXCHANGE_TZ)
    return py


def is_fresh_session(bar_time: datetime | None, now_et: datetime) -> bool:
    """A 2H bar is "fresh" if it belongs to the current US trading session:
    same calendar date as `now_et`, on a weekday, after the 09:30 ET open.
    Holiday-proof: on a holiday yfinance's latest bar carries the prior
    session's date, so the date comparison fails — no hardcoded calendar."""
    if bar_time is None:
        return False
    if now_et.weekday() >= 5:           # Sat/Sun
        return False
    if now_et.time() < time(9, 30):     # before today's open → no fresh bar yet
        return False
    return bar_time.astimezone(EXCHANGE_TZ).date() == now_et.date()


# ─── Structured trade (the per-trade contract) ───────────────────────────────
def _f(x) -> float | None:
    """Coerce pandas/numpy numerics to native float for JSON; pass None through."""
    return None if x is None else float(x)


def build_trade(setup, account) -> dict | None:
    """Structured, actionable trade dict — the single source of truth that both
    renderers consume. Returns None if the discipline gate REJECTS the trade
    (counter-regime without a divergence thesis): not actionable, so dropped.

    Numbers are native floats (JSON-serializable). Strike is either a
    `suggested_strike` object (0.20Δ BS-derived) or a `strike_ladder`
    (ATM/OTM fallback when HV was unavailable) — never both."""
    kind = "call" if setup.direction == "long" else "put"
    try:
        scan_row = populate_trigger_bar(scan_ticker(setup.ticker, timeframe="1d"),
                                        setup.ticker, "2H")
        ks = build_standard(scan_row, direction=setup.direction, account=account,
                            account_key="lotto", intent="SCALP", trigger_tf="2H",
                            risk_conviction="default", skill="lotto-options")
    except Exception:
        ks = None  # fall through to scan-only fields rather than drop the trade

    if ks is not None and ks.status == "REJECTED":
        return None  # discipline gate rejects → not actionable

    suggested_strike = None
    strike_ladder = None
    if setup.suggested_strike is not None:
        suggested_strike = {"value": _f(setup.suggested_strike),
                            "dte": setup.suggested_dte,
                            "basis": "0.20Δ BS-derived"}
    elif setup.close:
        sug = suggest_strikes(setup.close, direction=kind, ticker=setup.ticker,
                              bar_date=setup.bar_date)
        cand = sug.calls if kind == "call" else sug.puts
        strike_ladder = [{"moneyness": c.moneyness, "strike": _f(c.strike)}
                         for c in cand]

    return {
        "ticker": setup.ticker,
        "direction": setup.direction,
        "kind": kind,
        "spot": _f(setup.close) if setup.close else None,
        "suggested_strike": suggested_strike,
        "strike_ladder": strike_ladder,
        "stock_target": _f(setup.target_price),
        "stock_stop": _f(setup.stop_price),
        "options_target_pct": LOTTO_TARGET_PCT,
        "options_cut_pct": _f(account.raw.get("cut_rule_pct")),  # e.g. -0.70
        "why_now": setup.why_now,
    }


# ─── Result computation (the contract) ───────────────────────────────────────
def run_scan() -> dict:
    """Compute the structured scan result. No printing, no disk writes.
    `status` discriminates the four outcomes; renderers dispatch on it."""
    now_pt = datetime.now(PT)
    now_et = now_pt.astimezone(ET)
    base = {
        "generated_at": now_pt.isoformat(),
        "timestamp_label": now_pt.strftime("%Y-%m-%d %H:%M %Z"),
        "trigger_tf": "2H",
        "dte_band": "0-14 DTE",
        "universe": UNIVERSE_LABEL,
        "universe_count": len(NASDAQ_50),
    }

    # 1. Fresh-bar / trading-session guard (cheap single-ticker fetch first).
    bar_time = latest_2h_bar_time()
    if not is_fresh_session(bar_time, now_et):
        bar_str = (bar_time.astimezone(PT).strftime("%Y-%m-%d %H:%M %Z")
                   if bar_time else "no bar")
        return {"status": "skipped", **base, "trades": [],
                "message": f"market closed or no fresh 2H bar "
                           f"(latest 2H bar: {bar_str}), skipping."}
    base["window_close"] = bar_time.astimezone(PT).strftime("%H:%M %Z")

    # 2. Scan the NASDAQ-100 top 50. Scanner applies v2 gates (G2/G3) + price
    #    band internally; actionable = verdict "buy".
    result = scan_lotto_watchlist(tickers=NASDAQ_50)

    # 3. Distinguish a data blackout from a genuine no-setups result, so a
    #    yfinance rate-limit is never silently mistaken for "no trades".
    scanned_tickers = {s.ticker for s in result.setups}
    if not scanned_tickers or len(result.errors) > len(scanned_tickers):
        return {"status": "data_failed", **base, "trades": [],
                "message": f"DATA FETCH FAILED ({len(result.errors)} tickers errored, "
                           f"{len(scanned_tickers)} ok) — likely datacenter "
                           f"rate-limit; no scan produced."}

    config = load_config()              # baked-in defaults when no config.yaml
    account = config.account("lotto")

    # 4. Build a structured trade per actionable setup; drop discipline-rejected.
    trades = [t for s in result.actionable_setups
              if (t := build_trade(s, account)) is not None]
    if not trades:
        return {"status": "no_setups", **base, "trades": [],
                "message": "no actionable setups this window."}

    return {"status": "ok", **base,
            "actionable_count": len(trades), "trades": trades}


# ─── Markdown renderer ───────────────────────────────────────────────────────
def _strike_md(t: dict) -> str:
    s = t["suggested_strike"]
    if s is not None:
        return f"${s['value']:g}  (~0.20Δ, {s['dte']})"
    if t["strike_ladder"]:
        ladder = " · ".join(f"{c['moneyness']} ${c['strike']:g}"
                            for c in t["strike_ladder"])
        return f"verify ~0.20Δ on chain — ladder: {ladder}"
    return "n/a"


def trade_to_markdown(t: dict) -> str:
    """Compact, actionable SparkNotes block for one structured trade."""
    cut_pct = t["options_cut_pct"]
    cut_label = f"{abs(cut_pct) * 100:.0f}%" if cut_pct is not None else "70%"
    opt_floor = (f"≈{1 + cut_pct:.2f}× entry premium"
                 if cut_pct is not None else "≈0.30× entry")

    tgt = f"${t['stock_target']:g}" if t["stock_target"] is not None else "n/a"
    stop = f"${t['stock_stop']:g}" if t["stock_stop"] is not None else "n/a"
    spot = f"${t['spot']:g}" if t["spot"] else "n/a"

    return "\n".join([
        f"### {t['ticker']} — {t['kind'].upper()}  ·  spot {spot}",
        f"- **Recommended strike:** {_strike_md(t)}",
        f"- **Stock target:** {tgt}  ·  **stock invalidation (stop):** {stop}",
        f"- **Options target:** +{t['options_target_pct']}% "
        f"(≈{1 + t['options_target_pct']/100:g}× entry premium)",
        f"- **Options invalidation:** −{cut_label} of entry premium "
        f"({opt_floor}) — hard cut",
        f"- **Why now:** {t['why_now']}",
        "",
        "_Premium / IV / delta: ⚠️ verify on broker chain before entry._",
        "",
    ])


def render_markdown(res: dict) -> str:
    """Render the structured result to the Markdown stdout contract."""
    ts = res["timestamp_label"]
    if res["status"] != "ok":
        return f"LOTTO SCAN — {ts}: {res['message']}"

    out = [
        f"# Lotto Scan — {ts}",
        f"_2H window close: {res['window_close']} · {res['universe']} · 2H trigger · "
        f"{res['dte_band']} · long calls/puts only · {res['actionable_count']} actionable_",
        "",
        "> ⚠️ Cloud scan — blind to your open positions / concurrent caps / R1 "
        "balance. Verify against your book before entering.",
        "",
    ]
    out.extend(trade_to_markdown(t) for t in res["trades"])
    return "\n".join(out)


# ─── Main ───────────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    res = run_scan()
    if "--json" in argv:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(render_markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
