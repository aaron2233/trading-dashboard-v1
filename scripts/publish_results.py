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
    SUBJECT: <draft subject>
    ---
    <body to draft verbatim>
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
BM_TICKERS = ["QQQ", "SPY", "QQQM", "GLD", "NVDA", "MP", "QLD"]


def capture(script: str, extra_env: dict) -> tuple[str, str, int]:
    env = {**os.environ, **extra_env, "PYTHONPATH": str(ROOT / "src")}
    p = subprocess.run([PY, str(ROOT / "scripts" / script)],
                       capture_output=True, text=True, env=env, timeout=600)
    return p.stdout.strip(), p.stderr.strip(), p.returncode


def stage_beat_market(tmp: Path):
    """Stage the 7 beat-market 1d CSVs live so beat_market_monitor (which reads
    STAGED_DATA_DIR) can run in Actions without its own Yahoo fetch."""
    sys.path.insert(0, str(ROOT / "src"))
    from data.yfinance_loader import load_bars
    tmp.mkdir(parents=True, exist_ok=True)
    for t in BM_TICKERS:
        try:
            load_bars(t, interval="1d").to_csv(tmp / f"{t}__1d.csv")
        except Exception as e:
            print(f"BM stage fail {t}: {e}", file=sys.stderr)


def write_result(path: Path, generated: str, actionable: bool, subject: str, body: str):
    path.write_text(
        f"GENERATED: {generated}\n"
        f"ACTIONABLE: {'YES' if actionable else 'NO'}\n"
        f"SUBJECT: {subject}\n"
        f"---\n{body}\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--now", required=True, help="ISO-UTC generation timestamp")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # ---- LOTTO: run live (no STAGED_DATA_DIR -> scan.load_bars uses yfinance) ----
    lo, le, _ = capture("lotto_cloud_scan.py", {})
    l_actionable = lo.startswith("# Lotto Scan")
    if l_actionable:
        first = lo.splitlines()[0].replace("# Lotto Scan —", "").strip()
        l_subject = f"Lotto 2H scan — {first} PT"
        l_body = lo
    else:
        # not actionable (no_setups / skipped / data_failed) -> no draft, but
        # record the line so the routine/log can see what happened.
        l_subject = "Lotto 2H scan — no actionable setups"
        l_body = lo or le or "no output"
    write_result(out / "lotto_result.md", args.now, l_actionable, l_subject, l_body)

    # ---- BEAT-MARKET: stage 7 tickers live, then run the monitor off them ----
    bm_dir = Path("/tmp/bm_staged")
    stage_beat_market(bm_dir)
    bo, be, _ = capture("beat_market_monitor.py", {"STAGED_DATA_DIR": str(bm_dir)})
    b_actionable = bo.startswith("ACTIONABLE: YES")
    headline = next((ln.split(":", 1)[1].strip()
                     for ln in bo.splitlines() if ln.startswith("HEADLINE:")), "")
    b_subject = f"Beat-Market Monitor -- {headline} -- {args.now[:10]}"
    write_result(out / "beat_market_result.md", args.now, b_actionable, b_subject,
                 bo or be or "no output")

    print(f"lotto actionable={l_actionable} | beat-market actionable={b_actionable} "
          f"(headline: {headline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
