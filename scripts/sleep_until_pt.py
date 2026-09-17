"""Sleep until the next target America/Los_Angeles wall time (HH:MM) today.

GitHub Actions `schedule` is best-effort. Measured on this repo: 1.5-3.3h
late in 2026-06/07, then 2.5-6.3h late by 2026-09. A cron mapped 1:1 to a
publish target can't absorb that variance — once the delay exceeds the
lead, the target has already passed and the run publishes late.

So the scheduled workflow fires MANY crons (every 30 min through the
morning) and every run calls this script with the FULL slot list. Whatever
fires, the run sleeps to the next slot still ahead of it; the workflow's
concurrency group serializes the runs so one holds each slot and the extra
pending runs are cancelled by GitHub.

Behavior:
  - some target is still ahead today  -> sleep until the earliest one
  - every target has already passed   -> return immediately, run proceeds
    (keeps the "cloud-data always advances" contract)
  - sleep would exceed the sanity cap -> return immediately (bad target /
    misconfigured cron; don't hold a runner for hours)

DST-proof: targets are computed in America/Los_Angeles, so the UTC moment
shifts automatically at the spring/fall changes. Fixed-UTC crons simply
fire an hour earlier in PT during PST, i.e. they gain lead.

Usage: python3 scripts/sleep_until_pt.py HH:MM[,HH:MM...]
Stdlib only — runs on the runner's system python3 before any pip install.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
MAX_SLEEP_S = 5 * 3600  # cap: earliest cron leads the first slot by <5h


def parse_targets(arg: str) -> list[tuple[int, int]]:
    """'08:31,10:31' -> [(8, 31), (10, 31)]. Raises ValueError on junk."""
    out = []
    for part in arg.split(","):
        hh, mm = (int(p) for p in part.strip().split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(part)
        out.append((hh, mm))
    if not out:
        raise ValueError(arg)
    return out


def choose_target(now: datetime, targets: list[tuple[int, int]],
                  max_sleep_s: int = MAX_SLEEP_S) -> tuple[float, str]:
    """Return (seconds_to_sleep, message). 0 seconds = proceed immediately."""
    ahead = []
    for hh, mm in targets:
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        remaining = (t - now).total_seconds()
        if remaining > 0:
            ahead.append((remaining, f"{hh:02d}:{mm:02d}"))
    if not ahead:
        return 0, (f"every target PT already passed (now {now:%H:%M:%S} PT) "
                   "— proceeding immediately")
    remaining, label = min(ahead)
    if remaining > max_sleep_s:
        return 0, (f"refusing to sleep {remaining / 3600:.1f}h until {label} PT "
                   f"(> {max_sleep_s // 3600}h cap) — proceeding immediately")
    return remaining, (f"now {now:%H:%M:%S} PT — sleeping {remaining / 60:.1f} min "
                       f"until {label} PT")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sleep_until_pt.py HH:MM[,HH:MM...]", file=sys.stderr)
        return 2
    try:
        targets = parse_targets(sys.argv[1])
    except ValueError:
        print(f"invalid target list {sys.argv[1]!r} — expected HH:MM[,HH:MM...]",
              file=sys.stderr)
        return 2

    remaining, msg = choose_target(datetime.now(PT), targets)
    print(msg)
    if remaining > 0:
        time.sleep(remaining)
        print(f"woke at {datetime.now(PT):%H:%M:%S} PT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
