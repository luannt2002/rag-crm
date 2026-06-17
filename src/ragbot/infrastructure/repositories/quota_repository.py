"""Quota repository — token + cost budget per tenant."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import insert, select, update

from ragbot.application.ports.repository_ports import QuotaRepositoryPort
from ragbot.infrastructure.db.models import QuotaModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository
from ragbot.shared.constants import WORKSPACE_SYSTEM_SLUG
from ragbot.shared.types import TenantId


class SqlAlchemyQuotaRepository(TenantScopedRepository, QuotaRepositoryPort):
    """Repository cho bảng quota — quản lý hạn mức token và chi phí theo tenant."""

    async def get(self, *, record_tenant_id: TenantId) -> dict[str, object]:
        """Lấy quota hiện tại, tự tạo nếu chưa tồn tại.
        @param record_tenant_id: ID tenant
        @return: dict chứa thông tin quota
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            row = await session.scalar(select(QuotaModel).where(QuotaModel.record_tenant_id == tid))
            if row is None:
                # Auto-create with default — quota is tenant-level, no
                # per-workspace breakdown.
                row = QuotaModel(
                    record_tenant_id=tid,
                    workspace_id=WORKSPACE_SYSTEM_SLUG,
                )
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return {
                "tenant_id": row.record_tenant_id,
                "monthly_limit": row.monthly_limit,
                "used_tokens": row.used_tokens,
                "used_cost_usd": float(row.used_cost_usd),
                "blocked": row.blocked,
            }

    async def increment_usage(
        self,
        *,
        record_tenant_id: TenantId,
        tokens: int,
        cost_usd: float,
    ) -> None:
        """Cộng dồn lượng sử dụng token và chi phí.
        @param tokens: số token đã dùng
        @param cost_usd: chi phí USD
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            await session.execute(
                update(QuotaModel)
                .where(QuotaModel.record_tenant_id == tid)
                .values(
                    used_tokens=QuotaModel.used_tokens + tokens,
                    used_cost_usd=QuotaModel.used_cost_usd + Decimal(str(cost_usd)),
                ),
            )
            await session.commit()

    async def check_within_budget(
        self,
        *,
        record_tenant_id: TenantId,
        estimated_tokens: int,
    ) -> bool:
        """Kiểm tra tenant còn trong ngân sách cho số token dự kiến.
        @param estimated_tokens: số token ước tính sẽ dùng
        @return: True nếu còn trong hạn mức
        """
        info = await self.get(record_tenant_id=record_tenant_id)
        used = int(info["used_tokens"])  # type: ignore[arg-type]
        limit = int(info["monthly_limit"])  # type: ignore[arg-type]
        blocked = bool(info["blocked"])
        if blocked:
            return False
        return (used + estimated_tokens) <= limit


__all__ = ["SqlAlchemyQuotaRepository"]
