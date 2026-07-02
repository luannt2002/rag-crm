"""AI config repository — providers / models / bindings / prompts / intents / tools / audit."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.ports.ai_config_port import (
    AIConfigRepositoryPort,
    AuditEntry,
    BindingRow,
    ModelRow,
    PromptTemplateRow,
    ProviderRow,
)
from ragbot.infrastructure.db.models import (
    AIModelModel,
    AIProviderModel,
    AuditLogModel,
    BotModel,
    BotModelBindingModel,
    PromptTemplateModel,
)
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.shared.constants import PII_SURFACE_AUDIT_LOG, WORKSPACE_SYSTEM_SLUG
from ragbot.shared.pii_universal import redact_mapping, redact_text
from ragbot.shared.errors import RepositoryError, TenantIsolationViolation
from ragbot.shared.types import BotId, TenantId


def _to_provider(r: AIProviderModel) -> ProviderRow:
    """Map AIProviderModel → ProviderRow; back-fill code from name when DB column NULL."""
    code = (r.code or "").strip() or r.name.strip().lower()
    # 2026-05-21 — wire ``api_key_ref`` (env var name in DB) into the
    # ``credentials_vault_path`` slot that ``EnvSecretsAdapter.resolve()``
    # consumes. Pre-fix this field was hardcoded ``None`` since commit
    # 93b1258 (2026-05-12), so the secrets resolver always returned an
    # empty string and LiteLLM silently fell back to ``OPENAI_API_KEY``
    # for every provider. OpenAI / Anthropic happened to work because
    # their env var name matched the LiteLLM default; non-default
    # providers (Innocom LM Studio with ``LMSTUDIO_API_KEY``) had their
    # key swapped for the OpenAI key, which the LM Studio endpoint
    # rejected as "Malformed LM Studio API token" — root cause of the
    # Innocom swap failure documented in
    # ``plans/260521-INNOCOM-3SVC-SWAP/plan.md``.
    api_key_ref = getattr(r, "api_key_ref", None)
    credentials_vault_path = (
        f"env:{api_key_ref}" if api_key_ref and api_key_ref.strip() else None
    )
    return ProviderRow(
        id=r.id,
        name=r.name,
        code=code,
        type=r.type,
        base_url=r.base_url,
        auth_type=r.auth_type,
        credentials_vault_path=credentials_vault_path,
        enabled=r.enabled,
        metadata=dict(r.metadata_json or {}),
        # Alembic 010e — DB column controls LiteLLM prefix policy. Default
        # TRUE preserves the LiteLLM convention; OpenAI / Anthropic rows
        # are flipped to FALSE by the migration. ``getattr`` keeps the
        # mapper safe against pre-010e DB snapshots (treats missing
        # attribute as the default).
        requires_prefix=bool(getattr(r, "requires_prefix", True)),
    )


def _to_model(r: AIModelModel) -> ModelRow:
    """Chuyển đổi ORM AIModelModel sang DTO ModelRow.

    Pricing columns kept as ``Decimal`` (DB ``Numeric(10,6)``) end-to-end.
    Casting to ``float`` here would silently lose precision at scale — the
    boundary cast is deferred to the UI/JSON layer (see ``decimal_to_jsonable``).
    """
    return ModelRow(
        id=r.id,
        provider_id=r.record_provider_id,
        name=r.name,
        kind=r.kind,
        context_window=r.context_window,
        max_output_tokens=r.max_output_tokens,
        input_price_per_1k_usd=r.input_price_per_1k_usd,
        output_price_per_1k_usd=r.output_price_per_1k_usd,
        supports_streaming=r.supports_streaming,
        supports_tools=r.supports_tools,
        supports_vision=r.supports_vision,
        supports_json_mode=r.supports_json_mode,
        languages=tuple(r.languages or ()),
        enabled=r.enabled,
        metadata=dict(r.metadata_json or {}),
        embedding_dimension=getattr(r, "embedding_dimension", None),
    )


def _to_binding(r: BotModelBindingModel) -> BindingRow:
    """Chuyển đổi ORM BotModelBindingModel sang DTO BindingRow.

    ``record_fallback_model_id`` is the same-tier failover model UUID;
    NULL means no failover configured and the router treats the primary
    call as terminal.
    """
    return BindingRow(
        id=r.id,
        record_tenant_id=r.record_tenant_id,
        record_bot_id=r.record_bot_id,
        purpose=r.purpose,
        model_id=r.record_model_id,
        rank=r.rank,
        variant=r.variant,
        weight=r.weight,
        temperature=float(r.temperature),
        max_tokens=r.max_tokens,
        top_p=float(r.top_p),
        extra_params=dict(r.extra_params or {}),
        active=r.active,
        version=r.version,
        record_fallback_model_id=r.record_fallback_model_id,
    )


def _to_prompt(r: PromptTemplateModel) -> PromptTemplateRow:
    """Chuyển đổi ORM PromptTemplateModel sang DTO PromptTemplateRow."""
    return PromptTemplateRow(
        id=r.id,
        record_tenant_id=r.record_tenant_id,
        record_bot_id=r.record_bot_id,
        template_key=r.template_key,
        version=r.version,
        jinja_source=r.jinja_source,
        required_vars=tuple(r.required_vars or ()),
        model_hint=r.model_hint,
        active=r.active,
    )


class SqlAlchemyAIConfigRepository(AIConfigRepositoryPort):
    """Repository cho cấu hình AI — providers, models, bindings, prompts, audit."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        pii_redactor: Any | None = None,
        bot_repo: Any | None = None,
    ) -> None:
        """Khởi tạo repository với session factory.

        @param pii_redactor: optional ``PiiRedactorPort``. When provided
            AND the audit's owning bot has
            ``plan_limits.pii_redaction_universal=True``, the
            ``before`` / ``after`` JSONB + ``reason`` text columns are
            masked at the persistence boundary (Phase D2 universal
            coverage). Falsy / NullPiiRedactor = passthrough.
        @param bot_repo: optional ``BotRepository`` used to look up the
            owning bot's ``plan_limits`` when an audit row carries a
            ``record_bot_id``. ``None`` ⇒ universal redaction skipped
            (tenant-scoped audit rows go through unmasked, same as
            pre-D2 behaviour).
        """
        self._sf = session_factory
        self._pii_redactor = pii_redactor
        self._bot_repo = bot_repo

    @staticmethod
    def _ensure(record_tenant_id: TenantId | None) -> TenantId:
        if record_tenant_id is None:
            raise TenantIsolationViolation("record_tenant_id missing")
        return record_tenant_id

    # --- Providers ---------------------------------------------------------
    async def list_providers(self, *, enabled_only: bool = True) -> list[ProviderRow]:
        """Lấy danh sách AI providers.
        @param enabled_only: chỉ lấy provider đang bật
        @return: danh sách ProviderRow
        """
        async with self._sf() as session:
            stmt = select(AIProviderModel)
            if enabled_only:
                stmt = stmt.where(AIProviderModel.enabled.is_(True))
            rows = (await session.execute(stmt.order_by(AIProviderModel.name))).scalars().all()
            return [_to_provider(r) for r in rows]

    async def get_provider(self, provider_id: UUID) -> ProviderRow | None:
        """Lấy provider theo UUID.
        @return: ProviderRow hoặc None
        """
        async with self._sf() as session:
            row = await session.get(AIProviderModel, provider_id)
            return _to_provider(row) if row else None

    async def update_provider(self, provider_id: UUID, **fields: Any) -> ProviderRow:
        """Cập nhật thông tin provider.
        @param provider_id: UUID provider
        @return: ProviderRow đã cập nhật
        """
        async with self._sf() as session:
            existing = await session.get(AIProviderModel, provider_id)
            if existing is None:
                raise RepositoryError(f"provider {provider_id} not found")
            for k, v in fields.items():
                if v is None:
                    continue
                if hasattr(existing, k):
                    setattr(existing, k, v)
            await session.commit()
            await session.refresh(existing)
            return _to_provider(existing)

    async def delete_provider(self, provider_id: UUID) -> None:
        """Soft delete: disable. `deleted_at` set if column exists (migration 0009)."""
        async with self._sf() as session:
            existing = await session.get(AIProviderModel, provider_id)
            if existing is None:
                raise RepositoryError(f"provider {provider_id} not found")
            existing.enabled = False
            if hasattr(existing, "deleted_at"):
                from datetime import datetime, timezone
                existing.deleted_at = datetime.now(tz=timezone.utc)  # type: ignore[attr-defined]
            await session.commit()

    # --- Models ------------------------------------------------------------
    async def list_models(
        self,
        *,
        provider_id: UUID | None = None,
        kind: str | None = None,
        enabled_only: bool = True,
    ) -> list[ModelRow]:
        """Lấy danh sách AI models, lọc theo provider/kind.
        @return: danh sách ModelRow
        """
        async with self._sf() as session:
            stmt = select(AIModelModel)
            if provider_id:
                stmt = stmt.where(AIModelModel.record_provider_id == provider_id)
            if kind:
                stmt = stmt.where(AIModelModel.kind == kind)
            if enabled_only:
                stmt = stmt.where(AIModelModel.enabled.is_(True))
            rows = (await session.execute(stmt.order_by(AIModelModel.name))).scalars().all()
            return [_to_model(r) for r in rows]

    async def get_model(self, model_id: UUID) -> ModelRow | None:
        """Lấy model theo UUID.
        @return: ModelRow hoặc None
        """
        async with self._sf() as session:
            row = await session.get(AIModelModel, model_id)
            return _to_model(row) if row else None

    async def get_models_by_ids(
        self, model_ids: list[UUID],
    ) -> dict[str, ModelRow]:
        """Batch-fetch models in a single round-trip — fixes N+1 in
        ``ModelResolverService._get_cached`` where 20-30 bindings used to
        emit one ``SELECT ai_models WHERE id = ?`` per binding.

        @param model_ids: UUIDs to look up. Duplicates are tolerated;
            the IN-clause de-dups server-side.
        @return: ``{str(id): ModelRow}`` map for O(1) lookup. Missing IDs
            omitted from the map (caller checks `id in map`).
        """
        if not model_ids:
            return {}
        async with self._sf() as session:
            stmt = select(AIModelModel).where(AIModelModel.id.in_(model_ids))
            rows = (await session.execute(stmt)).scalars().all()
            return {str(r.id): _to_model(r) for r in rows}

    async def get_providers_by_ids(
        self, provider_ids: list[UUID],
    ) -> dict[str, ProviderRow]:
        """Batch-fetch providers in a single round-trip — paired with
        ``get_models_by_ids`` for the N+1 cascade fix.

        @param provider_ids: UUIDs to look up; duplicates tolerated.
        @return: ``{str(id): ProviderRow}`` map.
        """
        if not provider_ids:
            return {}
        async with self._sf() as session:
            stmt = select(AIProviderModel).where(
                AIProviderModel.id.in_(provider_ids),
            )
            rows = (await session.execute(stmt)).scalars().all()
            return {str(r.id): _to_provider(r) for r in rows}

    async def update_model(self, model_id: UUID, **fields: Any) -> ModelRow:
        """Cập nhật thông tin model.
        @param model_id: UUID model
        @return: ModelRow đã cập nhật
        """
        async with self._sf() as session:
            existing = await session.get(AIModelModel, model_id)
            if existing is None:
                raise RepositoryError(f"model {model_id} not found")
            for k, v in fields.items():
                if v is None:
                    continue
                if hasattr(existing, k):
                    setattr(existing, k, v)
            await session.commit()
            await session.refresh(existing)
            return _to_model(existing)

    async def delete_model(self, model_id: UUID) -> None:
        """Soft delete — guard: reject if active bindings still reference this model."""
        async with self._sf() as session:
            existing = await session.get(AIModelModel, model_id)
            if existing is None:
                raise RepositoryError(f"model {model_id} not found")
            active_ref = await session.scalar(
                select(BotModelBindingModel.id).where(
                    BotModelBindingModel.record_model_id == model_id,
                    BotModelBindingModel.active.is_(True),
                ).limit(1),
            )
            if active_ref is not None:
                raise RepositoryError(
                    f"model {model_id} still referenced by active binding {active_ref}",
                )
            existing.enabled = False
            if hasattr(existing, "deleted_at"):
                from datetime import datetime, timezone
                existing.deleted_at = datetime.now(tz=timezone.utc)  # type: ignore[attr-defined]
            await session.commit()

    # --- Bindings ----------------------------------------------------------
    async def list_bindings_multi_purpose(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        purposes: list[str],
        active_only: bool = True,
    ) -> dict[str, list[BindingRow]]:
        """Single round-trip variant of :meth:`list_bindings` covering N
        purposes at once via ``purpose IN (...)``.

        Returns a dict keyed by purpose where every requested purpose is
        present — missing purposes map to ``[]`` so callers can use one
        lookup style for the hot path.

        Tenant filter mirrors :meth:`list_bindings`: a non-None
        ``record_tenant_id`` matches both that tenant and the global
        (NULL tenant) rows; a None tenant matches NULL-only.
        """
        out: dict[str, list[BindingRow]] = {p: [] for p in purposes}
        if not purposes:
            return out
        from sqlalchemy import or_
        async with self._sf() as session:
            if record_tenant_id is not None:
                tenant_filter = or_(
                    BotModelBindingModel.record_tenant_id == record_tenant_id,
                    BotModelBindingModel.record_tenant_id.is_(None),
                )
            else:
                tenant_filter = BotModelBindingModel.record_tenant_id.is_(None)
            stmt = (
                select(BotModelBindingModel)
                .where(
                    tenant_filter,
                    BotModelBindingModel.record_bot_id == record_bot_id,
                    BotModelBindingModel.purpose.in_(purposes),
                )
                .order_by(BotModelBindingModel.rank.asc())
            )
            if active_only:
                stmt = stmt.where(BotModelBindingModel.active.is_(True))
            rows = (await session.execute(stmt)).scalars().all()
        for r in rows:
            out.setdefault(r.purpose, []).append(_to_binding(r))
        return out

    async def list_bindings(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        purpose: str | None = None,
        active_only: bool = True,
    ) -> list[BindingRow]:
        """Lấy danh sách bindings của bot, s��p xếp theo rank.
        @param record_bot_id: ID bot
        @param purpose: lọc theo mục đích (chat, embedding, ...)
        @return: danh sách BindingRow
        """
        # record_tenant_id can be None for system-level / test queries — skip _ensure
        # Query matches both tenant-specific AND global (NULL) bindings
        tid = record_tenant_id
        async with self._sf() as session:
            from sqlalchemy import or_
            if tid is not None:
                tenant_filter = or_(BotModelBindingModel.record_tenant_id == tid, BotModelBindingModel.record_tenant_id.is_(None))
            else:
                tenant_filter = BotModelBindingModel.record_tenant_id.is_(None)
            stmt = select(BotModelBindingModel).where(
                tenant_filter,
                BotModelBindingModel.record_bot_id == record_bot_id,
            )
            if purpose:
                stmt = stmt.where(BotModelBindingModel.purpose == purpose)
            if active_only:
                stmt = stmt.where(BotModelBindingModel.active.is_(True))
            stmt = stmt.order_by(BotModelBindingModel.rank.asc())
            rows = (await session.execute(stmt)).scalars().all()
            return [_to_binding(r) for r in rows]

    async def get_binding(
        self,
        binding_id: UUID,
        *,
        record_tenant_id: TenantId,
    ) -> BindingRow | None:
        """Lấy binding theo UUID.
        @return: BindingRow hoặc None
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            row = await session.scalar(
                select(BotModelBindingModel).where(
                    BotModelBindingModel.id == binding_id,
                    BotModelBindingModel.record_tenant_id == tid,
                ),
            )
            return _to_binding(row) if row else None

    async def create_binding(self, row: BindingRow) -> BindingRow:
        """Tạo binding mới giữa bot và model.
        @param row: BindingRow cần tạo
        @return: BindingRow đã persist
        """
        async with self._sf() as session:
            # bot_model_bindings is FK-chain scoped: slug inherits from
            # the parent ``bots`` row so the 4-key denormalisation stays
            # consistent without forcing the BindingRow DTO to carry it.
            # Bot-less / global bindings (record_bot_id NULL) fall back to
            # the system slug — they're tenant-level / platform rows.
            ws_slug = WORKSPACE_SYSTEM_SLUG
            if row.record_bot_id is not None:
                bot_ws = await session.scalar(
                    select(BotModel.workspace_id).where(
                        BotModel.id == row.record_bot_id,
                    ),
                )
                if bot_ws is not None:
                    ws_slug = bot_ws
            session.add(
                BotModelBindingModel(
                    id=row.id,
                    record_tenant_id=row.record_tenant_id,
                    workspace_id=ws_slug,
                    record_bot_id=row.record_bot_id,
                    purpose=row.purpose,
                    record_model_id=row.model_id,
                    rank=row.rank,
                    variant=row.variant,
                    weight=row.weight,
                    temperature=row.temperature,
                    max_tokens=row.max_tokens,
                    top_p=row.top_p,
                    extra_params=dict(row.extra_params),
                    active=row.active,
                    version=row.version,
                ),
            )
            await session.commit()
            return row

    async def update_binding(
        self,
        binding_id: UUID,
        *,
        record_tenant_id: TenantId,
        **fields: Any,
    ) -> BindingRow:
        """Cập nhật binding và tăng version.
        @param binding_id: UUID binding
        @return: BindingRow đã cập nhật
        """
        tid = self._ensure(record_tenant_id)
        # Drop unknown keys + the immutable ``version`` (server-managed below).
        mapper_cols = set(BotModelBindingModel.__mapper__.attrs.keys())
        values: dict[str, Any] = {
            k: v for k, v in fields.items() if k in mapper_cols and k != "version"
        }
        # Server-managed monotonic bump — keeps optimistic-lock semantics.
        values["version"] = BotModelBindingModel.version + 1
        async with self._sf() as session:
            # Atomic ownership check (Issue #20 TOCTOU): tenant filter pushed
            # into the UPDATE WHERE clause so no SELECT-then-mutate window.
            stmt = (
                update(BotModelBindingModel)
                .where(
                    BotModelBindingModel.id == binding_id,
                    BotModelBindingModel.record_tenant_id == tid,
                )
                .values(**values)
                .returning(BotModelBindingModel)
                .execution_options(synchronize_session=False)
            )
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                # rowcount==0 → either does not exist OR foreign-tenant; both
                # raise the same shape to avoid enumeration leak upstream.
                await session.rollback()
                raise RepositoryError(f"binding {binding_id} not found")
            await session.commit()
            return _to_binding(row)

    async def delete_binding(self, binding_id: UUID, *, record_tenant_id: TenantId) -> None:
        """Vô hiệu hoá binding (soft delete — set active=False).
        @param binding_id: UUID binding
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            # Atomic ownership check (Issue #20 TOCTOU): tenant filter inline.
            result = await session.execute(
                update(BotModelBindingModel)
                .where(
                    BotModelBindingModel.id == binding_id,
                    BotModelBindingModel.record_tenant_id == tid,
                )
                .values(active=False),
            )
            if result.rowcount == 0:
                await session.rollback()
                raise RepositoryError(f"binding {binding_id} not found")
            await session.commit()

    # --- Prompt templates --------------------------------------------------
    async def get_prompt_template(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None,
        template_key: str,
        version: int | None = None,
        active_only: bool = True,
    ) -> PromptTemplateRow | None:
        """Lấy prompt template theo key, ưu tiên version mới nhất.
        @param template_key: khoá định danh template
        @return: PromptTemplateRow hoặc None
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = select(PromptTemplateModel).where(
                PromptTemplateModel.record_tenant_id == tid,
                PromptTemplateModel.template_key == template_key,
            )
            if record_bot_id is None:
                stmt = stmt.where(PromptTemplateModel.record_bot_id.is_(None))
            else:
                stmt = stmt.where(PromptTemplateModel.record_bot_id == record_bot_id)
            if version is not None:
                stmt = stmt.where(PromptTemplateModel.version == version)
            if active_only:
                stmt = stmt.where(PromptTemplateModel.active.is_(True))
            stmt = stmt.order_by(PromptTemplateModel.version.desc())
            row = await session.scalar(stmt)
            return _to_prompt(row) if row else None

    async def list_prompt_template_versions(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None,
        template_key: str,
    ) -> list[int]:
        """Lấy danh sách version numbers của prompt template.
        @param template_key: khoá template
        @return: danh sách version giảm dần
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = select(PromptTemplateModel.version).where(
                PromptTemplateModel.record_tenant_id == tid,
                PromptTemplateModel.template_key == template_key,
            )
            if record_bot_id is None:
                stmt = stmt.where(PromptTemplateModel.record_bot_id.is_(None))
            else:
                stmt = stmt.where(PromptTemplateModel.record_bot_id == record_bot_id)
            rows = (await session.execute(stmt.order_by(PromptTemplateModel.version.desc()))).scalars().all()
            return [int(v) for v in rows]

    async def create_prompt_template(self, row: PromptTemplateRow) -> PromptTemplateRow:
        """Tạo prompt template mới.
        @param row: PromptTemplateRow cần tạo
        @return: PromptTemplateRow đã persist
        """
        async with self._sf() as session:
            session.add(
                PromptTemplateModel(
                    id=row.id,
                    record_tenant_id=row.record_tenant_id,
                    workspace_id=WORKSPACE_SYSTEM_SLUG,
                    record_bot_id=row.record_bot_id,
                    template_key=row.template_key,
                    version=row.version,
                    jinja_source=row.jinja_source,
                    required_vars=list(row.required_vars),
                    model_hint=row.model_hint,
                    active=row.active,
                ),
            )
            await session.commit()
            return row

    async def set_prompt_template_active(
        self,
        template_id: UUID,
        *,
        record_tenant_id: TenantId,
        active: bool,
    ) -> None:
        """Bật/tắt trạng thái active cho prompt template.
        @param template_id: UUID template
        @param active: trạng thái mới
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            await session.execute(
                update(PromptTemplateModel)
                .where(
                    PromptTemplateModel.id == template_id,
                    PromptTemplateModel.record_tenant_id == tid,
                )
                .values(active=active),
            )
            await session.commit()

    # Migration 0010: intent routes + bot tools repositories removed (dead
    # tables). Intent routing uses binding purposes; tool configs moved to
    # per-bot JSON config when needed.

    # --- AI Keys (alembic 0066) -------------------------------------------

    async def insert_key(
        self,
        *,
        provider_id: UUID,
        api_key_encrypted: str,
        fingerprint: str,
        is_default: bool,
    ) -> UUID:
        """Insert a new ai_keys row. Returns the generated key UUID."""
        from datetime import datetime, timezone
        from uuid import uuid4

        new_id = uuid4()
        async with self._sf() as session:
            from sqlalchemy import text
            await session.execute(
                text(
                    "INSERT INTO ai_keys "
                    "(id, record_provider_id, api_key_encrypted, fingerprint, status, is_default, created_at, updated_at) "
                    "VALUES (:id, :provider_id, :api_key_encrypted, :fingerprint, 'active', :is_default, :now, :now)"
                ),
                {
                    "id": new_id,
                    "provider_id": provider_id,
                    "api_key_encrypted": api_key_encrypted,
                    "fingerprint": fingerprint,
                    "is_default": is_default,
                    "now": datetime.now(tz=timezone.utc),
                },
            )
            await session.commit()
        return new_id

    async def get_key(self, key_id: UUID) -> dict | None:
        """Fetch a single ai_keys row by PK. Returns masked dict (no plain key)."""
        async with self._sf() as session:
            from sqlalchemy import text
            row = (await session.execute(
                text(
                    "SELECT id, record_provider_id, api_key_encrypted, fingerprint, status, "
                    "is_default, last_health_check_at, last_health_status, last_used_at, "
                    "rotated_at, rotated_by_user_id, created_at, updated_at "
                    "FROM ai_keys WHERE id = :id"
                ),
                {"id": key_id},
            )).mappings().first()
            if row is None:
                return None
            d = dict(row)
            # Keep encrypted blob for internal decrypt; caller must not expose it.
            return d

    async def list_keys(self, provider_id: UUID) -> list[dict]:
        """List all ai_keys for a provider ordered newest first (masked — no plain key)."""
        async with self._sf() as session:
            from sqlalchemy import text
            rows = (await session.execute(
                text(
                    "SELECT id, record_provider_id, fingerprint, status, is_default, "
                    "last_health_check_at, last_health_status, last_used_at, "
                    "rotated_at, created_at "
                    "FROM ai_keys "
                    "WHERE record_provider_id = :pid "
                    "ORDER BY created_at DESC"
                ),
                {"pid": provider_id},
            )).mappings().all()
            return [dict(r) for r in rows]

    async def update_key_health(
        self,
        key_id: UUID,
        *,
        status_code: int,
        latency_ms: float,
    ) -> None:
        """Stamp last_health_check_at + last_health_status after a verify probe."""
        from datetime import datetime, timezone

        health_status = "ok" if status_code == 200 else str(status_code)
        async with self._sf() as session:
            from sqlalchemy import text
            await session.execute(
                text(
                    "UPDATE ai_keys "
                    "SET last_health_check_at = :now, last_health_status = :hs, updated_at = :now "
                    "WHERE id = :id"
                ),
                {"id": key_id, "now": datetime.now(tz=timezone.utc), "hs": health_status},
            )
            await session.commit()

    async def mark_key_rotated_out(self, key_id: UUID) -> None:
        """Demote a key: status=rotated_out, is_default=false, rotated_at=now."""
        from datetime import datetime, timezone

        async with self._sf() as session:
            from sqlalchemy import text
            await session.execute(
                text(
                    "UPDATE ai_keys "
                    "SET status = 'rotated_out', is_default = false, "
                    "rotated_at = :now, updated_at = :now "
                    "WHERE id = :id"
                ),
                {"id": key_id, "now": datetime.now(tz=timezone.utc)},
            )
            await session.commit()

    # --- Audit -------------------------------------------------------------
    async def write_audit(self, entry: AuditEntry) -> None:
        """Ghi một bản ghi audit log.

        Phase D2 universal coverage: when the owning bot opted into
        ``plan_limits.pii_redaction_universal=True`` AND a redactor is
        wired via DI, the ``before`` / ``after`` JSONB payloads and
        ``reason`` text are masked BEFORE the row hits Postgres. The
        masking is best-effort — any redactor failure degrades silent
        and the original payload still persists (auditors MUST see
        every mutation; compliance signal is the structured
        ``pii_redacted`` event emitted alongside).

        @param entry: AuditEntry cần lưu
        """
        before = entry.before
        after = entry.after
        reason = entry.reason

        bot_cfg = await self._lookup_audit_bot_cfg(entry)
        if bot_cfg is not None and self._pii_redactor is not None:
            before = redact_mapping(
                before,
                redactor=self._pii_redactor,
                bot_cfg=bot_cfg,
                surface=PII_SURFACE_AUDIT_LOG,
                record_tenant_id=entry.record_tenant_id,
                record_bot_id=entry.record_bot_id,
                extra={"audit_field": "before", "audit_action": entry.action},
            )
            after = redact_mapping(
                after,
                redactor=self._pii_redactor,
                bot_cfg=bot_cfg,
                surface=PII_SURFACE_AUDIT_LOG,
                record_tenant_id=entry.record_tenant_id,
                record_bot_id=entry.record_bot_id,
                extra={"audit_field": "after", "audit_action": entry.action},
            )
            reason = redact_text(
                reason,
                redactor=self._pii_redactor,
                bot_cfg=bot_cfg,
                surface=PII_SURFACE_AUDIT_LOG,
                record_tenant_id=entry.record_tenant_id,
                record_bot_id=entry.record_bot_id,
                extra={"audit_field": "reason", "audit_action": entry.action},
            )

        async with self._sf() as session:
            # alembic 010g: row_hash chain populated by insert_audit_row.
            await insert_audit_row(
                session,
                record_tenant_id=entry.record_tenant_id,
                workspace_id=WORKSPACE_SYSTEM_SLUG,
                actor_user_id=entry.actor_user_id,
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=str(entry.resource_id),
                before_json=before,
                after_json=after,
                reason=reason,
                trace_id=entry.trace_id,
            )
            await session.commit()

    async def _lookup_audit_bot_cfg(self, entry: AuditEntry) -> Any | None:
        """Resolve the bot config that gates universal redaction.

        Returns ``None`` (⇒ no masking) when:
          * no ``bot_repo`` injected,
          * ``record_bot_id`` missing (tenant-scoped audit row), OR
          * the lookup itself fails (must NEVER 5xx an audit write).
        """
        if self._bot_repo is None or entry.record_bot_id is None:
            return None
        try:
            return await self._bot_repo.get_by_id(
                entry.record_bot_id,
                record_tenant_id=entry.record_tenant_id,
            )
        except (SQLAlchemyError, AttributeError, TypeError):
            # bot-repo DB failure or stub repo without get_by_id MUST
            # never 5xx the audit write. Skip universal coverage; tenant
            # still sees pre-D2 behaviour. Compliance signal: zero
            # `pii_redacted` event for this row.
            return None

    async def list_audit(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId | None = None,
        limit: int = 100,
    ) -> Sequence[dict[str, Any]]:
        """Lấy danh sách audit log theo tenant, mới nhất trước.
        @param tenant_id: ID tenant
        @param limit: số bản ghi tối đa
        @return: danh sách dict audit entries
        """
        tid = self._ensure(record_tenant_id)
        async with self._sf() as session:
            stmt = select(AuditLogModel).where(AuditLogModel.record_tenant_id == tid)
            # record_bot_id filter retained for API-compat; maps to resource_id
            # when caller passes a bot UUID.
            if record_bot_id:
                stmt = stmt.where(AuditLogModel.resource_id == str(record_bot_id))
            stmt = stmt.order_by(AuditLogModel.created_at.desc()).limit(limit)
            rows = (await session.execute(stmt)).scalars().all()
            return [
                {
                    "id": r.id,
                    "actor_user_id": r.actor_user_id,
                    "action": r.action,
                    "resource_type": r.resource_type,
                    "resource_id": r.resource_id,
                    "before": r.before_json,
                    "after": r.after_json,
                    "reason": r.reason,
                    "trace_id": r.trace_id,
                    "created_at": r.created_at,
                }
                for r in rows
            ]


__all__ = ["SqlAlchemyAIConfigRepository"]
