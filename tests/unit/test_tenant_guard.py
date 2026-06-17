"""Unit tests for ``application.services.tenant_guard``.

Covers cross-tenant defense at the application layer:
- ``ensure_same_tenant`` no-op on empty / single scope.
- ``ensure_same_tenant`` accepts homogeneous scopes.
- ``ensure_same_tenant`` raises on first mismatch with both ids in details.
- ``assert_owns`` accepts matching tenant id, raises on mismatch.
- ``assert_all_owned`` raises on first foreign entity in iterable.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ragbot.application.services.tenant_guard import TenantGuardService
from ragbot.domain.value_objects.tenant_scope import TenantScope
from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import TenantId


def _scope(tid: TenantId) -> TenantScope:
    return TenantScope(record_tenant_id=tid)


def test_ensure_same_tenant_no_op_on_empty() -> None:
    # No scopes — nothing to enforce. Must not raise.
    TenantGuardService.ensure_same_tenant()


def test_ensure_same_tenant_accepts_single_scope() -> None:
    TenantGuardService.ensure_same_tenant(_scope(TenantId(uuid4())))


def test_ensure_same_tenant_accepts_homogeneous() -> None:
    tid = TenantId(uuid4())
    TenantGuardService.ensure_same_tenant(_scope(tid), _scope(tid), _scope(tid))


def test_ensure_same_tenant_rejects_mismatch_with_details() -> None:
    a = TenantId(uuid4())
    b = TenantId(uuid4())

    with pytest.raises(TenantIsolationViolation) as exc:
        TenantGuardService.ensure_same_tenant(_scope(a), _scope(b))

    assert exc.value.details["expected"] == str(a)
    assert exc.value.details["got"] == str(b)


def test_assert_owns_accepts_matching_tenant() -> None:
    tid = TenantId(uuid4())
    TenantGuardService.assert_owns(tid, tid)


def test_assert_owns_rejects_foreign_entity() -> None:
    owner = TenantId(uuid4())
    requester = TenantId(uuid4())

    with pytest.raises(TenantIsolationViolation) as exc:
        TenantGuardService.assert_owns(owner, requester)

    assert exc.value.details["entity_tenant"] == str(owner)
    assert exc.value.details["request_tenant"] == str(requester)
    assert exc.value.http_status == 403


def test_assert_all_owned_accepts_homogeneous_iterable() -> None:
    tid = TenantId(uuid4())
    # Iterable consumes once — make sure the guard works on a generator too.
    TenantGuardService.assert_all_owned(iter([tid, tid, tid]), tid)


def test_assert_all_owned_rejects_first_foreign_entity() -> None:
    requester = TenantId(uuid4())
    foreign = TenantId(uuid4())

    with pytest.raises(TenantIsolationViolation):
        TenantGuardService.assert_all_owned([requester, foreign, requester], requester)
