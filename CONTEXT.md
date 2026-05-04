# Trading Dashboard v0.1 — Context

## What this is

Python CLI implementation of the indicator engine from the Trading Dashboard PRD. Validates indicator accuracy against TradingView before any UI gets built.

## Source of truth

- **PRD:** `~/_bmad-output/planning-artifacts/prd.md`
- **Spec handoffs:** `~/Documents/Product Specs/Trading Dashboard/`
- **Methodology skills (authoritative trading logic):** `~/.claude/skills/user/trading-edge/`, `apex-options-trader/`, `lotto-options/`, `weekly-trend-trader/`, `trade-devil/`
- **Orchestrator rules:** `~/CLAUDE.md`
- **PineScript reference implementations:** `~/.claude/skills/user/trading-edge/assets/*.pine`

## v0.1 scope

- Three indicators implementing `IndicatorProtocol`:
  - MA Ribbon (10/20/50/200) with 6-state stack classification
  - Stochastic (14/7/7) with 7 signal types
  - SQN Regime (100-day) with 5-regime classification
- `yfinance` data loader (hardcoded; provider abstraction deferred to v0.2)
- TradingView snapshot accuracy harness (>95% match required)
- `scan` CLI that prints per-ticker state
- Shadow-trade + stress-time event loggers (JSONL, `~/.trading-dashboard/events.jsonl`)

## Explicitly NOT in v0.1

React/Vite UI, FastAPI, SQLite, plugin loader, Alpaca provider, watchlist scanner, kill sheets, options structure builder, trade devil gate, account rules engine, TradingView widget, streak counters, journal.

## Ship gate

- [ ] All three indicators pass accuracy harness at >95% across 10 tickers (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMD, TSLA, META, GLD)
- [ ] `python -m scan SPY QQQ IWM` runs end-to-end in <10s on warm cache
- [ ] Shadow-trade + stress-time loggers write to events file
- [ ] Tagged `v0.1.0` in git

## Story backlog (from Bob's sprint plan)

1. Scaffold repo + `IndicatorProtocol` contract — **in progress**
2. TradingView truth-value snapshot harness
3. Port MA Ribbon from PineScript
4. Port Stochastic (14/7/7) from PineScript
5. Port SQN Regime (100-day) from PineScript
6. `scan` CLI entry point
7. Shadow-trade + stress-time loggers
