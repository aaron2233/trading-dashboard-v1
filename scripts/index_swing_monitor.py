"""Index-swing 2H breakout monitor (cloud — rides the lotto result email).

Runs scan_index_swing_watchlist() live (GitHub Actions can reach Yahoo) and
prints a compact trigger report. publish_results.py appends this stdout to the
lotto result body at the 3x/day scan windows — the same cadence as the 2H
trigger TF — and flips the email ACTIONABLE only on a HIGH-CONVICTION breakout
(the PF 1.96 cohort from the TF-comparison backtest). Standard breakouts print
as informational and do not draft on their own.

R1 note: index-swing is an uncapped validated-cohort test per the Trading
Recovery Plan amendment 2026-07-07 — size per the skill's own 2% stop / 2R
spec, kill sheet + trade-devil still required.
"""


def main() -> int:
    from index_swing.scanner import scan_index_swing_watchlist

    res = scan_index_swing_watchlist()
    hc = [s for s in res.actionable_setups
          if s.confluence == "breakout_high_conviction"]
    std = [s for s in res.actionable_setups
           if s.confluence == "breakout_standard"]
    if hc:
        state = "HIGH-CONVICTION BREAKOUT"
    elif std:
        state = "standard breakout (info only)"
    else:
        state = "quiet"
    print(f"INDEX-SWING 2H: {state}")
    for s in res.setups:
        line = f"  {s.ticker}: {s.confluence}"
        if s.close is not None:
            line += f"  close {s.close:.2f}"
        if s.entry_price is not None and s.stop_price is not None:
            line += f"  entry {s.entry_price:.2f} stop {s.stop_price:.2f}"
        if s.target_price is not None:
            line += f" target2R {s.target_price:.2f}"
        if s.suggested_strike is not None:
            line += f"  ~45DTE call strike {s.suggested_strike}"
        if s.verdict:
            line += f"  [{s.verdict}]"
        if s.blockers:
            line += f"  -- {'; '.join(s.blockers)}"
        print(line)
    for t, e in sorted(res.errors.items()):
        print(f"  {t}: ERROR {e}")
    if hc:
        print("  ACTION: high-conviction cohort (PF 1.96) -- kill sheet + "
              "trade-devil before entry; size per skill spec (R1-exempt "
              "validated cohort, 2026-07-07 amendment).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
