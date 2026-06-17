"""token CRUD audit emit.

Pre-fix bug: ``POST /test/tokens`` (mint), ``POST /test/tokens/{name}/regenerate``
(rotate), and ``DELETE /test/tokens/{name}`` (revoke) mutated ``api_tokens``
without writing an ``audit_log`` row. Token mint = security event;
auditors require full traceability.

The audit row MUST NEVER include the plaintext JWT — only the row id +
role + rate-limit shape.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.interfaces.http.routes import test_chat


def _request(*, container: MagicMock, role: str = "super_admin") -> Any:
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            tenant_id=uuid4(),
            user_id="token-admin",
            trace_id="tok-trace",
        ),
    )


def _container_with_audit(redis: MagicMock | None = None) -> tuple[
    MagicMock, MagicMock,
]:
    audit_repo = MagicMock()
    audit_repo.write_audit = AsyncMock(return_value=None)
    container = MagicMock()
    container.ai_config_repo = MagicMock(return_value=audit_repo)
    container.redis_client = MagicMock(return_value=redis or MagicMock())
    container.session_factory = MagicMock(return_value=MagicMock())
    return container, audit_repo


class TestTokenCreateAudit:
    @pytest.mark.asyncio
    async def test_create_token_writes_audit_row(self, monkeypatch: Any) -> None:
        token_id = str(uuid4())
        fake_svc = MagicMock()
        fake_svc.create_token = AsyncMock(return_value={
            "id": token_id,
            "service_name": "nestjs",
            "token": "PLAINTEXT-MUST-NEVER-LEAK",
            "version": 1,
            "role": "service",
            "rate_limit_value": 120,
            "rate_limit_window": 60,
        })

        async def _fake_token_service(_req: Any) -> Any:
            return fake_svc

        monkeypatch.setattr(test_chat, "_token_service", _fake_token_service)

        container, audit_repo = _container_with_audit()
        req = _request(container=container)
        body = test_chat.CreateTokenRequest(
            service_name="nestjs", description="upstream BE", role="service",
        )
        resp = await test_chat.create_token(body, req)
        assert resp["ok"] is True
        assert resp["id"] == token_id

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "token_create"
        assert entry.resource_type == "api_token"
        assert str(entry.resource_id) == token_id
        # Plaintext token MUST NOT appear anywhere on the audit row.
        for blob in (entry.before, entry.after):
            if blob is None:
                continue
            assert "PLAINTEXT-MUST-NEVER-LEAK" not in repr(blob)
        assert entry.after is not None
        assert entry.after["service_name"] == "nestjs"
        assert entry.after["role"] == "service"
        assert entry.after["rate_limit_value"] == 120


class TestTokenRotateAudit:
    @pytest.mark.asyncio
    async def test_rotate_token_writes_audit_row(self, monkeypatch: Any) -> None:
        fake_svc = MagicMock()
        fake_svc.regenerate_token = AsyncMock(return_value={
            "service_name": "nestjs",
            "token": "PLAINTEXT-MUST-NEVER-LEAK",
            "old_version": 3,
            "new_version": 4,
            "role": "service",
        })

        async def _fake_token_service(_req: Any) -> Any:
            return fake_svc

        monkeypatch.setattr(test_chat, "_token_service", _fake_token_service)

        container, audit_repo = _container_with_audit()
        req = _request(container=container)
        resp = await test_chat.regenerate_token("nestjs", req)
        assert resp["ok"] is True
        assert resp["new_version"] == 4

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.action == "token_rotate"
        assert entry.resource_type == "api_token"
        assert entry.resource_id == "nestjs"
        # Diff captures only the version pivot, never the plaintext.
        assert entry.before == {"version": 3}
        assert entry.after is not None
        assert entry.after["version"] == 4
        for blob in (entry.before, entry.after):
            assert "PLAINTEXT-MUST-NEVER-LEAK" not in repr(blob)


class TestTokenRevokeAudit:
    @pytest.mark.asyncio
    async def test_revoke_token_writes_audit_row(self, monkeypatch: Any) -> None:
        fake_svc = MagicMock()
        fake_svc.revoke_token = AsyncMock(return_value=True)

        async def _fake_token_service(_req: Any) -> Any:
            return fake_svc

        monkeypatch.setattr(test_chat, "_token_service", _fake_token_service)

        container, audit_repo = _container_with_audit()
        req = _request(container=container)
        resp = await test_chat.revoke_token("nestjs", req)
        assert resp == {"ok": True, "revoked": True}

        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert entry.action == "token_revoke"
        assert entry.resource_type == "api_token"
        assert entry.resource_id == "nestjs"
        assert entry.before == {"revoked": False}
        assert entry.after == {"revoked": True}
