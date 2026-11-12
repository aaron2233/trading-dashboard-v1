"""One-time setup for draft_gmail.py: authorize Gmail drafts and store the
credentials as GitHub repo secrets. Run locally, not in Actions.

Before running, in Google Cloud console (one project, any name):
  1. Enable the Gmail API.
  2. OAuth consent screen: External, add the scope .../auth/gmail.compose,
     then PUBLISH THE APP (status "In
     production"). In "Testing" Google expires the refresh token after 7 days
     and the drafts would quietly stop. Unverified is fine for your own use —
     you click through a "Google hasn't verified this app" warning once.
  3. Credentials -> Create OAuth client ID -> Desktop app -> download JSON.

Then:
    python scripts/gmail_oauth_setup.py ~/Downloads/client_secret_XXX.json --to you@example.com

Sign in with the account whose Drafts should hold the emails. The script
catches the redirect on 127.0.0.1, writes GMAIL_CLIENT_ID /
GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN / GMAIL_TO with `gh secret set`
(values go over stdin, never printed or written to disk), and creates one
test draft so you can see it worked. Stdlib + the gh CLI only.
"""
from __future__ import annotations

import argparse
import http.server
import json
import secrets
import subprocess
import sys
import urllib.parse
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import draft_gmail  # noqa: E402

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/gmail.compose"
REPO = "aaron2233/trading-dashboard-v1"


def loopback_server(state: str):
    """A server on a free 127.0.0.1 port that captures one OAuth redirect.
    Returns (server, result); result["redirect_uri"] is set now, and
    result["code"] once server.handle_request() has served the redirect."""
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            ok = q.get("state", [""])[0] == state and "code" in q
            result["code"] = q["code"][0] if ok else None
            result["error"] = q.get("error", ["state mismatch or no code"])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Done - you can close this tab." if ok else b"Authorization failed.")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}"
    result["redirect_uri"] = redirect_uri
    return server, result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("client_json", help="OAuth client JSON downloaded from Google Cloud (Desktop app)")
    ap.add_argument("--to", required=True, help="address the drafts are addressed to")
    args = ap.parse_args()

    client = json.loads(Path(args.client_json).expanduser().read_text())["installed"]
    state = secrets.token_urlsafe(16)
    server, result = loopback_server(state)
    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": result["redirect_uri"],
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    print("Opening the browser to authorize Gmail drafts. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    server.handle_request()
    if not result.get("code"):
        print(f"Authorization failed: {result.get('error')}", file=sys.stderr)
        return 1

    data = urllib.parse.urlencode({
        "code": result["code"],
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": result["redirect_uri"],
        "grant_type": "authorization_code",
    }).encode()
    tokens = draft_gmail._post("https://oauth2.googleapis.com/token", data,
                               {"Content-Type": "application/x-www-form-urlencoded"})
    if "refresh_token" not in tokens:
        print("Google returned no refresh token — remove the app's access at "
              "myaccount.google.com/permissions and run again.", file=sys.stderr)
        return 1

    values = {
        "GMAIL_CLIENT_ID": client["client_id"],
        "GMAIL_CLIENT_SECRET": client["client_secret"],
        "GMAIL_REFRESH_TOKEN": tokens["refresh_token"],
        "GMAIL_TO": args.to,
    }
    for name, value in values.items():
        subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=value,
                       text=True, check=True, capture_output=True)
        print(f"set secret {name}")

    msg = draft_gmail.build_message(args.to, "Lotto 2H scan — setup test",
                                    "Gmail drafts from GitHub Actions are authorized.")
    print(f"test draft created: {draft_gmail.create_draft(tokens['access_token'], msg)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
