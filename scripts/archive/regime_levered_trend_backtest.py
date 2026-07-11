"""Signal-level backtest of the regime-levered-trend skill's Layer 1 core.

Layer 1 rules implemented (per ~/.claude/skills/user/regime-levered-trend/SKILL.md):
  Entry (completed weekly close):
    - Full Bull weekly ribbon: 10>20>50>200 WMA, 10 & 20 rising (vs 4 wks ago)
    - Own daily SQN(100) >= +0.7
    - Broad SPY daily SQN(100) >= +0.7
    - Pullback-to-20WMA + Stoch turn: 2-week low touched 20WMA*1.03 zone,
      weekly Stoch(14,7,7) %K turned up this week from below 65, close > 20WMA
  Exit:
    - Weekly close < 19WMA (structural stop), OR
    - Broad SPY SQN(100) <= -0.7 (regime flip close-all), OR
    - Synthetic option premium -60% (cut rule, option sim only)
  Portfolio: max 2 concurrent positions, one per name; free slot goes to the
  highest own-SQN signal that week.

Option overlay is SYNTHETIC (no historical chain data): 80-delta LEAPS
approximated as LEVERAGE x underlying weekly return minus DRAG/52 per week on
premium, floored at -100%. Position size = 37.5% of equity at entry, rest cash.
Layer 2 (dip-buy) is NOT re-tested here — see stoch_oversold_*.py (2026-06-24).
"""
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import yfinance as yf

UNIVERSE = ["QQQ", "SPY", "IWM", "GLD", "META", "MU", "AAPL", "MSFT",
            "NVDA", "AMD", "AMZN", "NFLX", "TSLA"]
START = "1996-01-01"          # warmup for 200WMA; portfolio starts 2000-01
PORTFOLIO_START = "2000-01-07"
LEVERAGE = 2.7                # ~80-delta LEAPS at ~30% of notional
DRAG = 0.12                   # assumed annual extrinsic bleed on premium
CUT = -0.60                   # premium cut rule
ALLOC = 0.375                 # premium per position as fraction of equity
MAX_POS = 2


def sqn_series(close, lb=100):
    lr = np.log(close / close.shift(1))
    return lr.rolling(lb).mean() / lr.rolling(lb).std() * np.sqrt(lb)


def stoch(df, k=14, ks=7, ds=7):
    lo = df["Low"].rolling(k).min()
    hi = df["High"].rolling(k).max()
    raw = 100 * (df["Close"] - lo) / (hi - lo)
    K = raw.rolling(ks).mean()
    return K, K.rolling(ds).mean()


def weekly(d):
    w = d.resample("W-FRI").agg({"Open": "first", "High": "max",
                                 "Low": "min", "Close": "last"}).dropna()
    return w


def prep(ticker):
    d = yf.download(ticker, start=START, interval="1d",
                    auto_adjust=True, progress=False)
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    if d.empty:
        return None
    w = weekly(d)
    c = w["Close"]
    f = pd.DataFrame(index=w.index)
    f["close"] = c
    f["low"] = w["Low"]
    for n in (10, 20, 50, 200):
        f[f"ma{n}"] = c.rolling(n).mean()
    f["ma19"] = c.rolling(19).mean()
    f["K"], f["D"] = stoch(w)
    f["sqn"] = sqn_series(d["Close"]).resample("W-FRI").last()
    return f


def entry_signal(f, i, require_touch=False):
    """Adopted rule (2026-07-01 sensitivity run): Stoch reset-turn with close
    above 20WMA. require_touch=True reproduces the rejected strict variant."""
    r, p = f.iloc[i], f.iloc[i - 1]
    if not (r.ma10 > r.ma20 > r.ma50 > r.ma200):
        return False
    if not (r.ma10 > f.iloc[i - 4].ma10 and r.ma20 > f.iloc[i - 4].ma20):
        return False
    if not (r.sqn >= 0.7):
        return False
    if require_touch:
        touched = min(r.low, p.low) <= r.ma20 * 1.03
        turned = (r.K > p.K) and (p.K < 65) and (p.K <= f.iloc[i - 2].K)
        return touched and turned and (r.close > r.ma20) and (r.K < 80)
    turned = (r.K > p.K) and (p.K < 70)
    return turned and (r.close > r.ma20) and (r.K < 80)


def main():
    frames = {t: prep(t) for t in UNIVERSE}
    frames = {t: f for t, f in frames.items() if f is not None}
    spy_sqn = frames["SPY"]["sqn"]

    idx = frames["SPY"].index
    idx = idx[idx >= PORTFOLIO_START]

    equity, spy_bh = 1.0, None
    open_pos = {}      # ticker -> dict(entry_px, prem_ret, entry_dt)
    closed = []
    curve = []
    spy_close = frames["SPY"]["close"]

    for dt in idx:
        broad = spy_sqn.get(dt, np.nan)
        regime_ok = broad >= 0.7
        regime_flip = broad <= -0.7

        # update / exit open positions
        for t in list(open_pos):
            f = frames[t]
            if dt not in f.index:
                continue
            i = f.index.get_loc(dt)
            r = f.iloc[i]
            pos = open_pos[t]
            wk_ret = r.close / f.iloc[i - 1].close - 1
            pos["prem_ret"] = max(-1.0, (1 + pos["prem_ret"])
                                  * (1 + LEVERAGE * wk_ret) - 1 - DRAG / 52)
            stop = r.close < r.ma19
            cut = pos["prem_ret"] <= CUT
            if stop or cut or regime_flip:
                equity += equity_at_entry_frac(pos) * pos["prem_ret"]
                closed.append(dict(t=t, entry=pos["entry_dt"], exit=dt,
                                   weeks=(dt - pos["entry_dt"]).days // 7,
                                   und_ret=r.close / pos["entry_px"] - 1,
                                   prem_ret=pos["prem_ret"],
                                   why="regime" if regime_flip else
                                       ("stop" if stop else "cut")))
                del open_pos[t]

        # entries
        if regime_ok and len(open_pos) < MAX_POS:
            sigs = []
            for t, f in frames.items():
                if t in open_pos or dt not in f.index:
                    continue
                i = f.index.get_loc(dt)
                if i < 205:
                    continue
                if entry_signal(f, i):
                    sigs.append((f.iloc[i].sqn, t))
            for _, t in sorted(sigs, reverse=True)[:MAX_POS - len(open_pos)]:
                f = frames[t]
                open_pos[t] = dict(entry_px=float(f.loc[dt, "close"]),
                                   prem_ret=0.0, entry_dt=dt,
                                   alloc_eq=equity)
        if spy_bh is None:
            spy_bh = float(spy_close.loc[dt])
        curve.append((dt, equity, float(spy_close.loc[dt]) / spy_bh))

    cv = pd.DataFrame(curve, columns=["dt", "strat", "spy"]).set_index("dt")
    tr = pd.DataFrame(closed)

    print(f"Period: {idx[0].date()} -> {idx[-1].date()}  "
          f"({(idx[-1]-idx[0]).days/365.25:.1f} yrs)")
    print(f"Closed trades: {len(tr)}   open now: {list(open_pos)}")
    if not tr.empty:
        for lbl, col in (("UNDERLYING (signal quality)", "und_ret"),
                         ("SYNTHETIC LEAPS (premium)", "prem_ret")):
            s = tr[col]
            print(f"\n{lbl}: WR {(s>0).mean()*100:.0f}%  avg {s.mean()*100:+.1f}%"
                  f"  median {s.median()*100:+.1f}%  best {s.max()*100:+.0f}%"
                  f"  worst {s.min()*100:+.0f}%")
        print(f"avg hold {tr.weeks.mean():.0f} wks   "
              f"exits: {tr.why.value_counts().to_dict()}")
        print("\nPer-ticker (n, WR, avg premium ret):")
        g = tr.groupby("t")["prem_ret"]
        for t, s in sorted(g, key=lambda x: -x[1].mean()):
            print(f"  {t:5s} n={len(s):3d}  WR {(s>0).mean()*100:3.0f}%  "
                  f"avg {s.mean()*100:+6.1f}%")
    yrs = (idx[-1] - idx[0]).days / 365.25
    st, sp = cv.strat.iloc[-1], cv.spy.iloc[-1]
    dd = (cv.strat / cv.strat.cummax() - 1).min()
    sdd = (cv.spy / cv.spy.cummax() - 1).min()
    print(f"\nPORTFOLIO ({ALLOC*100:.0f}% premium/position, {LEVERAGE}x delta lev, "
          f"{DRAG*100:.0f}%/yr drag):")
    print(f"  Strategy: {st:8.2f}x  CAGR {st**(1/yrs)-1:+7.2%}  MaxDD {dd:+.0%}")
    print(f"  SPY B&H : {sp:8.2f}x  CAGR {sp**(1/yrs)-1:+7.2%}  MaxDD {sdd:+.0%}")
    # time invested
    print(f"  Weeks with >=1 position: "
          f"{(tr.weeks.sum() if not tr.empty else 0)} of {len(cv)} "
          f"(rough exposure {tr.weeks.sum()/len(cv)*100:.0f}%, single-count)")


def equity_at_entry_frac(pos):
    return ALLOC * pos["alloc_eq"]


if __name__ == "__main__":
    main()
