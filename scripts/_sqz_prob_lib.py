"""SQZ PROB v3 — Python port of the TradingView Pine v6 indicator.

Source: a private TradingView Pine v6 script (sqz_prob_v3_2.pine) maintained
by the author. Not bundled with this repo — port it from your own copy if
you want to use this module.

Computes 6 component scores (squeeze, volume, momentum, price position, stoch
divergence, short interest) → bull/bear composites (0-100). SQN regime boost
and VIX filter applied on top. Mirrors the Pine implementation closely so the
backtest matches the TradingView output.

Skipped vs. Pine source:
  - Manual short-interest inputs (Pine accepts user-typed numbers). The
    backtest leaves these unset; si_has_data=False so SI weight is dropped
    and weights renormalize to the remaining 5 components.

Default constants match the Pine input defaults.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


# ─── Defaults from Pine inputs ──────────────────────────────────────────────

BB_LEN = 20
BB_MULT = 2.0
KC_LEN = 20
KC_MULT = 1.5
VOL_AVG_LEN = 20
VOL_SURGE_THRESH = 2.0
ROC_LEN = 10
RSI_LEN = 14
EMA_FAST = 9
EMA_SLOW = 21
STOCH_K_LEN = 14
STOCH_K_SMOOTH = 7
STOCH_D_SMOOTH = 3
DIV_LOOKBACK = 30
SQN_LEN = 100
SQN_BOOST = 0.25
GAP_PCT_THRESH = 4.0
GAP_RESET_BARS = 3
VIX_CAUTION = 20.0
VIX_FEAR = 30.0
WRAPPER_PCT_THRESH = 1.5
PIVOT_LEFT = 5
PIVOT_RIGHT = 5

# Component weights (must sum to ~100 when SI active; renormalized to remaining 5 when not)
W_SQUEEZE = 20.0
W_VOLUME = 20.0
W_MOMENTUM = 20.0
W_PRICE = 15.0
W_DIVERGENCE = 10.0
W_SI = 15.0

# Tickers Pine treats as wrapper-ETFs (commodity / fixed-income ETFs where
# the per-share short-interest signal is meaningless — flagged manually).
WRAPPER_ETFS: frozenset[str] = frozenset({
    "GLD", "SLV", "USO", "TLT", "HYG", "LQD", "UNG",
})


# ─── Primitives (Pine ta.*) ─────────────────────────────────────────────────


def _sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def _ema(s: pd.Series, n: int) -> pd.Series:
    """Pine ta.ema: SMA seed at bar n-1, then EMA after."""
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def _stdev(s: pd.Series, n: int) -> pd.Series:
    """Pine ta.stdev — sample stdev (ddof=1)."""
    return s.rolling(n, min_periods=n).std(ddof=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """Wilder ATR (Pine ta.atr default)."""
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def _rsi(close: pd.Series, n: int) -> pd.Series:
    """Wilder RSI (Pine ta.rsi)."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def _roc(close: pd.Series, n: int) -> pd.Series:
    """ta.roc = (close - close[n]) / close[n] * 100."""
    return (close - close.shift(n)) / close.shift(n) * 100


def _percent_rank(s: pd.Series, n: int) -> pd.Series:
    """Pine ta.percentrank: percent of past `n` values <= current. 0-100."""
    def _pr(window):
        cur = window[-1]
        if np.isnan(cur):
            return np.nan
        prior = window[:-1]
        prior = prior[~np.isnan(prior)]
        if len(prior) == 0:
            return np.nan
        return float((prior <= cur).mean() * 100)
    # n+1-wide rolling: n prior bars + current
    return s.rolling(n + 1, min_periods=n + 1).apply(_pr, raw=True)


def _stoch_k_raw(close: pd.Series, high: pd.Series, low: pd.Series, n: int) -> pd.Series:
    """ta.stoch — raw %K."""
    hh = high.rolling(n, min_periods=n).max()
    ll = low.rolling(n, min_periods=n).min()
    rng = hh - ll
    return ((close - ll) / rng.replace(0, np.nan)) * 100


def _pivot_high(s: pd.Series, left: int, right: int) -> pd.Series:
    """Pine ta.pivothigh — at bar t, return s[t-right] if it's a pivot high
    with `left` bars rising into it and `right` bars falling away, else NaN.

    Pine's pivothigh is bar-indexed forward: the pivot value appears at the
    bar `right` bars after the actual pivot (when confirmed by the right
    side). We mirror that for divergence-detection timing parity.
    """
    out = pd.Series(np.nan, index=s.index)
    vals = s.values
    for i in range(left + right, len(vals)):
        center = i - right
        if center - left < 0:
            continue
        c = vals[center]
        if np.isnan(c):
            continue
        left_window = vals[center - left:center]
        right_window = vals[center + 1:center + 1 + right]
        if len(left_window) < left or len(right_window) < right:
            continue
        if np.all(c > left_window) and np.all(c > right_window):
            out.iloc[i] = c
    return out


def _pivot_low(s: pd.Series, left: int, right: int) -> pd.Series:
    out = pd.Series(np.nan, index=s.index)
    vals = s.values
    for i in range(left + right, len(vals)):
        center = i - right
        if center - left < 0:
            continue
        c = vals[center]
        if np.isnan(c):
            continue
        left_window = vals[center - left:center]
        right_window = vals[center + 1:center + 1 + right]
        if len(left_window) < left or len(right_window) < right:
            continue
        if np.all(c < left_window) and np.all(c < right_window):
            out.iloc[i] = c
    return out


def _vwap_anchored_daily(high: pd.Series, low: pd.Series, close: pd.Series,
                         volume: pd.Series) -> pd.Series:
    """Pine ta.vwap(hlc3) — daily-anchored cumulative VWAP from intraday bars.
    Resets each calendar date in the index timezone."""
    hlc3 = (high + low + close) / 3.0
    df = pd.DataFrame({"hlc3": hlc3, "vol": volume}, index=high.index)
    df["pv"] = df["hlc3"] * df["vol"]
    # Anchor by date in the index's tz
    idx = df.index
    if hasattr(idx, "tz") and idx.tz is not None:
        anchor = idx.tz_convert("America/New_York").normalize()
    else:
        anchor = idx.normalize()
    g = df.groupby(anchor.values)
    cum_pv = g["pv"].cumsum()
    cum_v = g["vol"].cumsum()
    return (cum_pv / cum_v.replace(0, np.nan)).reindex(idx)


def _crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """a > b AND a[1] <= b[1]."""
    return (a > b) & (a.shift(1) <= b.shift(1))


def _crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a < b) & (a.shift(1) >= b.shift(1))


# ─── Public compute ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SqzProbInputs:
    """Optional knobs that override Pine defaults. Mostly here so a test or
    a future hyperparameter sweep can re-run with different settings."""
    bb_len: int = BB_LEN
    bb_mult: float = BB_MULT
    kc_len: int = KC_LEN
    kc_mult: float = KC_MULT
    vol_avg_len: int = VOL_AVG_LEN
    vol_surge_thresh: float = VOL_SURGE_THRESH
    roc_len: int = ROC_LEN
    rsi_len: int = RSI_LEN
    ema_fast: int = EMA_FAST
    ema_slow: int = EMA_SLOW
    stoch_k_len: int = STOCH_K_LEN
    stoch_k_smooth: int = STOCH_K_SMOOTH
    stoch_d_smooth: int = STOCH_D_SMOOTH
    div_lookback: int = DIV_LOOKBACK
    sqn_len: int = SQN_LEN
    sqn_boost: float = SQN_BOOST
    gap_pct_thresh: float = GAP_PCT_THRESH
    gap_reset_bars: int = GAP_RESET_BARS
    vix_caution: float = VIX_CAUTION
    vix_fear: float = VIX_FEAR
    wrapper_pct_thresh: float = WRAPPER_PCT_THRESH
    pivot_left: int = PIVOT_LEFT
    pivot_right: int = PIVOT_RIGHT


def compute_sqz_prob_v3(
    bars: pd.DataFrame,
    *,
    ticker: str | None = None,
    vix_close: pd.Series | None = None,
    si_ratio: float = 0.0,
    utilization: float = 0.0,
    ctb_rate: float = 0.0,
    inputs: SqzProbInputs | None = None,
) -> pd.DataFrame:
    """Compute the full SQZ PROB v3 stack on `bars`.

    Args:
        bars: DataFrame indexed by timestamp with columns:
            open, high, low, close, volume (lowercase or capitalized OK).
        ticker: optional — if in WRAPPER_ETFS, applies wrapper-ETF volume
            adjustment per Pine v3.
        vix_close: optional VIX close series. When provided + indexed to
            match `bars`, the VIX regime filter is applied. When None, the
            filter is disabled (Pine `use_vix_filter=false`).
        si_ratio, utilization, ctb_rate: optional short-interest inputs.
            Default 0 = no SI data (component weight redistributes).
        inputs: optional override of Pine defaults.

    Returns: a DataFrame indexed like `bars` with all intermediate component
    scores plus `bull_composite`, `bear_composite`, `net_score`.
    """
    ip = inputs or SqzProbInputs()

    def _col(name: str) -> str:
        return name if name in bars.columns else name.capitalize()

    o = bars[_col("open")].astype(float)
    h = bars[_col("high")].astype(float)
    l = bars[_col("low")].astype(float)
    c = bars[_col("close")].astype(float)
    v = bars[_col("volume")].astype(float)

    out = pd.DataFrame(index=bars.index)

    # ─── Component 1: Vol Squeeze (BB inside KC) ───────────────────────────
    bb_basis = _sma(c, ip.bb_len)
    bb_dev = ip.bb_mult * _stdev(c, ip.bb_len)
    bb_upper = bb_basis + bb_dev
    bb_lower = bb_basis - bb_dev
    bb_width = (bb_upper - bb_lower) / bb_basis * 100

    kc_basis = _sma(c, ip.kc_len)
    kc_range = ip.kc_mult * _atr(h, l, c, ip.kc_len)
    kc_upper = kc_basis + kc_range
    kc_lower = kc_basis - kc_range

    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    # Bar counters (Pine `var int squeeze_bars := squeeze_on ? squeeze_bars+1 : 0`)
    squeeze_bars = np.zeros(len(c), dtype=int)
    bars_since_release = np.full(len(c), 0, dtype=int)
    prior_was_squeezed = False
    rel_counter = 0
    for i in range(len(c)):
        on = bool(squeeze_on.iloc[i]) if not pd.isna(squeeze_on.iloc[i]) else False
        if on:
            squeeze_bars[i] = (squeeze_bars[i - 1] if i > 0 else 0) + 1
            rel_counter = 0
        else:
            squeeze_bars[i] = 0
            if prior_was_squeezed:
                rel_counter = 1
            elif rel_counter > 0:
                rel_counter += 1
            else:
                rel_counter = 0
        bars_since_release[i] = rel_counter
        prior_was_squeezed = on

    squeeze_energy = np.minimum(squeeze_bars / 10.0, 1.0) * 100
    release_signal = np.where(
        (bars_since_release >= 1) & (bars_since_release <= 5), 100.0, 0.0
    )
    bb_width_pctile = 100 - _percent_rank(bb_width, 100)
    compression_score = bb_width_pctile.clip(upper=100)

    squeeze_score = np.minimum(
        np.maximum.reduce([
            np.where(squeeze_on.fillna(False).values, squeeze_energy, 0.0),
            release_signal,
            (compression_score * 0.5).fillna(0).values,
        ]),
        100,
    )
    out["squeeze_score"] = squeeze_score
    out["squeeze_on"] = squeeze_on.fillna(False).values
    out["squeeze_bars"] = squeeze_bars

    # ─── Component 2: Volume Surge + wrapper-ETF adjustment ────────────────
    vol_avg = _sma(v, ip.vol_avg_len)
    vol_ratio = (v / vol_avg.replace(0, np.nan)).fillna(0.0)
    # Pine: ratio<=1 → 0; ratio>1 → linear up to surge_thresh = 50, plus
    # excess past surge_thresh
    def _vol_score_one(r: float) -> float:
        if r <= 1.0:
            return 0.0
        s = (r - 1.0) / (ip.vol_surge_thresh - 1.0) * 50
        if r > ip.vol_surge_thresh:
            s += (r - ip.vol_surge_thresh) / ip.vol_surge_thresh * 50
        return min(s, 100.0)

    vol_score_raw = vol_ratio.apply(_vol_score_one)
    # 3-bar simple avg (current + 2 prior)
    vol_score = (vol_score_raw + vol_score_raw.shift(1).fillna(0) + vol_score_raw.shift(2).fillna(0)) / 3.0

    # Wrapper ETF volume override
    daily_pct_change = ((c - c.shift(1)) / c.shift(1) * 100).abs()
    is_wrapper = ticker is not None and ticker.upper() in WRAPPER_ETFS
    wrapper_vol_unreliable = (
        is_wrapper & (daily_pct_change > ip.wrapper_pct_thresh) & (vol_score < 5.0)
    )
    vol_score_adj = vol_score.where(
        ~wrapper_vol_unreliable,
        (daily_pct_change / ip.wrapper_pct_thresh * 40).clip(upper=80.0),
    )
    out["vol_score"] = vol_score_adj
    out["wrapper_vol_unreliable"] = wrapper_vol_unreliable

    # ─── Gap detection ─────────────────────────────────────────────────────
    gap_pct = ((o - c.shift(1)) / c.shift(1) * 100).abs()
    gap_detected = (gap_pct > ip.gap_pct_thresh) & (vol_ratio > 3.0)

    bars_since_gap = np.full(len(c), 100, dtype=int)
    last_gap_dir = np.zeros(len(c), dtype=int)
    cur_dir = 0
    cur_bars = 100
    for i in range(len(c)):
        if bool(gap_detected.iloc[i]) and not pd.isna(o.iloc[i]) and not pd.isna(c.iloc[i - 1] if i > 0 else np.nan):
            cur_bars = 0
            cur_dir = 1 if o.iloc[i] > c.iloc[i - 1] else -1
        else:
            cur_bars = min(cur_bars + 1, 1000)
        bars_since_gap[i] = cur_bars
        last_gap_dir[i] = cur_dir

    gap_suppression_active = bars_since_gap < ip.gap_reset_bars
    out["gap_detected"] = gap_detected.fillna(False).values
    out["gap_suppression_active"] = gap_suppression_active
    out["last_gap_dir"] = last_gap_dir

    # ─── Component 3: Momentum (bidirectional) ─────────────────────────────
    roc_val = _roc(c, ip.roc_len)
    rsi_val = _rsi(c, ip.rsi_len)
    roc_pctile = _percent_rank(roc_val, 100)

    # Bullish
    roc_score_bull = roc_val.where(roc_val > 0, 0.0).clip(upper=100).where(roc_val > 0, 0.0)
    # Pine: roc_val > 0 ? min(roc_pctile, 100) : 0
    roc_score_bull = roc_pctile.where(roc_val > 0, 0.0).clip(upper=100)

    rsi_accel_bull = rsi_val - rsi_val.shift(3)
    rsi_score_bull = (rsi_accel_bull / 20.0 * 100).clip(upper=100)
    rsi_score_bull = rsi_score_bull.where((rsi_val > 50) & (rsi_accel_bull > 0), 0.0)

    # Green streak: close > open AND volume > prior volume
    green_bar = (c > o) & (v > v.shift(1).fillna(0))
    green_streak = green_bar.astype(int).groupby((~green_bar).cumsum()).cumsum()
    green_streak = green_streak.where(green_bar, 0)
    streak_score_bull = (green_streak / 5.0 * 100).clip(upper=100)

    momentum_bull_raw = (roc_score_bull.fillna(0) + rsi_score_bull.fillna(0) + streak_score_bull.fillna(0)) / 3.0

    # Bearish (inverted)
    roc_score_bear = (100 - roc_pctile).where(roc_val < 0, 0.0).clip(upper=100)

    rsi_accel_bear = rsi_val.shift(3) - rsi_val
    rsi_score_bear = (rsi_accel_bear / 20.0 * 100).clip(upper=100)
    rsi_score_bear = rsi_score_bear.where((rsi_val < 50) & (rsi_accel_bear > 0), 0.0)

    red_bar = (c < o) & (v > v.shift(1).fillna(0))
    red_streak = red_bar.astype(int).groupby((~red_bar).cumsum()).cumsum()
    red_streak = red_streak.where(red_bar, 0)
    streak_score_bear = (red_streak / 5.0 * 100).clip(upper=100)

    momentum_bear_raw = (roc_score_bear.fillna(0) + rsi_score_bear.fillna(0) + streak_score_bear.fillna(0)) / 3.0

    # Apply gap suppression
    momentum_bull = momentum_bull_raw.copy()
    momentum_bear = momentum_bear_raw.copy()
    suppress_bull = gap_suppression_active & (last_gap_dir == -1)
    suppress_bear = gap_suppression_active & (last_gap_dir == 1)
    momentum_bull[suppress_bull] = 0.0
    momentum_bear[suppress_bear] = 0.0

    out["momentum_bull"] = momentum_bull
    out["momentum_bear"] = momentum_bear

    # ─── Component 4: Price Position ───────────────────────────────────────
    ema_f = _ema(c, ip.ema_fast)
    ema_s = _ema(c, ip.ema_slow)
    vwap = _vwap_anchored_daily(h, l, c, v)

    # Bullish
    bull_fast = (c > ema_f).astype(float) * 30
    bull_slow = (c > ema_s).astype(float) * 30
    bull_vwap = (c > vwap).astype(float) * 20
    bull_cross_event = _crossover(ema_f, ema_s)
    bull_cross_state = (ema_f > ema_s).astype(float) * 10
    bull_cross = bull_cross_event.astype(float) * 20 + bull_cross_state * (~bull_cross_event)
    bull_reclaim = ((c > ema_f) & (c.shift(3) < ema_f.shift(3))).astype(float) * 10
    price_bull = (bull_fast + bull_slow + bull_vwap + bull_cross + bull_reclaim).clip(upper=100)

    # Bearish
    bear_fast = (c < ema_f).astype(float) * 30
    bear_slow = (c < ema_s).astype(float) * 30
    bear_vwap = (c < vwap).astype(float) * 20
    bear_cross_event = _crossunder(ema_f, ema_s)
    bear_cross_state = (ema_f < ema_s).astype(float) * 10
    bear_cross = bear_cross_event.astype(float) * 20 + bear_cross_state * (~bear_cross_event)
    bear_break = ((c < ema_f) & (c.shift(3) > ema_f.shift(3))).astype(float) * 10
    price_bear = (bear_fast + bear_slow + bear_vwap + bear_cross + bear_break).clip(upper=100)

    out["price_bull"] = price_bull
    out["price_bear"] = price_bear

    # ─── Component 5: Stoch Divergence ─────────────────────────────────────
    sk_raw = _stoch_k_raw(c, h, l, ip.stoch_k_len)
    sk = _sma(sk_raw, ip.stoch_k_smooth)

    price_low_piv = _pivot_low(l, ip.pivot_left, ip.pivot_right)
    price_high_piv = _pivot_high(h, ip.pivot_left, ip.pivot_right)
    stoch_low_piv = _pivot_low(sk, ip.pivot_left, ip.pivot_right)
    stoch_high_piv = _pivot_high(sk, ip.pivot_left, ip.pivot_right)

    # Walk forward tracking prev pivots + bars_since_div
    n = len(c)
    bars_since_bull_div = np.full(n, 100, dtype=int)
    bars_since_bear_div = np.full(n, 100, dtype=int)
    prev_price_low = np.nan
    prev_stoch_low = np.nan
    prev_price_high = np.nan
    prev_stoch_high = np.nan
    cur_bull = 100
    cur_bear = 100
    rb = ip.pivot_right
    for i in range(n):
        if i >= rb:
            # Pine compares to low[5] / stoch_k[5] at the pivot detection bar
            pl_val = price_low_piv.iloc[i]
            ph_val = price_high_piv.iloc[i]
            l5 = l.iloc[i - rb] if i - rb >= 0 else np.nan
            h5 = h.iloc[i - rb] if i - rb >= 0 else np.nan
            sk5 = sk.iloc[i - rb] if i - rb >= 0 else np.nan
            sl_val = stoch_low_piv.iloc[i]
            sh_val = stoch_high_piv.iloc[i]

            if not pd.isna(pl_val):
                if not pd.isna(prev_price_low) and not pd.isna(prev_stoch_low):
                    if l5 < prev_price_low and not pd.isna(sl_val) and sk5 > prev_stoch_low:
                        cur_bull = 0
                prev_price_low = l5
                if not pd.isna(sl_val):
                    prev_stoch_low = sk5

            if not pd.isna(ph_val):
                if not pd.isna(prev_price_high) and not pd.isna(prev_stoch_high):
                    if h5 > prev_price_high and not pd.isna(sh_val) and sk5 < prev_stoch_high:
                        cur_bear = 0
                prev_price_high = h5
                if not pd.isna(sh_val):
                    prev_stoch_high = sk5

        bars_since_bull_div[i] = cur_bull
        bars_since_bear_div[i] = cur_bear
        cur_bull += 1
        cur_bear += 1

    div_bull_score = np.where(
        bars_since_bull_div <= ip.div_lookback,
        np.maximum(100 - bars_since_bull_div / ip.div_lookback * 100, 0),
        0.0,
    )
    div_bear_score = np.where(
        bars_since_bear_div <= ip.div_lookback,
        np.maximum(100 - bars_since_bear_div / ip.div_lookback * 100, 0),
        0.0,
    )
    out["div_bull_score"] = div_bull_score
    out["div_bear_score"] = div_bear_score

    # ─── Component 6: Short Interest ───────────────────────────────────────
    si_has_data = (si_ratio > 0) or (utilization > 0) or (ctb_rate > 0)

    def _sir_score(sir: float) -> float:
        if sir <= 1:
            return 0.0
        if sir <= 3:
            return (sir - 1) / 2 * 30
        if sir <= 5:
            return 30 + (sir - 3) / 2 * 30
        if sir <= 7:
            return 60 + (sir - 5) / 2 * 20
        return 80 + min((sir - 7) / 3 * 20, 20)

    def _util_score(u: float) -> float:
        if u <= 20:
            return 0.0
        return min((u - 20) / 80 * 100, 100)

    def _ctb_score(ctb: float) -> float:
        if ctb <= 1:
            return 0.0
        if ctb <= 10:
            return (ctb - 1) / 9 * 30
        if ctb <= 30:
            return 30 + (ctb - 10) / 20 * 30
        return 60 + min((ctb - 30) / 70 * 40, 40)

    if si_has_data:
        si_score = (
            _sir_score(si_ratio) + _util_score(utilization) + _ctb_score(ctb_rate)
        ) / 3.0
    else:
        si_score = 0.0

    # ─── SQN regime ─────────────────────────────────────────────────────────
    sqn_avg = _sma(c, ip.sqn_len)
    sqn_std = _stdev(c, ip.sqn_len)
    sqn_val = ((c - sqn_avg) / sqn_std.replace(0, np.nan) * np.sqrt(ip.sqn_len) / ip.sqn_len)
    sqn_regime = pd.Series(0, index=c.index, dtype=int)
    sqn_regime = sqn_regime.where(~(sqn_val > 1.5), 2)
    sqn_regime = sqn_regime.where(~((sqn_val > 0.5) & (sqn_val <= 1.5)), 1)
    sqn_regime = sqn_regime.where(~((sqn_val > -0.5) & (sqn_val <= 0.5)), 0)
    sqn_regime = sqn_regime.where(~((sqn_val > -1.5) & (sqn_val <= -0.5)), -1)
    sqn_regime = sqn_regime.where(~(sqn_val <= -1.5), -2)
    out["sqn_regime"] = sqn_regime.fillna(0).astype(int)

    # ─── VIX filter ────────────────────────────────────────────────────────
    if vix_close is not None and not vix_close.empty:
        vix_aligned = vix_close.reindex(c.index, method="ffill")
        vix_prev = vix_aligned.shift(1)
        vix_rising = vix_aligned > vix_prev
        vix_regime = pd.Series(0, index=c.index, dtype=int)
        vix_regime = vix_regime.where(~(vix_aligned >= ip.vix_fear), 2)
        vix_regime = vix_regime.where(
            ~((vix_aligned >= ip.vix_caution) & (vix_aligned < ip.vix_fear)), 1
        )
        vix_bull_mult = pd.Series(1.0, index=c.index)
        vix_bull_mult = vix_bull_mult.where(~((vix_regime == 2) & vix_rising), 0.70)
        vix_bull_mult = vix_bull_mult.where(
            ~((vix_regime == 2) & ~vix_rising), 0.85,
        ) if True else vix_bull_mult
        # Re-apply since the prior masked write may not be commutative when
        # the same bar matches multiple conditions; use explicit np.select.
        cond_bull = [
            (vix_regime == 2) & vix_rising,
            (vix_regime == 2) & ~vix_rising,
            (vix_regime == 1) & vix_rising,
        ]
        choices_bull = [0.70, 0.85, 0.90]
        vix_bull_mult = pd.Series(
            np.select(cond_bull, choices_bull, default=1.0), index=c.index
        )
        cond_bear = [
            (vix_regime == 2) & vix_rising,
            (vix_regime == 2) & ~vix_rising,
            (vix_regime == 1) & vix_rising,
        ]
        choices_bear = [1.20, 1.10, 1.05]
        vix_bear_mult = pd.Series(
            np.select(cond_bear, choices_bear, default=1.0), index=c.index
        )
    else:
        vix_bull_mult = pd.Series(1.0, index=c.index)
        vix_bear_mult = pd.Series(1.0, index=c.index)

    # ─── Composite ─────────────────────────────────────────────────────────
    w_total = W_SQUEEZE + W_VOLUME + W_MOMENTUM + W_PRICE + W_DIVERGENCE + (W_SI if si_has_data else 0)

    sqz = pd.Series(squeeze_score, index=c.index)
    bull_raw = (
        sqz * W_SQUEEZE
        + vol_score_adj.fillna(0) * W_VOLUME
        + momentum_bull.fillna(0) * W_MOMENTUM
        + price_bull * W_PRICE
        + pd.Series(div_bull_score, index=c.index) * W_DIVERGENCE
        + (si_score * W_SI if si_has_data else 0)
    ) / w_total
    bear_raw = (
        sqz * W_SQUEEZE
        + vol_score_adj.fillna(0) * W_VOLUME
        + momentum_bear.fillna(0) * W_MOMENTUM
        + price_bear * W_PRICE
        + pd.Series(div_bear_score, index=c.index) * W_DIVERGENCE
        + (si_score * W_SI if si_has_data else 0)
    ) / w_total

    # SQN multiplier
    bull_sqn_mult = pd.Series(1.0, index=c.index)
    bear_sqn_mult = pd.Series(1.0, index=c.index)
    bull_sqn_mult[sqn_regime >= 1] = 1 + ip.sqn_boost
    bull_sqn_mult[sqn_regime <= -1] = 1 - ip.sqn_boost * 0.5
    bear_sqn_mult[sqn_regime <= -1] = 1 + ip.sqn_boost
    bear_sqn_mult[sqn_regime >= 1] = 1 - ip.sqn_boost * 0.5

    bull_composite = (bull_raw * bull_sqn_mult * vix_bull_mult).clip(upper=100)
    bear_composite = (bear_raw * bear_sqn_mult * vix_bear_mult).clip(upper=100)
    net_score = bull_composite - bear_composite

    out["bull_composite"] = bull_composite
    out["bear_composite"] = bear_composite
    out["net_score"] = net_score
    return out


__all__ = [
    "compute_sqz_prob_v3",
    "SqzProbInputs",
    "WRAPPER_ETFS",
]
