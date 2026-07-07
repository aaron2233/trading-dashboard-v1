"""Beat-the-Market $10K daily scaling-in monitor (cloud routine).

Recomputes a few indicators off the most recent CONFIRMED daily close and
prints the plan's TRIGGER STATE. The scheduled cloud routine reads this stdout
and drafts a Gmail only when ACTIONABLE.

Reads daily bars from pre-staged CSVs under STAGED_DATA_DIR (the cloud sandbox
can't reach Yahoo; a GitHub Action stages bars to the cloud-data branch — see
scripts/stage_market_data.py and src/data/staged_loader.py). Was previously an
inline yf.download script in the routine prompt; moved in-repo so it's testable
and version-controlled.
"""
import os
import datetime as dt
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TODAY = dt.date.today()
EXPIRY = dt.date(2026, 12, 18)
QQQM_DERISK = 270.0
# Manually revised on plan-state changes (the monitor is position-blind and
# cannot read fills). Last revised 2026-07-07: June thematic fills (NVDA/MP/GLD
# shares) all exited on invalidation; MP + GLD legs are DEAD (thesis-broken).
# The QQQM Dec-18 $270 core was never bought — it is the plan's one open
# decision, backstopped by the Sep 15-16 FOMC no-dip fallback.
PLAN_STATE = "core UNBOUGHT (QQQM Dec-18 270C); thematic legs exited; book is cash"
CATALYSTS = {
    "PLTR earnings": dt.date(2026, 8, 10), "NVDA earnings": dt.date(2026, 8, 26),
    "FOMC": dt.date(2026, 7, 29),
    "FOMC ": dt.date(2026, 9, 16), "FOMC  ": dt.date(2026, 10, 28), "FOMC   ": dt.date(2026, 12, 9),
}
TICKERS = ["QQQ", "SPY", "QQQM", "NVDA", "QLD"]


def sqn(close, n):
    lr = np.log(close / close.shift(1))
    m = lr.rolling(n).mean()
    s = lr.rolling(n).std(ddof=1)
    return float(((m / s.where(s != 0)) * np.sqrt(n)).iloc[-1])


def reg100(v):
    return ("strong_bull" if v > 1.5 else "bull" if v > 0.7 else "neutral" if v >= -0.7 else "bear" if v >= -1.5 else "strong_bear")


def reg20(v):
    return ("strong_bull" if v > 1.4 else "bull" if v > 0.5 else "neutral" if v >= -1.1 else "bear" if v >= -1.9 else "strong_bear")


def stoch_k(df, length=14, smooth=7):
    hh = df["High"].rolling(length).max()
    ll = df["Low"].rolling(length).min()
    rng = (hh - ll).replace(0, np.nan)
    raw = 100 * (df["Close"] - ll) / rng
    return raw.rolling(smooth).mean()


STAGED = os.environ.get("STAGED_DATA_DIR", "/tmp/cloud-data")
data = {}
for t in TICKERS:
    try:
        df = pd.read_csv(os.path.join(STAGED, t + "__1d.csv"), index_col=0, parse_dates=True)
        data[t] = df.rename(columns=str.capitalize).dropna()
    except Exception:
        data[t] = None

# Loud failure: if NO staged bars loaded, the cloud-data clone failed or the
# staging Action didn't run. Emit an ACTIONABLE block so the routine drafts a
# visible gap notice rather than silently reporting empty triggers.
if all(v is None for v in data.values()):
    print("ACTIONABLE: YES")
    print("HEADLINE: STAGED DATA MISSING")
    print(f"AS OF CLOSE: n/a  (report generated {TODAY.isoformat()})")
    print("=" * 60)
    print(f"STAGED DATA MISSING -- no 1d CSVs found under {STAGED}. The cloud-data")
    print("branch clone likely failed, or the stage-market-data GitHub Action did")
    print("not run. No triggers were evaluated. Fix staging, then re-run.")
    raise SystemExit(0)


def metrics(t):
    df = data.get(t)
    if df is None or len(df) < 220:
        return None
    c = df["Close"]
    k = stoch_k(df)
    k_now = float(k.iloc[-1])
    k_prev = float(k.iloc[-2])
    ma200 = float(c.rolling(200).mean().iloc[-1])
    hi10 = float(c.iloc[-10:].max())
    return dict(close=float(c.iloc[-1]), date=str(df.index[-1].date()),
                sqn100=sqn(c, 100), sqn20=sqn(c, 20), k=k_now, k_prev=k_prev, ma200=ma200,
                pull10=float(c.iloc[-1] / hi10 - 1))


M = {t: metrics(t) for t in TICKERS}
q = M["QQQ"]
flags = []
if q:
    if q["sqn20"] < -1.9:
        if q["sqn100"] > -0.7:
            flags.append(("B", "DEEP DIP -- DEPLOY THE BOOK", f"QQQ SQN(20) {q['sqn20']:.2f} < -1.9 with SQN(100) {q['sqn100']:.2f} ({reg100(q['sqn100'])}) still constructive -- rule-12 high-edge dip. Deploy the unbought book: QQQM Dec-18 $270 core (or QLD shares, the no-vega alternative) at the deep-dip price."))
        else:
            flags.append(("B", "SKIP / PRESERVE", f"QQQ SQN(20) {q['sqn20']:.2f} < -1.9 BUT SQN(100) {q['sqn100']:.2f} ({reg100(q['sqn100'])}) = regime break (rule 18). Do NOT deploy -- stay in cash."))
    if q["sqn20"] < -1.9 and q["k"] < 25 and q["sqn100"] > -0.7:
        flags.append(("B+", "CORE BUY -- STOCH-CONFIRMED DIP", f"HIGH-CONVICTION CORE ENTRY: QQQ daily Stoch %K {q['k']:.0f} < 25 AND SQN(20) {q['sqn20']:.2f} < -1.9, SQN(100) {q['sqn100']:.2f} ({reg100(q['sqn100'])}) still bull -- the daily washout we've been waiting for (Apr-2026 analog). BUY the QQQM Dec-18 $270 CORE. Verify the 270C live ask before entry."))
    reset = q["k_prev"] > 80 and q["k"] <= 80
    if reset or q["pull10"] <= -0.03:
        why = []
        if reset:
            why.append(f"daily Stoch reset out of overbought ({q['k_prev']:.0f}->{q['k']:.0f})")
        if q["pull10"] <= -0.03:
            why.append(f"QQQ {q['pull10']:+.1%} off its 10-day high")
        flags.append(("A", "QQQM CORE ENTRY WINDOW", f"{' & '.join(why)} -- the core (QQQM Dec-18 $270C) is UNBOUGHT and this is the pullback entry window committed to on 2026-06-03. Buy at the better price (QLD shares = no-vega alternative), or consciously pass and wait for the next reset / Sep FOMC fallback. Verify the 270C live ask."))
qm = M["QQQM"]
if qm and qm["close"] < QQQM_DERISK:
    flags.append(("C", "CORE DE-RISK / ENTRY INVALID", f"QQQM closed {qm['close']:.2f} < {QQQM_DERISK:.0f} (call de-risk level) -- de-risk if the core is held; if still unbought, the entry setup is invalidated."))
dte = (EXPIRY - TODAY).days
if 0 <= dte <= 21:
    flags.append(("C", "ROLL CORE CALL", f"{dte} days to Dec-18 expiry -- roll or close the QQQM call if held (don't hold into expiry)."))
for t in ["QQQM", "NVDA"]:
    m = M[t]
    if m and m["close"] < m["ma200"]:
        flags.append(("T", f"{t} < 200DMA", f"{t} {m['close']:.2f} below its 200DMA {m['ma200']:.2f} -- trend deteriorating, reassess."))
for name, d in CATALYSTS.items():
    delta = (d - TODAY).days
    if 0 <= delta <= 5:
        flags.append(("K", "CATALYST NEAR", f"{name.strip()} in {delta}d ({d.isoformat()})."))
if dt.date(2026, 9, 15) <= TODAY <= dt.date(2026, 9, 30):
    flags.append(("F", "NO-DIP FALLBACK", "Sep FOMC window reached -- the QQQM core is the plan's engine; if no dip ever came, deploy it now (or QLD shares) to stay invested for Q4."))

actionable = len(flags) > 0
order = {"B+": -1, "B": 0, "C": 1, "A": 2, "F": 3, "K": 4, "T": 5}
flags.sort(key=lambda x: order.get(x[0], 9))
headline = flags[0][1] if actionable else "HOLD -- no triggers"

print(f"ACTIONABLE: {'YES' if actionable else 'NO'}")
print(f"HEADLINE: {headline}")
print(f"AS OF CLOSE: {q['date'] if q else 'n/a'}  (report generated {TODAY.isoformat()})")
print(f"PLAN STATE (manual, rev 2026-07-07): {PLAN_STATE}")
print("=" * 60)
if actionable:
    print("TODAY'S ACTIONS:")
    for _, tag, detail in flags:
        print(f"  [{tag}] {detail}")
    print("=" * 60)
print("REGIME / LEVELS:")
for t in TICKERS:
    m = M[t]
    if not m:
        print(f"  {t}: no data")
        continue
    extra = ""
    if t == "QQQ":
        extra = f" Stoch {m['k']:.0f}"
    if t == "QQQM":
        extra = f" (de-risk<{QQQM_DERISK:.0f})"
    print(f"  {t}: {m['close']:.2f}  SQN100 {m['sqn100']:+.2f}({reg100(m['sqn100'])}) SQN20 {m['sqn20']:+.2f}  vs200DMA {m['close']/m['ma200']-1:+.1%}{extra}")
print("=" * 60)
print("NOTE: position-blind -- this is the plan's TRIGGER STATE, not your fills. PLAN STATE above is a manual constant (revise it in scripts/beat_market_monitor.py on any fill or exit). Verify live quotes before trading.")
