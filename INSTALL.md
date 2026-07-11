# Install Guide — Trading Dashboard v0.1

For: a friend who's about to clone this repo and run it locally.

This is a localhost-only personal trading discipline tool — single user, no auth, no shared instance. You run it on your own machine; your data stays on your machine. The original author's account profile (`$10K main / $1K lotto`) is the default; you'll point it at your own numbers in step 4.

Estimated setup time: 5–10 minutes if your prereqs are in place, 20–30 minutes if you need to install Python/Node from scratch.

---

## 1. Prereqs

| Tool | Version | Check | Install |
|---|---|---|---|
| Python | 3.11+ | `python3 --version` | macOS: `brew install python@3.12` · Linux: package manager · Windows: python.org installer |
| Node.js | 20+ (Vite 8 supports 18/20/22+; 20 LTS or newer recommended) | `node --version` | `brew install node` or [nodejs.org](https://nodejs.org/) |
| git | any recent | `git --version` | usually preinstalled |

If `python3 --version` shows 3.10 or older, install 3.12 alongside the system Python — don't replace it. On macOS, `brew install python@3.12` puts a `python3.12` on your PATH that you can use explicitly.

You do **not** need: Docker, a database, or any API keys.

---

## 2. Clone

```bash
git clone https://github.com/aaron2233/trading-dashboard-v1.git
cd trading-dashboard-v1
```

---

## 3. Install dependencies

```bash
# Python — create venv and install editable
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Frontend
cd frontend
npm install
cd ..
```

The `pip install` pulls pandas, yfinance, FastAPI, uvicorn, pydantic, pyyaml, plus pytest + httpx for the test suite. About 60 MB of dependencies; takes 1–3 minutes on a normal connection.

`npm install` pulls React 19, Vite 8, Tailwind, lightweight-charts, and types. About 200 MB of `node_modules`; takes 1–2 minutes.

---

## 4. Configure your accounts

The dashboard ships with the original author's profile baked in (`$10K main / $1K lotto`, cash account, long calls/puts only). To use your own numbers, create `~/.trading-dashboard/config.yaml`:

```bash
mkdir -p ~/.trading-dashboard
```

Then drop a YAML file at `~/.trading-dashboard/config.yaml` with whatever fields you want to override:

```yaml
accounts:
  main:
    balance_usd: 25000          # your actual main account balance
    max_open_positions: 8       # cap on concurrent positions
    max_premium_at_risk_pct: 0.08   # 8% max premium at risk across open positions
    risk_per_trade:
      high: 0.025               # 2.5% per high-conviction trade
      medium: 0.015
      speculative: 0.0075
  lotto:
    balance_usd: 2500
    max_per_trade_usd: 250      # hard cap per lotto trade
```

Anything you don't override keeps its default. The full schema (every field, every default) is in `src/config/loader.py` (`_DEFAULT_ACCOUNTS`).

**If you don't trade a lotto book**, leave it alone — the lotto views and rules only fire on `account=lotto` kill sheets/positions, so they stay dormant. Same for `weekly` (the longer-horizon position account).

---

## 5. First run — smoke test

```bash
# In your terminal with the venv active:
python -m scan SPY
```

Expected output: a tabular indicator readout for SPY (MA stack, Stochastic K/D, SQN regime). If you see that, the indicator engine + yfinance pipeline is working.

If yfinance times out or rate-limits (rare but possible from fresh installs), retry once. If it still fails, check your network and try a different ticker.

Now the browser dashboard:

```bash
# Terminal 1 — API server (keep venv active)
python -m api --port 8000

# Terminal 2 — frontend (NEW terminal, NO venv needed)
cd frontend
npm run dev
```

Open http://localhost:5173. You should see the dark-themed dashboard with a regime header (SPY/QQQ/IWM) up top and a nav with a Scan group (Scan ticker, Weekly trend, Index swing, Regime-levered trend, Lotto) plus Regime, Kill Sheet, Positions, Journal, Weekly Review.

Click **Scan**, type a ticker, hit scan. If you get an indicator panel back, you're done.

---

## 6. Run the test suite (optional sanity check)

```bash
pytest
```

You should see ~1,260 tests collected, with ~28 skipping (TradingView-fixture accuracy tests skip until the truth CSVs are hand-labeled), in 3–5 minutes. If tests fail, that's worth flagging back.

---

## 7. Where state lives

Everything the dashboard writes goes to `~/.trading-dashboard/`:

- `scans/YYYY-MM-DD.json` — raw scan output, one per day
- `kill_sheets/<id>.json` — every kill sheet you generate
- `positions.json` — your open + closed positions
- `discipline/<position_id>.json` — discipline scorecard per closed trade (weekly reviews under `discipline/weekly/`)
- `regime_health/` — regime-health snapshot history
- `events.jsonl` — instrumentation log (useful for self-audit)
- `cache.sqlite` — market-data cache (safe to delete; rebuilt on demand)
- `config.yaml` — your account overrides
- `plugins/*.py` — your indicator plugins (optional)

Backing up `~/.trading-dashboard/` backs up your trading history. Deleting it resets you to clean state. None of it is committed to the repo — your data never leaves your machine.

---

## 8. Common gotchas

| Symptom | Likely cause | Fix |
|---|---|---|
| `python: command not found` | macOS default has `python3` but not `python` | Use `python3` everywhere, or `alias python=python3` |
| `pip install` fails on `pandas` build | Missing C compiler (Linux fresh install) | `sudo apt install build-essential python3-dev` |
| API server says "address already in use" | Port 8000 taken by something else | `python -m api --port 8001` and set `VITE_API_URL=http://127.0.0.1:8001` in `frontend/.env.local` |
| Frontend loads but shows "API unreachable" | API server not running, or wrong port | Check terminal 1, check `VITE_API_URL` |
| yfinance returns empty data | Yahoo rate-limiting, or ticker malformed | Wait 60 seconds, retry; check ticker spelling |
| Scanning a crypto symbol fails | Crypto.com REST is region-blocked in some countries | Stocks/ETFs still work; crypto is optional |
| `import yfinance` slow on first scan | yfinance lazy-loads its own deps | One-time delay; subsequent scans are fast |

---

## 9. What's NOT in scope

Things this dashboard intentionally does not do:

- No spreads, strangles, condors, margin strategies — long calls / long puts only
- No order placement — this is a discipline tool, you place trades in your broker
- No paid market-data feeds — yfinance + Crypto.com public REST only
- No multi-user — there's no auth, no user accounts, no shared deployment
- No mobile app — desktop browser only
- No live options data — options input is manual paste from your broker (yfinance options data is too stale to trust for kill sheets)

If something here annoys you in a way that feels like a bug rather than a design choice, that's worth flagging back.

---

## 10. Where to look next

- `README.md` — high-level project description, full feature inventory, CLI reference
- `src/config/loader.py` — full account schema (every field you can override)
- `tests/` — ~1,260 examples of how the API and modules behave

Questions, weirdness, or things the README doesn't cover — open an issue on the repo. If a doc gap is real, it's worth patching.
