"""Run both scans LIVE in GitHub Actions and publish small result files that
the cloud routines can WebFetch + draft via Gmail MCP.

Why: the scheduled cloud-routine sandbox can reach NEITHER Yahoo NOR GitHub
(egress proxy blocks both; the claude.ai allowlist setting doesn't propagate to
containers — verified repeatedly). But GitHub Actions has full egress, and the
routines' WebFetch + Gmail-MCP channels bypass the sandbox proxy (capex proves
it). So Actions does all the work and writes results the routines just relay.
See project-cloud-routine-egress-allowlist.

Writes <out>/lotto_result.md and <out>/beat_market_result.md, each:
    GENERATED: <iso-utc>
    ACTIONABLE: YES|NO
    STATUS: OK|NO_SETUPS|SKIPPED|FAILED
    SUBJECT: <draft subject>
    ---
    <body to draft verbatim>

ACTIONABLE means "the routine should draft an email": YES for real trades AND
for failures that need attention (a blackout must never read as a quiet day).
STATUS carries the actual outcome. Every code path writes BOTH result files —
a crash in one scan must never leave the previous window's file as the latest
on cloud-data, where it would be re-emailed as fresh.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BM_TICKERS = ["QQQ", "SPY", "QQQM", "NVDA", "QLD"]


def capture(script: str, extra_env: dict) -> tuple[str, str, int]:
    """Run a scan subprocess. Never raises — a hang (timeout) or spawn
    failure returns a nonzero code so the caller writes a FAILED result
    instead of aborting with the previous window's files still current."""
    env = {**os.environ, **extra_env, "PYTHONPATH": str(ROOT / "src")}
    try:
        p = subprocess.run([PY, str(ROOT / "scripts" / script)],
                           capture_output=True, text=True, env=env, timeout=600)
        return p.stdout.strip(), p.stderr.strip(), p.returncode
    except subprocess.TimeoutExpired:
        return "", f"{script} timed out after 600s", 124
    except Exception as e:  # spawn failure, OS error
        return "", f"{script} failed to run: {type(e).__name__}: {e}", 1


def stage_beat_market(tmp: Path) -> list[str]:
    """Stage the 5 beat-market 1d CSVs live so beat_market_monitor (which reads
    STAGED_DATA_DIR) can run in Actions without its own Yahoo fetch.
    Returns the tickers that FAILED to stage — the monitor treats a missing
    CSV as 'no data' and silently skips that ticker's triggers, so the caller
    must surface partial staging instead of letting it read as a HOLD day."""
    sys.path.insert(0, str(ROOT / "src"))
    from data.yfinance_loader import load_bars
    tmp.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    for t in BM_TICKERS:
        try:
            load_bars(t, interval="1d").to_csv(tmp / f"{t}__1d.csv")
        except Exception as e:
            failed.append(t)
            print(f"BM stage fail {t}: {e}", file=sys.stderr)
    return failed


def write_result(path: Path, generated: str, actionable: bool, status: str,
                 subject: str, body: str):
    path.write_text(
        f"GENERATED: {generated}\n"
        f"ACTIONABLE: {'YES' if actionable else 'NO'}\n"
        f"STATUS: {status}\n"
        f"SUBJECT: {subject}\n"
        f"---\n{body}\n"
    )


def classify_lotto(stdout: str, returncode: int) -> str:
    """Map the lotto script's stdout contract to a STATUS. Anything
    unrecognized (crash traceback, empty output, nonzero exit) is FAILED —
    fail loud, never 'no actionable setups'."""
    if returncode != 0 or not stdout:
        return "FAILED"
    if stdout.startswith("# Lotto Scan"):
        return "OK"
    if "DATA FETCH FAILED" in stdout:
        return "FAILED"
    if "market closed" in stdout or "skipping" in stdout:
        return "SKIPPED"
    if "no actionable setups" in stdout:
        return "NO_SETUPS"
    return "FAILED"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--now", required=True, help="ISO-UTC generation timestamp")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- LOTTO: run live (no STAGED_DATA_DIR -> scan.load_bars uses yfinance) ----
    lo, le, lc = capture("lotto_cloud_scan.py", {})
    l_status = classify_lotto(lo, lc)
    if l_status == "OK":
        first = lo.splitlines()[0].replace("# Lotto Scan —", "").strip()
        l_subject = f"Lotto 2H scan — {first} PT"
        l_body = lo
    elif l_status == "FAILED":
        # A blackout/crash drafts a failure notice — it must never be
        # indistinguishable from a genuine quiet day.
        l_subject = "Lotto 2H scan — SCAN FAILED (needs attention)"
        l_body = (
            "⚠️ The lotto scan did not produce a result this window. Do NOT "
            "trade off any earlier scan email — its levels are stale.\n\n"
            f"Output:\n{lo or '(none)'}\n\nErrors:\n{le or '(none)'}"
        )
    else:  # SKIPPED / NO_SETUPS — quiet outcomes, no draft
        l_subject = "Lotto 2H scan — no actionable setups"
        l_body = lo or le or "no output"
    l_actionable = l_status in ("OK", "FAILED")
    write_result(out / "lotto_result.md", args.now, l_actionable, l_status,
                 l_subject, l_body)

    # ---- BEAT-MARKET: stage 7 tickers live, then run the monitor off them ----
    bm_dir = Path("/tmp/bm_staged")
    try:
        bm_failed = stage_beat_market(bm_dir)
    except Exception as e:  # import failure etc. — still publish a result
        bm_failed = list(BM_TICKERS)
        print(f"BM staging crashed: {e}", file=sys.stderr)
    bo, be, bc = capture("beat_market_monitor.py", {"STAGED_DATA_DIR": str(bm_dir)})
    if bc != 0 or not bo:
        b_status = "FAILED"
        b_actionable = True
        b_subject = f"Beat-Market Monitor -- SCAN FAILED -- {args.now[:10]}"
        bo = (f"⚠️ Monitor did not run. Output:\n{bo or '(none)'}\n\n"
              f"Errors:\n{be or '(none)'}")
        headline = "SCAN FAILED"
    else:
        b_actionable = bo.startswith("ACTIONABLE: YES")
        b_status = "OK"
        headline = next((ln.split(":", 1)[1].strip()
                         for ln in bo.splitlines() if ln.startswith("HEADLINE:")), "")
        if bm_failed:
            # Partial staging = those tickers' triggers were never evaluated.
            # A HOLD verdict with missing inputs is not a verdict — draft it.
            b_status = "DEGRADED"
            b_actionable = True
            headline = f"{headline} (INCOMPLETE — {','.join(bm_failed)} unstaged)"
            bo = (f"⚠️ STAGING INCOMPLETE: {', '.join(bm_failed)} failed to "
                  f"stage — triggers on these names were NOT evaluated this "
                  f"run.\n\n{bo}")
        b_subject = f"Beat-Market Monitor -- {headline} -- {args.now[:10]}"
    write_result(out / "beat_market_result.md", args.now, b_actionable, b_status,
                 b_subject, bo or be or "no output")

    print(f"lotto status={l_status} actionable={l_actionable} | "
          f"beat-market status={b_status} actionable={b_actionable} "
          f"(headline: {headline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
