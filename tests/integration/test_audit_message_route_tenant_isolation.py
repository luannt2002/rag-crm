"""CRIT-1 (F8 red-team report) — `/audit/messages/{message_id}` must
NOT leak cross-tenant pipeline traces.

End-to-end at the route layer with a stubbed
``InvocationLogger.fetch_by_message_id`` that records the ``record_tenant_id``
argument, plus an injected ``request.state.tenant_id`` from JWT. The
test pins the contract that the route forwards JWT tenant to the repo
and that a tenant-A admin asking for tenant-B's message gets the empty
shape (which the repo achieves by SQL filter on ``record_tenant_id``).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from ragbot.interfaces.http.routes import admin_audit


def _request(*, tenant_uuid: UUID | None, logger: MagicMock) -> Any:
    container = MagicMock()
    container.invocation_logger = MagicMock(return_value=logger)
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    state_kwargs: dict[str, Any] = {"role": "tenant_admin"}
    if tenant_uuid is not None:
        state_kwargs["tenant_id"] = tenant_uuid
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(**state_kwargs),
    )


# ---------------------------------------------------------------------------
# 1. Cross-tenant probe blocked: tenant A admin queries tenant B's
#    message_id → empty result returned, AND repo received tenant A
#    (NOT tenant B) — so the SQL filter actually triggers.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_message_blocks_cross_tenant_access() -> None:
    tenant_a = uuid4()
    tenant_b_message_id = 999  # belongs to a different tenant

    captured: dict[str, Any] = {}

    async def _fake_fetch(message_id: int, *, record_tenant_id: UUID) -> dict:
        captured["message_id"] = message_id
        captured["record_tenant_id"] = record_tenant_id
        # Mirror real repo: tenant filter eliminates the row → empty shape.
        if str(record_tenant_id) != str(tenant_a):  # belt-and-braces guard
            raise AssertionError(
                "repo received the wrong tenant — route did not forward JWT"
            )
        return {
            "request_logs": [],
            "request_steps": [],
            "model_invocations": [],
        }

    fake_logger = MagicMock()
    fake_logger.fetch_by_message_id = _fake_fetch  # type: ignore[assignment]

    req = _request(tenant_uuid=tenant_a, logger=fake_logger)
    resp = await admin_audit.audit_message(req, tenant_b_message_id)

    assert resp == {
        "ok": True,
        "data": {
            "request_logs": [],
            "request_steps": [],
            "model_invocations": [],
        },
    }, "cross-tenant probe must return empty data, not leak rows"

    # The route forwarded the caller's JWT tenant — not the message's owner.
    assert captured["record_tenant_id"] == tenant_a
    assert captured["message_id"] == tenant_b_message_id


# ---------------------------------------------------------------------------
# 2. Missing tenant context → 401 (not 200 with leaked data).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_message_rejects_missing_tenant_context() -> None:
    fake_logger = MagicMock()
    fake_logger.fetch_by_message_id = AsyncMock(
        side_effect=AssertionError("repo must NOT be called without tenant"),
    )
    req = _request(tenant_uuid=None, logger=fake_logger)

    with pytest.raises(HTTPException) as excinfo:
        await admin_audit.audit_message(req, 1)
    assert excinfo.value.status_code == 401
