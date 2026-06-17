"""system_config PUT audit emit.

Pre-fix bug: ``PUT /test/admin/config/{key}`` mutated ``system_config``
without writing an ``audit_log`` row. ``system_config`` drives every
threshold + flag in the platform; mutations MUST be traceable per
change-management policy.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.ports.ai_config_port import AuditEntry
from ragbot.interfaces.http.routes import test_chat


def _request(*, container: MagicMock, role: str = "super_admin",
             tenant_uuid: Any = None) -> Any:
    app = MagicMock()
    app.state = SimpleNamespace(container=container)
    return SimpleNamespace(
        app=app,
        state=SimpleNamespace(
            role=role,
            tenant_id=tenant_uuid,
            user_id="config-admin",
            trace_id="cfg-trace",
        ),
    )


class TestAdminConfigAudit:
    @pytest.mark.asyncio
    async def test_put_admin_config_writes_audit_row(self,
                                                     monkeypatch: Any) -> None:
        # Stub SystemConfigService inside the route — get returns the
        # pre-update value; set succeeds without DB.
        fake_svc = MagicMock()
        fake_svc.get = AsyncMock(return_value="old-val")
        fake_svc.set = AsyncMock(return_value=None)
        monkeypatch.setattr(test_chat, "_sys_config", lambda req: fake_svc)

        audit_repo = MagicMock()
        audit_repo.write_audit = AsyncMock(return_value=None)
        container = MagicMock()
        container.ai_config_repo = MagicMock(return_value=audit_repo)

        tenant_uuid = uuid4()
        req = _request(container=container, tenant_uuid=tenant_uuid)
        body = test_chat.UpdateConfigRequest(value="new-val")

        resp = await test_chat.admin_update_config(
            "chat_max_history", body, req,
        )
        assert resp == {"ok": True, "key": "chat_max_history", "value": "new-val"}

        # Sequencing: get(old) -> set(new) -> audit row.
        fake_svc.get.assert_awaited_once_with("chat_max_history")
        fake_svc.set.assert_awaited_once_with("chat_max_history", "new-val")
        audit_repo.write_audit.assert_awaited_once()
        entry = audit_repo.write_audit.await_args.args[0]
        assert isinstance(entry, AuditEntry)
        assert entry.action == "system_config_update"
        assert entry.resource_type == "system_config"
        assert entry.resource_id == "chat_max_history"
        assert entry.before == {"value": "old-val"}
        assert entry.after == {"value": "new-val"}
        assert str(entry.record_tenant_id) == str(tenant_uuid)
        assert entry.actor_user_id == "config-admin"
        assert entry.trace_id == "cfg-trace"
