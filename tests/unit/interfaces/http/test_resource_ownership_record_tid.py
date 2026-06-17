"""Regression test for mega-sprint-G9 — resource_ownership reads record_tenant_id.

Bug: ``require_binding_ownership`` previously called
``getattr(request.state, "tenant_id", None)`` but TenantContextMiddleware
lifts the JWT claim onto ``request.state.record_tenant_id`` (per CLAUDE.md
identity rule). The wrong attr always returned ``None`` → every caller
fell into the 404 branch, masking real ownership-boundary breaches.

Fix: read ``record_tenant_id`` to honour the documented identity contract.

Pre-fix: assertions fail (wrong attr captured).
Post-fix: assertions pass — record_tenant_id is the read attr.
"""
from __future__ import annotations

import inspect
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from ragbot.interfaces.http import _resource_ownership


def test_source_reads_record_tenant_id_not_tenant_id() -> None:
    """Static guard: source must reference record_tenant_id getattr key."""
    src = inspect.getsource(_resource_ownership.require_binding_ownership)
    assert 'getattr(request.state, "record_tenant_id"' in src, (
        "require_binding_ownership must read record_tenant_id from "
        "request.state per CLAUDE.md 4-key identity rule."
    )
    # Make sure the buggy literal is gone — defence against accidental
    # revert by future grep-based refactors.
    assert 'getattr(request.state, "tenant_id"' not in src, (
        "Legacy attr name 'tenant_id' must not be read — "
        "TenantContextMiddleware lifts the JWT claim onto record_tenant_id."
    )


@pytest.mark.asyncio
async def test_passes_record_tenant_id_to_repo_get_binding() -> None:
    """Behavioural guard: caller_tid plumbed via record_tenant_id attr.

    Build a fake request whose state carries ONLY ``record_tenant_id``
    (no legacy ``tenant_id``). The function must extract the UUID and
    pass it as ``record_tenant_id=`` to the repo. Pre-fix the lookup
    would return None → 404 raised before repo invocation.
    """
    caller_uuid = uuid4()
    binding_uuid = uuid4()

    request = MagicMock()
    request.state = MagicMock(spec=["record_tenant_id", "role"])
    request.state.record_tenant_id = caller_uuid
    # role default = guest so check_min_level(super_admin) returns False
    request.state.role = "tenant"

    repo = MagicMock()
    repo.get_binding = AsyncMock(return_value=MagicMock())  # found
    request.app.state.container.ai_config_repo = MagicMock(return_value=repo)

    await _resource_ownership.require_binding_ownership(
        request, binding_id=binding_uuid,
    )

    repo.get_binding.assert_awaited_once()
    _, kwargs = repo.get_binding.call_args
    assert kwargs["record_tenant_id"] == caller_uuid


@pytest.mark.asyncio
async def test_missing_record_tenant_id_raises_404() -> None:
    """When the JWT lift never ran, request.state has no record_tenant_id
    → guard collapses to 404 (avoids enumeration oracle).
    """
    request = MagicMock()
    request.state = MagicMock(spec=["role"])
    request.state.role = "tenant"
    # access to record_tenant_id raises AttributeError → getattr default None

    with pytest.raises(HTTPException) as exc:
        await _resource_ownership.require_binding_ownership(
            request, binding_id=uuid4(),
        )
    assert exc.value.status_code == 404
