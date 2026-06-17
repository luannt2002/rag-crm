"""TokenBudgetPolicy."""

from __future__ import annotations

from ragbot.application.ports.repository_ports import QuotaRepositoryPort
from ragbot.shared.errors import QuotaExceeded
from ragbot.shared.types import TenantId


class TokenBudgetPolicy:
    def __init__(self, quota_repo: QuotaRepositoryPort, *, soft_warn_ratio: float = 0.8) -> None:
        self._quota = quota_repo
        self._soft = soft_warn_ratio

    async def ensure_affordable(
        self,
        *,
        record_tenant_id: TenantId,
        estimated_tokens: int,
    ) -> None:
        ok = await self._quota.check_within_budget(
            record_tenant_id=record_tenant_id,
            estimated_tokens=estimated_tokens,
        )
        if not ok:
            raise QuotaExceeded(
                "monthly token budget exhausted",
                details={"tenant_id": str(record_tenant_id)},
            )

    async def record_usage(
        self,
        *,
        record_tenant_id: TenantId,
        tokens: int,
        cost_usd: float,
    ) -> None:
        await self._quota.increment_usage(
            record_tenant_id=record_tenant_id,
            tokens=tokens,
            cost_usd=cost_usd,
        )


__all__ = ["TokenBudgetPolicy"]
