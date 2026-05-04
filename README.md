# Trading Dashboard

**The trading dashboard that won't let you break your own rules.**

Built because I kept breaking mine.

A localhost-first web app + Python CLI that runs an opinionated indicator stack (MA Ribbon, Stochastic 14/7/7, SQN regime — 100-day + 20-day windows) against live market data, generates kill sheets with built-in discipline gates, and tracks positions through close.

Single-user, cash account, long calls/puts only. No spreads, no margin, no shared/multi-tenant instance.

**What's here:** browser dashboard (React + Vite + Tailwind, dark theme) covering regime header, scan, free-range scan, weekly trend, lotto, crypto (Crypto.com), focus (QQQ/GLD), kill sheet builder with options-input paste/screenshot, trade-devil, 15-rule discipline scorecard, 3-tranche pyramid state machine, positions, journal (P&L + discipline tabs), weekly review. CLI mirrors every action. 740 pytest tests.

**Data sources:** stocks/ETFs via yfinance (no key); crypto via Crypto.com public REST (no key); options via manual paste or screenshot upload (Anthropic vision, `ANTHROPIC_API_KEY` required — see "Screenshot privacy" below).

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

# Screenshot extraction (requires ANTHROPIC_API_KEY) — sends image to Claude vision
# to auto-fill strike/premium/IV/OI/spread from a broker options chain shot
ANTHROPIC_API_KEY=sk-... \
  python -m kill_sheet SPY --direction long \
    --screenshot ~/Desktop/spy-options-chain.png \
    --target 590 --invalidation 575

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

# Kill sheets now hit the account-rules engine before rendering. They are
# blocked (exit code 4) when opening would violate max_open_positions /
# max_premium_at_risk_pct / cash_floor_usd. Add --bypass-rules to override
# (logged); add --skip-rules to skip the check entirely.

# Help
python -m scan --help
python -m kill_sheet --help
python -m positions --help
```

### Screenshot privacy

`--screenshot` uploads the image to the Anthropic API for vision extraction. Nothing else does. If you don't pass `--screenshot`, no data leaves your machine. Get an API key at https://console.anthropic.com and set it as `ANTHROPIC_API_KEY`.

**Persistence:**
- Scans → `~/.trading-dashboard/scans/YYYY-MM-DD.json`
- Kill sheets → `~/.trading-dashboard/kill_sheets/<timestamp>-<ticker>-<direction>.{md,json}`
- Instrumentation events → `~/.trading-dashboard/events.jsonl`
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

The frontend reads `VITE_API_URL` (default `http://127.0.0.1:8000`). Persistent regime header (SPY/QQQ/IWM SQN(100) + SQN(20) + MA stack) plus full nav: Scan, Free-Range Scan, Weekly Trend, Lotto, Crypto, Focus (QQQ/GLD), Kill Sheet, Pyramid, Positions, Journal, Weekly Review. Dark trading-terminal theme. Manual refresh — no WebSockets in V1.

To build static assets: `cd frontend && npm run build` — outputs to `frontend/dist/`.

## Status

Production-personal. Tag `v0.1.0` ships when MA Ribbon and Stochastic match TradingView at >95% accuracy across 10 tickers — populate `tests/fixtures/truth/<TICKER>_ma_ribbon.csv` and `<TICKER>_stochastic.csv` with hand-sourced TradingView values (see `tests/fixtures/truth/README.md`); accuracy tests skip until fixtures exist, then fire automatically. SQN is excluded from the gate (regime-context indicator, no canonical external reference; covered by unit tests).

See `CONTEXT.md` for the story backlog and `HANDOFF.md` for the deep technical handoff. Source-of-truth specs live at `~/Documents/Product Specs/Trading Dashboard/`.
