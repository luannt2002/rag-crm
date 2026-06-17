"""TenantModelPolicy + Capability + AuditLog repository (v0.2.0)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ragbot.infrastructure.db.models import AuditLogModel
from ragbot.infrastructure.db.models_monitoring import (
    ModelCapabilityModel,
    TenantModelPolicyModel,
)
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.shared.constants import WORKSPACE_SYSTEM_SLUG
from ragbot.shared.errors import (
    InvariantViolation,
    PolicyViolation,
    TenantIsolationViolation,
)
from ragbot.shared.types import BotId, TenantId


class TenantPolicyRepository:
    """Repository cho tenant model policies và model capabilities."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """Khởi tạo repository với session factory."""
        self._sf = session_factory

    @staticmethod
    def _ensure(record_tenant_id: TenantId | None) -> TenantId:
        if record_tenant_id is None:
            raise TenantIsolationViolation("record_tenant_id required")
        return record_tenant_id

    # --- Capability -------------------------------------------------------
    async def upsert_capability(
        self,
        *,
        record_model_id: UUID,
        tier: str = "standard",
        can_web_search: bool = False,
        can_read_private_docs: bool = True,
        can_reasoning: bool = False,
        can_tool_use: bool = False,
        can_vision: bool = False,
        quality_score: float = 5.0,
        hallucination_rate: float = 0.0,
        suitable_for: list[str] | None = None,
        not_suitable_for: list[str] | None = None,
        updated_by: str | None = None,
    ) -> None:
        """Tạo hoặc cập nhật capability cho model (web search, reasoning, ...).
        @param record_model_id: UUID model
        @param tier: tầng chất lượng (standard, premium, ...)
        """
        async with self._sf() as session:
            existing = await session.get(ModelCapabilityModel, record_model_id)
            if existing is None:
                session.add(
                    ModelCapabilityModel(
                        record_model_id=record_model_id,
                        tier=tier,
                        can_web_search=can_web_search,
                        can_read_private_docs=can_read_private_docs,
                        can_reasoning=can_reasoning,
                        can_tool_use=can_tool_use,
                        can_vision=can_vision,
                        quality_score=Decimal(str(quality_score)),
                        hallucination_rate=Decimal(str(hallucination_rate)),
                        suitable_for=list(suitable_for or []),
                        not_suitable_for=list(not_suitable_for or []),
                        updated_by=updated_by,
                    ),
                )
            else:
                existing.tier = tier
                existing.can_web_search = can_web_search
                existing.can_read_private_docs = can_read_private_docs
                existing.can_reasoning = can_reasoning
                existing.can_tool_use = can_tool_use
                existing.can_vision = can_vision
                existing.quality_score = Decimal(str(quality_score))
                existing.hallucination_rate = Decimal(str(hallucination_rate))
                existing.suitable_for = list(suitable_for or [])
                existing.not_suitable_for = list(not_suitable_for or [])
                existing.updated_by = updated_by
            await session.commit()

    async def get_capability(self, record_model_id: UUID) -> dict[str, Any] | None:
        """Lấy capability của model theo UUID.
        @return: dict capability hoặc None
        """
        async with self._sf() as session:
            row = await session.get(ModelCapabilityModel, record_model_id)
            if row is None:
                return None
            return {
                "model_id": str(row.record_model_id),
                "tier": row.tier,
                "can_web_search": row.can_web_search,
                "can_read_private_docs": row.can_read_private_docs,
                "can_reasoning": row.can_reasoning,
                "can_tool_use": row.can_tool_use,
                "can_vision": row.can_vision,
                "quality_score": float(row.quality_score),
                "hallucination_rate": float(row.hallucination_rate),
                "suitable_for": list(row.suitable_for),
                "not_suitable_for": list(row.not_suitable_for),
            }

    # --- Policy -----------------------------------------------------------
    async def upsert_policy(
        self,
        *,
        record_tenant_id: TenantId,
        record_model_id: UUID,
        record_bot_id: BotId | None = None,
        private_doc_ratio: int = 100,
        web_search_ratio: int = 0,
        general_knowledge_ratio: int = 0,
        record_fallback_model_id: UUID | None = None,
        default_for_task: dict[str, Any] | None = None,
        enabled: bool = True,
        actor_user_id: str = "system",
        trace_id: str = "",
    ) -> UUID:
        """Tạo hoặc cập nhật policy cho tenant/bot/model (tổng ratio phải = 100).
        @param record_model_id: UUID model
        @param private_doc_ratio: tỷ lệ private docs (%)
        @return: UUID policy
        """
        tid = self._ensure(record_tenant_id)
        if private_doc_ratio + web_search_ratio + general_knowledge_ratio != 100:
            raise InvariantViolation(
                "private_doc + web_search + general_knowledge ratios must sum to 100",
                details={
                    "private": private_doc_ratio,
                    "web": web_search_ratio,
                    "general": general_knowledge_ratio,
                },
            )

        # Validate against capability — model phải support web_search nếu ratio > 0
        cap = await self.get_capability(record_model_id)
        if cap is not None:
            if web_search_ratio > 0 and not cap["can_web_search"]:
                raise PolicyViolation(
                    "model does not support web search but web_search_ratio > 0",
                    details={"model_id": str(record_model_id), "capability": cap},
                )
            if private_doc_ratio > 0 and not cap["can_read_private_docs"]:
                raise PolicyViolation(
                    "model does not support private docs but private_doc_ratio > 0",
                    details={"model_id": str(record_model_id), "capability": cap},
                )

        async with self._sf() as session:
            existing = await session.scalar(
                select(TenantModelPolicyModel).where(
                    TenantModelPolicyModel.record_tenant_id == tid,
                    TenantModelPolicyModel.record_bot_id == record_bot_id,
                    TenantModelPolicyModel.record_model_id == record_model_id,
                ),
            )
            before: dict[str, Any] | None = None
            if existing is None:
                policy_id = uuid4()
                row = TenantModelPolicyModel(
                    id=policy_id,
                    record_tenant_id=tid,
                    workspace_id=WORKSPACE_SYSTEM_SLUG,
                    record_bot_id=record_bot_id,
                    record_model_id=record_model_id,
                    private_doc_ratio=private_doc_ratio,
                    web_search_ratio=web_search_ratio,
                    general_knowledge_ratio=general_knowledge_ratio,
                    record_fallback_model_id=record_fallback_model_id,
                    default_for_task=dict(default_for_task or {}),
                    enabled=enabled,
                    created_by=actor_user_id,
                    updated_by=actor_user_id,
                )
                session.add(row)
                await session.flush()
            else:
                policy_id = existing.id
                before = self._policy_dict(existing)
                existing.private_doc_ratio = private_doc_ratio
                existing.web_search_ratio = web_search_ratio
                existing.general_knowledge_ratio = general_knowledge_ratio
                existing.record_fallback_model_id = record_fallback_model_id
                existing.default_for_task = dict(default_for_task or {})
                existing.enabled = enabled
                existing.updated_by = actor_user_id

            # Migration 0010: policy audits written to unified `audit_log`.
            # alembic 010g: row_hash chain populated by insert_audit_row.
            await insert_audit_row(
                session,
                record_tenant_id=tid,
                workspace_id=WORKSPACE_SYSTEM_SLUG,
                actor_user_id=actor_user_id,
                action="update" if before else "create",
                resource_type="policy",
                resource_id=str(policy_id),
                before_json=before,
                after_json={
                    "private_doc_ratio": private_doc_ratio,
                    "web_search_ratio": web_search_ratio,
                    "general_knowledge_ratio": general_knowledge_ratio,
                    "fallback_model_id": str(record_fallback_model_id) if record_fallback_model_id else None,
                    "enabled": enabled,
                },
                trace_id=trace_id,
            )
            await session.commit()
            return policy_id

    async def get_policy(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None = None,
        record_model_id: UUID | None = None,
    ) -> dict[str, Any] | None:
        """Lấy policy đang bật cho tenant, lọc theo bot/model.
        @return: dict policy hoặc None
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = select(TenantModelPolicyModel).where(
                TenantModelPolicyModel.record_tenant_id == tid,
                TenantModelPolicyModel.enabled.is_(True),
            )
            if record_bot_id is not None:
                stmt = stmt.where(TenantModelPolicyModel.record_bot_id == record_bot_id)
            if record_model_id is not None:
                stmt = stmt.where(TenantModelPolicyModel.record_model_id == record_model_id)
            row = await session.scalar(stmt)
            return self._policy_dict(row) if row else None

    async def list_policies(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None = None,
    ) -> list[dict[str, Any]]:
        """Lấy tất cả policies của tenant.
        @return: danh sách dict policy
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = select(TenantModelPolicyModel).where(
                TenantModelPolicyModel.record_tenant_id == tid,
            )
            if record_bot_id is not None:
                stmt = stmt.where(TenantModelPolicyModel.record_bot_id == record_bot_id)
            rows = (await session.execute(stmt)).scalars().all()
            return [self._policy_dict(r) for r in rows]

    async def disable_policy(
        self,
        policy_id: UUID,
        *,
        record_tenant_id: TenantId,
        actor_user_id: str,
    ) -> None:
        """Vô hiệu hoá policy (set enabled=False).
        @param policy_id: UUID policy
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            await session.execute(
                update(TenantModelPolicyModel)
                .where(
                    TenantModelPolicyModel.id == policy_id,
                    TenantModelPolicyModel.record_tenant_id == tid,
                )
                .values(enabled=False, updated_by=actor_user_id),
            )
            await session.commit()

    @staticmethod
    def _policy_dict(row: TenantModelPolicyModel) -> dict[str, Any]:
        """Chuyển đổi ORM policy row sang dict."""
        return {
            "id": str(row.id),
            "tenant_id": str(row.record_tenant_id),
            "bot_id": str(row.record_bot_id) if row.record_bot_id else None,
            "model_id": str(row.record_model_id),
            "private_doc_ratio": row.private_doc_ratio,
            "web_search_ratio": row.web_search_ratio,
            "general_knowledge_ratio": row.general_knowledge_ratio,
            "fallback_model_id": (
                str(row.record_fallback_model_id) if row.record_fallback_model_id else None
            ),
            "default_for_task": dict(row.default_for_task or {}),
            "enabled": row.enabled,
        }


__all__ = ["TenantPolicyRepository"]
