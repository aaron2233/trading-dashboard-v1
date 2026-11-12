"""Tests for draft_gmail.py — the Actions-side lotto draft that replaced the
relay routine's WebFetch copy (which paraphrased trade cards into STALE drafts).
No network: the HTTP helper is monkeypatched."""
from __future__ import annotations

import base64
import email
import json
import sys
from email import policy
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import draft_gmail  # noqa: E402

CARD = """GENERATED: 2026-11-12T18:31:00Z
ACTIONABLE: YES
STATUS: OK
SUBJECT: Lotto 2H scan — 2026-11-12 10:31 PST PT
---
# Lotto Scan — 2026-11-12 10:31 PST
### TST1 — PUT  ·  spot $13.38
- **Options invalidation:** −50% of entry premium
"""


def test_parse_result_splits_header_and_keeps_body_verbatim():
    fields, body = draft_gmail.parse_result(CARD)
    assert fields["STATUS"] == "OK"
    assert fields["SUBJECT"] == "Lotto 2H scan — 2026-11-12 10:31 PST PT"
    assert body.startswith("# Lotto Scan — 2026-11-12 10:31 PST\n### TST1")
    assert body.endswith("entry premium")


def test_parse_result_rejects_file_without_separator():
    with pytest.raises(ValueError):
        draft_gmail.parse_result("GENERATED: x\nSUBJECT: y\n")


def test_dry_run_without_credentials(tmp_path, monkeypatch, capsys):
    for k in draft_gmail.ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(draft_gmail, "_post", lambda *a: pytest.fail("no network in dry run"))
    f = tmp_path / "lotto_result.md"
    f.write_text(CARD, encoding="utf-8")
    assert draft_gmail.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("DRY RUN")
    assert "TST1 — PUT" in out


def test_drafts_subject_and_body_unchanged(tmp_path, monkeypatch):
    for k in draft_gmail.ENV_KEYS:
        monkeypatch.setenv(k, "x")
    calls = []

    def fake_post(url, data, headers):
        calls.append((url, data))
        return {"access_token": "tok"} if url == draft_gmail.TOKEN_URL else {"id": "r1"}

    monkeypatch.setattr(draft_gmail, "_post", fake_post)
    f = tmp_path / "lotto_result.md"
    f.write_text(CARD, encoding="utf-8")
    assert draft_gmail.main([str(f)]) == 0

    assert [u for u, _ in calls] == [draft_gmail.TOKEN_URL, draft_gmail.DRAFTS_URL]
    raw = json.loads(calls[1][1])["message"]["raw"]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
    assert msg["Subject"] == "Lotto 2H scan — 2026-11-12 10:31 PST PT"
    assert msg.get_content().rstrip("\n") == draft_gmail.parse_result(CARD)[1]
