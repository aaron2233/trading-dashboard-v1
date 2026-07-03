"""Telegram push for the lotto cloud scan — decoupled, optional, stdlib-only.

Reads the scan result JSON (written by `lotto_cloud_scan.py --json`) from a path
arg or stdin, then routes on `status`:

  - "ok"          → send the full actionable scan as a Telegram message.
  - "data_failed" → send a LOUD blackout alert (a Yahoo rate-limit must never be
                    silent — that was the whole point of the cloud-IP test).
  - "skipped"     → send nothing (market closed / no fresh bar). exit 0.
  - "no_setups"   → send nothing (clean scan, just no trades). exit 0.

Message is sent as PLAIN TEXT (no parse_mode) on purpose: the scan body is dense
with '.', '-', '+', '!', '(', ')' which would all 400 the API under MarkdownV2's
18-char escaping rule. Plain text renders the layout fine and needs no escaping.

Credentials come from env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). If either is
missing this is a DRY RUN: the message is printed to stdout and the script exits
0, so it's locally testable and so the GitHub Actions yfinance-IP test can run
Telegram-free. The token is never printed.

Run:
    PYTHONPATH=src python scripts/lotto_cloud_scan.py --json | python scripts/notify_telegram.py
    python scripts/notify_telegram.py result.json
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

TELEGRAM_MAX_CHARS = 4096
# Statuses that warrant a push. "skipped"/"no_setups" are intentionally absent.
PUSH_STATUSES = {"ok", "data_failed"}


def load_result(argv: list[str]) -> dict:
    """Read the scan-result JSON from a path arg, else from stdin."""
    if argv:
        with open(argv[0], encoding="utf-8") as fh:
            return json.load(fh)
    return json.load(sys.stdin)


def _strike_line(t: dict) -> str:
    s = t.get("suggested_strike")
    if s is not None:
        return f"${s['value']:g} (~0.20d, {s['dte']})"
    ladder = t.get("strike_ladder")
    if ladder:
        rungs = " / ".join(f"{c['moneyness']} ${c['strike']:g}" for c in ladder)
        return f"verify ~0.20d on chain — ladder: {rungs}"
    return "n/a"


def _trade_block(t: dict) -> str:
    """Plain-text block per trade, mirroring the scan's Markdown layout."""
    cut = t.get("options_cut_pct")
    cut_label = f"{abs(cut) * 100:.0f}%" if cut is not None else "50%"
    tgt = f"${t['stock_target']:g}" if t.get("stock_target") is not None else "n/a"
    stop = f"${t['stock_stop']:g}" if t.get("stock_stop") is not None else "n/a"
    spot = f"${t['spot']:g}" if t.get("spot") else "n/a"
    return "\n".join([
        f"{t['ticker']} — {t['kind'].upper()}  ·  spot {spot}",
        f"  strike: {_strike_line(t)}",
        f"  stock target {tgt}  ·  stop {stop}",
        f"  options: +{t['options_target_pct']}% target / -{cut_label} hard cut",
        f"  why: {t['why_now']}",
    ])


def build_message(res: dict) -> str:
    """Build the plain-text message body for a push-worthy result."""
    ts = res.get("timestamp_label", "?")
    if res["status"] == "data_failed":
        return (
            "🚨 LOTTO CLOUD SCAN — DATA BLACKOUT\n"
            f"{ts}\n\n"
            f"{res.get('message', 'data fetch failed')}\n\n"
            "No scan produced this window. Likely a datacenter rate-limit on the "
            "Actions runner IP. Verify yfinance health / Polygon fallback."
        )
    # status == "ok"
    head = (
        f"LOTTO SCAN — {ts}\n"
        f"2H close {res.get('window_close', '?')} · {res.get('universe', '?')} · "
        f"{res.get('dte_band', '?')} · {res.get('actionable_count', 0)} actionable\n"
        "⚠️ Cloud scan — blind to your open book / caps / R1 balance. "
        "Verify before entering. Premium/IV/delta: verify on chain."
    )
    blocks = "\n\n".join(_trade_block(t) for t in res.get("trades", []))
    return f"{head}\n\n{blocks}"


def send_telegram(text: str, token: str, chat_id: str) -> bool:
    """POST to sendMessage as plain text. Returns True on Telegram ok:true.
    Raises urllib.error.HTTPError / URLError on transport/HTTP failure."""
    if len(text) > TELEGRAM_MAX_CHARS:
        text = text[: TELEGRAM_MAX_CHARS - 16] + "\n…(truncated)"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
        # parse_mode omitted on purpose → plain text, no escaping needed.
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return bool(json.load(resp).get("ok", False))


def _redact(msg: str, *secrets: str) -> str:
    """Mask secrets before printing. A urllib URLError stringifies the full
    request URL — which embeds the bot token — into its message; this scrubs it
    so the token never reaches stderr / logs / archived transcripts."""
    for s in secrets:
        if s:
            msg = msg.replace(s, "***")
    return msg


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    res = load_result(argv)
    status = res.get("status")

    if status not in PUSH_STATUSES:
        # skipped / no_setups → nothing to push.
        print(f"notify: status '{status}' — no push.", file=sys.stderr)
        return 0

    text = build_message(res)

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        # DRY RUN: no creds → print the message, exit 0. Token never printed.
        print("notify: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID unset — dry run:\n",
              file=sys.stderr)
        print(text)
        return 0

    try:
        ok = send_telegram(text, token, chat_id)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(_redact(f"notify: Telegram HTTP {e.code} — {body}", token),
              file=sys.stderr)
        return 1
    except Exception as e:  # URLError, timeout, etc.
        print(_redact(f"notify: Telegram send error — {e}", token),
              file=sys.stderr)
        return 1

    if not ok:
        print("notify: Telegram returned ok:false", file=sys.stderr)
        return 1
    print("notify: sent.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
