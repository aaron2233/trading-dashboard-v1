"""QQQM-core strategy evidence: single-name QQQ, ONE position, synthetic deep-ITM call.

Run 2026-07-11 to design the qqqm-core skill (the long-horizon leg of the
three-strategy book). Adapts scripts/archive/regime_levered_trend_backtest.py's
synthetic option overlay (LEVERAGE x weekly underlying return minus DRAG/52 on
premium, floored at -100%) to a one-position QQQ core and answers three
questions in one pass:

1. VARIANT GRID -- which entry philosophy and stop structure capture the trend?
   Entries (all require own daily SQN(100) >= +0.7 at entry):
     A regime-only : weekly ribbon full bull (10>20>50>200, 10&20 rising)
     B stoch-turn  : A + weekly Stoch reset-turn (the old RLT Layer-1 rule)
     C dip-only    : daily Stoch(14,7,7) %K < 20 intraweek (CLAUDE.md rule 11,
                     the 2026-H2 beat-market plan's wait-for-the-dip posture)
     D 40WMA+SQN   : weekly close > 40WMA (~200DMA). No ribbon, no timing.
   Exits (all include the -60% premium cut):
     1 structural  : weekly close < 19WMA, or SQN(100) <= -0.7
     2 macro-only  : weekly close < 40WMA, or SQN(100) <= -0.7

2. HYBRID -- does adding the rule-11 dip as an ADD tranche (or staging the
   entry) beat the pure engine at matched capital / matched drawdown?

3. LATE ENTRY -- entering k weeks after a signal turns on vs waiting flat for
   the next fresh signal.

ADOPTED (2026-07-11, with sensitivity across leverage 2.0-3.5 x drag 8-16%
preserving the ranking): D2 -- enter close>40WMA & SQN>=0.7, exit close<40WMA
or SQN<=-0.7, -60% cut backstop. 17.8x / +11.47% CAGR / -29% MaxDD vs QQQ B&H
9.6x / +8.89% / -83% (2000 -> 2026-07). Hybrids are dominated at matched
capital; late entry beats waiting (~+24.7% avg vs ~+3% cash over the same
span). NOT adopted for SPY -- the same rules underperform SPY B&H (+4.3% vs
+8.3%), so the strategy is QQQM-only.

Known limits: synthetic option model (no IV dynamics, spreads, or roll costs
beyond DRAG); single historical path; QQQ is the QQQM proxy (identical index,
QQQM history too short). Treat live fills as a forward-test cohort.
"""
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

START = "1996-01-01"
PORTFOLIO_START = "2000-01-07"
LEVERAGE = 2.7   # ~deep-ITM call premium leverage, long-run average
DRAG = 0.12      # assumed annual extrinsic bleed on premium
CUT = -0.60      # premium cut backstop (options-book standard)
ALLOC = 0.5      # premium per position as fraction of sleeve equity (adopted)
CASH_YIELD = 0.02  # avg T-bill on idle equity 2000-2026


def sqn_series(close, lb=100):
    lr = np.log(close / close.shift(1))
    return lr.rolling(lb).mean() / lr.rolling(lb).std() * np.sqrt(lb)


def stoch_k(df, k=14, ks=7):
    lo = df["Low"].rolling(k).min()
    hi = df["High"].rolling(k).max()
    raw = 100 * (df["Close"] - lo) / (hi - lo)
    return raw.rolling(ks).mean()


def prep(ticker):
    d = yf.download(ticker, start=START, interval="1d",
                    auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    w = d.resample("W-FRI").agg({"Open": "first", "High": "max",
                                 "Low": "min", "Close": "last"}).dropna()
    c = w["Close"]
    f = pd.DataFrame(index=w.index)
    f["close"] = c
    for n in (10, 20, 50, 200):
        f[f"ma{n}"] = c.rolling(n).mean()
    f["ma19"] = c.rolling(19).mean()
    f["ma40"] = c.rolling(40).mean()
    f["K"] = stoch_k(w)
    f["sqn"] = sqn_series(d["Close"]).resample("W-FRI").last()
    # weekly min of DAILY stoch K -- catches an intraweek rule-11 oversold tag
    f["dailyK_min"] = stoch_k(d).resample("W-FRI").min()
    return f


def ribbon_bull(f, i):
    r = f.iloc[i]
    return (r.ma10 > r.ma20 > r.ma50 > r.ma200
            and r.ma10 > f.iloc[i - 4].ma10 and r.ma20 > f.iloc[i - 4].ma20)


def entry(kind, f, i):
    r, p = f.iloc[i], f.iloc[i - 1]
    if not (r.sqn >= 0.7):
        return False
    if kind == "A":
        return ribbon_bull(f, i)
    if kind == "B":
        return (ribbon_bull(f, i) and r.K > p.K and p.K < 70
                and r.K < 80 and r.close > r.ma20)
    if kind == "C":
        return r.dailyK_min < 20
    if kind == "D":
        return r.close > r.ma40
    raise ValueError(kind)


def exit_hit(kind, f, i, prem_ret):
    r = f.iloc[i]
    if prem_ret <= CUT:
        return "cut"
    if r.sqn <= -0.7:
        return "regime"
    if kind == "1" and r.close < r.ma19:
        return "stop"
    if kind == "2" and r.close < r.ma40:
        return "stop"
    return None


def run(f, ekind, xkind):
    idx = f.index[f.index >= PORTFOLIO_START]
    equity, pos = 1.0, None
    closed, curve = [], []
    for dtm in idx:
        i = f.index.get_loc(dtm)
        if i < 205:
            curve.append((dtm, equity))
            continue
        r = f.iloc[i]
        cash = equity - (pos["alloc"] if pos else 0.0)
        equity += cash * CASH_YIELD / 52
        if pos is not None:
            wk_ret = r.close / f.iloc[i - 1].close - 1
            pos["prem_ret"] = max(-1.0, (1 + pos["prem_ret"])
                                  * (1 + LEVERAGE * wk_ret) - 1 - DRAG / 52)
            why = exit_hit(xkind, f, i, pos["prem_ret"])
            if why:
                equity += pos["alloc"] * pos["prem_ret"]
                closed.append(dict(entry=pos["dt"], exit=dtm,
                                   weeks=(dtm - pos["dt"]).days // 7,
                                   prem_ret=pos["prem_ret"], why=why))
                pos = None
        if pos is None and entry(ekind, f, i):
            pos = dict(dt=dtm, prem_ret=0.0, alloc=ALLOC * equity)
        curve.append((dtm, equity + (pos["alloc"] * pos["prem_ret"] if pos else 0.0)))
    cv = pd.Series(dict(curve))
    tr = pd.DataFrame(closed)
    yrs = (idx[-1] - idx[0]).days / 365.25
    in_weeks = int(tr.weeks.sum()) if not tr.empty else 0
    if pos is not None:
        in_weeks += (idx[-1] - pos["dt"]).days // 7
    return dict(mult=float(cv.iloc[-1]),
                cagr=float(cv.iloc[-1]) ** (1 / yrs) - 1,
                maxdd=float((cv / cv.cummax() - 1).min()),
                n=len(tr), wr=float((tr.prem_ret > 0).mean()) if len(tr) else np.nan,
                avg=float(tr.prem_ret.mean()) if len(tr) else np.nan,
                med_hold=float(tr.weeks.median()) if len(tr) else np.nan,
                expo=in_weeks / len(cv),
                exits=tr.why.value_counts().to_dict() if len(tr) else {},
                open_now=pos is not None,
                open_since=str(pos["dt"].date()) if pos else "")


def fresh_dip(f, i):
    # rule-11: daily Stoch K dipped <20 this week (fresh cross), SQN(100) Bull
    r, p = f.iloc[i], f.iloc[i - 1]
    return r.dailyK_min < 20 and p.dailyK_min >= 20 and r.sqn > 0.7


def hybrid(f, mode, core_alloc=0.5, add_alloc=0.25):
    """mode 'add': D2 trend core + one rule-11 dip add per hold, exit together.
       mode 'staged': enter core_alloc/2 on trend signal, complete to full on
       first dip or after 8 weeks; exits are the D2 macro rules throughout."""
    idx = f.index[f.index >= PORTFOLIO_START]
    yrs = (idx[-1] - idx[0]).days / 365.25
    equity, legs, curve = 1.0, [], []
    n_core = n_add = 0
    trades = []
    core_on, core_start, added = False, None, False
    for dtm in idx:
        i = f.index.get_loc(dtm)
        if i < 205:
            curve.append((dtm, equity))
            continue
        r = f.iloc[i]
        deployed = sum(l["alloc"] for l in legs)
        equity += (equity - deployed) * CASH_YIELD / 52
        if legs:
            wk = r.close / f.iloc[i - 1].close - 1
            for l in legs:
                l["prem_ret"] = max(-1.0, (1 + l["prem_ret"])
                                    * (1 + LEVERAGE * wk) - 1 - DRAG / 52)
            trend_break = r.close < r.ma40 or r.sqn <= -0.7
            still = []
            for l in legs:
                if trend_break or l["prem_ret"] <= CUT:
                    equity += l["alloc"] * l["prem_ret"]
                    trades.append(l["prem_ret"])
                else:
                    still.append(l)
            legs = still
            if trend_break:
                core_on = False
        trend_ok = r.close > r.ma40 and r.sqn >= 0.7
        if not core_on and trend_ok:
            core_on, core_start, added = True, dtm, False
            first = core_alloc / 2 if mode == "staged" else core_alloc
            legs.append(dict(alloc=first * equity, prem_ret=0.0))
            n_core += 1
        elif core_on and legs and not added:
            if mode == "add" and fresh_dip(f, i):
                legs.append(dict(alloc=add_alloc * equity, prem_ret=0.0))
                n_add += 1
                added = True
            if mode == "staged" and (fresh_dip(f, i)
                                     or (dtm - core_start).days >= 56):
                legs.append(dict(alloc=core_alloc / 2 * equity, prem_ret=0.0))
                n_add += 1
                added = True
        curve.append((dtm, equity + sum(l["alloc"] * l["prem_ret"] for l in legs)))
    cv = pd.Series(dict(curve))
    tr = pd.Series(trades)
    return dict(mult=float(cv.iloc[-1]), cagr=float(cv.iloc[-1]) ** (1 / yrs) - 1,
                maxdd=float((cv / cv.cummax() - 1).min()),
                n_core=n_core, n_add=n_add,
                wr=float((tr > 0).mean()) if len(tr) else np.nan)


def episodes_d2(f):
    """Signal-on episodes under the adopted D2 rules (last may be open)."""
    idx = f.index[f.index >= PORTFOLIO_START]
    eps, on = [], False
    for dtm in idx:
        i = f.index.get_loc(dtm)
        if i < 205:
            continue
        r = f.iloc[i]
        if on and (r.close < r.ma40 or r.sqn <= -0.7):
            eps[-1]["end"] = dtm
            on = False
        if not on and r.close > r.ma40 and r.sqn >= 0.7:
            eps.append({"start": dtm, "end": None})
            on = True
    if on:
        eps[-1]["end"] = idx[-1]
        eps[-1]["open"] = True
    return eps


def prem_return(f, start, end):
    """Synthetic premium return entering at `start` weekly close, held to `end`."""
    sub = f.loc[start:end, "close"]
    pr = 0.0
    for a, b in zip(sub.iloc[:-1], sub.iloc[1:]):
        pr = max(-1.0, (1 + pr) * (1 + LEVERAGE * (b / a - 1)) - 1 - DRAG / 52)
        if pr <= CUT:
            return CUT
    return pr


def main():
    global ALLOC
    q = prep("QQQ")
    idx = q.index[q.index >= PORTFOLIO_START]
    yrs = (idx[-1] - idx[0]).days / 365.25
    bh = q.close.loc[idx[-1]] / q.close.loc[idx[0]]
    bh_dd = float((q.close.loc[idx] / q.close.loc[idx].cummax() - 1).min())
    print(f"Period {idx[0].date()} -> {idx[-1].date()} ({yrs:.1f}y)  "
          f"ALLOC {ALLOC:.0%}  {LEVERAGE}x lev, {DRAG:.0%}/yr drag, {CUT:.0%} cut")
    print(f"QQQ  B&H : {bh:6.2f}x  CAGR {bh**(1/yrs)-1:+6.2%}  MaxDD {bh_dd:+.0%}")

    print("\n=== 1. VARIANT GRID ===")
    names = {"A": "regime-only", "B": "stoch-turn ",
             "C": "dip-only   ", "D": "40WMA+SQN  "}
    xnames = {"1": "19WMA stop", "2": "macro-only"}
    print(f"{'entry':12s} {'exit':11s} {'mult':>7s} {'CAGR':>7s} {'MaxDD':>6s} "
          f"{'n':>3s} {'WR':>4s} {'avgPrem':>8s} {'medHold':>7s} {'expo':>5s}  exits")
    for e in "ABCD":
        for x in "12":
            r = run(q, e, x)
            tag = " *OPEN since " + r["open_since"] if r["open_now"] else ""
            print(f"{names[e]:12s} {xnames[x]:11s} {r['mult']:6.2f}x {r['cagr']:+7.2%} "
                  f"{r['maxdd']:+5.0%} {r['n']:3d} {r['wr']*100:3.0f}% {r['avg']:+8.1%} "
                  f"{r['med_hold']:6.0f}w {r['expo']:5.0%}  {r['exits']}{tag}")

    print("\n=== 2. HYBRID (trend core + rule-11 dip) vs capital-matched pure ===")
    base_alloc = ALLOC
    print(f"{'variant':34s} {'mult':>7s} {'CAGR':>7s} {'MaxDD':>6s} {'cores':>5s} {'adds':>4s}")
    for alloc in (0.5, 0.75):
        ALLOC = alloc
        r = run(q, "D", "2")
        print(f"pure engine D2 @ {alloc:<17.0%} {r['mult']:6.2f}x {r['cagr']:+7.2%} "
              f"{r['maxdd']:+5.0%} {r['n']:5d}")
    ALLOC = base_alloc
    for label, kw in (
        ("hybrid: core 50% + dip-add 25%", dict(mode="add", core_alloc=0.5, add_alloc=0.25)),
        ("staged: 25% now, +25% dip/8wk", dict(mode="staged", core_alloc=0.5)),
    ):
        r = hybrid(q, **kw)
        print(f"{label:34s} {r['mult']:6.2f}x {r['cagr']:+7.2%} {r['maxdd']:+5.0%} "
              f"{r['n_core']:5d} {r['n_add']:4d}")

    print("\n=== 3. LATE ENTRY (enter k weeks after signal start, hold to episode end) ===")
    eps = episodes_d2(q)
    closed_eps = [e for e in eps if not e.get("open")]
    print(f"{len(eps)} episodes"
          + (f" (current open since {eps[-1]['start'].date()})" if eps[-1].get("open") else ""))
    print(f"{'k wks':>6s} {'n':>3s} {'WR':>4s} {'avg prem':>9s} {'median':>8s} {'worst':>7s}")
    for k in (0, 4, 8, 12, 16, 26):
        rets = []
        for ep in closed_eps:
            weeks = q.loc[ep["start"]:ep["end"]].index
            if len(weeks) <= k + 1:
                continue
            rets.append(prem_return(q, weeks[k], ep["end"]))
        s = pd.Series(rets)
        print(f"{k:6d} {len(s):3d} {(s>0).mean()*100:3.0f}% {s.mean():+9.1%} "
              f"{s.median():+8.1%} {s.min():+7.1%}")

    print("\n=== SPY check (adopted D2 rules do NOT generalize) ===")
    s = prep("SPY")
    r = run(s, "D", "2")
    sidx = s.index[s.index >= PORTFOLIO_START]
    syrs = (sidx[-1] - sidx[0]).days / 365.25
    sbh = s.close.loc[sidx[-1]] / s.close.loc[sidx[0]]
    print(f"SPY D2: {r['mult']:.2f}x CAGR {r['cagr']:+.2%} MaxDD {r['maxdd']:+.0%} "
          f"n={r['n']} WR {r['wr']*100:.0f}%   (SPY B&H {sbh:.2f}x CAGR {sbh**(1/syrs)-1:+.2%})")


if __name__ == "__main__":
    main()
