"""Standalone cloud lotto scan → Markdown email-draft body on stdout.

Designed to run in a scheduled cloud environment with NO local
~/.trading-dashboard files. Config falls back to the baked-in defaults in
config/loader.py (load_config() merges an absent config.yaml onto defaults),
so the LOTTO account ($1K, $150/trade cap) is available without a config file.

All scan / kill-sheet / formatting logic lives here ("fat script, thin
agent"). The scheduled routine just runs this and pipes stdout into a Gmail
draft. Nothing is written to disk.

Reuses, verbatim:
  - lotto.scan_lotto_watchlist          (src/lotto/scanner.py:157)
  - scan.populate_trigger_bar           (src/scan.py:168)  → 2H trigger bar
  - kill_sheet.builder.build_standard   (src/kill_sheet/builder.py:190)
  - lotto.suggest_strikes               (src/lotto/strikes.py:88)
  - config.load_config                  (src/config/loader.py:182)

Run locally from repo root:
    PYTHONPATH=src .venv/bin/python scripts/lotto_cloud_scan.py
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, time, timedelta, timezone
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
# yfinance returns naive timestamps in US/Eastern (exchange time).
EXCHANGE_TZ = ET

FREE_RANGE_UNIVERSE = ["nasdaq_100", "sp500_top_50", "russell_2000_top_50"]
# Representative liquid ticker used only to read the latest 2H bar timestamp
# for the fresh-bar / trading-session guard.
GUARD_TICKER = "QQQ"


# ─── Fresh-bar / trading-session guard ──────────────────────────────────────
def latest_2h_bar_time() -> datetime | None:
    """Return the latest 2H bar's timestamp (tz-aware, exchange tz) for the
    guard ticker, or None if bars can't be fetched."""
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
    same calendar date as `now_et`, on a weekday. Pre-open (before 9:30 ET)
    or weekend/holiday → not fresh (no bar will carry today's date).

    Holiday-proof: we never hardcode a calendar — on a holiday yfinance's
    latest bar carries the prior session's date, so the date comparison fails.
    """
    if bar_time is None:
        return False
    bar_et = bar_time.astimezone(EXCHANGE_TZ)
    if now_et.weekday() >= 5:  # Sat/Sun
        return False
    # Before today's open there is no fresh bar yet.
    if now_et.time() < time(9, 30):
        return False
    return bar_et.date() == now_et.date()


# ─── Regime summary ─────────────────────────────────────────────────────────
def regime_line(setup) -> str:
    sqn100 = f"{setup.sqn_100_value:+.2f}" if setup.sqn_100_value is not None else "n/a"
    sqn20 = f"{setup.sqn_20_value:+.2f}" if setup.sqn_20_value is not None else "n/a"
    return (
        f"SQN(100): **{setup.sqn_100_regime or 'n/a'}** ({sqn100}) · "
        f"SQN(20): **{setup.sqn_20_regime or 'n/a'}** ({sqn20})"
    )


def regime_summary(result) -> str:
    """One-line backdrop from QQQ + GLD baseline rows (long side, dedup)."""
    seen: dict[str, str] = {}
    for s in result.setups:
        if s.ticker in ("QQQ", "GLD") and s.ticker not in seen:
            seen[s.ticker] = regime_line(s)
    if not seen:
        return "_Regime backdrop unavailable._"
    return "\n".join(f"- **{t}** — {line}" for t, line in seen.items())


# ─── Per-setup kill-sheet block ─────────────────────────────────────────────
def render_setup_block(setup, account) -> str:
    direction = setup.direction  # "long" | "short"
    kind = "call" if direction == "long" else "put"

    # Build a daily scan_row + inject the 2H trigger bar, then a kill sheet.
    try:
        scan_row = scan_ticker(setup.ticker, timeframe="1d")
        scan_row = populate_trigger_bar(scan_row, setup.ticker, "2H")
        ks = build_standard(
            scan_row,
            direction=direction,
            account=account,
            account_key="lotto",
            intent="SCALP",
            trigger_tf="2H",
            risk_conviction="default",
            skill="lotto-options",
            scan_phase=("baseline" if setup.source_universe is None else "free_range"),
        )
    except Exception as exc:
        return f"### {setup.ticker} — {direction.upper()}\n\n⚠️ Kill-sheet build failed: {exc}\n"

    # Strike levels (prices only — premium/IV/delta intentionally absent).
    strikes_md = "_strike suggestion unavailable_"
    if setup.close:
        sug = suggest_strikes(
            setup.close, direction=kind, ticker=setup.ticker, bar_date=setup.bar_date
        )
        cand = sug.calls if kind == "call" else sug.puts
        strikes_md = " · ".join(f"{c.moneyness} ${c.strike:g}" for c in cand)

    color = (ks.trigger_bar_color or "n/a").capitalize()
    in_dir = (
        "in-direction" if ks.trigger_bar_in_direction
        else ("AGAINST direction" if ks.trigger_bar_in_direction is False else "n/a")
    )

    sqn100 = f"{ks.sqn_value:+.2f}" if ks.sqn_value is not None else "n/a"
    sqn20 = f"{ks.sqn_20_value:+.2f}" if ks.sqn_20_value is not None else "n/a"

    # Sizing under the fixed $150 lotto cap (R1). build_standard already caps
    # max_risk_usd at the account's max_per_trade_usd (=$150) when present.
    cap_note = " (capped by $150 R1 cap)" if ks.risk_capped_by_max_trade else ""
    cut_pct = account.raw.get("cut_rule_pct")
    cut_label = f"{abs(cut_pct) * 100:.0f}%" if cut_pct is not None else "60-70%"

    universe = setup.source_universe or "QQQ+GLD baseline"

    lines = [
        f"### {setup.ticker} — {direction.upper()} ({kind}s)  ·  _{universe}_",
        "",
        f"- **Verdict:** {setup.verdict.upper()} — {setup.verdict_reason}",
        f"- **Entry-authorized (discipline gate):** "
        f"{'YES' if ks.discipline_attestation and ks.discipline_attestation.entry_authorized else 'NO'}"
        f"  ·  **Kill-sheet status:** {ks.status}",
        f"- **Regime:** SQN(100) {ks.regime} ({sqn100}) · SQN(20) {ks.regime_20 or 'n/a'} ({sqn20})",
        f"- **MA stack (daily):** {ks.ma_stack}  ·  "
        f"10/20/50/200 = ${ks.ma_10:g} / ${ks.ma_20:g} / ${ks.ma_50:g} / ${ks.ma_200:g}",
        f"- **Stochastic (daily 14,7,7):** %K/%D {ks.stoch_k:.1f}/{ks.stoch_d:.1f}"
        f"  ·  {ks.stoch_signal} ({ks.stoch_zone})",
        f"- **2H trigger bar:** {color} ({in_dir})",
        f"- **Spot:** ${setup.close:g}" if setup.close else "- **Spot:** n/a",
        f"- **Suggested strikes ({kind}s):** {strikes_md}",
        f"- **DTE band:** {ks.dte_band_label}",
        f"- **Sizing (R1 fixed cap):** max risk ${ks.max_risk_usd:,.0f}{cap_note} "
        f"on ${ks.account_balance_usd:,.0f} lotto account",
        f"- **Cut rule:** hard stop at -{cut_label} of premium",
        f"- **Why now:** {setup.why_now}",
        "",
        "**Live option quote — ⚠️ VERIFY on broker chain (not fabricated):**",
        "",
        "| Premium | IV / IVR | Delta | Open Int | Bid-Ask Spread |",
        "|---|---|---|---|---|",
        "| ⚠️ VERIFY | ⚠️ VERIFY | ⚠️ VERIFY | ⚠️ VERIFY | ⚠️ VERIFY |",
        "",
    ]
    return "\n".join(lines)


# ─── Main ───────────────────────────────────────────────────────────────────
def main() -> int:
    now_pt = datetime.now(PT)
    now_et = now_pt.astimezone(ET)
    ts_label = now_pt.strftime("%Y-%m-%d %H:%M %Z")

    # 1. Run the scan (baseline + free-range). The scanner applies the v2
    #    gates (G2/G3) + price band internally; actionable = verdict "buy".
    baseline = scan_lotto_watchlist()  # QQQ + GLD
    free_range = scan_lotto_watchlist(universe=FREE_RANGE_UNIVERSE)

    # 2. Fresh-bar / trading-session guard.
    bar_time = latest_2h_bar_time()
    if not is_fresh_session(bar_time, now_et):
        bar_str = bar_time.astimezone(PT).strftime("%Y-%m-%d %H:%M %Z") if bar_time else "no bar"
        print(
            f"LOTTO SCAN — {ts_label}: market closed or no fresh 2H bar "
            f"(latest 2H bar: {bar_str}), skipping."
        )
        return 0

    window_label = bar_time.astimezone(PT).strftime("%H:%M %Z")

    # Merge actionable setups (dedup on ticker+direction; baseline wins).
    actionable: list = []
    seen: set[tuple[str, str]] = set()
    for s in list(baseline.actionable_setups) + list(free_range.actionable_setups):
        key = (s.ticker, s.direction)
        if key in seen:
            continue
        seen.add(key)
        actionable.append(s)

    config = load_config()  # baked-in defaults when no config.yaml exists
    account = config.account("lotto")

    # 3. Header + disclaimer.
    out: list[str] = []
    out.append(f"# Lotto Scan — {ts_label}")
    out.append(f"_2H window close: {window_label} · trigger TF 2H · 0-14 DTE · long calls/puts only_")
    out.append("")
    out.append(
        "> ⚠️ This scan runs in the cloud and CANNOT see your open positions / "
        "concurrent-override caps / R1 remaining balance — verify against your "
        "book before entering."
    )
    out.append("")
    out.append("## Regime backdrop (QQQ + GLD)")
    out.append("")
    out.append(regime_summary(baseline))
    out.append("")

    if not actionable:
        out.append("## Setups")
        out.append("")
        out.append("**No qualifying lotto setups this window.**")
        print("\n".join(out))
        return 0

    out.append(f"## {len(actionable)} qualifying setup(s)")
    out.append("")
    for s in actionable:
        out.append(render_setup_block(s, account))

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
