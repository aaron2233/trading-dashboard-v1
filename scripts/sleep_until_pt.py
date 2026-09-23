"""Sleep until the next target America/Los_Angeles wall time (HH:MM) today.

GitHub Actions `schedule` is best-effort. Measured on this repo: 1.5-3.3h
late in 2026-06/07, then 2.5-6.3h late by 2026-09. A cron mapped 1:1 to a
publish target can't absorb that variance — once the delay exceeds the
lead, the target has already passed and the run publishes late.

GitHub also DROPS most crons: 09-18..09-22 it fired 2 of 16 per day. So one
run must cover every slot still ahead of it. The publish workflow chains one
job per slot (each job gets its own 6h limit); each job calls this script
with its slot, and non-final jobs pass --skip-if-passed so a late run skips
the slots it missed instead of publishing late.

Behavior:
  - some target is still ahead today  -> sleep until the earliest one
  - every target has already passed   -> return immediately, run proceeds
    (keeps the "cloud-data always advances" contract)
    ... unless --skip-if-passed: write skip=true to $GITHUB_OUTPUT so the
    job's later steps skip
  - sleep would exceed the sanity cap -> return immediately (bad target /
    misconfigured cron; don't hold a runner for hours)

DST-proof: targets are computed in America/Los_Angeles, so the UTC moment
shifts automatically at the spring/fall changes. Fixed-UTC crons simply
fire an hour earlier in PT during PST, i.e. they gain lead.

Usage: python3 scripts/sleep_until_pt.py HH:MM[,HH:MM...] [--skip-if-passed]
Stdlib only — runs on the runner's system python3 before any pip install.
"""
from __future__ import annotations

import os
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


def all_passed(now: datetime, targets: list[tuple[int, int]]) -> bool:
    """True when every target is already behind `now` today."""
    return all(now.replace(hour=hh, minute=mm, second=0, microsecond=0) <= now
               for hh, mm in targets)


def main() -> int:
    args = sys.argv[1:]
    skip_if_passed = "--skip-if-passed" in args
    args = [a for a in args if a != "--skip-if-passed"]
    if len(args) != 1:
        print("usage: sleep_until_pt.py HH:MM[,HH:MM...] [--skip-if-passed]",
              file=sys.stderr)
        return 2
    try:
        targets = parse_targets(args[0])
    except ValueError:
        print(f"invalid target list {args[0]!r} — expected HH:MM[,HH:MM...]",
              file=sys.stderr)
        return 2

    now = datetime.now(PT)
    if skip_if_passed and all_passed(now, targets):
        print(f"{args[0]} PT already passed (now {now:%H:%M:%S} PT) — skipping "
              "this slot; a later slot's job publishes")
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as f:
                f.write("skip=true\n")
        return 0

    remaining, msg = choose_target(now, targets)
    print(msg)
    if remaining > 0:
        time.sleep(remaining)
        print(f"woke at {datetime.now(PT):%H:%M:%S} PT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
