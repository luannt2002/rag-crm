"""Bot management service — CRUD + cache invalidation + audit + outbox.

4-key identity (record_tenant_id UUID, workspace_id slug, bot_id,
channel_type) enforced end-to-end. Routes lift ``record_tenant_id`` from
JWT bearer onto ``request.state`` and pass it to this service via the
``admin_record_tenant`` kwarg (``None`` = platform-admin RBAC level-100
bypass). ``workspace_id`` is supplied by the caller (route resolves a
missing slug to ``str(record_tenant_id)`` upstream).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.dto.bot_config import BotConfig, BotSettingOptions
from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.shared.constants import (
    MAX_SYSTEM_PROMPT_CHARS,
    MAX_BOT_ID_LENGTH,
    MAX_BOT_NAME_LENGTH,
    MAX_CHANNEL_TYPE_LENGTH,
    WORKSPACE_ID_MAX_LEN,
    WORKSPACE_ID_MIN_LEN,
)
from ragbot.infrastructure.db.uow import UnitOfWorkFactory
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.infrastructure.repositories.bot_repository import SqlAlchemyBotRepository
from ragbot.shared.types import WorkspaceId
from ragbot.shared.workspace_id_validator import resolve_workspace_id

logger = structlog.get_logger(__name__)


# ── Commands ────────────────────────────────────────────────────────────────

class CreateBotCommand(BaseModel):
    bot_id: str = Field(min_length=1, max_length=MAX_BOT_ID_LENGTH)
    channel_type: str = Field(min_length=1, max_length=MAX_CHANNEL_TYPE_LENGTH)
    bot_name: str = Field(min_length=1, max_length=MAX_BOT_NAME_LENGTH)
    # 4-key identity: (record_tenant_id, workspace_id, bot_id, channel_type)
    # all REQUIRED. Admin route lifts record_tenant_id from JWT bearer and
    # resolves workspace_id via ``resolve_workspace_id`` (missing slug →
    # ``str(record_tenant_id)`` fallback) before constructing this command.
    record_tenant_id: UUID
    workspace_id: str | None = Field(
        default=None,
        min_length=WORKSPACE_ID_MIN_LEN,
        max_length=WORKSPACE_ID_MAX_LEN,
    )
    model_id: UUID | None = None
    embedding_model_id: UUID | None = None
    system_prompt: str = Field(default="", max_length=MAX_SYSTEM_PROMPT_CHARS)
    setting_options: BotSettingOptions | None = None
    callback_url: str | None = None


class UpdateBotCommand(BaseModel):
    bot_name: str | None = None
    # Tenant transfer is not supported via PATCH — would orphan FK rows.
    # Use a dedicated migration tool if a bot must move tenants.
    # ``workspace_id`` rename is similarly not supported here — moving a
    # bot between workspaces would break FK-chained data scoping; use a
    # dedicated tool for that flow.
    model_id: UUID | None = None
    embedding_model_id: UUID | None = None
    system_prompt: str | None = Field(default=None, max_length=MAX_SYSTEM_PROMPT_CHARS)
    setting_options: BotSettingOptions | None = None
    callback_url: str | None = None
    model_config = {"extra": "forbid"}


# ── Errors ──────────────────────────────────────────────────────────────────

class BotNotFoundError(Exception):
    pass


class CrossTenantForbiddenError(Exception):
    pass


# ── Service ─────────────────────────────────────────────────────────────────

class BotManagementService:
    """Orchestrates bot CRUD, cache invalidation, audit logging, and outbox events."""

    def __init__(
        self,
        repo: SqlAlchemyBotRepository,
        registry: BotRegistryService,
        uow_factory: UnitOfWorkFactory,
        session_factory: Any,
    ) -> None:
        self._repo = repo
        self._registry = registry
        self._uow_factory = uow_factory
        self._session_factory = session_factory

    # ── Public API ──────────────────────────────────────────────────────────

    async def create_bot(
        self,
        cmd: CreateBotCommand,
        *,
        admin_record_tenant: UUID | None,
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> BotConfig:
        """Create bot + registry cache invalidation + audit log + outbox event.

        ``admin_record_tenant=None`` is the platform-admin (RBAC 100) bypass;
        otherwise the request must stay within ``admin_record_tenant``.
        """
        if (
            admin_record_tenant is not None
            and cmd.record_tenant_id != admin_record_tenant
        ):
            raise CrossTenantForbiddenError("cross-tenant forbidden")

        # Single-source-of-truth resolver: validates the slug shape and
        # falls back to the per-tenant UUID slug when the caller omitted
        # ``workspace_id``. Bot row + audit + outbox event share the result.
        workspace_slug: WorkspaceId = resolve_workspace_id(
            cmd.workspace_id, record_tenant_id=cmd.record_tenant_id,
        )

        cfg = await self._repo.create_bot(
            bot_id=cmd.bot_id,
            channel_type=cmd.channel_type,
            bot_name=cmd.bot_name,
            record_tenant_id=cmd.record_tenant_id,
            workspace_id=workspace_slug,
            model_id=cmd.model_id,
            embedding_model_id=cmd.embedding_model_id,
            system_prompt=cmd.system_prompt,
            setting_options=(
                cmd.setting_options.model_dump() if cmd.setting_options else None
            ),
            callback_url=cmd.callback_url,
        )
        await self._write_audit(
            record_tenant_id=cfg.record_tenant_id,
            workspace_id=cfg.workspace_id,
            actor_user_id=actor_user_id,
            action="create",
            resource_id=str(cfg.id),
            before=None,
            after=cfg.model_dump(mode="json"),
            trace_id=trace_id,
        )
        await self._registry.invalidate(
            cfg.record_tenant_id, cfg.workspace_id, cfg.bot_id, cfg.channel_type,
        )
        await self._publish_registry_changed(
            record_tenant_id=cfg.record_tenant_id,
            workspace_id=cfg.workspace_id,
            bot_id=cfg.bot_id,
            channel_type=cfg.channel_type,
            action="created",
            bot_uuid=cfg.id,
            trace_id=trace_id,
        )
        return cfg

    async def update_bot(
        self,
        bot_uuid: UUID,
        cmd: UpdateBotCommand,
        *,
        admin_record_tenant: UUID | None,
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> BotConfig:
        """Update bot + invalidate cache + audit log + outbox event."""
        cfg = await self._repo.get_by_id(
            bot_uuid, record_tenant_id=admin_record_tenant,
        )
        if cfg is None:
            raise BotNotFoundError("bot not found")

        before_snapshot = cfg.model_dump(mode="json")
        fields = cmd.model_dump(exclude_unset=True, mode="python")
        if "setting_options" in fields and fields["setting_options"] is not None:
            so = fields["setting_options"]
            if hasattr(so, "model_dump"):
                fields["setting_options"] = so.model_dump()

        updated = await self._repo.update_bot(
            bot_uuid, record_tenant_id=admin_record_tenant, **fields,
        )
        if updated is None:
            raise BotNotFoundError("bot not found")

        await self._write_audit(
            record_tenant_id=updated.record_tenant_id,
            workspace_id=updated.workspace_id,
            actor_user_id=actor_user_id,
            action="update",
            resource_id=str(updated.id),
            before=before_snapshot,
            after=updated.model_dump(mode="json"),
            trace_id=trace_id,
        )
        await self._registry.invalidate(
            updated.record_tenant_id,
            updated.workspace_id,
            updated.bot_id,
            updated.channel_type,
        )
        await self._publish_registry_changed(
            record_tenant_id=updated.record_tenant_id,
            workspace_id=updated.workspace_id,
            bot_id=updated.bot_id,
            channel_type=updated.channel_type,
            action="updated",
            bot_uuid=updated.id,
            trace_id=trace_id,
        )
        return updated

    async def delete_bot(
        self,
        bot_uuid: UUID,
        *,
        admin_record_tenant: UUID | None,
        actor_user_id: str,
        trace_id: str | None = None,
    ) -> bool:
        """Soft delete + audit log + cache invalidation + outbox event."""
        cfg = await self._repo.get_by_id(
            bot_uuid, record_tenant_id=admin_record_tenant,
        )
        if cfg is None:
            raise BotNotFoundError("bot not found")

        before_snapshot = cfg.model_dump(mode="json")
        ok = await self._repo.soft_delete(
            bot_uuid, record_tenant_id=admin_record_tenant,
        )

        await self._write_audit(
            record_tenant_id=cfg.record_tenant_id,
            workspace_id=cfg.workspace_id,
            actor_user_id=actor_user_id,
            action="delete",
            resource_id=str(bot_uuid),
            before=before_snapshot,
            after=None,
            trace_id=trace_id,
        )
        await self._registry.invalidate(
            cfg.record_tenant_id, cfg.workspace_id, cfg.bot_id, cfg.channel_type,
        )
        if ok:
            await self._publish_registry_changed(
                record_tenant_id=cfg.record_tenant_id,
                workspace_id=cfg.workspace_id,
                bot_id=cfg.bot_id,
                channel_type=cfg.channel_type,
                action="deleted",
                bot_uuid=cfg.id,
                trace_id=trace_id,
            )
        return ok

    async def get_bot(
        self,
        bot_uuid: UUID,
        *,
        admin_record_tenant: UUID | None,
    ) -> BotConfig:
        """Fetch one bot, tenant-scoped (``None`` = platform-admin bypass)."""
        cfg = await self._repo.get_by_id(
            bot_uuid, record_tenant_id=admin_record_tenant,
        )
        if cfg is None:
            raise BotNotFoundError("bot not found")
        return cfg

    async def list_bots(
        self,
        *,
        admin_record_tenant: UUID | None,
        record_tenant_id: UUID | None = None,
        channel_type: str | None = None,
    ) -> list[BotConfig]:
        """List active bots, scoped by tenant.

        - ``admin_record_tenant=None`` → platform-admin listing across all tenants.
        - ``record_tenant_id=None`` → no caller filter (only valid when admin
          is platform-level too; otherwise scoped admin always sees own tenant).
        - When both are non-None, admin_record_tenant wins.
        """
        effective_tenant = (
            admin_record_tenant if admin_record_tenant is not None else record_tenant_id
        )
        bots = await self._repo.list_active(record_tenant_id=effective_tenant)
        if channel_type:
            bots = [b for b in bots if b.channel_type == channel_type]
        return bots

    # ── Private helpers ─────────────────────────────────────────────────────

    async def _write_audit(
        self,
        *,
        record_tenant_id: UUID | None,
        workspace_id: str,
        actor_user_id: str,
        action: str,
        resource_id: str,
        before: dict | None = None,
        after: dict | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        """Forensic audit-log write — RAISES on failure (never swallows).

        ``workspace_id`` scopes the audit row to the same slug as the bot
        being mutated, so admin-trail filters can pivot on workspace.
        """
        try:
            async with self._session_factory() as session:
                # alembic 010g: row_hash chain populated by insert_audit_row.
                await insert_audit_row(
                    session,
                    record_tenant_id=record_tenant_id,
                    workspace_id=workspace_id,
                    actor_user_id=actor_user_id,
                    action=action,
                    resource_type="bot",
                    resource_id=resource_id,
                    before_json=before,
                    after_json=after,
                    reason=reason,
                    trace_id=trace_id,
                )
                await session.commit()
        except (SQLAlchemyError, TypeError, ValueError):
            logger.exception(
                "admin_bot_audit_write_failed",
                action=action,
                resource_id=resource_id,
            )
            raise

    async def _publish_registry_changed(
        self,
        *,
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
        action: str,
        bot_uuid: UUID,
        trace_id: str | None = None,
    ) -> None:
        """Emit ``bot.registry.changed.v1`` into outbox for peer replicas."""
        async with self._uow_factory() as uow:
            await uow.add_outbox_raw(
                subject="bot.registry.changed.v1",
                payload={
                    "event_type": "bot.registry.changed.v1",
                    "record_tenant_id": str(record_tenant_id),
                    "workspace_id": workspace_id,
                    "bot_id": bot_id,
                    "channel_type": channel_type,
                    "action": action,
                    "bot_uuid": str(bot_uuid),
                },
                workspace_id=workspace_id,
                trace_id=trace_id or "",
            )
            await uow.commit()


__all__ = [
    "BotManagementService",
    "BotNotFoundError",
    "CreateBotCommand",
    "CrossTenantForbiddenError",
    "UpdateBotCommand",
]
