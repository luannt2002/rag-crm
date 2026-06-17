"""Bot repository — 4-key identity + record_tenant_id UUID FK.

Schema:
- ``id`` UUID PK
- ``bot_id`` VARCHAR external slug (caller-supplied)
- ``channel_type`` VARCHAR external (caller-supplied)
- ``workspace_id`` VARCHAR external slug (caller-supplied; falls back to
  ``str(record_tenant_id)`` when the wire payload omits it)
- ``record_tenant_id`` UUID FK ``tenants(id)`` (lifted from JWT bearer)
- ``record_model_id`` / ``record_embedding_model_id`` soft refs
- standard timestamps + soft-delete

The 4-tuple ``(record_tenant_id, workspace_id, bot_id, channel_type)``
is the unique constraint at DB level. ``find_by_4key`` requires the
UUID + 3 string keys; dropping any of them would either leak across
tenants or collapse two distinct workspaces into one row match.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import structlog
from pydantic import ValidationError
from sqlalchemy import select, update

from ragbot.application.dto.bot_config import (
    BotConfig,
    BotSettingOptions,
    RerankIntentWhitelist,
)
from ragbot.infrastructure.db.models import BotModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository
from ragbot.shared.bot_limits import COLUMN_DEFAULTS
from ragbot.shared.types import WorkspaceId

logger = structlog.get_logger(__name__)


def _row_to_config(row: BotModel) -> BotConfig:
    """Map ORM ``BotModel`` → ``BotConfig`` Pydantic DTO."""
    opts_raw = dict(row.setting_options or {})
    try:
        setting_options = BotSettingOptions(**opts_raw)
    except ValidationError as e:
        logger.warning(
            "bot_setting_options_drift",
            bot_id=row.bot_id,
            channel_type=row.channel_type,
            errors=e.errors(),
            raw=row.setting_options,
        )
        setting_options = BotSettingOptions()
    raw_whitelist = getattr(row, "rerank_intent_whitelist", None)
    whitelist: RerankIntentWhitelist | None = None
    if raw_whitelist is not None:
        try:
            whitelist = RerankIntentWhitelist.model_validate(raw_whitelist)
        except ValidationError as e:
            logger.warning(
                "bot_rerank_intent_whitelist_drift",
                bot_id=row.bot_id,
                channel_type=row.channel_type,
                errors=e.errors(),
                raw=raw_whitelist,
            )
            whitelist = None

    return BotConfig(
        id=row.id,
        bot_id=row.bot_id,
        channel_type=row.channel_type,
        record_tenant_id=row.record_tenant_id,
        workspace_id=row.workspace_id,
        bot_name=row.bot_name,
        model_id=row.record_model_id,
        embedding_model_id=row.record_embedding_model_id,
        system_prompt=row.system_prompt or "",
        setting_options=setting_options,
        custom_vocabulary=dict(row.custom_vocabulary or {}),
        max_history=getattr(row, "max_history", COLUMN_DEFAULTS["max_history"]),
        max_documents=getattr(row, "max_documents", COLUMN_DEFAULTS["max_documents"]) or COLUMN_DEFAULTS["max_documents"],
        prompt_max_tokens=getattr(row, "prompt_max_tokens", COLUMN_DEFAULTS["prompt_max_tokens"]),
        rerank_top_n=getattr(row, "rerank_top_n", COLUMN_DEFAULTS["rerank_top_n"]),
        plan_limits=dict(getattr(row, "plan_limits", None) or {}),
        callback_url=getattr(row, "callback_url", None),
        language=getattr(row, "language", "vi"),
        oos_answer_template=getattr(row, "oos_answer_template", None),
        rerank_intent_whitelist=whitelist,
        # Token-quota columns (alembic 0100). ``getattr`` keeps the
        # mapper tolerant of pre-migration rows during a rolling deploy:
        # the DB ``server_default`` is the source of truth post-upgrade.
        tokens_used=getattr(row, "tokens_used", 0) or 0,
        extra_max_tokens=getattr(row, "extra_max_tokens", 0) or 0,
        extra_output_tokens_per_response=getattr(
            row, "extra_output_tokens_per_response", 0,
        ) or 0,
        bypass_token_check=bool(getattr(row, "bypass_token_check", False)),
        # Per-bot conversational-action + metadata + threshold columns.
        # ``getattr`` tolerates pre-migration rows during a rolling deploy;
        # the DB JSONB default ({} / NULL) is the source of truth post-upgrade.
        # Mapping these closes the schema-drift that left ``action_config``
        # always empty (slot-filling gate never fired).
        action_config=dict(getattr(row, "action_config", None) or {}),
        metadata_extraction_config=getattr(row, "metadata_extraction_config", None),
        threshold_overrides=dict(getattr(row, "threshold_overrides", None) or {}),
        bypass_token_limit=bool(getattr(row, "bypass_token_limit", False)),
        bypass_rate_limit=bool(getattr(row, "bypass_rate_limit", False)),
        # M14 — used by ``generate`` to default XML chunk-wrap ON for
        # bots created on/after the cutoff date. ``getattr`` tolerates
        # a rolling deploy where the ORM column hasn't been refreshed
        # in this process yet (None falls back to OFF — no behaviour
        # change for legacy bots).
        created_at=getattr(row, "created_at", None),
    )


class SqlAlchemyBotRepository(TenantScopedRepository):
    """Repository cho bảng ``bots``."""

    async def create_bot(
        self,
        *,
        bot_id: str,
        channel_type: str,
        bot_name: str,
        record_tenant_id: UUID,
        workspace_id: WorkspaceId,
        model_id: UUID | None = None,
        embedding_model_id: UUID | None = None,
        system_prompt: str = "",
        setting_options: dict[str, Any] | None = None,
        custom_vocabulary: dict[str, Any] | None = None,
        callback_url: str | None = None,
    ) -> BotConfig:
        """Insert a new ``bots`` row; ``record_tenant_id`` + ``workspace_id`` REQUIRED.

        The caller MUST resolve the slug via ``resolve_workspace_id``
        before invoking this method — the repository never falls back
        silently. The slug arrives as ``WorkspaceId`` (str newtype) so
        the format check has already been performed.
        """
        async with self._new_session() as session:
            row = BotModel(
                id=uuid4(),
                bot_id=bot_id.strip(),
                channel_type=channel_type.strip(),
                record_tenant_id=record_tenant_id,
                workspace_id=workspace_id,
                bot_name=bot_name,
                record_model_id=model_id,
                record_embedding_model_id=embedding_model_id,
                system_prompt=system_prompt or "",
                setting_options=setting_options
                or BotSettingOptions().model_dump(),
                custom_vocabulary=custom_vocabulary or {},
                callback_url=callback_url,
                is_deleted=False,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _row_to_config(row)

    async def find_by_4key(
        self,
        record_tenant_id: UUID,
        workspace_id: str,
        bot_id: str,
        channel_type: str,
    ) -> BotConfig | None:
        """Lookup bot by 4-tuple ``(record_tenant_id, workspace_id, bot_id, channel_type)``.

        All 4 keys REQUIRED. Dropping ``record_tenant_id`` would leak
        across tenants; dropping ``workspace_id`` would collide bots
        sharing the same external slug across distinct workspaces.
        """
        async with self._new_session() as session:
            stmt = select(BotModel).where(
                BotModel.record_tenant_id == record_tenant_id,
                BotModel.workspace_id == workspace_id.strip(),
                BotModel.bot_id == bot_id.strip(),
                BotModel.channel_type == channel_type.strip(),
                BotModel.is_deleted.is_(False),
            )
            row = await session.scalar(stmt)
            return _row_to_config(row) if row is not None else None

    async def find_by_3key_unique(
        self,
        record_tenant_id: UUID,
        bot_id: str,
        channel_type: str,
    ) -> BotConfig | None:
        """Resolve a bot by ``(record_tenant_id, bot_id, channel_type)`` when no
        workspace slug is supplied — read-path convenience only.

        Returns the bot ONLY when exactly one row matches (the slug is
        unambiguous within the tenant). If two workspaces share the same
        ``(bot_id, channel_type)`` the result is ambiguous and this returns
        ``None`` so the caller must disambiguate with an explicit
        ``workspace_id``. The 4-key :meth:`find_by_4key` stays the canonical
        resolve for the chat / write boundary where a cross-workspace match
        would be a leak; this helper never widens that boundary because it
        refuses to guess when ambiguous.
        """
        async with self._new_session() as session:
            stmt = (
                select(BotModel)
                .where(
                    BotModel.record_tenant_id == record_tenant_id,
                    BotModel.bot_id == bot_id.strip(),
                    BotModel.channel_type == channel_type.strip(),
                    BotModel.is_deleted.is_(False),
                )
                .limit(2)
            )
            rows = list((await session.execute(stmt)).scalars().all())
            return _row_to_config(rows[0]) if len(rows) == 1 else None

    async def list_active(
        self, *, record_tenant_id: UUID | None,
    ) -> list[BotConfig]:
        """List active bots; ``record_tenant_id`` REQUIRED kw arg.

        UUID = tenant-scoped; ``None`` = platform-admin / bootstrap load
        (called only by ``BotRegistryService`` warmup with super-admin token).
        """
        async with self._new_session() as session:
            stmt = select(BotModel).where(BotModel.is_deleted.is_(False))
            if record_tenant_id is not None:
                stmt = stmt.where(BotModel.record_tenant_id == record_tenant_id)
            result = await session.execute(stmt)
            rows = list(result.scalars().all())
            return [_row_to_config(r) for r in rows]

    async def get_by_id(
        self,
        id: UUID,  # noqa: A002
        *,
        record_tenant_id: UUID | None,
    ) -> BotConfig | None:
        """Lookup bot by internal UUID PK.

        UUID = tenant-scoped (cross-tenant UUID returns None);
        ``None`` = platform-admin bypass.
        """
        async with self._new_session() as session:
            stmt = select(BotModel).where(
                BotModel.id == id,
                BotModel.is_deleted.is_(False),
            )
            if record_tenant_id is not None:
                stmt = stmt.where(BotModel.record_tenant_id == record_tenant_id)
            row = await session.scalar(stmt)
            return _row_to_config(row) if row is not None else None

    async def update_bot(
        self,
        id: UUID,  # noqa: A002
        *,
        record_tenant_id: UUID | None,
        **fields: Any,
    ) -> BotConfig | None:
        """Update allow-listed fields; ``record_tenant_id`` REQUIRED kw arg."""
        if not fields:
            return await self.get_by_id(id, record_tenant_id=record_tenant_id)
        allowed = {
            "bot_name", "record_model_id", "record_embedding_model_id",
            "system_prompt", "setting_options", "custom_vocabulary",
            "max_history", "max_documents", "prompt_max_tokens",
            "rerank_top_n", "plan_limits", "callback_url",
        }
        # Back-compat: callers may still pass model_id/embedding_model_id
        # using the old DTO field names; remap to new column names.
        if "model_id" in fields and "record_model_id" not in fields:
            fields["record_model_id"] = fields.pop("model_id")
        if "embedding_model_id" in fields and "record_embedding_model_id" not in fields:
            fields["record_embedding_model_id"] = fields.pop("embedding_model_id")
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return await self.get_by_id(id, record_tenant_id=record_tenant_id)
        async with self._new_session() as session:
            stmt = update(BotModel).where(
                BotModel.id == id,
                BotModel.is_deleted.is_(False),
            )
            if record_tenant_id is not None:
                stmt = stmt.where(BotModel.record_tenant_id == record_tenant_id)
            res = await session.execute(stmt.values(**updates))
            if res.rowcount == 0:
                await session.rollback()
                return None
            await session.commit()
            row = await session.scalar(
                select(BotModel).where(BotModel.id == id),
            )
            return _row_to_config(row) if row is not None else None

    async def soft_delete(
        self,
        id: UUID,  # noqa: A002
        *,
        record_tenant_id: UUID | None,
    ) -> bool:
        """Soft-delete bot."""
        async with self._new_session() as session:
            stmt = update(BotModel).where(
                BotModel.id == id,
                BotModel.is_deleted.is_(False),
            )
            if record_tenant_id is not None:
                stmt = stmt.where(BotModel.record_tenant_id == record_tenant_id)
            res = await session.execute(
                stmt.values(
                    is_deleted=True,
                    deleted_at=datetime.now(tz=timezone.utc),
                ),
            )
            await session.commit()
            return int(res.rowcount or 0) > 0


__all__ = ["SqlAlchemyBotRepository"]
