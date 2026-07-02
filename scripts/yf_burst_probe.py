"""TEMP diagnostic — does a 50-ticker yfinance burst get throttled on this IP?

Fetches daily bars for the full NASDAQ-50 universe SEQUENTIALLY (matching the
real scan's per-ticker load pattern), counts ok/error, classifies rate-limit
(429 / "Too Many Requests" / YFRateLimitError) vs other errors, and reports
timing. Pure fetch test — no indicator math, no scan, no market-hours guard, so
it runs any day. Reads the IP-throttling answer the weekend scan can't.

DELETE this file + .github/workflows/yf-burst-probe.yml once the cloud-IP burst
question is settled.

Run:
    PYTHONPATH=src python scripts/yf_burst_probe.py
"""
from __future__ import annotations

import sys
import time
import warnings
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

warnings.filterwarnings("ignore")

from data.yfinance_loader import load_bars  # noqa: E402

# Copied from lotto_cloud_scan.NASDAQ_50 (kept inline so this throwaway probe is
# self-contained and free of the scan's heavy import chain). It is a diagnostic
# with a short life — no drift concern.
NASDAQ_50 = [
    "AAPL", "ADBE", "ADI", "ADP", "AMAT", "AMD", "AMGN", "AMZN", "ARM", "ASML",
    "AVGO", "AZN", "BKNG", "CDNS", "CEG", "CMCSA", "COST", "CRWD", "CSCO", "CSX",
    "FTNT", "GILD", "GOOG", "GOOGL", "HON", "INTC", "INTU", "ISRG", "KLAC", "LIN",
    "LRCX", "MAR", "MELI", "META", "MNST", "MRVL", "MSFT", "MU", "NFLX", "NVDA",
    "PANW", "PDD", "PEP", "QCOM", "SBUX", "SNPS", "TMUS", "TSLA", "TXN", "VRTX",
]


def classify(exc: Exception) -> str:
    s = f"{type(exc).__name__}: {exc}".lower()
    if "429" in s or "too many" in s or "ratelimit" in s or "rate limit" in s:
        return "rate_limit"
    if "no data" in s or "empty" in s or "delisted" in s:
        return "empty"
    return "other"


def main() -> int:
    ok = 0
    errs: dict[str, int] = {}
    detail: list[str] = []
    t0 = time.monotonic()
    for tk in NASDAQ_50:
        try:
            df = load_bars(tk, interval="1d")
            ok += 1
            detail.append(f"{tk}:ok({len(df)})")
        except Exception as exc:  # noqa: BLE001 — diagnostic: bucket everything
            kind = classify(exc)
            errs[kind] = errs.get(kind, 0) + 1
            detail.append(f"{tk}:{kind}")
    dur = time.monotonic() - t0
    n = len(NASDAQ_50)
    err_total = sum(errs.values())

    print(f"YF BURST PROBE — {ok}/{n} ok, {err_total} err in {dur:.1f}s")
    print(f"errors by kind: {errs or 'none'}")
    print(f"per-ticker: {' '.join(detail)}")

    rl = errs.get("rate_limit", 0)
    if rl:
        print(f"VERDICT: RATE-LIMITED on this cloud IP — {rl}/{n} hit 429/rate-limit. "
              "yfinance burst is NOT reliable here; plan the paid-Polygon path.")
    elif err_total == 0:
        print("VERDICT: burst SURVIVED — all 50 fetched clean on this cloud IP. "
              "yfinance is viable for the unattended scan (sample more days to confirm).")
    else:
        print(f"VERDICT: {err_total} non-rate-limit error(s) (see kinds) — not a clean "
              "throttle signal; inspect per-ticker before concluding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
