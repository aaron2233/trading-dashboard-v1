"""qqq-gld-focus Sunday scan: regime + per-asset reads + ranked setups.

Implements the workflow from ~/.claude/skills/user/qqq-gld-focus/SKILL.md
"Sunday Scan Workflow":

  1. SPY SQN regime read (broad gatekeeper)
  2. QQQ daily read (asset-level)
  3. GLD daily read (asset-level)
  4. Rank the 4 candidate setups (QQQ long/short, GLD long/short)
  5. Recommend the top setup, or "cash week" if nothing fires

Scoring is a transparent additive heuristic over three axes — regime alignment,
asset MA stack, and stochastic position. Tunable in one place. Not magic.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from storage.atomic import load_json_safe, write_json_atomic


SUNDAY_SCANS_DIR = Path.home() / ".trading-dashboard" / "sunday_scans"

# Setup score thresholds — tuned to skill semantics:
#   ≥ FIRES_THRESHOLD: top-rank candidate, recommend pre-writing kill sheet
#   ≥ WATCH_THRESHOLD: keep an eye on, set 2H alerts, don't pre-write
#   below: blocked / cash
FIRES_THRESHOLD: int = 60
WATCH_THRESHOLD: int = 30


# ─────────────────────────────────────────────────────────────────────────
# Scoring
# ─────────────────────────────────────────────────────────────────────────

def _regime_score(spy_regime: str | None, asset: str, direction: str) -> int:
    """SPY-SQN regime alignment, max ±40."""
    if spy_regime is None:
        return 0
    r = spy_regime.lower()
    long_ = direction == "long"

    if r == "strong_bull":
        if asset == "QQQ":
            return 40 if long_ else -30
        # GLD in strong-bull: typically risk-on, gold mixed
        return 0 if long_ else 10
    if r == "bull":
        if asset == "QQQ":
            return 30 if long_ else -15
        return 5 if long_ else 5
    if r == "neutral":
        return 15
    if r == "bear":
        if asset == "QQQ":
            return -15 if long_ else 30
        # GLD often catches a bid in risk-off
        return 25 if long_ else -10
    if r == "strong_bear":
        if asset == "QQQ":
            return -30 if long_ else 40
        return 35 if long_ else -20
    return 0


def _stack_score(stack_state: str | None, direction: str) -> int:
    """MA Ribbon stack alignment, max ±30."""
    if stack_state is None:
        return 0
    s = stack_state.lower()
    long_ = direction == "long"

    if s == "full_bull":
        return 30 if long_ else -20
    if s == "bull_developing":
        return 20 if long_ else -10
    if s == "compression":
        return 5
    if s in ("chop", "tangled"):
        # Skill rule: tangled MAs = no trade, ever
        return -25
    if s == "bear_developing":
        return -10 if long_ else 20
    if s == "full_bear":
        return -20 if long_ else 30
    return 0


def _stoch_score(zone: str | None, signal: str | None, direction: str) -> int:
    """Stochastic alignment, max ±30."""
    if zone is None and signal is None:
        return 0
    z = (zone or "").lower()
    sig = (signal or "").lower()
    long_ = direction == "long"

    # Strong trigger signals
    if long_ and sig in ("bull_cross_oversold", "bullish_divergence", "bull_continuation"):
        return 30
    if not long_ and sig in ("bear_cross_overbought", "bearish_divergence", "bear_continuation"):
        return 30

    # Wrong-side signals
    if long_ and sig in ("bear_cross_overbought", "bearish_divergence"):
        return -20
    if not long_ and sig in ("bull_cross_oversold", "bullish_divergence"):
        return -20

    # Zone-only fallback (no fresh cross)
    if long_ and z == "oversold":
        return 15
    if not long_ and z == "overbought":
        return 15
    if long_ and z == "overbought":
        return -10
    if not long_ and z == "oversold":
        return -10
    return 0


@dataclass
class Setup:
    asset: str               # "QQQ" | "GLD"
    direction: str           # "long" | "short"
    score: int
    status: str              # "fires" | "watch" | "blocked"
    components: dict[str, int] = field(default_factory=dict)
    blockers: list[str] = field(default_factory=list)
    # Focus action verdict — populated when 2H read available for the
    # asset. Computed via classify_focus_action(daily, 2h, direction).
    action_verdict: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def score_setup(asset: str, direction: str, asset_row: dict[str, Any],
                spy_row: dict[str, Any]) -> Setup:
    spy_sqn = (spy_row.get("sqn") or {}).get("regime")
    stack = (asset_row.get("ma_ribbon") or {}).get("stack_state")
    stoch_zone = (asset_row.get("stochastic") or {}).get("zone")
    stoch_signal = (asset_row.get("stochastic") or {}).get("signal")

    regime = _regime_score(spy_sqn, asset, direction)
    stack_pts = _stack_score(stack, direction)
    stoch = _stoch_score(stoch_zone, stoch_signal, direction)
    total = regime + stack_pts + stoch

    blockers: list[str] = []
    if stack and stack.lower() in ("chop", "tangled"):
        blockers.append("MA tangle — no trend, no trade")
    if regime <= -20:
        blockers.append(f"SPY SQN regime ({spy_sqn}) opposes {direction} {asset}")

    if blockers or total < WATCH_THRESHOLD:
        status = "blocked"
    elif total >= FIRES_THRESHOLD:
        status = "fires"
    else:
        status = "watch"

    return Setup(
        asset=asset,
        direction=direction,
        score=total,
        status=status,
        components={"regime": regime, "stack": stack_pts, "stoch": stoch},
        blockers=blockers,
    )


def rank_setups(qqq_row: dict[str, Any], gld_row: dict[str, Any],
                spy_row: dict[str, Any]) -> list[Setup]:
    setups = [
        score_setup("QQQ", "long", qqq_row, spy_row),
        score_setup("QQQ", "short", qqq_row, spy_row),
        score_setup("GLD", "long", gld_row, spy_row),
        score_setup("GLD", "short", gld_row, spy_row),
    ]
    setups.sort(key=lambda s: s.score, reverse=True)
    return setups


# ─────────────────────────────────────────────────────────────────────────
# Top-level Sunday scan
# ─────────────────────────────────────────────────────────────────────────

@dataclass
class SundayScan:
    spy: dict[str, Any] | None
    qqq: dict[str, Any] | None
    gld: dict[str, Any] | None
    setups: list[Setup]
    recommendation: str            # "trade" | "watch" | "cash"
    headline: str                  # human-readable one-liner
    errors: dict[str, str]         # ticker → error string for any failed scan
    scan_time_utc: str             # ISO-8601 UTC timestamp set when scan ran
    # Index-swing setups on QQQ/IWM/SPY. Independent from the focus setups
    # above — the Sunday scan surfaces both views so the user can compare
    # weekly-bias (focus) and daily-breakout (index-swing) reads on the
    # overlapping universe.
    index_swing_setups: list[dict[str, Any]] = field(default_factory=list)
    index_swing_actionable: list[dict[str, Any]] = field(default_factory=list)
    # Weekly-trend (Track B) read on QQQ/GLD. One log line per ticker records
    # why the weekly skill did or didn't fire this week — added 2026-07-01
    # after the skill went 8 straight no-trade weeks and nothing on disk
    # could distinguish "correctly selective" from "silently broken".
    weekly_trend_setups: list[dict[str, Any]] = field(default_factory=list)
    weekly_trend_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_time_utc": self.scan_time_utc,
            "spy": self.spy,
            "qqq": self.qqq,
            "gld": self.gld,
            "setups": [s.to_dict() for s in self.setups],
            "recommendation": self.recommendation,
            "headline": self.headline,
            "errors": self.errors,
            "index_swing_setups": self.index_swing_setups,
            "index_swing_actionable": self.index_swing_actionable,
            "weekly_trend_setups": self.weekly_trend_setups,
            "weekly_trend_log": self.weekly_trend_log,
        }


def _headline(top: Setup, recommendation: str) -> str:
    if recommendation == "cash":
        return "Cash week — no setup fires. Don't force a trade."
    if recommendation == "watch":
        return (
            f"Watch {top.asset} {top.direction} (score {top.score}). "
            "Wait for a 2H Stoch trigger before pre-writing the kill sheet."
        )
    # trade
    return (
        f"Pre-write kill sheet: {top.asset} {top.direction} "
        f"(score {top.score}). Set 2H trigger alerts, then close laptop."
    )


def run_sunday_scan(
    scan_fn: Callable[..., dict[str, Any]],
) -> SundayScan:
    """Run the Sunday scan workflow.

    scan_fn is injected so tests can mock without touching yfinance. Production
    callers pass `scan_ticker` from src/scan.py. SPY/QQQ/GLD failures are
    captured in `errors` rather than raising — a partial scan still produces
    useful setups for the assets that did load.

    For action_verdict computation, this also attempts a 2H scan on QQQ
    + GLD. 2H fetch failures are silently ignored — verdict stays None
    and the rest of the scan proceeds normally.
    """
    rows: dict[str, dict[str, Any] | None] = {"SPY": None, "QQQ": None, "GLD": None}
    rows_2h: dict[str, dict[str, Any] | None] = {"QQQ": None, "GLD": None}
    errors: dict[str, str] = {}
    for ticker in ("SPY", "QQQ", "GLD"):
        try:
            rows[ticker] = scan_fn(ticker)
        except Exception as exc:
            errors[ticker] = str(exc)
    for ticker in ("QQQ", "GLD"):
        try:
            rows_2h[ticker] = scan_fn(ticker, timeframe="2h")
        except TypeError:
            # Legacy single-arg fixture — verdict not computable.
            break
        except Exception:
            # 2H fetch failed for this asset; skip its verdict only.
            pass

    spy_row = rows["SPY"] or {}
    qqq_row = rows["QQQ"]
    gld_row = rows["GLD"]

    setups: list[Setup] = []
    if qqq_row is not None:
        setups.extend([
            score_setup("QQQ", "long", qqq_row, spy_row),
            score_setup("QQQ", "short", qqq_row, spy_row),
        ])
    if gld_row is not None:
        setups.extend([
            score_setup("GLD", "long", gld_row, spy_row),
            score_setup("GLD", "short", gld_row, spy_row),
        ])
    setups.sort(key=lambda s: s.score, reverse=True)

    # Action verdicts — best-effort enrichment.
    for s in setups:
        daily = qqq_row if s.asset == "QQQ" else gld_row
        two_h = rows_2h.get(s.asset)
        if daily is None or two_h is None:
            continue
        try:
            from action_gate import classify_focus_action
            verdict = classify_focus_action(
                {"1d": daily, "2h": two_h},
                s.direction,  # type: ignore[arg-type]
            )
            s.action_verdict = verdict.to_dict()
        except Exception:
            import logging as _logging
            _logging.getLogger(__name__).exception(
                "focus verdict failed for %s %s", s.asset, s.direction,
            )

    if not setups:
        recommendation = "cash"
        headline = "Scan failed — no setups. Try again or check yfinance."
    else:
        top = setups[0]
        if top.status == "fires":
            recommendation = "trade"
        elif top.status == "watch":
            recommendation = "watch"
        else:
            recommendation = "cash"
        headline = _headline(top, recommendation)

    # Index-swing overlay on QQQ/IWM/SPY — best-effort. Failure is silent;
    # the focus scan (SPY/QQQ/GLD) is the canonical Sunday output.
    index_swing_setups: list[dict[str, Any]] = []
    index_swing_actionable: list[dict[str, Any]] = []
    try:
        from index_swing import scan_index_swing_watchlist
        is_result = scan_index_swing_watchlist()  # default QQQ/IWM/SPY universe
        index_swing_setups = [s.to_dict() for s in is_result.setups]
        index_swing_actionable = [s.to_dict() for s in is_result.actionable_setups]
    except Exception as exc:
        errors["INDEX_SWING"] = f"index-swing scan failed: {exc}"

    # Weekly-trend (Track B) overlay on QQQ/GLD — best-effort. Reuses the
    # injected scan_fn so tests stay offline. Failure is captured in errors,
    # not raised: the Sunday focus scan remains the canonical output.
    weekly_trend_setups: list[dict[str, Any]] = []
    weekly_trend_log: list[str] = []
    try:
        from weekly_trend import scan_weekly_watchlist

        def _wk_scan(ticker: str, timeframe: str) -> dict[str, Any]:
            try:
                return scan_fn(ticker, timeframe=timeframe)
            except TypeError:
                # Single-arg scan_fns (test fixtures) — same fallback as 2H.
                return scan_fn(ticker)

        wt = scan_weekly_watchlist(tickers=["QQQ", "GLD"], scan_fn=_wk_scan)
        weekly_trend_setups = [s.to_dict() for s in wt.setups]
        for s in wt.setups:
            k = f"{s.stoch_k:.0f}" if s.stoch_k is not None else "?"
            d = f"{s.stoch_d:.0f}" if s.stoch_d is not None else "?"
            why = "; ".join(s.blockers) if s.blockers else s.why_now
            weekly_trend_log.append(
                f"{s.ticker}: {s.confluence} — stack={s.ma_stack_state}, "
                f"Stoch K={k}/D={d}, SQN(100)={s.sqn_100_regime} — {why}"
            )
    except Exception as exc:
        errors["WEEKLY_TREND"] = f"weekly-trend scan failed: {exc}"

    return SundayScan(
        spy=rows["SPY"],
        qqq=rows["QQQ"],
        gld=rows["GLD"],
        setups=setups,
        recommendation=recommendation,
        headline=headline,
        errors=errors,
        scan_time_utc=datetime.now(timezone.utc).isoformat(),
        index_swing_setups=index_swing_setups,
        index_swing_actionable=index_swing_actionable,
        weekly_trend_setups=weekly_trend_setups,
        weekly_trend_log=weekly_trend_log,
    )


def persist_sunday_scan(
    scan: SundayScan,
    sunday_scans_dir: Path | None = None,
    now: datetime | None = None,
    cache: Any | None = None,
) -> Path:
    """Write the scan to ~/.trading-dashboard/sunday_scans/YYYY-MM-DD.json.

    Overwrites on the same day to mirror persist_scan's behavior. Returns the
    path written. Caller is responsible for catching disk failures if running
    inside a request handler.

    `sunday_scans_dir` defaults to the module-level SUNDAY_SCANS_DIR resolved
    at call time so tests can monkeypatch the constant without re-importing.

    If `cache` is provided, the scan is also upserted to the SQLite cache.
    Cache failures are logged but never raised — JSON remains canonical.
    """
    import logging
    logger = logging.getLogger(__name__)

    if sunday_scans_dir is None:
        sunday_scans_dir = SUNDAY_SCANS_DIR
    sunday_scans_dir.mkdir(parents=True, exist_ok=True)
    payload = scan.to_dict()
    if now is not None:
        # Caller explicitly passed a time — override the scan's intrinsic
        # timestamp (used by tests for deterministic file naming).
        payload["scan_time_utc"] = now.isoformat()
        date_str = now.strftime("%Y-%m-%d")
    else:
        date_str = scan.scan_time_utc[:10]  # YYYY-MM-DD prefix from ISO timestamp
    path = sunday_scans_dir / f"{date_str}.json"
    write_json_atomic(path, payload)
    if cache is not None:
        try:
            cache.upsert_sunday_scan(payload)
        except Exception:
            logger.exception(
                "cache upsert failed for sunday scan date=%s", date_str
            )
    return path


@dataclass
class SundayScanSummary:
    """Lightweight summary used by the recent-scans listing."""
    date: str                   # YYYY-MM-DD parsed from filename
    scan_time_utc: str          # full ISO timestamp from the file payload
    recommendation: str
    headline: str
    top_setup: dict[str, Any] | None    # {asset, direction, score, status} or None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def list_recent_sunday_scans(
    limit: int = 10,
    sunday_scans_dir: Path | None = None,
) -> list[SundayScanSummary]:
    """Read the sunday_scans/ directory and return summaries newest-first.

    Skips any file that fails to parse — partial corruption shouldn't break
    the listing. Filenames must match YYYY-MM-DD.json; anything else is
    ignored.
    """
    if sunday_scans_dir is None:
        sunday_scans_dir = SUNDAY_SCANS_DIR
    if not sunday_scans_dir.exists():
        return []

    summaries: list[SundayScanSummary] = []
    for path in sunday_scans_dir.glob("*.json"):
        date_part = path.stem
        # Cheap filter — only YYYY-MM-DD-shaped filenames
        if len(date_part) != 10 or date_part[4] != "-" or date_part[7] != "-":
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        setups = payload.get("setups") or []
        top = setups[0] if setups else None
        top_summary: dict[str, Any] | None = None
        if top:
            top_summary = {
                "asset": top.get("asset"),
                "direction": top.get("direction"),
                "score": top.get("score"),
                "status": top.get("status"),
            }
        summaries.append(SundayScanSummary(
            date=date_part,
            scan_time_utc=payload.get("scan_time_utc", ""),
            recommendation=payload.get("recommendation", "cash"),
            headline=payload.get("headline", ""),
            top_setup=top_summary,
        ))

    summaries.sort(key=lambda s: s.date, reverse=True)
    return summaries[:limit]


def iter_saved_scans(
    sunday_scans_dir: Path | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Yield (date_str, payload) tuples for every saved scan, newest-first.

    Skips files that fail to parse or whose filenames don't match
    YYYY-MM-DD.json.
    """
    if sunday_scans_dir is None:
        sunday_scans_dir = SUNDAY_SCANS_DIR
    if not sunday_scans_dir.exists():
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for path in sunday_scans_dir.glob("*.json"):
        date_str = path.stem
        if len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out.append((date_str, payload))
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def load_sunday_scan(
    date_str: str,
    sunday_scans_dir: Path | None = None,
) -> dict[str, Any] | None:
    """Read a single saved scan by YYYY-MM-DD date.

    Returns the parsed payload (matches SundayScan.to_dict() shape) or None
    if the file is missing or the date format is invalid. Malformed JSON
    raises — callers must distinguish "not found" from "broken file."
    """
    if sunday_scans_dir is None:
        sunday_scans_dir = SUNDAY_SCANS_DIR
    if len(date_str) != 10 or date_str[4] != "-" or date_str[7] != "-":
        return None
    path = sunday_scans_dir / f"{date_str}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())
