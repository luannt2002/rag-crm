"""Base helpers for tenant-scoped repositories."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import TenantId


class TenantScopedRepository:
    """Base class — enforce tenant_id presence at runtime."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Khởi tạo repository với session factory.
        @param session_factory: async session maker của SQLAlchemy
        """
        self._session_factory = session_factory

    @staticmethod
    def _ensure_tenant(record_tenant_id: TenantId | None) -> TenantId:
        """Đảm bảo tenant_id không None, raise nếu thiếu.
        @param tenant_id: ID tenant cần kiểm tra
        @return: tenant_id đã xác nhận
        """
        if record_tenant_id is None:
            raise TenantIsolationViolation("tenant_id missing in repository call")
        return record_tenant_id

    def _new_session(self) -> AsyncSession:
        """Tạo async session mới từ factory.
        @return: AsyncSession cho truy vấn DB
        """
        return self._session_factory()


__all__ = ["TenantScopedRepository"]
