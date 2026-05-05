"""FRED client unit tests — no network access.

The client uses an injectable `fetch` callable so we hand it fakes that
return whatever payload shape we want. Real HTTP path is not exercised
here (would need vcr or a sandbox FRED key).
"""
from __future__ import annotations

import pytest

from regime_health.fred_client import (
    FredFetchError,
    FredObservation,
    fetch_latest,
    fetch_observations,
)


def _payload(rows: list[dict]) -> dict:
    """Mimic FRED /series/observations response."""
    return {
        "realtime_start": "2026-05-05",
        "realtime_end": "2026-05-05",
        "observation_start": "1776-07-04",
        "observation_end": "9999-12-31",
        "units": "lin",
        "output_type": 1,
        "file_type": "json",
        "order_by": "observation_date",
        "sort_order": "desc",
        "count": len(rows),
        "offset": 0,
        "limit": len(rows),
        "observations": rows,
    }


# ── Happy paths ──────────────────────────────────────────────────────────────


def test_fetch_observations_happy_path():
    captured: dict[str, str] = {}

    def fake_fetch(url: str) -> dict:
        captured["url"] = url
        return _payload([
            {"date": "2026-04-30", "value": "4.50"},
            {"date": "2026-04-29", "value": "4.42"},
        ])

    obs = fetch_observations(
        "BAMLH0A0HYM2", limit=2, fetch=fake_fetch, api_key="test_key",
    )
    assert len(obs) == 2
    assert obs[0].date == "2026-04-30"
    assert obs[0].value == 4.50
    assert obs[0].series_id == "BAMLH0A0HYM2"
    # URL constructed with required params
    assert "series_id=BAMLH0A0HYM2" in captured["url"]
    assert "api_key=test_key" in captured["url"]
    assert "file_type=json" in captured["url"]
    assert "limit=2" in captured["url"]


def test_fetch_latest_returns_first_obs():
    obs = fetch_latest(
        "T10Y2Y",
        fetch=lambda url: _payload([{"date": "2026-04-30", "value": "0.42"}]),
        api_key="k",
    )
    assert obs.date == "2026-04-30"
    assert obs.value == 0.42


# ── Missing-data tolerance ───────────────────────────────────────────────────


def test_fetch_skips_dot_missing_values():
    """FRED encodes missing data as '.' — we drop those rows."""
    obs = fetch_observations(
        "T5YIE", limit=5,
        fetch=lambda url: _payload([
            {"date": "2026-04-30", "value": "2.45"},
            {"date": "2026-04-29", "value": "."},
            {"date": "2026-04-28", "value": "2.42"},
        ]),
        api_key="k",
    )
    assert len(obs) == 2
    assert [o.date for o in obs] == ["2026-04-30", "2026-04-28"]


def test_fetch_skips_blank_value():
    obs = fetch_observations(
        "X", limit=2,
        fetch=lambda url: _payload([
            {"date": "2026-04-30", "value": ""},
            {"date": "2026-04-29", "value": "1.0"},
        ]),
        api_key="k",
    )
    assert len(obs) == 1


def test_fetch_skips_non_numeric_value():
    obs = fetch_observations(
        "X", limit=2,
        fetch=lambda url: _payload([
            {"date": "2026-04-30", "value": "garbage"},
            {"date": "2026-04-29", "value": "1.0"},
        ]),
        api_key="k",
    )
    assert len(obs) == 1
    assert obs[0].value == 1.0


# ── Error paths ──────────────────────────────────────────────────────────────


def test_fetch_raises_when_key_not_configured(monkeypatch):
    """No api_key arg + no env var → FredFetchError("not configured")."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(FredFetchError, match="not configured"):
        fetch_observations("X", fetch=lambda url: _payload([]))


def test_fetch_uses_env_key_when_arg_omitted(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "env_key_xyz")
    captured = {}

    def fake_fetch(url: str) -> dict:
        captured["url"] = url
        return _payload([{"date": "2026-04-30", "value": "1.0"}])

    fetch_observations("X", fetch=fake_fetch)
    assert "api_key=env_key_xyz" in captured["url"]


def test_fetch_raises_on_fred_error_code():
    """FRED returns error_code in JSON when (e.g.) series doesn't exist."""
    def fake_fetch(url: str) -> dict:
        return {"error_code": 400, "error_message": "Bad series_id"}
    with pytest.raises(FredFetchError, match="Bad series_id"):
        fetch_observations("WRONG_ID", fetch=fake_fetch, api_key="k")


def test_fetch_raises_on_missing_observations():
    def fake_fetch(url: str) -> dict:
        return {"realtime_start": "..."}
    with pytest.raises(FredFetchError, match="missing 'observations'"):
        fetch_observations("X", fetch=fake_fetch, api_key="k")


def test_fetch_raises_on_non_dict_payload():
    def fake_fetch(url: str) -> dict:
        return []  # type: ignore[return-value]
    with pytest.raises(FredFetchError, match="not an object"):
        fetch_observations("X", fetch=fake_fetch, api_key="k")


def test_fetch_latest_raises_when_no_observations():
    """fetch_latest must surface empty rather than return a sentinel."""
    with pytest.raises(FredFetchError, match="No observations"):
        fetch_latest(
            "X",
            fetch=lambda url: _payload([]),
            api_key="k",
        )


# ── Param validation ─────────────────────────────────────────────────────────


def test_fetch_rejects_empty_series_id():
    with pytest.raises(FredFetchError, match="series_id"):
        fetch_observations("", fetch=lambda url: _payload([]), api_key="k")


def test_fetch_rejects_zero_limit():
    with pytest.raises(FredFetchError, match="limit"):
        fetch_observations("X", limit=0, fetch=lambda url: _payload([]), api_key="k")


def test_fetch_rejects_huge_limit():
    with pytest.raises(FredFetchError, match="limit"):
        fetch_observations(
            "X", limit=10_000_000,
            fetch=lambda url: _payload([]), api_key="k",
        )


# ── Observation dataclass ────────────────────────────────────────────────────


def test_observation_to_dict_round_trip():
    o = FredObservation(date="2026-04-30", value=4.5, series_id="HY")
    assert o.to_dict() == {"date": "2026-04-30", "value": 4.5, "series_id": "HY"}
