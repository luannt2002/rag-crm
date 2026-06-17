"""TenantGuardService — defense at application layer."""

from __future__ import annotations

from collections.abc import Iterable

from ragbot.domain.value_objects.tenant_scope import TenantScope
from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import TenantId


class TenantGuardService:
    @staticmethod
    def ensure_same_tenant(*scopes: TenantScope) -> None:
        if not scopes:
            return
        first = scopes[0].record_tenant_id
        for s in scopes[1:]:
            if s.record_tenant_id != first:
                raise TenantIsolationViolation(
                    "Cross-tenant scope mismatch",
                    details={"expected": str(first), "got": str(s.record_tenant_id)},
                )

    @staticmethod
    def assert_owns(entity_tenant_id: TenantId, request_tenant_id: TenantId) -> None:
        if entity_tenant_id != request_tenant_id:
            raise TenantIsolationViolation(
                "Tenant does not own the requested entity",
                details={
                    "entity_tenant": str(entity_tenant_id),
                    "request_tenant": str(request_tenant_id),
                },
            )

    @staticmethod
    def assert_all_owned(
        entities_tenant_ids: Iterable[TenantId],
        request_tenant_id: TenantId,
    ) -> None:
        for tid in entities_tenant_ids:
            TenantGuardService.assert_owns(tid, request_tenant_id)


__all__ = ["TenantGuardService"]
