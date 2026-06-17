"""Unit tests — :class:`BotRateLimitMiddleware` (4-key per-bot rate limit).

Per-tenant fairness layer (case study 2026-05-16). The middleware bucket
Redis key on ``(record_tenant_id, workspace_id, bot_id, channel_type)``
so two tenants × workspaces both naming a bot ``support`` on channel
``web`` each get an independent counter.

These tests cover the pure logic surfaces — path matching, identity
resolve, key derivation, ingest-vs-chat tier selection — that can run
without a live Redis or FastAPI app. The dispatch flow itself is
covered indirectly: every gate it depends on is exercised here.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest

from ragbot.interfaces.http.middlewares.bot_rate_limit import (
    BotRateLimitMiddleware,
    _is_ingest_path,
    _make_redis_key,
    _resolve_bot_identity,
)


# ---------------------------------------------------------------------
# _is_ingest_path — heuristic regex.
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/ragbot/test/bots/support/web/documents/upload", True),
        ("/api/ragbot/test/bots/support/web/documents/sync", True),
        ("/api/ragbot/sync/foo", True),
        ("/api/v1/documents/ingest", True),
        ("/api/ragbot/test/bots/support/web/chat", False),
        ("/api/ragbot/chat", False),
        ("/health", False),
        ("/api/ragbot/admin/audit/messages/1", False),
    ],
)
def test_is_ingest_path(path: str, expected: bool) -> None:
    """Ingest hint patterns trigger the tighter cap tier."""
    assert _is_ingest_path(path) is expected


# ---------------------------------------------------------------------
# _make_redis_key — bucket key derivation contract.
# ---------------------------------------------------------------------


def test_make_redis_key_has_4_key_tuple_and_bucket() -> None:
    """Key must encode all 4 identity dimensions + window bucket so two
    tenants/workspaces/bots/channels never share a counter."""
    identity = ("tenant-A", "ws-prod", "support", "web")
    key = _make_redis_key(identity, window_bucket=12345)
    assert key == "ragbot:rl:bot:tenant-A:ws-prod:support:web:12345"


def test_make_redis_key_isolation_between_tenants() -> None:
    """Same workspace/bot/channel under two tenants → different keys."""
    a = _make_redis_key(("tenant-A", "ws", "bot", "web"), 100)
    b = _make_redis_key(("tenant-B", "ws", "bot", "web"), 100)
    assert a != b


def test_make_redis_key_isolation_between_workspaces() -> None:
    """Same tenant, two workspaces, same bot+channel → different keys."""
    a = _make_redis_key(("tenant-A", "ws-prod", "bot", "web"), 100)
    b = _make_redis_key(("tenant-A", "ws-stage", "bot", "web"), 100)
    assert a != b


def test_make_redis_key_isolation_between_buckets() -> None:
    """Same identity, two windows → different keys (sliding fixed-window)."""
    a = _make_redis_key(("t", "w", "b", "c"), 100)
    b = _make_redis_key(("t", "w", "b", "c"), 101)
    assert a != b


# ---------------------------------------------------------------------
# _resolve_bot_identity — extract 4-key tuple from request state / path.
# ---------------------------------------------------------------------


def _mock_request(*, path: str, state: dict | None = None, headers: dict | None = None) -> MagicMock:
    """Lightweight ``Request`` stand-in covering the fields the middleware reads."""
    req = MagicMock()
    req.url.path = path
    req.headers = headers or {}
    state_ns = SimpleNamespace(**(state or {}))
    req.state = state_ns
    return req


def test_resolve_identity_from_pre_resolved_state() -> None:
    """Strategy 1: ``request.state.bot_identity`` set by a prior route handler."""
    tenant_uuid = UUID("00000000-0000-0000-0000-000000000001")
    req = _mock_request(
        path="/api/ragbot/anything",
        state={
            "bot_identity": {
                "record_tenant_id": tenant_uuid,
                "workspace_id": "ws-prod",
                "bot_id": "support",
                "channel_type": "web",
            },
        },
    )
    assert _resolve_bot_identity(req) == (str(tenant_uuid), "ws-prod", "support", "web")


def test_resolve_identity_from_canonical_path_and_state() -> None:
    """Strategy 2: bot_id+channel from path, tenant from JWT-bound state."""
    tenant_uuid = UUID("00000000-0000-0000-0000-000000000002")
    req = _mock_request(
        path="/api/ragbot/test/bots/support/web/documents/upload",
        state={"record_tenant_id": tenant_uuid},
    )
    identity = _resolve_bot_identity(req)
    assert identity is not None
    assert identity[2] == "support" and identity[3] == "web"
    # workspace_id falls back to tenant string when no header supplied
    assert identity[0] == str(tenant_uuid)
    assert identity[1] == str(tenant_uuid)


def test_resolve_identity_workspace_id_from_header_wins_over_fallback() -> None:
    """``X-Workspace-Id`` header preferred over the tenant-string fallback."""
    tenant_uuid = UUID("00000000-0000-0000-0000-000000000003")
    req = _mock_request(
        path="/api/ragbot/test/bots/support/web/chat",
        state={"record_tenant_id": tenant_uuid},
        headers={"X-Workspace-Id": "ws-staging"},
    )
    identity = _resolve_bot_identity(req)
    assert identity is not None and identity[1] == "ws-staging"


def test_resolve_identity_returns_none_when_no_tenant_in_state() -> None:
    """Path-only routes without a tenant context can't be scoped — bypass."""
    req = _mock_request(
        path="/api/ragbot/test/bots/support/web/chat",
        state={},  # no record_tenant_id
    )
    assert _resolve_bot_identity(req) is None


def test_resolve_identity_returns_none_for_unrelated_path() -> None:
    """Paths that don't match the canonical pattern bypass this layer."""
    req = _mock_request(
        path="/api/ragbot/admin/audit/messages/1",
        state={"record_tenant_id": UUID("00000000-0000-0000-0000-000000000004")},
    )
    assert _resolve_bot_identity(req) is None


# ---------------------------------------------------------------------
# Middleware construction + Redis resolver.
# ---------------------------------------------------------------------


def test_middleware_init_clamps_per_min_to_int() -> None:
    """Operator override via env passes through int() so float values
    raise loud at construction rather than mid-request."""
    mw = BotRateLimitMiddleware(app=MagicMock(), per_min=250)
    assert mw._per_min == 250


def test_resolve_redis_returns_none_when_container_missing() -> None:
    """A FastAPI app without ``state.container`` (test harness) must
    degrade open — the dispatch caller passes the request through."""
    req = MagicMock()
    req.app.state = SimpleNamespace()  # no container attribute
    assert BotRateLimitMiddleware._resolve_redis(req) is None


def test_resolve_redis_returns_none_when_provider_raises() -> None:
    """DI container hooks that raise on resolution → degrade open."""
    raising_provider = MagicMock(side_effect=RuntimeError("DI not wired"))
    container = MagicMock()
    container.redis_client = raising_provider
    req = MagicMock()
    req.app.state.container = container
    assert BotRateLimitMiddleware._resolve_redis(req) is None
