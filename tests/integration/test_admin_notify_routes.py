"""Integration tests for ``admin_notify`` routes.

The routes are exercised against handler-shaped ``Request`` doubles so
the test surface is narrow + deterministic — same approach as
``test_admin_tenants_api.py``. Coverage:

1. GET masks the webhook_key (only the first 8 chars surface).
2. PATCH 422 when ``path_template`` is missing the placeholder.
3. PATCH 200 happy path → ``system_config.set`` + cache invalidate.
4. DELETE 200 stores ``None`` in DB and busts cache.
5. POST /test fires dispatcher with severity=info; returns upstream status.
6. RBAC: non-admin role → ``ForbiddenError``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from ragbot.application.dto.notify_channel import NotifyChannelConfig
from ragbot.interfaces.http.routes import admin_notify
from ragbot.shared.errors import ForbiddenError


_VALID_DICT = {
    "method": "POST",
    "domain": "https://example.com",
    "path_template": "/hooks/{conversation_id}/in",
    "conversation_id": "conv-1",
    "webhook_key": "whk_secret_value_12345",
    "enabled": True,
}


def _build_request(
    *,
    role: str,
    resolver: Any,
    scs: Any,
    dispatcher: Any,
) -> SimpleNamespace:
    container = MagicMock()
    container.notify_resolver = MagicMock(return_value=resolver)
    container.system_config_service = MagicMock(return_value=scs)
    container.webhook_dispatcher = MagicMock(return_value=dispatcher)

    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(role=role, user_id="ops@example.com"),
    )


@pytest.mark.asyncio
async def test_get_returns_masked_config_and_source():
    cfg = NotifyChannelConfig.model_validate(_VALID_DICT)
    resolver = SimpleNamespace(
        resolve=AsyncMock(return_value=(cfg, "env")),
        invalidate=AsyncMock(),
    )
    req = _build_request(
        role="admin", resolver=resolver, scs=MagicMock(), dispatcher=MagicMock(),
    )

    out = await admin_notify.admin_get_notify_channel(req)

    assert out["ok"] is True
    assert out["source"] == "env"
    # Secret webhook_key must NOT surface in full.
    assert "whk_secret_value_12345" not in str(out)
    # Prefix is fine — it helps ops differentiate keys.
    assert out["config"]["webhook_key_prefix"].startswith("whk_secr")


@pytest.mark.asyncio
async def test_patch_invalid_payload_raises_validation_error():
    """Pydantic enforces the placeholder constraint at parse-time."""
    bad = dict(_VALID_DICT)
    bad["path_template"] = "/hooks/missing-placeholder"

    with pytest.raises(ValidationError):
        NotifyChannelConfig.model_validate(bad)


@pytest.mark.asyncio
async def test_patch_writes_db_and_invalidates_cache():
    cfg = NotifyChannelConfig.model_validate(_VALID_DICT)
    resolver = SimpleNamespace(
        resolve=AsyncMock(),
        invalidate=AsyncMock(),
    )
    scs = SimpleNamespace(set=AsyncMock())
    req = _build_request(
        role="admin", resolver=resolver, scs=scs, dispatcher=MagicMock(),
    )

    out = await admin_notify.admin_patch_notify_channel(cfg, req)

    assert out["ok"] is True
    assert out["source"] == "db"
    scs.set.assert_awaited_once()
    args, kwargs = scs.set.await_args
    assert args[0] == "notify_channel.config"
    assert isinstance(args[1], dict)
    assert args[1]["webhook_key"] == "whk_secret_value_12345"
    resolver.invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_clears_db_and_invalidates_cache():
    resolver = SimpleNamespace(
        resolve=AsyncMock(), invalidate=AsyncMock(),
    )
    scs = SimpleNamespace(set=AsyncMock())
    req = _build_request(
        role="admin", resolver=resolver, scs=scs, dispatcher=MagicMock(),
    )

    out = await admin_notify.admin_delete_notify_channel(req)

    assert out["ok"] is True
    scs.set.assert_awaited_once()
    args, _ = scs.set.await_args
    assert args[0] == "notify_channel.config"
    assert args[1] is None  # JSONB null
    resolver.invalidate.assert_awaited_once()


@pytest.mark.asyncio
async def test_test_endpoint_calls_dispatcher_with_info_severity():
    dispatcher = SimpleNamespace(
        dispatch=AsyncMock(
            return_value={"dispatched": True, "reason": None, "upstream_status": 200},
        ),
    )
    resolver = SimpleNamespace(resolve=AsyncMock(), invalidate=AsyncMock())
    scs = SimpleNamespace(set=AsyncMock())
    req = _build_request(
        role="admin", resolver=resolver, scs=scs, dispatcher=dispatcher,
    )

    out = await admin_notify.admin_test_notify_channel(req, payload={"message": "smoke"})

    assert out["ok"] is True
    assert out["dispatched"] is True
    assert out["upstream_status"] == 200
    dispatcher.dispatch.assert_awaited_once()
    call_kwargs = dispatcher.dispatch.await_args.kwargs
    assert call_kwargs["severity"] == "info"
    assert call_kwargs["component"] == "admin_test"
    assert call_kwargs["message"] == "smoke"


@pytest.mark.asyncio
async def test_rbac_blocks_non_admin_caller():
    resolver = SimpleNamespace(resolve=AsyncMock(), invalidate=AsyncMock())
    req = _build_request(
        role="user", resolver=resolver, scs=MagicMock(), dispatcher=MagicMock(),
    )

    with pytest.raises(ForbiddenError):
        await admin_notify.admin_get_notify_channel(req)
    # Resolver must not be reached when RBAC blocks.
    resolver.resolve.assert_not_awaited()
