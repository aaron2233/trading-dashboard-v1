"""SQLite cache layer over the JSON canonical store.

Design contract (from V2-ARCHITECTURE-DECISIONS-2026-05-03.md):
- JSON files on disk are canonical (source of truth).
- SQLite is a derived index — never the only copy of any record.
- Always rebuildable from JSON; missing/corrupt sqlite file = trigger rebuild.
- Write-through: when a store saves a JSON file, the cache upserts. If the
  cache write fails, the JSON write still succeeded — log + continue. Data
  is never lost because of cache trouble.

Read API:
- query_positions / query_discipline / query_weekly_reviews
- aggregate helpers (account_pnl, weekly_pnl, full_adherence_streak, etc.)

These power the L0 read-only agent and any future cross-cutting analytics
the JSON file-scan can't deliver fast.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

logger = logging.getLogger(__name__)


DEFAULT_CACHE_PATH = Path.home() / ".trading-dashboard" / "cache.sqlite"

# Bump when DDL changes in a way old caches can't handle. On version
# mismatch, the cache drops all tables and recreates with the new DDL —
# data is recoverable via /api/v1/cache/rebuild since JSON is canonical.
SCHEMA_VERSION = 4

DDL = """
CREATE TABLE IF NOT EXISTS _cache_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,
    instrument TEXT NOT NULL,
    account_key TEXT NOT NULL,
    status TEXT NOT NULL,
    skill TEXT,
    tier INTEGER,
    entry_date TEXT NOT NULL,
    closed_date TEXT,
    contracts INTEGER,
    shares INTEGER,
    strike REAL,
    expiry TEXT,
    premium_paid_per_contract REAL,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    max_loss_usd REAL NOT NULL DEFAULT 0,
    target_price REAL,
    invalidation_price REAL,
    pnl_usd REAL,
    notes TEXT,
    entry_ts INTEGER,
    closed_ts INTEGER,
    -- Greeks / IV at entry (snapshot)
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    iv REAL,
    iv_rank REAL,
    premium_stop REAL,
    premium_target REAL,
    -- Phase B: every non-bypassed position references the kill sheet that
    -- authorized it. Nullable for legacy positions and explicit bypasses.
    kill_sheet_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_positions_kill_sheet ON positions(kill_sheet_id);

CREATE TABLE IF NOT EXISTS kill_sheets (
    id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    direction TEXT NOT NULL,
    intent TEXT NOT NULL,
    trigger_tf TEXT NOT NULL,
    account_key TEXT NOT NULL,
    status TEXT NOT NULL,           -- 'AUTHORIZED' | 'REJECTED'
    rejection_reason TEXT,
    skill TEXT,
    tier INTEGER,
    bias TEXT,
    confidence TEXT,
    risk_conviction TEXT,
    max_risk_usd REAL,
    sqn_value REAL,
    sqn_20_value REAL,
    regime TEXT,
    regime_20 TEXT,
    sqn_diagnostic TEXT,
    generated_at TEXT NOT NULL,
    generated_ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_kill_sheets_ticker_status ON kill_sheets(ticker, status);
CREATE INDEX IF NOT EXISTS idx_kill_sheets_generated_ts ON kill_sheets(generated_ts);
CREATE INDEX IF NOT EXISTS idx_positions_account_status ON positions(account_key, status);
CREATE INDEX IF NOT EXISTS idx_positions_ticker_status ON positions(ticker, status);
CREATE INDEX IF NOT EXISTS idx_positions_closed_ts ON positions(closed_ts);

CREATE TABLE IF NOT EXISTS discipline_scores (
    position_id TEXT PRIMARY KEY,
    kill_sheet_id TEXT,
    closed_at TEXT NOT NULL,
    closed_ts INTEGER NOT NULL,
    ticker TEXT,
    direction TEXT,
    instrument TEXT,
    entry_at TEXT,
    pnl_usd REAL,
    score_numerator INTEGER NOT NULL,
    score_denominator INTEGER NOT NULL,
    score REAL NOT NULL,
    profitable_violation INTEGER NOT NULL,
    counterfactual_loss_usd REAL,
    full_adherence INTEGER NOT NULL,
    profitable_violation_resolution TEXT,
    notes TEXT,
    scored_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_discipline_closed_ts ON discipline_scores(closed_ts);
CREATE INDEX IF NOT EXISTS idx_discipline_full_adh ON discipline_scores(full_adherence);
CREATE INDEX IF NOT EXISTS idx_discipline_prof_viol ON discipline_scores(profitable_violation);

CREATE TABLE IF NOT EXISTS discipline_rules (
    position_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    score TEXT NOT NULL,
    auto_evaluated INTEGER NOT NULL,
    note TEXT,
    PRIMARY KEY (position_id, rule_id)
);
CREATE INDEX IF NOT EXISTS idx_rules_id_score ON discipline_rules(rule_id, score);

CREATE TABLE IF NOT EXISTS weekly_reviews (
    week_start TEXT PRIMARY KEY,
    week_end TEXT NOT NULL,
    trades_scored INTEGER NOT NULL,
    avg_discipline_score REAL NOT NULL,
    full_adherence_count INTEGER NOT NULL,
    any_violation_count INTEGER NOT NULL,
    profitable_violation_count INTEGER NOT NULL,
    most_violated_rule TEXT,
    drift_trend TEXT NOT NULL,
    pnl_usd REAL NOT NULL,
    lockdown_behavior TEXT
);

CREATE TABLE IF NOT EXISTS sunday_scans (
    scan_date TEXT PRIMARY KEY,
    scan_time_utc TEXT NOT NULL,
    recommendation TEXT NOT NULL,
    headline TEXT NOT NULL,
    top_setup_asset TEXT,
    top_setup_direction TEXT,
    top_setup_score INTEGER,
    top_setup_status TEXT
);
"""


def _to_ts(iso: str | None) -> int | None:
    """Convert ISO 8601 string to unix timestamp. Returns None on parse fail."""
    if not iso:
        return None
    try:
        # Tolerate trailing 'Z' and date-only strings.
        s = iso.replace("Z", "+00:00")
        if len(s) == 10:  # bare YYYY-MM-DD
            s += "T00:00:00+00:00"
        return int(datetime.fromisoformat(s).timestamp())
    except (ValueError, TypeError):
        return None


def _bool_int(v: Any) -> int:
    return 1 if v else 0


# ── Cache class ─────────────────────────────────────────────────────────────


class Cache:
    """SQLite cache. Open one per process. Methods are not thread-safe; if
    needed in async contexts, wrap in a lock or use one cache per worker."""

    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else DEFAULT_CACHE_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ── Schema management ──────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        # Step 1: ensure the meta table exists so we can read schema_version.
        with self._tx() as cur:
            cur.executescript(
                "CREATE TABLE IF NOT EXISTS _cache_meta ("
                " key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            )

        # Step 2: check stored schema version. If it's missing or stale,
        # drop every table and recreate. JSON canonical store is unaffected;
        # caller can repopulate via rebuild_from_json().
        existing = self.schema_version()
        if existing is not None and existing != SCHEMA_VERSION:
            logger.warning(
                "cache schema mismatch (have v%d, want v%d) — dropping and "
                "recreating tables. Hit /api/v1/cache/rebuild to repopulate.",
                existing, SCHEMA_VERSION,
            )
            with self._tx() as cur:
                # Order matters: drop tables with FK refs first if any
                for table in (
                    "discipline_rules",
                    "discipline_scores",
                    "weekly_reviews",
                    "sunday_scans",
                    "kill_sheets",
                    "positions",
                ):
                    cur.execute(f"DROP TABLE IF EXISTS {table}")

        # Step 3: apply DDL (idempotent CREATE TABLE IF NOT EXISTS) and
        # stamp the current schema version.
        with self._tx() as cur:
            cur.executescript(DDL)
            cur.execute(
                "INSERT OR REPLACE INTO _cache_meta (key, value) VALUES (?, ?)",
                ("schema_version", str(SCHEMA_VERSION)),
            )

    def schema_version(self) -> int | None:
        row = self.conn.execute(
            "SELECT value FROM _cache_meta WHERE key = ?",
            ("schema_version",),
        ).fetchone()
        return int(row["value"]) if row else None

    def clear_all(self) -> None:
        """Drop all rows from every table. Used by rebuild_from_json."""
        with self._tx() as cur:
            for table in (
                "discipline_rules",
                "discipline_scores",
                "weekly_reviews",
                "sunday_scans",
                "kill_sheets",
                "positions",
            ):
                cur.execute(f"DELETE FROM {table}")

    # ── Upserts (called by stores after JSON write) ────────────────────────

    def upsert_position(self, p: dict[str, Any]) -> None:
        """Upsert a position from its to_dict() payload."""
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO positions (
                    id, ticker, direction, instrument, account_key, status,
                    skill, tier, entry_date, closed_date, contracts, shares,
                    strike, expiry, premium_paid_per_contract,
                    total_cost_usd, max_loss_usd, target_price,
                    invalidation_price, pnl_usd, notes, entry_ts, closed_ts,
                    delta, gamma, theta, vega, iv, iv_rank,
                    premium_stop, premium_target, kill_sheet_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p["id"], p["ticker"], p["direction"], p["instrument"],
                    p["account_key"], p["status"],
                    p.get("skill"), p.get("tier"),
                    p["entry_date"], p.get("closed_date"),
                    p.get("contracts"), p.get("shares"),
                    p.get("strike"), p.get("expiry"),
                    p.get("premium_paid_per_contract"),
                    p.get("total_cost_usd", 0.0),
                    p.get("max_loss_usd", 0.0),
                    p.get("target_price"),
                    p.get("invalidation_price"),
                    p.get("pnl_usd"),
                    p.get("notes"),
                    _to_ts(p.get("entry_date")),
                    _to_ts(p.get("closed_date")),
                    p.get("delta"),
                    p.get("gamma"),
                    p.get("theta"),
                    p.get("vega"),
                    p.get("iv"),
                    p.get("iv_rank"),
                    p.get("premium_stop"),
                    p.get("premium_target"),
                    p.get("kill_sheet_id"),
                ),
            )

    def upsert_kill_sheet(self, ks: dict[str, Any]) -> None:
        """Upsert a kill sheet from its to_dict() payload."""
        generated_at = ks.get("generated_at", "")
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO kill_sheets (
                    id, ticker, direction, intent, trigger_tf, account_key,
                    status, rejection_reason, skill, tier, bias, confidence,
                    risk_conviction, max_risk_usd, sqn_value, sqn_20_value,
                    regime, regime_20, sqn_diagnostic,
                    generated_at, generated_ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ks["id"], ks["ticker"], ks["direction"], ks["intent"],
                    ks["trigger_tf"], ks.get("account_key", "main"),
                    ks.get("status", "AUTHORIZED"),
                    ks.get("rejection_reason"),
                    ks.get("skill"), ks.get("tier"),
                    ks.get("bias"), ks.get("confidence"),
                    ks.get("risk_conviction"),
                    ks.get("max_risk_usd"),
                    ks.get("sqn_value"), ks.get("sqn_20_value"),
                    ks.get("regime"), ks.get("regime_20"),
                    ks.get("sqn_diagnostic"),
                    generated_at, _to_ts(generated_at),
                ),
            )

    def delete_position(self, position_id: str) -> None:
        with self._tx() as cur:
            cur.execute("DELETE FROM positions WHERE id = ?", (position_id,))

    def upsert_discipline_score(self, s: dict[str, Any]) -> None:
        """Upsert a discipline score + its rules."""
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO discipline_scores (
                    position_id, kill_sheet_id, closed_at, closed_ts,
                    ticker, direction, instrument, entry_at, pnl_usd,
                    score_numerator, score_denominator, score,
                    profitable_violation, counterfactual_loss_usd,
                    full_adherence, profitable_violation_resolution,
                    notes, scored_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s["position_id"], s.get("kill_sheet_id"),
                    s["closed_at"], _to_ts(s["closed_at"]) or 0,
                    s.get("ticker"), s.get("direction"), s.get("instrument"),
                    s.get("entry_at"), s.get("pnl_usd"),
                    s.get("score_numerator", 0),
                    s.get("score_denominator", 0),
                    s.get("score", 0.0),
                    _bool_int(s.get("profitable_violation")),
                    s.get("counterfactual_loss_usd"),
                    _bool_int(s.get("full_adherence")),
                    s.get("profitable_violation_resolution"),
                    s.get("notes", ""),
                    s.get("scored_at", ""),
                ),
            )
            cur.execute(
                "DELETE FROM discipline_rules WHERE position_id = ?",
                (s["position_id"],),
            )
            for r in s.get("rules", []) or []:
                cur.execute(
                    """
                    INSERT INTO discipline_rules (
                        position_id, rule_id, score, auto_evaluated, note
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        s["position_id"], r["rule_id"], r["score"],
                        _bool_int(r.get("auto_evaluated")),
                        r.get("note"),
                    ),
                )

    def delete_discipline_score(self, position_id: str) -> None:
        with self._tx() as cur:
            cur.execute(
                "DELETE FROM discipline_rules WHERE position_id = ?",
                (position_id,),
            )
            cur.execute(
                "DELETE FROM discipline_scores WHERE position_id = ?",
                (position_id,),
            )

    def upsert_weekly_review(self, w: dict[str, Any]) -> None:
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO weekly_reviews (
                    week_start, week_end, trades_scored, avg_discipline_score,
                    full_adherence_count, any_violation_count,
                    profitable_violation_count, most_violated_rule,
                    drift_trend, pnl_usd, lockdown_behavior
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    w["week_start"], w["week_end"], w["trades_scored"],
                    w["avg_discipline_score"], w["full_adherence_count"],
                    w["any_violation_count"], w["profitable_violation_count"],
                    w.get("most_violated_rule"), w["drift_trend"],
                    w["pnl_usd"], w.get("lockdown_behavior"),
                ),
            )

    def upsert_sunday_scan(self, payload: dict[str, Any]) -> None:
        """Upsert a Sunday scan from the SundayScan.to_dict() shape."""
        scan_time = payload.get("scan_time_utc", "")
        scan_date = scan_time[:10] if scan_time else ""
        setups = payload.get("setups") or []
        top = setups[0] if setups else None
        with self._tx() as cur:
            cur.execute(
                """
                INSERT OR REPLACE INTO sunday_scans (
                    scan_date, scan_time_utc, recommendation, headline,
                    top_setup_asset, top_setup_direction, top_setup_score,
                    top_setup_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scan_date,
                    scan_time,
                    payload.get("recommendation", ""),
                    payload.get("headline", ""),
                    (top or {}).get("asset"),
                    (top or {}).get("direction"),
                    (top or {}).get("score"),
                    (top or {}).get("status"),
                ),
            )

    # ── Read API ───────────────────────────────────────────────────────────

    def query_positions(
        self,
        *,
        account: str | None = None,
        status: str | None = None,
        ticker: str | None = None,
        closed_after: str | None = None,
        closed_before: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if account is not None:
            clauses.append("account_key = ?")
            params.append(account)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if ticker is not None:
            clauses.append("ticker = ?")
            params.append(ticker.upper())
        ts_after = _to_ts(closed_after)
        if ts_after is not None:
            clauses.append("closed_ts >= ?")
            params.append(ts_after)
        ts_before = _to_ts(closed_before)
        if ts_before is not None:
            clauses.append("closed_ts < ?")
            params.append(ts_before)
        sql = "SELECT * FROM positions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY entry_ts DESC NULLS LAST, id DESC"
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_discipline_scores(
        self,
        *,
        full_adherence: bool | None = None,
        profitable_violation: bool | None = None,
        closed_after: str | None = None,
        closed_before: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if full_adherence is not None:
            clauses.append("full_adherence = ?")
            params.append(_bool_int(full_adherence))
        if profitable_violation is not None:
            clauses.append("profitable_violation = ?")
            params.append(_bool_int(profitable_violation))
        ts_after = _to_ts(closed_after)
        if ts_after is not None:
            clauses.append("closed_ts >= ?")
            params.append(ts_after)
        ts_before = _to_ts(closed_before)
        if ts_before is not None:
            clauses.append("closed_ts < ?")
            params.append(ts_before)
        sql = "SELECT * FROM discipline_scores"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY closed_ts DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_weekly_reviews(
        self,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM weekly_reviews ORDER BY week_start DESC"
        params: list[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def query_recent_sunday_scans(
        self, *, limit: int = 10,
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM sunday_scans ORDER BY scan_date DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Aggregates ─────────────────────────────────────────────────────────

    def realized_pnl(
        self,
        *,
        account: str | None = None,
        closed_after: str | None = None,
        closed_before: str | None = None,
    ) -> float:
        clauses = ["status = 'closed'"]
        params: list[Any] = []
        if account is not None:
            clauses.append("account_key = ?")
            params.append(account)
        ts_after = _to_ts(closed_after)
        if ts_after is not None:
            clauses.append("closed_ts >= ?")
            params.append(ts_after)
        ts_before = _to_ts(closed_before)
        if ts_before is not None:
            clauses.append("closed_ts < ?")
            params.append(ts_before)
        sql = "SELECT COALESCE(SUM(pnl_usd), 0.0) AS total FROM positions"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        row = self.conn.execute(sql, params).fetchone()
        return float(row["total"] or 0.0)

    def discipline_summary(self) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) AS scored,
                COALESCE(AVG(score), 0.0) AS avg_score,
                SUM(full_adherence) AS full_adh,
                SUM(profitable_violation) AS prof_viol
            FROM discipline_scores
            """
        ).fetchone()
        return {
            "scored": int(row["scored"] or 0),
            "avg_score": float(row["avg_score"] or 0.0),
            "full_adherence_count": int(row["full_adh"] or 0),
            "profitable_violation_count": int(row["prof_viol"] or 0),
        }

    # ── Migration / rebuild ────────────────────────────────────────────────

    def rebuild_from_json(
        self,
        *,
        positions: Iterable[dict[str, Any]] = (),
        discipline_scores: Iterable[dict[str, Any]] = (),
        weekly_reviews: Iterable[dict[str, Any]] = (),
        sunday_scans: Iterable[dict[str, Any]] = (),
    ) -> dict[str, int]:
        """Wipe all cached data and re-populate from the JSON canonical store.

        Caller passes already-parsed payloads (typically harvested via the
        existing store loaders so partial-corruption recovery is consistent).
        Returns a count of records loaded per table for reporting.
        """
        self.clear_all()
        counts: dict[str, int] = {
            "positions": 0,
            "discipline_scores": 0,
            "weekly_reviews": 0,
            "sunday_scans": 0,
        }
        for p in positions:
            try:
                self.upsert_position(p)
                counts["positions"] += 1
            except Exception:
                logger.exception("rebuild: skipping bad position payload")
        for s in discipline_scores:
            try:
                self.upsert_discipline_score(s)
                counts["discipline_scores"] += 1
            except Exception:
                logger.exception("rebuild: skipping bad discipline score")
        for w in weekly_reviews:
            try:
                self.upsert_weekly_review(w)
                counts["weekly_reviews"] += 1
            except Exception:
                logger.exception("rebuild: skipping bad weekly review")
        for sc in sunday_scans:
            try:
                self.upsert_sunday_scan(sc)
                counts["sunday_scans"] += 1
            except Exception:
                logger.exception("rebuild: skipping bad sunday scan")
        return counts


# ── Top-level singleton accessor ───────────────────────────────────────────

_cache_singleton: Cache | None = None


def get_cache(path: Path | None = None) -> Cache:
    """Return the process-wide cache singleton. Creates it on first call.

    Tests should pass an explicit path (or use the `cache` fixture in
    conftest) to avoid touching the user's real ~/.trading-dashboard/.
    """
    global _cache_singleton
    if _cache_singleton is None or (path is not None and _cache_singleton.path != path):
        _cache_singleton = Cache(path=path)
    return _cache_singleton


def reset_cache_singleton() -> None:
    """Reset the singleton — for tests between scenarios."""
    global _cache_singleton
    if _cache_singleton is not None:
        try:
            _cache_singleton.close()
        except Exception:
            pass
    _cache_singleton = None


@dataclass
class RebuildResult:
    """Summary returned by rebuild_cache_from_stores for logging/CLI."""
    counts: dict[str, int]
    cache_path: Path
