"""Sleep until a target America/Los_Angeles wall time (HH:MM) today.

GitHub Actions `schedule` is best-effort: measured on this repo, scheduled
runs start 1.5-3.3h after their cron time (2026-06/07 run history). The
scheduled workflows therefore set their crons ~3h BEFORE each market window
and call this script to absorb the variable queue delay, so the actual work
(fetch/scan) starts at the intended wall time.

Behavior:
  - runner starts before the target  -> sleep the remainder, wake on time
  - runner starts after the target   -> return immediately, run proceeds
    late (no worse than the pre-fix status quo)
  - sleep would exceed the sanity cap -> return immediately (bad target /
    misconfigured cron; don't hold a runner for hours)

DST-proof: the target is computed in America/Los_Angeles, so the UTC moment
shifts automatically at the spring/fall changes. The crons need NO PDT/PST
swap — winter runs simply sleep up to an hour longer.

Usage: python3 scripts/sleep_until_pt.py HH:MM
Stdlib only — runs on the runner's system python3 before any pip install.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

PT = ZoneInfo("America/Los_Angeles")
MAX_SLEEP_S = 5 * 3600  # cap: crons lead by ~3h (PDT) / ~4h (PST)


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: sleep_until_pt.py HH:MM", file=sys.stderr)
        return 2
    try:
        hh, mm = (int(p) for p in sys.argv[1].split(":"))
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError
    except ValueError:
        print(f"invalid target {sys.argv[1]!r} — expected HH:MM", file=sys.stderr)
        return 2

    now = datetime.now(PT)
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    remaining = (target - now).total_seconds()

    if remaining <= 0:
        print(f"target {sys.argv[1]} PT already passed (now {now:%H:%M:%S} PT) "
              "— proceeding immediately")
        return 0
    if remaining > MAX_SLEEP_S:
        print(f"refusing to sleep {remaining / 3600:.1f}h (> {MAX_SLEEP_S // 3600}h cap) "
              "— proceeding immediately")
        return 0

    print(f"now {now:%H:%M:%S} PT — sleeping {remaining / 60:.1f} min "
          f"until {sys.argv[1]} PT")
    time.sleep(remaining)
    print(f"woke at {datetime.now(PT):%H:%M:%S} PT")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
