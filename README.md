# Trading Dashboard

**A scaffold for building a trading-discipline dashboard that flags every rule break at kill-sheet time and scores it after the close.**

> Journal-first by design: the dashboard surfaces, records, and scores rule breaks — it does **not** hard-block you from opening a position (a deliberate 2026-05-10 decision so the journal can never refuse a real fill). Hard gates run at kill-sheet generation; `rules_blocked` + the violation list are persisted on the sheet and resurface in the discipline scorecard.

Built originally because the author kept breaking theirs. Released as an opinionated reference implementation you can fork, configure, or rip out and replace with your own strategy logic — see [CUSTOMIZATION.md](./CUSTOMIZATION.md) for how.

A localhost-first web app + Python CLI that runs an indicator stack (MA Ribbon, Stochastic 14/7/7, SQN regime — 100-day + 20-day windows) against live market data, generates kill sheets with built-in discipline gates, and tracks positions through close.

The bundled reference implementation is single-user, cash account, long calls/puts only. The framework around it (data layer, indicator plugin system, kill-sheet builder, positions store, journal, regime dashboard, trade-devil gate) is strategy-agnostic. Read CUSTOMIZATION.md to see what's keep-as-is vs replace-with-your-own.

**What's here:** browser dashboard (React + Vite + Tailwind, dark theme) covering a persistent regime header, regime health, scan, weekly trend, index swing, lotto, kill sheet builder with options-input paste, trade-devil, 15-rule discipline scorecard, positions, journal (P&L + discipline tabs), weekly review. Free-range scan runs via CLI/API (no dedicated view). CLIs cover scan, kill sheets, positions, journal, discipline, and free-range. ~1,260 pytest tests.

**Data sources:** stocks/ETFs via yfinance (no key); crypto via Crypto.com public REST (no key); options via manual paste from your broker. No API keys required.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend (only needed for the browser dashboard)
cd frontend && npm install && cd ..
```

## Configure your accounts

Account defaults are baked in for the original author's profile (`$10K main / $1K lotto`, cash account, long calls/puts only). To run with your own numbers, drop a YAML file at `~/.trading-dashboard/config.yaml` with only the fields you want to change:

```yaml
accounts:
  main:
    balance_usd: 25000
    max_open_positions: 8
    max_premium_at_risk_pct: 0.08
  lotto:
    balance_usd: 2500
    max_per_trade_usd: 250
```

Anything you don't override keeps its default. Full schema is in `src/config/loader.py` (`_DEFAULT_ACCOUNTS`). If you don't run a lotto book, leave the defaults alone — lotto views and rules only fire on `account=lotto` kill sheets and positions, so they stay dormant.

The header stage banner shows a live balance: by default it sums your config account balances + realized P&L on closed positions, which drifts from reality as soon as you deposit, withdraw, or true up. To pin it to your real broker number, add a balance anchor:

```yaml
balance:
  anchor_usd: 25310.50     # authoritative combined balance...
  anchor_date: 2026-07-01  # ...as of this date (trades closed later adjust it live)
```

Re-stamp the two lines whenever cash moves or the number drifts; between true-ups the banner tracks every closed trade.

## Run

```bash
# Scan one or more tickers
python -m scan SPY QQQ IWM

# Generate a Standard kill sheet (auto-fills indicators + position sizing)
python -m kill_sheet SPY --direction long --intent SWING --conviction high

# Apex options template — full options block + auto-fired trade devil
python -m kill_sheet SPY --direction long \
  --strike 580 --premium 5.50 --expiry 2026-06-19 --type call \
  --iv-rank 28 --oi 12000 --spread 0.05 \
  --target 590 --invalidation 575

# Lotto kill sheet (uses the $1K lotto account, lotto DTE band)
python -m kill_sheet GLD --direction long --account lotto --intent SCALP \
  --strike 250 --premium 0.80 --expiry 2026-05-09

# Interactive mode — prompts for any --target/--invalidation/--notes/options not set
python -m kill_sheet SPY --direction long --interactive

# Draft accuracy fixture CSVs for the v0.1.0 ship gate (numerics auto-filled
# from yfinance, categoricals left blank for TradingView verification)
python -m fixtures_draft SPY                       # both indicators to stdout
python -m fixtures_draft SPY --write               # writes to tests/fixtures/truth/SPY_*.csv
python -m fixtures_draft QQQ --indicator stochastic --days 30

# List user-authored indicator plugins (drop *.py files in ~/.trading-dashboard/plugins/)
python -m scan --list-plugins

# Log a trade you took outside the dashboard (for self-audit)
python -m scan --shadow-trade AAPL --note "caught the breakout, didn't use dashboard"

# Mark a prior flag as resolved
python -m scan --mark-resolved AAPL --note "closed at +40%"

# Position management (open/close/list)
python -m positions open SPY --instrument call --strike 580 --expiry 2026-06-19 \
  --premium 5.50 --contracts 1 --account main --invalidation 575 --target 600
python -m positions list
python -m positions list --all                    # include closed positions
python -m positions close <id> --pnl 87.50 --notes "took profits at 50%"
python -m positions show <id>
python -m positions alerts                        # evaluate alert rules vs fresh scans

# Discipline scorecard + weekly review
python -m discipline score <position_id>
python -m discipline weekly-review

# P&L analytics over closed positions
python -m journal stats

# Free-range scan: QQQ+GLD baseline → your tickers → top-5 free-range candidates
python -m free_range --user-tickers AAPL AMD

# Kill sheets now hit the account-rules engine before rendering. They are
# blocked (exit code 4) when opening would violate max_open_positions /
# max_premium_at_risk_pct / cash_floor_usd. Add --bypass-rules to override
# (logged); add --skip-rules to skip the check entirely.

# Help
python -m scan --help
python -m kill_sheet --help
python -m positions --help
```

**Persistence:**
- Scans → `~/.trading-dashboard/scans/YYYY-MM-DD.json`
- Kill sheets → `~/.trading-dashboard/kill_sheets/<id>.json`
- Positions → `~/.trading-dashboard/positions.json`
- Discipline scorecards → `~/.trading-dashboard/discipline/<position_id>.json` (weekly reviews under `discipline/weekly/`)
- Instrumentation events → `~/.trading-dashboard/events.jsonl`
- Market-data cache → `~/.trading-dashboard/cache.sqlite`
- User config → `~/.trading-dashboard/config.yaml` (optional; defaults baked in)
- User-authored indicators → `~/.trading-dashboard/plugins/*.py`

**Authoring an indicator plugin:** drop any `*.py` file in `~/.trading-dashboard/plugins/` exposing either a module-level `INDICATOR` instance or a class named `Indicator` that satisfies `IndicatorProtocol` (`name: str`, `inputs: list[str]`, `compute(df) -> pd.DataFrame`). Files starting with `_` are skipped.

## Test

```bash
pytest
```

## Browser dashboard (frontend)

```bash
# Terminal 1 — start the API server
python -m api --port 8000

# Terminal 2 — start the Vite dev server
cd frontend
npm install        # first time only
npm run dev        # opens http://localhost:5173
```

The frontend reads `VITE_API_URL` (default `http://127.0.0.1:8000`). Persistent regime header (SPY/QQQ/IWM SQN(100) + SQN(20) + MA stack) plus nav: a Scan group (Scan ticker, Weekly trend, Index swing, Regime-levered trend, Lotto) and top-level Regime, Kill Sheet, Positions, Journal, Weekly Review. Dark trading-terminal theme. Manual refresh — no WebSockets in V1.

To build static assets: `cd frontend && npm run build` — outputs to `frontend/dist/`.

## Status

Production-personal. Tag `v0.1.0` ships when MA Ribbon and Stochastic match TradingView at >95% accuracy across 10 tickers. Draft fixture CSVs for all 10 tickers are committed at `tests/fixtures/truth/` with numerics pre-filled from yfinance; the categorical truth columns (`stack_state`, `signal`) are still blank, so the accuracy tests skip until those are hand-labeled from TradingView (see `tests/fixtures/truth/README.md`), then fire automatically. SQN is excluded from the gate (regime-context indicator, no canonical external reference; covered by unit tests).

See [CUSTOMIZATION.md](./CUSTOMIZATION.md) for how to adapt the scaffold to your own trading style.
