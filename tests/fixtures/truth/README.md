# Truth-Value Fixtures

Per-(ticker, indicator) CSV files used as the ground truth for accuracy testing. Values are sourced by hand from TradingView charts with the corresponding indicator applied, then committed here.

## File naming

`<TICKER>_<indicator>.csv`

Examples:
- `SPY_ma_ribbon.csv`
- `QQQ_stochastic.csv`
- `IWM_sqn.csv`

## Format

First column must be `date` in ISO format (`YYYY-MM-DD`). Remaining columns match the indicator's output DataFrame columns exactly — column names and values.

### MA Ribbon

```
date,ma_10,ma_20,ma_50,ma_200,stack_state
2026-01-02,580.12,578.45,572.33,548.20,full_bull
2026-01-03,581.05,579.11,572.78,548.55,full_bull
```

`stack_state` values: `full_bull`, `bull_developing`, `compression`, `chop`, `bear_developing`, `full_bear`.

### Stochastic (14,7,7)

```
date,k,d,zone,signal
2026-01-02,75.3,72.1,mid,neutral
2026-01-03,82.4,76.8,overbought,neutral
2026-01-06,79.1,78.3,mid,bear_cross_overbought
```

`zone` values: `oversold` (K<20), `mid` (20<=K<=80), `overbought` (K>80).

`signal` values (per-bar events; `neutral` when no event fires): `bull_cross_oversold`, `bear_cross_overbought`, `bull_continuation`, `bear_continuation`, `bullish_divergence`, `bearish_divergence`, `neutral`.

### SQN (100-day)

```
date,sqn_value,regime
2026-01-02,1.72,strong_bull
2026-01-03,1.68,strong_bull
```

`regime` values: `strong_bull`, `bull`, `neutral`, `bear`, `strong_bear`.

## How to source truth values

1. Open the ticker in TradingView on the daily chart.
2. **Set chart to unadjusted prices.** Settings (gear icon) → Symbol tab → uncheck "Adjustment for dividends" and "Adjustment for splits". Our production yfinance loader uses `auto_adjust=False`, so the truth fixtures must match raw bars, not split/dividend-adjusted ones. Forgetting this step is the most common source of accuracy-test failures on dividend-paying single names (AAPL, MSFT, META) and ETFs (GLD, SPY).
3. Apply the matching indicator (MA Ribbon 10/20/50/200, Stochastic 14/7/7, SQN 100-day).
4. Right-click chart → Show Data Window.
5. Read the per-bar values for the dates you want and paste into the CSV.
6. Spot-check by hand on a few rows — this is the ground truth and must be correct.

The 30 template CSVs in this directory (one per ticker × indicator) are pre-named with header rows; just paste data rows under each. Fixtures with fewer than 5 data rows are auto-skipped by `tests/test_accuracy.py`, so you can fill them in incrementally without breaking the suite.

## v0.1 ship gate

10 tickers: SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMD, TSLA, META, GLD.

Minimum 20 daily bars per fixture. More is better — more bars raise confidence in the accuracy score. Target: >=95% row-level pass rate across all fixtures for each indicator.

## Tolerance defaults

- Numeric columns (MA values, K, D, SQN value): 1% relative tolerance.
- Categorical columns (`stack_state`, `signal`, `regime`): exact string match.

Per-indicator tests can override these in `tests/test_accuracy.py`.
