"""Loadtest-bypass helper unit tests.

Five mock-only cases pinning the env+header+localhost gate matrix:

a. Header matches env, env set, peer is loopback → bypass granted.
b. Header value mismatches env → no bypass.
c. Env unset (or empty) → fail-closed even with header present.
d. Peer is non-loopback → bypass denied even with valid token.
e. Header absent → no bypass.

Each case asserts the boolean directly and (where bypass occurs) verifies
that exactly one ``loadtest_bypass_used`` structlog event fires with the
expected fields and no PII (token / header value / IP-prefix beyond peer).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from structlog.testing import capture_logs

from ragbot.interfaces.http.middlewares.loadtest_bypass import (
    LOADTEST_BYPASS_LOCALHOSTS,
    is_loadtest_bypass,
)
from ragbot.shared.constants import (
    RAGBOT_LOADTEST_BYPASS_ENV,
    RAGBOT_LOADTEST_BYPASS_HEADER,
)

_TOKEN = "operator-loadtest-secret-not-real"  # noqa: S105 — fixture string, not a credential
_PROBE_PATH = "/api/ragbot/test/chat"


def _make_request(
    *,
    header_value: str = "",
    peer: str,
    path: str = _PROBE_PATH,
) -> MagicMock:
    """Mock a Starlette Request exposing only the surface the helper reads.

    An empty ``header_value`` models the header-absent case (the helper
    treats missing and empty header values identically).
    """
    request = MagicMock()
    headers: dict[str, str] = {}
    if header_value:
        headers[RAGBOT_LOADTEST_BYPASS_HEADER] = header_value
    request.headers = headers
    client = MagicMock()
    client.host = peer
    request.client = client
    request.url.path = path
    return request


def test_a_header_match_env_set_localhost_grants_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All three gates pass → bypass returns True and emits structured log."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    request = _make_request(header_value=_TOKEN, peer="127.0.0.1")

    with capture_logs() as events:
        result = is_loadtest_bypass(request)

    assert result is True
    bypass_events = [e for e in events if e.get("event") == "loadtest_bypass_used"]
    assert len(bypass_events) == 1
    only = bypass_events[0]
    assert only["peer"] == "127.0.0.1"
    assert only["path"] == _PROBE_PATH
    # Token / header value never leaked into the log.
    assert _TOKEN not in repr(only)
    assert RAGBOT_LOADTEST_BYPASS_HEADER not in only


def test_b_header_mismatch_denies_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrong header value (constant-time compare) → False, no log."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    request = _make_request(header_value="not-the-token", peer="127.0.0.1")

    with capture_logs() as events:
        result = is_loadtest_bypass(request)

    assert result is False
    assert [e for e in events if e.get("event") == "loadtest_bypass_used"] == []


def test_c_env_empty_fails_closed_even_with_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env unset OR empty string → bypass disabled regardless of header."""
    monkeypatch.delenv(RAGBOT_LOADTEST_BYPASS_ENV, raising=False)
    request_unset = _make_request(header_value=_TOKEN, peer="127.0.0.1")
    assert is_loadtest_bypass(request_unset) is False

    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, "")
    request_empty = _make_request(header_value=_TOKEN, peer="127.0.0.1")
    with capture_logs() as events:
        result_empty = is_loadtest_bypass(request_empty)
    assert result_empty is False
    assert [e for e in events if e.get("event") == "loadtest_bypass_used"] == []


def test_d_non_localhost_peer_denies_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid token from a public IP must not earn bypass."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    public_request = _make_request(header_value=_TOKEN, peer="203.0.113.7")

    with capture_logs() as events:
        result = is_loadtest_bypass(public_request)

    assert result is False
    assert [e for e in events if e.get("event") == "loadtest_bypass_used"] == []
    # Loopback set is exactly the two RFC-defined loopback addresses.
    assert frozenset({"127.0.0.1", "::1"}) == LOADTEST_BYPASS_LOCALHOSTS


def test_e_header_absent_denies_bypass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env set but header missing → False, no log (do not advertise feature)."""
    monkeypatch.setenv(RAGBOT_LOADTEST_BYPASS_ENV, _TOKEN)
    request = _make_request(peer="127.0.0.1")

    with capture_logs() as events:
        result = is_loadtest_bypass(request)

    assert result is False
    assert [e for e in events if e.get("event") == "loadtest_bypass_used"] == []
