"""Unit tests for ``application.services.token_budget``.

Covers cost-control boundary at the application layer:
- ``ensure_affordable`` consults the quota repo with the right kwargs.
- ``ensure_affordable`` raises ``QuotaExceeded`` (HTTP 429) when over budget.
- ``ensure_affordable`` is a no-op when quota repo says ok.
- ``record_usage`` forwards (tokens, cost_usd) tuple to the repo as kwargs.
- Soft-warn ratio is captured by the constructor and not used for the hard
  enforcement decision (delegated to the repo). This guards against silent
  policy drift (e.g. someone adding a soft-warn-as-hard-block regression).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ragbot.application.ports.repository_ports import QuotaRepositoryPort
from ragbot.application.services.token_budget import TokenBudgetPolicy
from ragbot.shared.errors import QuotaExceeded
from ragbot.shared.types import TenantId


class _StubQuotaRepo:
    """Test double honoring ``QuotaRepositoryPort``.

    Records every call so each test can assert exact kwargs forwarded.
    """

    def __init__(self, *, allow: bool = True) -> None:
        self._allow = allow
        self.checks: list[dict] = []
        self.increments: list[dict] = []

    async def get(self, *, record_tenant_id: TenantId) -> dict:  # pragma: no cover
        return {}

    async def increment_usage(
        self,
        *,
        record_tenant_id: TenantId,
        tokens: int,
        cost_usd: float,
    ) -> None:
        self.increments.append(
            {"record_tenant_id": record_tenant_id, "tokens": tokens, "cost_usd": cost_usd},
        )

    async def check_within_budget(
        self,
        *,
        record_tenant_id: TenantId,
        estimated_tokens: int,
    ) -> bool:
        self.checks.append(
            {"record_tenant_id": record_tenant_id, "estimated_tokens": estimated_tokens},
        )
        return self._allow


async def test_ensure_affordable_passes_through_when_within_budget() -> None:
    repo = _StubQuotaRepo(allow=True)
    policy = TokenBudgetPolicy(repo)
    tid = TenantId(uuid4())

    await policy.ensure_affordable(record_tenant_id=tid, estimated_tokens=2048)

    assert repo.checks == [{"record_tenant_id": tid, "estimated_tokens": 2048}]


async def test_ensure_affordable_raises_quota_exceeded_when_over() -> None:
    repo = _StubQuotaRepo(allow=False)
    policy = TokenBudgetPolicy(repo)
    tid = TenantId(uuid4())

    with pytest.raises(QuotaExceeded) as exc:
        await policy.ensure_affordable(record_tenant_id=tid, estimated_tokens=999)

    assert exc.value.details["tenant_id"] == str(tid)
    assert exc.value.http_status == 429


async def test_record_usage_forwards_tokens_and_cost() -> None:
    repo = _StubQuotaRepo()
    policy = TokenBudgetPolicy(repo)
    tid = TenantId(uuid4())

    await policy.record_usage(record_tenant_id=tid, tokens=512, cost_usd=0.0034)

    assert repo.increments == [
        {"record_tenant_id": tid, "tokens": 512, "cost_usd": 0.0034},
    ]


def test_protocol_compatibility_with_stub() -> None:
    # Sanity: the stub satisfies the runtime-checkable Protocol — guards against
    # signature drift in QuotaRepositoryPort that would silently break callers.
    assert isinstance(_StubQuotaRepo(), QuotaRepositoryPort)


async def test_soft_warn_ratio_captured_but_not_enforced_locally() -> None:
    # Policy stores ``soft_warn_ratio`` but does NOT block on it — only the
    # repo's check_within_budget decides hard rejection. This protects
    # against a silent regression where soft warning would start blocking.
    repo = _StubQuotaRepo(allow=True)
    policy = TokenBudgetPolicy(repo, soft_warn_ratio=0.5)
    tid = TenantId(uuid4())

    # Even a "huge" estimate is allowed if repo says ok.
    await policy.ensure_affordable(record_tenant_id=tid, estimated_tokens=10**9)
    assert repo.checks[-1]["estimated_tokens"] == 10**9
