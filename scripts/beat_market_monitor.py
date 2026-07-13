"""QQQM-core signal monitor (cloud routine).

Successor to the beat-market $10K plan babysitter (rewritten 2026-07-11 when
the qqqm-core skill replaced the plan's wait-for-the-dip entry). Recomputes
the core's two-state signal off the most recent CONFIRMED daily close and
prints the state. The scheduled cloud routine reads this stdout and drafts a
Gmail only when ACTIONABLE.

The signal (qqqm-core skill; evidence: scripts/qqqm_core_backtest.py):
  LONG while QQQ weekly close > 40WMA AND daily SQN(100) >= +0.7
  EXIT on weekly close < 40WMA OR SQN(100) <= -0.7
  (-60% premium cut is the position-side backstop; this monitor is
  position-blind and cannot see premium.)

Signal flips are evaluated on COMPLETED weekly bars (Friday close). Midweek
the monitor also reports a provisional read of the in-progress week as a
heads-up — act only on the Friday close.

Also watched: Track A 19/39 weekly crosses (MU/META/ETH/BTC — weekly-trend-
trader) and the rule-11 Bull-regime Stoch-oversold dip (informational for the
rest of the book; NOT a core entry or add — that was backtested and rejected).

Reads daily bars from pre-staged CSVs under STAGED_DATA_DIR (the cloud sandbox
can't reach Yahoo; scripts/publish_results.py stages them in Actions).
"""
import os
import datetime as dt
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

TODAY = dt.date.today()
# Manually revised on fills (the monitor is position-blind). Last revised
# 2026-07-11: qqqm-core adopted; signal ON since 2026-04-17; entry pending
# kill sheet + trade-devil. On purchase set CORE_HELD=True and CORE_EXPIRY.
CORE_HELD = False
CORE_EXPIRY = None  # dt.date(YYYY, M, D) of the held call when CORE_HELD
CORE_STATE = "core UNBOUGHT — qqqm-core adopted 2026-07-11, entry pending kill sheet"
TICKERS = ["QQQ", "SPY", "QQQM"]
# Track A (weekly-trend-trader) 19/39 weekly-MA cross watch — Tier 1 refresh
# 2026-07-01: MU / META / ETH / BTC. A fresh weekly 19>39 cross is the Track A
# LEAPS entry signal; a fresh 19<39 cross is the exit / stand-aside signal.
TRACK_A = {"MU": "MU", "META": "META", "ETH": "ETH-USD", "BTC": "BTC-USD"}


def sqn(close, n=100):
    lr = np.log(close / close.shift(1))
    m = lr.rolling(n).mean()
    s = lr.rolling(n).std(ddof=1)
    return (m / s.where(s != 0)) * np.sqrt(n)


def reg100(v):
    return ("strong_bull" if v > 1.5 else "bull" if v > 0.7 else "neutral" if v >= -0.7 else "bear" if v >= -1.5 else "strong_bear")


def stoch_k(df, length=14, smooth=7):
    hh = df["High"].rolling(length).max()
    ll = df["Low"].rolling(length).min()
    rng = (hh - ll).replace(0, np.nan)
    raw = 100 * (df["Close"] - ll) / rng
    return raw.rolling(smooth).mean()


STAGED = os.environ.get("STAGED_DATA_DIR", "/tmp/cloud-data")
data = {}
for t in TICKERS + list(TRACK_A.values()):
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
    print("not run. No signals were evaluated. Fix staging, then re-run.")
    raise SystemExit(0)


def core_signal(df):
    """Weekly two-state signal on QQQ with entry/exit hysteresis: turns ON at
    close > 40WMA AND SQN(100) >= +0.7; stays ON until close < 40WMA OR
    SQN <= -0.7 (SQN drifting into Neutral does NOT exit). Returns
    (completed-week states, partial week state or None, weekly frame). A
    weekly bar is 'completed' only if the last daily bar lands on its Friday
    label."""
    w = df.resample("W-FRI").agg({"Close": "last"}).dropna()
    w["ma40"] = w["Close"].rolling(40).mean()
    w["sqn"] = sqn(df["Close"]).resample("W-FRI").last()
    states, on = [], False
    for close, ma40, s in zip(w["Close"], w["ma40"], w["sqn"]):
        if np.isnan(ma40) or np.isnan(s):
            states.append(False)
            continue
        if on:
            on = not (close < ma40 or s <= -0.7)
        else:
            on = close > ma40 and s >= 0.7
        states.append(on)
    w["on"] = states
    partial = df.index[-1].date() < w.index[-1].date()
    completed = w.iloc[:-1] if partial else w
    part_state = bool(w["on"].iloc[-1]) if partial else None
    return completed, part_state, w


def daily_metrics(t):
    df = data.get(t)
    if df is None or len(df) < 220:
        return None
    c = df["Close"]
    k = stoch_k(df)
    return dict(close=float(c.iloc[-1]), date=str(df.index[-1].date()),
                sqn100=float(sqn(c).iloc[-1]), sqn20=float(sqn(c, 20).iloc[-1]),
                k=float(k.iloc[-1]), ma200=float(c.rolling(200).mean().iloc[-1]))


def track_a_cross(t):
    df = data.get(t)
    if df is None:
        return None
    w = df["Close"].resample("W-FRI").last().dropna()
    if len(w) < 42:
        return None
    above = w.rolling(19).mean() > w.rolling(39).mean()
    state = "19>39 (long)" if bool(above.iloc[-1]) else "19<39 (out)"
    fresh = bool(above.iloc[-1] != above.iloc[-3])  # flipped within ~2 weekly bars
    return dict(state=state, fresh=fresh, close=float(w.iloc[-1]))


M = {t: daily_metrics(t) for t in TICKERS}
TA = {name: track_a_cross(sym) for name, sym in TRACK_A.items()}
flags = []

qdf = data.get("QQQ")
core_on = None
if qdf is not None and len(qdf) > 300:
    completed, part_state, w = core_signal(qdf)
    core_on = bool(completed["on"].iloc[-1])
    prev_on = bool(completed["on"].iloc[-2])
    last = completed.iloc[-1]
    since = None
    run = completed["on"][::-1]
    streak = int((run == core_on).cummin().sum())
    since = completed.index[-streak].date()

    if core_on != prev_on:
        if core_on:
            flags.append(("S", "CORE SIGNAL FLIPPED ON", f"QQQ completed weekly close {last.Close:.2f} > 40WMA {last.ma40:.2f} with SQN(100) {last.sqn:+.2f} -- qqqm-core signal is ON as of {completed.index[-1].date()}. ENTER per the skill: QQQM deep-ITM call D0.75-0.85, >=365 DTE, premium = 50% of sleeve. Kill sheet + trade-devil first."))
        else:
            flags.append(("S", "CORE SIGNAL FLIPPED OFF", f"QQQ completed weekly close {last.Close:.2f} vs 40WMA {last.ma40:.2f}, SQN(100) {last.sqn:+.2f} -- qqqm-core signal is OFF as of {completed.index[-1].date()}. EXIT the core if held (intraday limit, no GTC); if unbought, stand down."))
    if part_state is not None and part_state != core_on:
        verb = "ON" if part_state else "OFF"
        flags.append(("P", f"PROVISIONAL FLIP {verb}", f"Week-to-date QQQ would flip the core signal {verb} if held to Friday's close (close {w.Close.iloc[-1]:.2f} vs 40WMA {w.ma40.iloc[-1]:.2f}, SQN {w.sqn.iloc[-1]:+.2f}). Heads-up only -- the signal acts on COMPLETED weekly closes."))
    if core_on and not CORE_HELD:
        flags.append(("E", "SIGNAL ON, CORE UNBOUGHT", f"qqqm-core signal has been ON since {since} and the core is not held. Late entry is valid per the skill (62% WR / avg +24.7% at week 12 historically, vs ~+3% waiting in cash). Enter this week: kill sheet + trade-devil + live QQQM chain check."))
    if not core_on and CORE_HELD:
        flags.append(("E", "SIGNAL OFF, CORE STILL HELD", f"Signal went OFF (completed week {completed.index[-1].date()}) and CORE_HELD is still True -- exit the position or update the monitor constant."))

if CORE_HELD and CORE_EXPIRY:
    dte = (CORE_EXPIRY - TODAY).days
    if dte <= 60:
        flags.append(("R", "ROLL THE CORE", f"{dte} DTE remaining on the held QQQM call -- at/below the 60 DTE floor. Roll out to >=365 DTE D0.75-0.85 this week if the signal is still ON; otherwise exit."))
    elif dte <= 75:
        flags.append(("R", "ROLL WINDOW APPROACHING", f"{dte} DTE remaining on the held QQQM call -- plan the roll before the 60 DTE floor."))

# Rule-11 dip-buy: validated Bull-regime oversold edge (~+17-18%/12mo). NOT a
# qqqm-core entry/add (backtested and rejected 2026-07-11) -- informational
# timing context for the rest of the book, with the -60% premium cut and no
# tight stop if taken as a standalone long-horizon trade.
for t in ("QQQ", "SPY"):
    m = M.get(t)
    if m and m["k"] < 20 and m["sqn100"] > 0.7:
        flags.append(("D", "RULE-11 DIP (INFO)", f"{t} daily Stoch %K {m['k']:.0f} < 20 with SQN(100) {m['sqn100']:+.2f} ({reg100(m['sqn100'])}) -- validated Bull-regime oversold. Standalone signal only; the QQQM core neither waits for nor adds on dips."))

for name, ta in TA.items():
    if ta and ta["fresh"]:
        flags.append(("W", "TRACK A 19/39 CROSS", f"{name} weekly 19/39 MA cross flipped to {ta['state']} (close {ta['close']:.2f}) -- Track A LEAPS {'entry' if '>' in ta['state'] else 'exit / stand-aside'} signal (weekly-trend-trader)."))

actionable = len(flags) > 0
order = {"S": 0, "E": 1, "R": 2, "P": 3, "W": 4, "D": 5}
flags.sort(key=lambda x: order.get(x[0], 9))
headline = flags[0][1] if actionable else ("HOLD -- signal ON, core per plan" if core_on else "FLAT -- signal OFF")

q = M.get("QQQ")

# Optional machine-readable state for the dashboard Core view — written only
# when QQQM_CORE_JSON_OUT is set (the local launchd wrapper points it at
# ~/.trading-dashboard/qqqm_core_monitor/latest.json). The cloud routine
# reads stdout and never sets it. GET /api/v1/core/state serves this file.
_json_out = os.environ.get("QQQM_CORE_JSON_OUT")
if _json_out:
    import json
    doc = {
        "generated": TODAY.isoformat(),
        "as_of_close": q["date"] if q else None,
        "actionable": actionable,
        "headline": headline,
        "core_held": CORE_HELD,
        "core_expiry": CORE_EXPIRY.isoformat() if CORE_EXPIRY else None,
        "core_state_note": CORE_STATE,
        "signal": None,
        "actions": [{"tag": t, "title": title, "detail": d} for t, title, d in flags],
        "levels": {t: ({**m, "regime": reg100(m["sqn100"])} if (m := M.get(t)) else None)
                   for t in TICKERS},
        "track_a": TA,
    }
    if core_on is not None:
        _last = completed.iloc[-1]
        doc["signal"] = {
            "on": core_on,
            "since": since.isoformat(),
            "completed_week": completed.index[-1].date().isoformat(),
            "close": round(float(_last.Close), 2),
            "ma40": round(float(_last.ma40), 2),
            "sqn100": round(float(_last.sqn), 2),
            "provisional_on": part_state,
        }
    with open(_json_out, "w") as f:
        json.dump(doc, f, indent=1)

print(f"ACTIONABLE: {'YES' if actionable else 'NO'}")
print(f"HEADLINE: {headline}")
print(f"AS OF CLOSE: {q['date'] if q else 'n/a'}  (report generated {TODAY.isoformat()})")
print(f"CORE STATE (manual, rev 2026-07-11): {CORE_STATE}")
print("=" * 60)
if core_on is not None:
    last = completed.iloc[-1]
    print(f"CORE SIGNAL: {'ON' if core_on else 'OFF'} since {since}  "
          f"(completed wk {completed.index[-1].date()}: close {last.Close:.2f} "
          f"vs 40WMA {last.ma40:.2f}, SQN(100) {last.sqn:+.2f})")
    if part_state is not None:
        print(f"  week-to-date provisional: {'ON' if part_state else 'OFF'}")
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
    extra = f" Stoch {m['k']:.0f}" if t == "QQQ" else ""
    print(f"  {t}: {m['close']:.2f}  SQN100 {m['sqn100']:+.2f}({reg100(m['sqn100'])}) SQN20 {m['sqn20']:+.2f}  vs200DMA {m['close']/m['ma200']-1:+.1%}{extra}")
print("=" * 60)
print("TRACK A 19/39 WEEKLY (MU/META/ETH/BTC):")
for name, ta in TA.items():
    if not ta:
        print(f"  {name}: no data")
        continue
    print(f"  {name}: {ta['close']:.2f}  {ta['state']}{'  ** FRESH CROSS **' if ta['fresh'] else ''}")
print("=" * 60)
print("NOTE: position-blind -- this is the qqqm-core SIGNAL STATE, not your fills.")
print("CORE_HELD / CORE_EXPIRY / CORE_STATE are manual constants (revise in")
print("scripts/beat_market_monitor.py on any fill, exit, or roll). Signal acts on")
print("completed Friday closes; verify live quotes before trading.")
