"""Extract structured data from broker / TradingView screenshots via Claude vision.

Two extractors:
  - extract_options_chain : pulls strike/premium/IV/OI/spread/expiry from a
                            broker options-chain screenshot, returning a dict
                            ready to feed into OptionsStructure.
  - extract_truth_fixture : pulls (date, value) pairs from a TradingView Data
                            Window screenshot for indicator validation fixtures.

Privacy note: screenshots are sent to the Anthropic API. This is opt-in only —
nothing happens unless the user explicitly passes --screenshot. Set
ANTHROPIC_API_KEY in the environment to enable.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any

try:
    import anthropic
except ImportError:  # pragma: no cover — dep is required
    anthropic = None  # type: ignore


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ExtractError(Exception):
    """Vision extraction failed (API error or unparseable response)."""


def _media_type(path: Path) -> str:
    return _MEDIA_TYPES.get(path.suffix.lower(), "image/png")


def _get_client(client) -> "anthropic.Anthropic":
    if client is not None:
        return client
    if anthropic is None:
        raise ExtractError(
            "anthropic SDK not installed. Re-install the project with `pip install -e .`"
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractError(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Set it to use screenshot extraction "
            "(get a key at https://console.anthropic.com)."
        )
    return anthropic.Anthropic(api_key=api_key)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Drop opening fence
        lines = lines[1:]
        # Drop closing fence if present
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _parse_json_response(text: str, context: str) -> Any:
    text = _strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Try to recover by extracting the first JSON object
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise ExtractError(
            f"Could not parse {context} response as JSON. Raw: {text!r}"
        ) from exc


def _send_vision_request(
    client: "anthropic.Anthropic",
    image_path: Path | None,
    prompt: str,
    model: str,
    max_tokens: int = 768,
    image_bytes: bytes | None = None,
    media_type_override: str | None = None,
) -> str:
    """Send a vision request. Accepts either a filesystem path OR raw bytes
    (for HTTP uploads where there's no path on disk).
    """
    if image_bytes is not None:
        image_b64 = base64.b64encode(image_bytes).decode()
        media_type = media_type_override or "image/png"
    else:
        if image_path is None or not image_path.exists():
            raise ExtractError(f"Image not found: {image_path}")
        image_b64 = base64.b64encode(image_path.read_bytes()).decode()
        media_type = _media_type(image_path)

    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception as exc:
        raise ExtractError(f"Anthropic API call failed: {exc}") from exc

    if not response.content:
        raise ExtractError("Anthropic returned an empty response.")
    block = response.content[0]
    text = getattr(block, "text", None)
    if text is None:
        raise ExtractError(f"Unexpected response block type: {type(block).__name__}")
    return text


# ─── Options chain ────────────────────────────────────────────────────────────


_OPTIONS_PROMPT_TEMPLATE = """You are extracting options chain data for {ticker} from a broker screenshot.

Identify the row that matches the user's target trade, then extract these fields:
- strike (float)
- premium (float — mid or ask price per share)
- iv_rank (number 0-100; null if not visible)
- open_interest (integer; null if not visible)
- bid_ask_spread (float = ask - bid; null if not derivable)
- expiry (ISO date YYYY-MM-DD)
- contract_type (\"call\" or \"put\")
{target_clause}

Respond with ONLY a JSON object containing those fields. No commentary, no markdown
fences, no extra prose. If a field is genuinely not visible in the screenshot,
use null for that field."""


def extract_options_chain(
    image_path: Path | str | None = None,
    ticker: str = "",
    target_strike: float | None = None,
    target_expiry: str | None = None,
    contract_type: str | None = None,
    client=None,
    model: str = DEFAULT_MODEL,
    image_bytes: bytes | None = None,
    media_type: str | None = None,
) -> dict[str, Any]:
    """Extract options chain row from a screenshot.

    Accepts either `image_path` (filesystem) OR `image_bytes` + `media_type`
    (HTTP upload — no disk write needed).

    Returns a dict with the keys: strike, premium, iv_rank, open_interest,
    bid_ask_spread, expiry, contract_type. Caller should validate and feed
    into OptionsStructure.
    """
    if image_path is not None:
        image_path = Path(image_path)
    elif image_bytes is None:
        raise ExtractError("Must supply either image_path or image_bytes")

    client = _get_client(client)

    constraints: list[str] = []
    if target_strike is not None:
        constraints.append(f"strike near ${target_strike}")
    if target_expiry is not None:
        constraints.append(f"expiry {target_expiry}")
    if contract_type is not None:
        constraints.append(f"contract type {contract_type}")
    target_clause = ""
    if constraints:
        target_clause = "\nTarget the row matching: " + ", ".join(constraints) + "."

    prompt = _OPTIONS_PROMPT_TEMPLATE.format(
        ticker=ticker.upper() if ticker else "the option",
        target_clause=target_clause,
    )
    text = _send_vision_request(
        client, image_path, prompt, model,
        image_bytes=image_bytes, media_type_override=media_type,
    )
    payload = _parse_json_response(text, context="options chain")

    if not isinstance(payload, dict):
        raise ExtractError(f"Expected JSON object, got: {type(payload).__name__}")

    return {
        "strike": payload.get("strike"),
        "premium": payload.get("premium"),
        "iv_rank": payload.get("iv_rank"),
        "open_interest": payload.get("open_interest"),
        "bid_ask_spread": payload.get("bid_ask_spread"),
        "expiry": payload.get("expiry"),
        "contract_type": payload.get("contract_type"),
    }


# ─── TradingView truth fixture ────────────────────────────────────────────────


_TRUTH_PROMPT_TEMPLATE = """You are extracting indicator values from a TradingView screenshot for {ticker}.

The image shows the TradingView Data Window for the {indicator_name} indicator.
Extract a list of (date, value) rows visible in the screenshot.

Output schema:
{{
  "rows": [
    {{"date": "YYYY-MM-DD", {value_keys}}},
    ...
  ]
}}

Respond with ONLY this JSON object. No commentary, no markdown fences."""


def extract_truth_fixture(
    image_path: Path | str,
    ticker: str,
    indicator_name: str,
    value_columns: list[str],
    client=None,
    model: str = DEFAULT_MODEL,
) -> list[dict[str, Any]]:
    """Extract per-bar truth values from a TradingView screenshot.

    Returns list of dicts, one per visible bar, with keys 'date' and each
    column in value_columns.
    """
    image_path = Path(image_path)
    client = _get_client(client)

    value_keys = ", ".join(f'"{c}": <value>' for c in value_columns)
    prompt = _TRUTH_PROMPT_TEMPLATE.format(
        ticker=ticker.upper(),
        indicator_name=indicator_name,
        value_keys=value_keys,
    )

    text = _send_vision_request(client, image_path, prompt, model)
    payload = _parse_json_response(text, context="truth fixture")

    if not isinstance(payload, dict) or "rows" not in payload:
        raise ExtractError(f"Expected {{'rows': [...]}}, got: {payload!r}")
    rows = payload["rows"]
    if not isinstance(rows, list):
        raise ExtractError(f"'rows' must be a list, got: {type(rows).__name__}")
    return rows
