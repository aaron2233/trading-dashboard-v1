"""Quarterly lotto-universe review: screen candidates, backtest, recommend rotation.

Process adopted 2026-07-17 (band-cost backtest session). The lotto watchlist's
single-stock sleeve is rotated on evidence, quarterly:

  1. SCREEN   — find in-band ($10-50), high-vol, liquid, optionable candidates
                from the repo universes + the focused-10-30 study cohort.
  2. BACKTEST — run scripts/archive/lotto_options_backtest.py (production
                lotto_verdict on 2H bars, BS option sim) over the trailing
                ~2y (yfinance 1h/2h limit is 730 days) on:
                current singles + SHADOW_ROTATED + screen candidates.
  3. REPORT   — per-name scorecard + rule verdicts:
                  DROP     n >= MIN_N and PF < PF_DROP
                  PROMOTE  candidate, n >= MIN_N, PF >= PF_PROMOTE, in-band
                  WATCH    PF >= PF_PROMOTE but affordability < AFF_FLOOR
                           (signal worth tracking; unbuyable under R1)
                  HOLD     insufficient n — no action either way
                Mag 7 and the ETF sleeve are structural (exempt cohorts) and
                are not scored for rotation here.

SHADOW_ROTATED names stay in every quarterly backtest after being dropped so
the rotation decision itself is testable: if the shadow cohort's forward PF
beats the promoted cohort's, the 2026-07 rotation was wrong (review trigger:
shadow PF > 1.3 while promoted < 1.0).

Cadence: quarterly, at a Sunday weekly review. First review due 2026-10-15.

Usage (from repo root):
    .venv/bin/python scripts/lotto_universe_review.py --stage screen
    .venv/bin/python scripts/lotto_universe_review.py --stage backtest
    .venv/bin/python scripts/lotto_universe_review.py --stage report

Artifacts land next to this script as lotto_review_<stage>_<date>.csv.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(REPO / "src"))

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

from free_range.universe import (  # noqa: E402
    LOTTO_HIGH_VOL_WATCHLIST,
    NASDAQ_100,
    RUSSELL_2000_TOP_50,
    SP500_TOP_50,
    is_etf,
)
from lotto.scanner import LOTTO_MAG7_PRICE_EXEMPT  # noqa: E402

# ── Rotation rule constants (adopted 2026-07-17; revisit at each review) ──
PRICE_MIN, PRICE_MAX = 10.0, 50.0   # in-band = affordable premium under R1
MIN_N = 8            # below this, per-name PF is noise — no drop/promote
PF_DROP = 0.7        # trailing-2y PF below this with n>=MIN_N → rotate out
PF_PROMOTE = 1.3     # candidate PF at/above this with n>=MIN_N → promote
R1_CAP = 150.0       # $ premium cap per lotto trade (recovery plan; dies at $20K)
AFF_FLOOR = 0.20     # < this share of trades affordable under R1 → watch-only
HV_FLOOR = 0.50      # annualized HV20 floor — lotto edge is a high-vol effect
DOLLAR_VOL_FLOOR = 30e6  # median daily $ volume — basic options-liquidity proxy

# Names rotated OUT of the live watchlist but kept in the quarterly backtest
# as the control cohort. Format: (ticker, rotated_out_on).
SHADOW_ROTATED: tuple[tuple[str, str], ...] = (
    # 2026-07-17 rotation (adopted): the 18 singles above the $50 cap at
    # 2026-07-17 closes + SMCI (in-band but PF 0.29 at n=11).
    ("AMD", "2026-07-17"), ("ARM", "2026-07-17"), ("ASML", "2026-07-17"),
    ("AVGO", "2026-07-17"), ("COIN", "2026-07-17"), ("CRWD", "2026-07-17"),
    ("DDOG", "2026-07-17"), ("LULU", "2026-07-17"), ("MDB", "2026-07-17"),
    ("MELI", "2026-07-17"), ("MRVL", "2026-07-17"), ("MSTR", "2026-07-17"),
    ("MU", "2026-07-17"), ("PANW", "2026-07-17"), ("PDD", "2026-07-17"),
    ("PLTR", "2026-07-17"), ("PYPL", "2026-07-17"), ("ZS", "2026-07-17"),
    ("SMCI", "2026-07-17"),
)

# Extra candidate seed beyond the index universes: the focused $10-30
# high-vol study cohort (scripts/lotto_focused_10_30_universe_2y.csv,
# 2026-05 — the backtest that set the $10 band floor, PF 2.54 slice).
FOCUSED_STUDY_SEED: tuple[str, ...] = (
    "ACHR", "AFRM", "AMC", "BBAI", "CIFR", "CLSK", "DKNG", "F", "GME",
    "HOOD", "IONQ", "JOBY", "LCID", "MARA", "NIO", "PLTR", "RDW", "RGTI",
    "RIOT", "SOFI", "SOUN", "WULF",
)

TODAY = date.today().isoformat()


def _current_singles() -> list[str]:
    return [t for t in LOTTO_HIGH_VOL_WATCHLIST
            if not is_etf(t) and t not in LOTTO_MAG7_PRICE_EXEMPT]


def _shadow_tickers() -> list[str]:
    return [t for t, _ in SHADOW_ROTATED]


# ── Stage 1: screen ─────────────────────────────────────────────────────────

def stage_screen(out_csv: Path) -> pd.DataFrame:
    import yfinance as yf

    # Incumbents + shadow are screened too (candidate=False) so every review
    # has a fresh close/HV row for them — the in-band-only policy needs it.
    seed = sorted(
        set(NASDAQ_100) | set(SP500_TOP_50) | set(RUSSELL_2000_TOP_50)
        | set(FOCUSED_STUDY_SEED) | set(LOTTO_HIGH_VOL_WATCHLIST)
        | set(_shadow_tickers())
    )
    exclude = set(LOTTO_HIGH_VOL_WATCHLIST) | set(_shadow_tickers())
    print(f"Screening {len(seed)} seed names "
          f"({len(exclude)} incumbents/shadow excluded from candidacy)...")

    bars = yf.download(seed, period="6mo", interval="1d", group_by="ticker",
                       auto_adjust=True, threads=True, progress=False)

    rows = []
    for t in seed:
        try:
            df = bars[t].dropna(subset=["Close"])
        except KeyError:
            continue
        if len(df) < 40:
            continue
        close = float(df["Close"].iloc[-1])
        logret = np.log(df["Close"]).diff()
        hv20 = float(logret.rolling(20).std().iloc[-1] * np.sqrt(252))
        dollar_vol = float((df["Close"] * df["Volume"]).tail(60).median())
        rows.append({
            "ticker": t,
            "close": round(close, 2),
            "hv20": round(hv20, 3),
            "med_dollar_vol_m": round(dollar_vol / 1e6, 1),
            "in_band": PRICE_MIN <= close <= PRICE_MAX,
            "etf": is_etf(t),
            "incumbent_or_shadow": t in exclude,
        })
    scr = pd.DataFrame(rows)
    scr["candidate"] = (
        scr.in_band & ~scr.etf & ~scr.incumbent_or_shadow
        & (scr.hv20 >= HV_FLOOR) & (scr.med_dollar_vol_m >= DOLLAR_VOL_FLOOR / 1e6)
    )

    # Optionability check only for names that passed everything else.
    for i, r in scr[scr.candidate].iterrows():
        try:
            if not yf.Ticker(r.ticker).options:
                scr.loc[i, "candidate"] = False
        except Exception:
            scr.loc[i, "candidate"] = False

    scr = scr.sort_values(["candidate", "hv20"], ascending=[False, False])
    scr.to_csv(out_csv, index=False)
    cands = scr[scr.candidate]
    print(f"\n{len(cands)} candidates pass screen "
          f"(in-band, HV20>={HV_FLOOR}, $vol>={DOLLAR_VOL_FLOOR/1e6:.0f}M, optionable):")
    print(cands[["ticker", "close", "hv20", "med_dollar_vol_m"]]
          .to_string(index=False))
    print(f"\nWrote {out_csv}")
    return scr


# ── Stage 2: backtest (wraps the archived harness) ──────────────────────────

def stage_backtest(screen_csv: Path, out_csv: Path,
                   extra_tickers: list[str] | None = None) -> None:
    tickers = set(_current_singles()) | set(_shadow_tickers())
    if screen_csv.exists():
        scr = pd.read_csv(screen_csv)
        tickers |= set(scr[scr.candidate].ticker)
    if extra_tickers:
        tickers |= {t.upper() for t in extra_tickers}
    # Trailing window, capped by yfinance's 730-day 1h/2h history limit.
    start = (date.today() - timedelta(days=715)).isoformat()
    end = date.today().isoformat()
    cmd = [
        str(REPO / ".venv" / "bin" / "python"),
        str(SCRIPTS / "archive" / "lotto_options_backtest.py"),
        "--tickers", ",".join(sorted(tickers)),
        "--start", start, "--end", end,
        "--csv", str(out_csv),
    ]
    print(f"Backtesting {len(tickers)} names {start} -> {end} ...")
    subprocess.run(cmd, check=True)


# ── Stage 3: report ─────────────────────────────────────────────────────────

def _pf(s: pd.Series) -> float:
    wins = s[s > 0].sum()
    losses = -s[s <= 0].sum()
    return float("inf") if losses == 0 else wins / losses


def stage_report(backtest_csv: Path, screen_csv: Path) -> None:
    df = pd.read_csv(backtest_csv, parse_dates=["entry_ts"])
    df["contract_cost"] = df.P_entry * 100

    singles = set(_current_singles())
    shadow = set(_shadow_tickers())
    in_band_now: dict[str, bool] = {}
    if screen_csv.exists():
        scr = pd.read_csv(screen_csv)
        in_band_now = dict(zip(scr.ticker, scr.in_band))

    g = df.groupby("ticker").agg(
        n=("R_multiple", "count"),
        wr=("R_multiple", lambda s: (s > 0).mean()),
        avg_R=("R_multiple", "mean"),
        med_cost=("contract_cost", "median"),
        aff=("contract_cost", lambda s: (s <= R1_CAP).mean()),
    )
    g["PF"] = df.groupby("ticker").R_multiple.apply(_pf)

    def role(t: str) -> str:
        if t in shadow:
            return "shadow"
        if t in singles:
            return "incumbent"
        return "candidate"

    def verdict(t: str, r: pd.Series) -> str:
        if role(t) == "shadow":
            return "SHADOW"
        # In-band-only policy (2026-07-17): singles that drift above the band
        # rotate out on price alone — PF doesn't override, that's how the
        # 2026-07 blocked-list situation formed. Missing screen row → no call.
        if role(t) == "incumbent" and not in_band_now.get(t, True):
            return "DROP (out of band)"
        if r.n < MIN_N:
            return "HOLD (n<%d)" % MIN_N
        if r.PF < PF_DROP:
            return "DROP" if role(t) != "candidate" else "REJECT"
        if r.PF >= PF_PROMOTE:
            if r.aff < AFF_FLOOR:
                return "WATCH (unaffordable)"
            if role(t) == "candidate":
                return "PROMOTE" if in_band_now.get(t, True) else "WATCH (out of band)"
            return "KEEP"
        return "KEEP" if role(t) != "candidate" else "REJECT (PF<%s)" % PF_PROMOTE

    g["role"] = [role(t) for t in g.index]
    g["verdict"] = [verdict(t, r) for t, r in g.iterrows()]
    g = g.sort_values("PF", ascending=False).round(2)

    # Names with zero fires in the window never enter df — surface them.
    silent = (singles | shadow) - set(df.ticker.unique())

    print(f"\n══ Lotto universe review {TODAY} "
          f"(window {df.entry_ts.min().date()} -> {df.entry_ts.max().date()}) ══")
    print(g.to_string())
    if silent:
        print(f"\nNo signals in window (review manually): {sorted(silent)}")

    shadow_df, live_df = df[df.ticker.isin(shadow)], df[~df.ticker.isin(shadow)]
    if not shadow_df.empty:
        print(f"\nShadow cohort check: PF={_pf(shadow_df.R_multiple):.2f} "
              f"(n={len(shadow_df)}) vs live {_pf(live_df.R_multiple):.2f} "
              f"(n={len(live_df)}) — rotation wrong if shadow >1.3 while live <1.0.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--stage", required=True,
                    choices=["screen", "backtest", "report"])
    ap.add_argument("--date", default=TODAY,
                    help="artifact date tag (default today) — lets report "
                         "re-read an earlier run's CSVs")
    ap.add_argument("--extra-tickers", default="",
                    help="backtest stage: comma-separated additions")
    args = ap.parse_args()

    screen_csv = SCRIPTS / f"lotto_review_screen_{args.date}.csv"
    backtest_csv = SCRIPTS / f"lotto_review_backtest_{args.date}.csv"

    if args.stage == "screen":
        stage_screen(screen_csv)
    elif args.stage == "backtest":
        extra = [t for t in args.extra_tickers.split(",") if t.strip()]
        stage_backtest(screen_csv, backtest_csv, extra)
    else:
        stage_report(backtest_csv, screen_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
