# Customization Guide

This dashboard ships as a working, opinionated trading-discipline system — a personal cash-account options system with a specific indicator stack, a specific set of strategies (weekly trend, lotto, index swing), a specific discipline scorecard, and bespoke per-strategy frontend views. **It is not a generic plugin framework.** It is a *scaffold you can fork and adapt to your own trading style.*

Below: what's strategy-agnostic substrate you can keep, what's opinionated and meant to be replaced, and how to do the replacement at five levels of depth — from "edit a config file" to "fork the whole thing and rewrite half the views."

## Substrate vs. Opinion

| Layer | Framework-level (keep) | Opinionated (replace if you want) |
|---|---|---|
| **Data** | yfinance + Crypto.com REST loaders, plugin-loadable providers, atomic JSON storage | The default Polygon adapter is unwired |
| **Indicators** | `IndicatorProtocol` contract + plugin loader (`~/.trading-dashboard/plugins/*.py`) | MA Ribbon / Stochastic / SQN are the bundled defaults — keep, add to, or replace |
| **Regime dashboard** | MA stack + Stoch + SQN(100)/(20) view — generic market state | — |
| **Kill sheet builder** | Form rendering, sizing math, options-input paste, atomic write | The exact sections + bias/conviction taxonomy reflect the author's workflow |
| **Trade devil gate** | 8-category KILL/FLAG/PASS framework — generic pre-entry gate | The specific category checks encode the author's anti-patterns |
| **Positions / journal** | Atomic store, partial-exit tracking, P&L stats, alert engine | Generic |
| **Discipline scorecard** | Per-position scoring, weekly review aggregation | 15-rule list is hardcoded to a cash-account, long-options-only profile |
| **Strategies** | — | weekly-trend-trader, lotto-options, index-swing, free-range scanners — all bespoke |
| **Frontend views** | App shell, nav, regime header, kill-sheet form, positions, journal | LottoView, WeeklyTrendView, IndexSwingView are per-strategy components |

If your trading style is also "cash account, long calls/puts, MA + momentum + regime stack" you can probably configure your way to a working setup without writing code. If it's not, expect to fork.

## Five customization tiers

### Tier 1 — Config only (5 minutes)

Override what you can via `~/.trading-dashboard/config.yaml`. Anything you don't set keeps its default from `src/config/loader.py::_DEFAULT_ACCOUNTS`. Full schema lives there.

```yaml
accounts:
  main:
    balance_usd: 25000
    max_open_positions: 8
    max_premium_at_risk_pct: 0.08
    single_stock_price_min: 5.0      # widen the band
    single_stock_price_max: 200.0
    cut_rule_pct: -0.40              # tighter than default
  lotto:
    balance_usd: 2500
    max_per_trade_usd: 250
```

Same file accepts overrides for regime-health thresholds, capex ticker lists, and per-skill default watchlists (the `skills:` block), etc. — the merge happens in `src/config/loader.py::load_config` (deep merge, user YAML over defaults); grep `load_config` for the read sites.

### Tier 2 — Indicator plugins (30 minutes)

The indicator system is already plugin-loaded. Drop a `.py` file in `~/.trading-dashboard/plugins/` that implements `IndicatorProtocol` (`src/indicators/protocol.py`) and it'll be discoverable by every scanner that asks for it by name.

```python
# ~/.trading-dashboard/plugins/my_rsi.py
import pandas as pd

# The loader looks for a module-level INDICATOR instance, or a class
# named exactly `Indicator` with a no-arg constructor.
class Indicator:
    name = "rsi_14"
    inputs = ["close"]

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        # Return a DataFrame with your indicator's output columns
        ...
```

Restart the API (`python -m api`) and the new indicator is available. No edits to the dashboard codebase.

### Tier 3 — Replace a scanner (1-3 hours)

Scanners are the "find me a setup on this ticker" units — one per strategy. Pattern to copy: `src/lotto/scanner.py`. It:

1. Pulls daily + lower-TF bars via `scan_ticker()` (from `src/scan.py`)
2. Reads MA stack, Stoch state, SQN regime from each bar
3. Calls a `*_verdict()` function (in `src/scan_verdict.py`) that returns `buy / wait / no_go` + reason
4. Returns a structured `Setup` dataclass per ticker × direction

**To plug in your own strategy:**

1. Add a `your_strategy_verdict()` function in `src/scan_verdict.py` (or your own module). Input: indicator state. Output: `TradeVerdict(verdict, reason)`.
2. Copy `src/lotto/scanner.py` → `src/your_strategy/scanner.py`. Swap the verdict call. Adjust the watchlist constants.
3. Expose a CLI: copy any existing `src/<module>/__main__.py` (e.g., `src/free_range/__main__.py` or `src/kill_sheet/__main__.py`) → `src/your_strategy/__main__.py`. Then `python -m your_strategy ...` works.
4. Wire an API route: add a router module in `src/api/routes/` (copy `src/api/routes/lotto.py`) and `include_router` it in `src/api/app.py`.
5. Build the frontend view (Tier 5).

If you only want one strategy, *delete the bundled ones first* — see Tier 4.

### Tier 4 — Remove a bundled strategy (15 minutes)

To strip out, say, lotto-options entirely:

```
src/lotto/                                      # delete
src/api/routes/lotto.py                         # delete
frontend/src/views/LottoView.tsx                # delete
frontend/src/components/lotto/                  # delete
tests/test_lotto*.py                            # delete
scripts/lotto_*.py + scripts/measure_lotto_*    # delete
```

Then grep `lotto` across `src/api/app.py`, `frontend/src/App.tsx`, and `frontend/src/api/client.ts` — there are imports, routes, nav entries, client functions to remove. Pattern matches what was already done for the `recovery_plan` module in commit `1e9535a` (good worked example to compare against).

### Tier 5 — Build a custom strategy view (3-8 hours)

Copy `frontend/src/views/IndexSwingView.tsx` (or LottoView for a richer reference) → `YourStrategyView.tsx`. Components you'll reuse: `TradeCard`, `Verdict`, scan-result rendering. Wire it in `frontend/src/App.tsx` (import, nav entry, route). The API client functions go in `frontend/src/api/client.ts`, types in `types.ts`. The shape is straightforward but it's React work, not just config.

## The skill-file pattern (manual today, agent-assisted later)

If you're a Claude Code user with strategies defined as skill files (`~/.claude/skills/user/<name>/SKILL.md`), those skill files are already a portable strategy spec. Today you translate them by hand into config + scanner + view. Future versions of this dashboard will support auto-import.

A skill file's frontmatter + sections map to dashboard concepts like this:

| Skill file field | Maps to | Dashboard location |
|---|---|---|
| `name` | scanner module name, API route segment, view name | `src/<name>/`, `/api/v1/<name>/scan`, `<Name>View.tsx` |
| `description` triggers | nav label + verdict-banner reason text | `App.tsx` nav, scanner verdict reason |
| Account constraints (DTE, sizing, position limits) | account override block | `~/.trading-dashboard/config.yaml` accounts section |
| Per-asset edge table (allowed tickers, blocked tickers) | scanner watchlist + verdict gates | scanner module constants + verdict function |
| Indicator stack | indicator names the scanner queries | scanner `scan_ticker()` calls |
| Entry rules / triggers | verdict function logic | `src/scan_verdict.py::<name>_verdict()` |
| Exit rules / stops / targets | kill-sheet template defaults + alert engine rules | `src/kill_sheet/builder.py`, `src/positions/alerts.py` |
| Anti-patterns / hard rules | discipline scorecard rules + trade-devil categories | `src/discipline/`, `src/trade_devil/` |
| Backtest references | scripts + CSV outputs | `scripts/<name>_backtest.py` |

Walked example: a skill file with `name: my-momo`, description trigger "momo scan", account constraint "max 3% risk / 30 DTE", asset table "QQQ + SOXL", indicator stack "MA Ribbon + Stoch", entry rule "ribbon bullish + Stoch crossing from <20" becomes:

1. **Config:** `accounts.main.risk_per_trade.high: 0.03`, `accounts.main.dte_min_for_options: 30`
2. **Scanner:** `src/my_momo/scanner.py` with `WATCHLIST = ("QQQ", "SOXL")`
3. **Verdict:** `src/scan_verdict.py::my_momo_verdict()` that returns `buy` when `ma_stack in ("bullish_developing", "full_bull")` AND `stoch_signal in ("crossing_oversold",)`
4. **CLI + API + View:** copy from the IndexSwing pattern

**Coming later (Phase 2):** A small Claude Agent SDK app — point it at a SKILL.md, it generates the scanner / verdict / config / scaffolded view from the file. Not built yet; tracked as a separate companion project.

## What this scaffold isn't (and probably won't be)

- **Not a multi-tenant SaaS.** Single-user, localhost. No auth, no shared instance.
- **Not a backtesting platform.** Scripts in `scripts/` show how to backtest custom strategies, but there's no UI for it.
- **Not broker-integrated.** Manual entry via the kill-sheet builder. No broker API connections.
- **Not strategy-agnostic out of the box.** The bundled strategies are opinionated and reflect one trader's profile. You replace them; the framework around them stays.
- **Not a no-code tool.** Tiers 1-2 are config-only. Tier 3+ requires Python and React.

## Get started

1. Clone, install, configure your account (see `INSTALL.md`).
2. Run it as-is to understand the bundled strategies. Hit the dashboard at `http://localhost:5173`, click through every view, generate a few kill sheets, look at the positions table.
3. Pick the customization tier that matches your scope. Most people start at Tier 1 (config), graduate to Tier 4 (remove bundled strategies you don't use), then Tier 3 (add their own scanner).
4. Open an issue if you hit a customization wall — those gaps are the highest-priority work.
