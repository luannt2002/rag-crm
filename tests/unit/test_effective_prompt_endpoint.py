"""Effective-prompt transparency endpoint (ADR-W1-S10 condition 1).

The owner must be able to SEE the final assembled system prompt — owner
content plus whatever platform-default rules the assembler appends — or
the governed-exception ruling for the platform append does not hold.

Contract surface:

1. Route registered at ``/api/ragbot/admin/bots/{bot_uuid}/effective-prompt``.
2. Handler returns base / appended / effective with an exact-prefix
   guarantee (``effective == base + appended``).
3. Tenant scoping flows through ``get_bot`` (BotNotFound → 404 handled by
   the route, cross-tenant callers never see another tenant's prompt).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4


def test_route_registered_on_app_router() -> None:
    from ragbot.interfaces.http.router import router as composed_router

    paths = {getattr(r, "path", None) for r in composed_router.routes}
    assert "/api/ragbot/admin/bots/{bot_uuid}/effective-prompt" in paths


def test_handler_exists() -> None:
    from ragbot.interfaces.http.routes import admin_bots

    assert hasattr(admin_bots, "admin_bot_effective_prompt")


def test_get_bot_service_method_exists_and_scopes_tenant() -> None:
    """``BotManagementService.get_bot`` must exist and pass the admin
    tenant down to the repo (the same scoping every other method uses)."""
    from ragbot.application.services.bot_management_service import (
        BotManagementService,
    )

    repo = AsyncMock()
    cfg = SimpleNamespace(id=uuid4(), system_prompt="p")
    repo.get_by_id.return_value = cfg
    svc = BotManagementService.__new__(BotManagementService)
    svc._repo = repo  # noqa: SLF001 — direct wiring for the unit
    tenant = uuid4()

    out = asyncio.run(svc.get_bot(cfg.id, admin_record_tenant=tenant))

    assert out is cfg
    repo.get_by_id.assert_awaited_once_with(cfg.id, record_tenant_id=tenant)


def test_effective_prompt_payload_shape() -> None:
    """base + appended == effective; disabled rule ids surfaced."""
    from ragbot.interfaces.http.routes.admin_bots import (
        _effective_prompt_payload,
    )

    bot = SimpleNamespace(
        system_prompt="OWNER",
        language="vi",
        plan_limits={"sysprompt_rules_disabled": ["rule_17"]},
    )
    payload = _effective_prompt_payload(bot, effective="OWNER\n\n15. ⭐ R\nbody")

    assert payload["base_prompt"] == "OWNER"
    assert payload["platform_appended"] == "\n\n15. ⭐ R\nbody"
    assert payload["effective_prompt"] == "OWNER\n\n15. ⭐ R\nbody"
    assert payload["disabled_rule_ids"] == ["rule_17"]
    assert (
        payload["base_prompt"] + payload["platform_appended"]
        == payload["effective_prompt"]
    )


def test_effective_prompt_payload_degraded_assembly() -> None:
    """Assembler degrade (effective == base) ⇒ appended is empty string."""
    from ragbot.interfaces.http.routes.admin_bots import (
        _effective_prompt_payload,
    )

    bot = SimpleNamespace(system_prompt="OWNER", language="vi", plan_limits={})
    payload = _effective_prompt_payload(bot, effective="OWNER")

    assert payload["platform_appended"] == ""
    assert payload["disabled_rule_ids"] == []
