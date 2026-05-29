"""Standalone cloud lotto scan → SparkNotes Markdown email-draft body on stdout.

Designed to run in a scheduled cloud environment with NO local
~/.trading-dashboard files. Config falls back to the baked-in defaults in
config/loader.py (load_config() merges an absent config.yaml onto defaults),
so the LOTTO account ($1K, $150/trade cap, -70% cut) is available with no
config file.

All scan / kill-sheet / formatting logic lives here ("fat script, thin
agent"). The scheduled routine just runs this and pipes stdout into a Gmail
draft. Nothing is written to disk.

STDOUT CONTRACT (the routine keys off these):
  - Normal, ≥1 actionable trade → full Markdown starting "# Lotto Scan".
        → routine DRAFTS it.
  - "market closed or no fresh 2H bar ... skipping"  (single line)
        → routine creates NO draft.
  - "no actionable setups this window"               (single line)
        → routine creates NO draft.
  - "DATA FETCH FAILED ..."                          (single line)
        → routine DRAFTS a short failure notice (so a yfinance blackout is
          never silently mistaken for "no trades").

Reuses, verbatim:
  - lotto.scan_lotto_watchlist          (src/lotto/scanner.py:157)
  - kill_sheet.builder.build_standard   (src/kill_sheet/builder.py:190)
  - scan.scan_ticker / populate_trigger_bar (src/scan.py)
  - lotto.suggest_strikes               (src/lotto/strikes.py:88)
  - config.load_config                  (src/config/loader.py:182)

Run locally from repo root:
    PYTHONPATH=src .venv/bin/python scripts/lotto_cloud_scan.py
"""
from __future__ import annotations

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

SCAN_UNIVERSE = ["nasdaq_100"]
GUARD_TICKER = "QQQ"  # liquid proxy used only to read the latest 2H bar timestamp
LOTTO_TARGET_PCT = 200  # lotto standard: +200% premium target (skill spec)


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


# ─── SparkNotes block per actionable trade ──────────────────────────────────
def recommended_strike(setup, kind: str) -> str:
    """The 0.20Δ BS-derived strike the scan already computed; falls back to
    the ATM/OTM strike ladder (pure math, no network) when HV was unavailable."""
    if setup.suggested_strike is not None:
        return f"${setup.suggested_strike:g}  (~0.20Δ, {setup.suggested_dte})"
    if setup.close:
        sug = suggest_strikes(setup.close, direction=kind, ticker=setup.ticker,
                              bar_date=setup.bar_date)
        cand = sug.calls if kind == "call" else sug.puts
        ladder = " · ".join(f"{c.moneyness} ${c.strike:g}" for c in cand)
        return f"verify ~0.20Δ on chain — ladder: {ladder}"
    return "n/a"


def render_sparknotes(setup, account) -> str | None:
    """Compact, actionable block. Returns None if the discipline gate REJECTS
    the trade (counter-regime without a divergence thesis) — those are not
    actionable, so they're dropped from the draft."""
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

    cut_pct = account.raw.get("cut_rule_pct")          # e.g. -0.70
    cut_label = f"{abs(cut_pct) * 100:.0f}%" if cut_pct is not None else "70%"
    opt_floor = f"≈{1 + cut_pct:.2f}× entry premium" if cut_pct is not None else "≈0.30× entry"

    tgt = f"${setup.target_price:g}" if setup.target_price is not None else "n/a"
    stop = f"${setup.stop_price:g}" if setup.stop_price is not None else "n/a"
    spot = f"${setup.close:g}" if setup.close else "n/a"

    return "\n".join([
        f"### {setup.ticker} — {kind.upper()}  ·  spot {spot}",
        f"- **Recommended strike:** {recommended_strike(setup, kind)}",
        f"- **Stock target:** {tgt}  ·  **stock invalidation (stop):** {stop}",
        f"- **Options target:** +{LOTTO_TARGET_PCT}% (≈{1 + LOTTO_TARGET_PCT/100:g}× entry premium)",
        f"- **Options invalidation:** −{cut_label} of entry premium ({opt_floor}) — hard cut",
        f"- **Why now:** {setup.why_now}",
        "",
        "_Premium / IV / delta: ⚠️ verify on broker chain before entry._",
        "",
    ])


# ─── Main ───────────────────────────────────────────────────────────────────
def main() -> int:
    now_pt = datetime.now(PT)
    now_et = now_pt.astimezone(ET)
    ts_label = now_pt.strftime("%Y-%m-%d %H:%M %Z")

    # 1. Fresh-bar / trading-session guard (cheap single-ticker fetch first).
    bar_time = latest_2h_bar_time()
    if not is_fresh_session(bar_time, now_et):
        bar_str = bar_time.astimezone(PT).strftime("%Y-%m-%d %H:%M %Z") if bar_time else "no bar"
        print(f"LOTTO SCAN — {ts_label}: market closed or no fresh 2H bar "
              f"(latest 2H bar: {bar_str}), skipping.")
        return 0
    window_label = bar_time.astimezone(PT).strftime("%H:%M %Z")

    # 2. Scan the NASDAQ 100. Scanner applies v2 gates (G2/G3) + price band
    #    internally; actionable = verdict "buy".
    result = scan_lotto_watchlist(universe=SCAN_UNIVERSE)

    # 3. Distinguish a data blackout from a genuine no-setups result, so a
    #    yfinance rate-limit is never silently mistaken for "no trades".
    scanned_tickers = {s.ticker for s in result.setups}
    if not scanned_tickers or len(result.errors) > len(scanned_tickers):
        print(f"LOTTO SCAN — {ts_label}: DATA FETCH FAILED "
              f"({len(result.errors)} tickers errored, {len(scanned_tickers)} ok) "
              f"— likely datacenter rate-limit; no scan produced.")
        return 0

    config = load_config()              # baked-in defaults when no config.yaml
    account = config.account("lotto")

    # 4. Build SparkNotes for each actionable trade; drop discipline-rejected.
    blocks = [b for s in result.actionable_setups
              if (b := render_sparknotes(s, account)) is not None]

    if not blocks:
        print(f"LOTTO SCAN — {ts_label}: no actionable setups this window.")
        return 0

    out = [
        f"# Lotto Scan — {ts_label}",
        f"_2H window close: {window_label} · NASDAQ 100 · 2H trigger · 0-14 DTE · "
        f"long calls/puts only · {len(blocks)} actionable_",
        "",
        "> ⚠️ Cloud scan — blind to your open positions / concurrent caps / R1 "
        "balance. Verify against your book before entering.",
        "",
    ]
    out.extend(blocks)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
