"""Draft the lotto scan email straight from GitHub Actions via the Gmail API.

Replaces the relay routine's WebFetch hop. The relay read lotto_result.md off
cloud-data through WebFetch, whose summarizer model paraphrases longer files;
the relay then failed closed with a STALE/UNAVAILABLE draft. That hit exactly
the windows with a trade card (2026-11-11 PL, 2026-11-12 WULF) while one-line
quiet files relayed fine. Drafting here, where the file is written, has no
copy step to go wrong.

Reads a result file written by publish_results.py:
    GENERATED / ACTIONABLE / STATUS / SUBJECT header lines, '---', body
and creates one Gmail draft per run, whatever the outcome — trade card, quiet
window, or failure notice — so a missing draft means the job didn't run.
The subject is the file's SUBJECT; the body is copied verbatim as plain text.

Credentials come from env (GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET,
GMAIL_REFRESH_TOKEN, GMAIL_TO), set as repo secrets by gmail_oauth_setup.py.
If any is missing this is a DRY RUN: the draft is printed and the script exits
0, so the workflow is safe to merge before the secrets exist. Tokens are never
printed. An API failure exits 1, which fails the job — GitHub's failed-run
email is the alert.

Run:
    python scripts/draft_gmail.py results_out/lotto_result.md
Stdlib only.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

TOKEN_URL = "https://oauth2.googleapis.com/token"
DRAFTS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts"
ENV_KEYS = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_TO")


def parse_result(text: str) -> tuple[dict, str]:
    """Split a result file into its header fields and the body after '---'."""
    head, sep, body = text.partition("\n---\n")
    if not sep:
        raise ValueError("result file has no '---' separator")
    fields = {}
    for line in head.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    if not fields.get("SUBJECT"):
        raise ValueError("result file has no SUBJECT line")
    return fields, body.rstrip("\n")


def build_message(to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def _post(url: str, data: bytes, headers: dict) -> dict:
    """POST with one retry on a network error or 5xx; 4xx fails at once."""
    for attempt in (1, 2):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 2:
                # The error body names the problem (e.g. invalid_grant) and
                # carries no credentials.
                raise RuntimeError(f"{url} -> HTTP {e.code}: {e.read().decode(errors='replace')}") from None
        except urllib.error.URLError as e:
            if attempt == 2:
                raise RuntimeError(f"{url} -> {e.reason}") from None
        time.sleep(5)
    raise AssertionError("unreachable")


def access_token(client_id: str, client_secret: str, refresh_token: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    return _post(TOKEN_URL, data, {"Content-Type": "application/x-www-form-urlencoded"})["access_token"]


def create_draft(token: str, msg: EmailMessage) -> str:
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    data = json.dumps({"message": {"raw": raw}}).encode()
    resp = _post(DRAFTS_URL, data, {"Authorization": f"Bearer {token}",
                                    "Content-Type": "application/json"})
    return resp["id"]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: draft_gmail.py <result-file>", file=sys.stderr)
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        fields, body = parse_result(fh.read())
    subject = fields["SUBJECT"]

    env = {k: os.environ.get(k, "") for k in ENV_KEYS}
    missing = [k for k, v in env.items() if not v]
    if missing:
        print(f"DRY RUN (missing {', '.join(missing)}) — would draft:\n"
              f"Subject: {subject}\n\n{body}")
        return 0

    token = access_token(env["GMAIL_CLIENT_ID"], env["GMAIL_CLIENT_SECRET"],
                         env["GMAIL_REFRESH_TOKEN"])
    draft_id = create_draft(token, build_message(env["GMAIL_TO"], subject, body))
    print(f"drafted {draft_id}: {subject} (STATUS {fields.get('STATUS', '?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
