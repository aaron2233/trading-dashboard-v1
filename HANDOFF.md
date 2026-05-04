# Trading Dashboard v0.2 — Session Handoff

Snapshot for resuming in a fresh Claude session. **As of 2026-04-28**.

---

## What this is

A localhost-only, BYOK, OSS trading discipline engine. Frame: "the trading dashboard that won't let you break your own rules." Built as a marketing-asset / open-source portfolio piece for franky (solo trader using AI) — *not* a company. Awareness is the goal, monetization optional. See [Conversation Archive: 2026-04-22-2126-trading-dashboard-build-out.md](#archived-conversations) for the full strategic context if available.

**Core thesis:** discipline is a UX problem, not a willpower problem. The path to a trade *is* the methodology.

---

## Status — what shipped

37 stories, **424 pytest tests passing + 30 skipped** (skipped = TradingView fixtures not yet populated), TypeScript frontend builds clean (7 routes).

| # | Story | Module |
|---|---|---|
| 1 | Repo scaffold + IndicatorProtocol contract | `src/indicators/protocol.py` |
| 2 | TradingView truth-value harness | `src/testing/accuracy_harness.py` |
| 3 | MA Ribbon (10/20/50/200, 6 stack states, SMA) | `src/indicators/ma_ribbon.py` |
| 4 | Stochastic (14/7/7, 7 signal types) | `src/indicators/stochastic.py` |
| 5 | SQN Regime (100-day log returns, 5 regimes) | `src/indicators/sqn_regime.py` |
| 6 | scan CLI | `src/scan.py` |
| 7 | Shadow-trade + flag event loggers | `src/events/log.py` |
| 8 | Plugin loader (importlib, `~/.trading-dashboard/plugins/*.py`) | `src/indicators/loader.py` |
| 9 | Standard kill sheet (bias, confidence, sizing) | `src/kill_sheet/` |
| 11 | Trade devil (8 categories, KILL/FLAG/PASS) | `src/trade_devil/` |
| 13 | Apex options template (DTE/delta/IV/liquidity) | `src/kill_sheet/options.py` |
| 15 | Multi-timeframe bars (4H resampled, weekly native) | `src/data/yfinance_loader.py` |
| 18 | Account rules engine + position manager | `src/positions/` |
| 21 | Manual input + interactive mode + sizing cap | `src/kill_sheet/cli.py` |
| 22 | Screenshot extraction (Claude vision) | `src/vision/options_extractor.py` |
| 23 | Position alerts (DTE / target / invalidation / MA flip) | `src/positions/alerts.py` |
| 24 | Trade journal (P&L analytics from closed positions) | `src/journal/` |
| 25 | FastAPI HTTP backend (versioned `/api/v1/`) | `src/api/` |
| 26 | React/Vite frontend scaffold (Tailwind dark theme) | `frontend/` |
| 27 | Position + Journal UI views | `frontend/src/views/{Positions,Journal}View.tsx` |
| 28 | Crypto support via Crypto.com Exchange REST | `src/data/crypto_loader.py` |
| 29 | qqq-gld-focus mode toggle (`--focus` on scan + kill_sheet) | `src/positions/focus_rules.py`, `src/scan.py`, `src/kill_sheet/cli.py` |
| 30 | Sunday scan view: scoring engine + `/api/v1/focus/sunday-scan` + `/focus` UI route | `src/focus/sunday_scan.py`, `src/api/app.py`, `frontend/src/views/SundayScanView.tsx` |
| 31 | Pre-write kill sheet from Focus: deep-link from SundayScanView → KillSheetView via URL params, focus banner on the kill sheet form | `frontend/src/views/SundayScanView.tsx`, `frontend/src/views/KillSheetView.tsx` |
| 32 | Persist Sunday scans to `~/.trading-dashboard/sunday_scans/YYYY-MM-DD.json` with `?persist=` opt-out and disk-failure tolerance | `src/focus/sunday_scan.py`, `src/api/app.py` |
| 33 | Recent scans listing: `/api/v1/focus/sunday-scan/recent` + saved-at indicator + Recent Scans strip on the Focus view | `src/focus/sunday_scan.py`, `src/api/app.py`, `frontend/src/views/SundayScanView.tsx` |
| 34 | Focus options-structure gates: $200 risk cap + per-asset DTE bands (QQQ 30-45/21-30, GLD 45-60/30-45). Wired through CLI + API + frontend (`focus: true` on KillSheetRequest) | `src/positions/focus_rules.py`, `src/kill_sheet/cli.py`, `src/api/app.py`, `frontend/src/views/KillSheetView.tsx` |
| 35 | Per-scan retro view: `GET /api/v1/focus/sunday-scan/{date}` + `/focus/:date` route + clickable Recent Scans rows | `src/focus/sunday_scan.py`, `src/api/app.py`, `frontend/src/views/SundayScanRetroView.tsx`, `frontend/src/views/SundayScanView.tsx` |
| 36 | Outcome attribution: `/api/v1/focus/sunday-scan/{date}/outcome` matches journal positions to recommendations (7-day window, ticker+direction); retro view shows Followed/Skipped/Closed-Winner/etc. + realized P&L | `src/focus/outcomes.py`, `src/api/app.py`, `frontend/src/views/SundayScanRetroView.tsx` |
| 37 | Cross-scan summary: `iter_saved_scans` + `summarize_recent_outcomes` + `/api/v1/focus/summary?weeks=N` + horizontal summary strip on `/focus` (scans/recs/followed/skipped/open/realized P&L). Cash weeks correctly excluded from `skipped_count` | `src/focus/outcomes.py`, `src/focus/sunday_scan.py`, `src/api/app.py`, `frontend/src/views/SundayScanView.tsx` |

The discipline loop is end-to-end functional: `scan → kill sheet → rules pre-flight → trade devil → KILL/PROCEED → open position → alerts → close position → journal stats`.

---

## Where things live

**Working directory:** `/Users/aaronrennell/Documents/App Development/Trading Dashboard/v0.1/`

```
v0.1/
├── pyproject.toml             pandas, yfinance, pyyaml, anthropic, fastapi, uvicorn
├── README.md                  user-facing docs
├── CONTEXT.md                 story backlog + spec source-of-truth links
├── HANDOFF.md                 this file
├── .venv/                     Python 3.11.3 venv (created during initial build)
├── src/
│   ├── config/loader.py       YAML config + CLAUDE.md-sourced defaults
│   ├── data/yfinance_loader.py
│   ├── data/crypto_loader.py    Crypto.com Exchange public REST
│   ├── events/log.py
│   ├── indicators/{protocol, loader, ma_ribbon, stochastic, sqn_regime}.py
│   ├── kill_sheet/            model.py, builder.py, bias.py, sizing.py,
│   │                          options.py, multi_tf.py, cli.py, __main__.py
│   ├── positions/             model.py, store.py, rules.py, alerts.py, cli.py
│   ├── trade_devil/           verdict.py, categories.py, runner.py
│   ├── journal/               stats.py, cli.py, __main__.py
│   ├── vision/options_extractor.py
│   ├── api/                   app.py, models.py, __main__.py
│   ├── testing/accuracy_harness.py
│   └── scan.py                scan_ticker, compute_multi_tf, scan CLI
├── tests/                     297 unit + 30 fixture-pending tests
│   └── fixtures/truth/        empty CSVs awaiting TradingView data
└── frontend/                  React 19 + TypeScript strict + Vite 6 + Tailwind
    ├── src/
    │   ├── api/{client,types}.ts
    │   ├── components/RegimeHeader.tsx
    │   ├── views/{Home,Scan,KillSheet,Positions,Journal}View.tsx
    │   ├── App.tsx, main.tsx
    │   └── index.css
    ├── preview.html           standalone single-file UI demo (no build needed)
    └── package.json, tsconfig*, vite/tailwind/postcss configs
```

**State directories** (all under `~/.trading-dashboard/`, BYOK style):
- `config.yaml` — optional user overrides; defaults baked into `src/config/loader.py`
- `positions.json` — open + closed positions
- `events.jsonl` — flag / shadow_trade / resolved events
- `kill_sheets/<timestamp>-<ticker>-<dir>.{md,json}` — generated kill sheets
- `scans/YYYY-MM-DD.json` — daily scan output
- `sunday_scans/YYYY-MM-DD.json` — focus Sunday scan snapshot (regime + ranked setups + recommendation)
- `plugins/*.py` — user-authored indicator plugins (auto-discovered)

---

## Source-of-truth references

**These are the inputs the project must faithfully implement — not reinterpret:**

| Path | What it owns |
|---|---|
| `~/CLAUDE.md` | Account profile (cash, $10K main / $1K lotto, 2-3% high-conviction risk, $15-50 stocks ETFs any). Skill routing rules. Anti-patterns. |
| `~/_bmad-output/planning-artifacts/prd.md` | The full PRD (467 lines). Phase 1a / 1b / 2 / 3 scope. FRs, NFRs, journeys. |
| `~/Documents/Product Specs/Trading Dashboard/TRADING-DASHBOARD-HANDOFF.md` | **Authoritative indicator math + dashboard module mapping.** SMA for MA Ribbon (not WMA) is confirmed here at line 315-318. |
| `~/.claude/skills/user/trading-edge/SKILL.md` | Kill sheet output format (lines 144-198), 5-step decision tree, setup library. |
| `~/.claude/skills/user/weekly-trend-trader/references/sqn-regime-guide.md` | SQN formula + regime thresholds + benchmark selection. |
| `~/.claude/skills/user/weekly-trend-trader/references/ma-ribbon-patterns.md` | 6 ribbon states + entry/stop/target per pattern (note: this doc says WMA but handoff says SMA — handoff wins). |
| `~/.claude/skills/user/trade-devil/SKILL.md` | 8 kill categories with KILL/FLAG/PASS thresholds + aggregate verdict logic. |
| `~/.claude/skills/user/apex-options-trader/SKILL.md` | Delta-by-conviction tables, DTE-by-trigger-TF, liquidity rules. |
| `~/.claude/skills/user/qqq-gld-focus/SKILL.md` | Focused two-asset playbook (QQQ + GLD only). Daily filter + 2H trigger. Sunday scan workflow. Max 2 concurrent positions, $200 risk/trade, no same-direction pair, 3-day cool-off after a stop. Asset-precedence: when scan or kill-sheet target is QQQ or GLD, this skill's rules win over apex/trading-edge defaults. Installed 2026-04-28. |
| `~/Documents/Product Specs/Trading Dashboard/DASHBOARD-SPEC-HANDOFF.md` | Spec-phase Party Mode handoff with decisions made + open questions. |

---

## How to run

```bash
cd "~/Documents/App Development/Trading Dashboard/v0.1"

# Activate venv
source .venv/bin/activate

# Run tests
pytest -q

# CLIs (all use ~/.trading-dashboard/ for state)
python -m scan SPY QQQ IWM
python -m scan --shadow-trade AAPL --note "took outside dashboard"
python -m scan --list-plugins

python -m kill_sheet SPY --direction long --intent SWING --conviction high
python -m kill_sheet SPY --direction long \
  --strike 580 --premium 5.50 --expiry 2026-06-19 --type call \
  --iv-rank 28 --oi 12000 --spread 0.05 \
  --target 590 --invalidation 575
python -m kill_sheet SPY --direction long --interactive   # prompts for missing fields
python -m kill_sheet SPY --direction long --screenshot ~/Desktop/options.png

python -m positions list
python -m positions open SPY --instrument call \
  --strike 580 --expiry 2026-06-19 --premium 5.50 --contracts 1 \
  --account main --invalidation 575 --target 600
python -m positions close <id> --pnl 87.50 --notes "took profits"
python -m positions alerts          # auto-fetches scans, prints by severity

python -m journal stats             # win rate, P&L, profit factor, expectancy
python -m journal recent --limit 10
python -m journal export ~/journal.csv

# HTTP server (for the React frontend or any other client)
python -m api --port 8000
# → http://127.0.0.1:8000/api/v1/health, /scan/SPY, etc.

# Browser dashboard
cd frontend
npm install                          # first time only
npm run dev                          # http://localhost:5173

# Or just preview the UI without building anything:
open frontend/preview.html
```

---

## Exit codes the kill sheet CLI uses

- `0` clean — devil PROCEED or CONDITIONAL
- `1` data fetch failure (e.g. yfinance returned empty)
- `2` bad CLI args / unknown account
- `3` trade devil killed
- `4` account rules engine blocked (max positions / premium-at-risk / cash floor)
- `5` (positions alerts only) at least one "action" severity alert fired

---

## Architectural decisions worth respecting

1. **`IndicatorProtocol` contract** (`src/indicators/protocol.py`). Every indicator (built-in or user-authored plugin) implements `name: str, inputs: list[str], compute(df) -> DataFrame`. This is the V1→V2 architectural seam Winston demanded; do not bypass.

2. **Versioned API URLs from day one** — all FastAPI routes start with `/api/v1/`. Adding a `v2` path is fine; never serve unversioned URLs.

3. **Per-user SQLite-or-JSON file model** — no row-level multi-tenancy. Each user's install owns its own `~/.trading-dashboard/positions.json`, `events.jsonl`, etc. The "whitelabel V2" story is "users run their own copy," not hosted SaaS.

4. **No framework imports in computation layer.** `src/indicators/*.py`, `src/kill_sheet/{model,sizing,bias,multi_tf,options}.py`, `src/positions/{model,rules}.py` should never import FastAPI. Pure-Python indicator/rules logic + a thin web adapter.

5. **CLI flags win over screenshot extraction** — `_maybe_apply_screenshot` only fills fields that aren't already set on `args`. Predictable layering.

6. **Trade devil's 4 stub categories** (Catalyst, Consensus, Correlation absent of position store, IV without options) default to `PASS` with explanatory notes rather than `FLAG`. This avoids "death by cuts" from data we just don't have yet. Explicitly noted in `categories.py` per skill rule #6 ("don't kill everything").

7. **BYOK / no telemetry / opt-in screenshot.** Nothing leaves the machine unless `--screenshot` is explicitly passed. Document this in any new feature that touches outbound network.

8. **Indicator accuracy gate is opt-in via fixtures.** Tests skip when `tests/fixtures/truth/<TICKER>_<indicator>.csv` is missing. When franky drops a fixture the test activates automatically. Do not lower thresholds (1% numeric tolerance, exact categorical match) without a documented reason.

---

## Open backlog (priority-ish order)

### High value
- **TradingView fixture validation** — populate the 30 skipped accuracy tests. `extract_truth_fixture()` in `src/vision/options_extractor.py` is ready to help: take a screenshot of the TradingView Data Window and have Claude vision parse it. Each fixture lights up one skipped test.

### Medium value
- **TradingView Advanced Chart embed** — per PRD module spec; embedded iframe in `frontend/src/components/`. Needs to handle Brave shield blocking.
- **Lightweight Charts integration** — local chart for the scanned ticker (no broker dependency). Replaces / supplements TradingView widget.
- **Devil categories with external data** — earnings (Catalyst Timing), VIX/Fed (Regime Mismatch), analyst ratings (Consensus/Crowding). Each needs a chosen data source.
- **SQLite migration of JSON stores** — `events.jsonl`, `positions.json`, `scans/*.json` all migrate to a single SQLite DB. NFR23 in the PRD anticipates this. Per-user SQLite file (no row-level tenancy).

### Lower priority but on the spec
- **Streak counters in the kill sheet header** — Sally's UX recommendation: visible "14 trades, 100% kill-sheet completion" reinforces compliance.
- ~~**Sunday scan workflow checklist**~~ — Shipped as Story 30 (2026-04-28). `GET /api/v1/focus/sunday-scan` returns regime (SPY) + per-asset reads (QQQ, GLD) + ranked setups (4 candidates scored across regime / stack / stochastic axes) + a `trade | watch | cash` recommendation. Frontend route `/focus` renders the "Sunday Scan — qqq-gld-focus" view. Scoring lives in `src/focus/sunday_scan.py` (transparent additive heuristic, FIRES_THRESHOLD=60, WATCH_THRESHOLD=30 — tunable in one place). Pre-writing the kill-sheet draft from the top setup and setting 2H trigger alerts are not yet wired — manual step for now.
- ~~**qqq-gld-focus mode toggle**~~ — Shipped as Story 29 (2026-04-28). `python -m scan --focus` defaults tickers to SPY/QQQ/GLD and rejects others; `python -m kill_sheet TICKER --focus` rejects non-QQQ/GLD tickers and adds focus-rule gates (one open position per asset, no same-direction QQQ+GLD pair, 3-trading-day cool-off after a stop) on top of the standard account rules. DTE bands + $200 risk cap are now enforced as gates per Story 34.
- **Crypto multi-TF** — Story 28 ships daily/weekly/4h crypto via Crypto.com REST; 2H resampling and a watchlist mode for crypto would round it out.

### Sally's instrumentation (already shipped, follow-up to use)
- **Time-from-flag-to-resolution analytics** — `events.jsonl` has flag + shadow_trade + resolved events. A `journal flag-resolution-time` command could derive avg time and surface the silent-abandonment metric Sally warned about.

---

## Active session preferences

- **Auto mode is on** — execute without asking, prefer action over planning, ship work in big bites.
- **Anti-fabrication** — every specific factual claim in deliverables carries `[src: ...]` or `⚠️ UNVERIFIED`. CLI/code is exempt unless it cites figures (sources, market values).
- **Git** — `git init`'d but no commits yet. GitHub not set up. Don't push or commit without explicit instruction.
- **Privacy** — local-only, BYOK, opt-in for any outbound calls (currently only `--screenshot` and `python -m api` if exposed beyond localhost).

---

## Dev environment notes

- Python 3.11.3 (homebrew). Project venv at `.venv/`.
- Node 25.5, npm 11.8 — both modern. `frontend/node_modules/` exists post-install.
- yfinance 1.3.0; latest bar today is 2026-04-24 close (market closed Friday for the weekend).
- Anthropic SDK 0.97.0 installed. `ANTHROPIC_API_KEY` env var required for screenshot extraction; not stored anywhere by the project.

---

## Things that intentionally don't exist (yet)

- **No GitHub remote** — franky said "leave git alone for now."
- **No CI** — single-user project, all testing is local.
- **No Dockerfile** — localhost only, V1 is "run locally with venv + npm."
- **No SQLite** — current persistence is JSON files. NFR23 anticipates migration when needed.
- **No auth** — single-user, localhost only, browser CORS is the only "perimeter."
- **No telemetry / analytics** — not collected, not transmitted (per PRD NFR15).

---

## How to pick up work in a fresh session

1. Read this file (`HANDOFF.md`).
2. Skim `README.md` for user-facing docs and `CONTEXT.md` for the story backlog.
3. Run `pytest -q` to confirm 305 pass / 30 skip baseline.
4. Run `python -m api` and `cd frontend && npm run dev` to confirm both halves serve.
5. Pick a story from the backlog — "Open backlog" section above lists in priority order.
6. Source-of-truth specs are in `~/CLAUDE.md`, `~/_bmad-output/planning-artifacts/prd.md`, `~/Documents/Product Specs/Trading Dashboard/`, and `~/.claude/skills/user/{trading-edge, apex-options-trader, lotto-options, weekly-trend-trader, trade-devil, qqq-gld-focus}/`. Cite them with `[src: path:lines]` in any deliverable.

---

## Archived conversations

Past Claude sessions live in `~/Documents/Claude Conversations/`. Grep there for prior context — particularly for the strategic Party Mode discussions that produced this v0.2 architecture (DriverEdge AI dismissed → OSS Workstation rejected → Trading Dashboard chosen as base, then expanded through Phase 1b + 2).
