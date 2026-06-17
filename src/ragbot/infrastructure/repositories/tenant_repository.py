"""Tenant repository — read / patch per-tenant runtime policy + admin CRUD.

Internal queries use ``record_tenant_id`` (UUID PK). The integer
``tenant_id`` claim from upstream JWTs is kept in
``tenants.config['tenant_id_int']`` and returned with the policy so
callers can invalidate the int-keyed Redis cache.

This module is also the data-layer backing for the super-admin tenant
CRUD routes (``/admin/tenants``). The CRUD methods are kept here (rather
than splitting into a separate file) because they share the same
``tenants`` row + the same ``async_session`` / commit ownership pattern.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ragbot.infrastructure.db.models import BotModel, TenantModel


class TenantSlugConflictError(Exception):
    """Raised when ``tenants.name`` UNIQUE collides on create.

    The slug itself lives in ``tenants.config['slug']`` (no dedicated
    column yet — see admin schema docstring), but ``name`` is uniquely
    indexed at the DB level so duplicate names also surface here.
    Surfaced via :meth:`TenantRepository.create_tenant` so the route
    layer can map to HTTP 409.
    """


class TenantHasActiveBotsError(Exception):
    """Raised when soft-delete is attempted on a tenant with active bots.

    Soft-delete preserves the FK ``bots.record_tenant_id → tenants.id``
    (``ondelete=RESTRICT``) so we MUST check before flipping
    ``deleted_at``. The route maps this to HTTP 409 Conflict.
    """

    def __init__(self, active_bot_count: int) -> None:
        super().__init__(
            f"tenant has {active_bot_count} active bot(s); delete bots first",
        )
        self.active_bot_count = active_bot_count


class TenantRepository:
    """Per-tenant policy CRUD on the ``tenants`` row."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind to an active async session — caller owns the transaction."""
        self._session = session

    # ---------------------------------------------------------------
    # Legacy P33 policy methods (admin_tenant_policy)
    # ---------------------------------------------------------------
    async def get_policy(self, record_tenant_id: UUID) -> dict[str, Any] | None:
        """Read the 4 policy columns + name + JWT int mapping.

        Returns ``None`` if no row matches. ``tenant_id_int`` is sourced
        from ``tenants.config->>'tenant_id_int'`` and may be ``None`` for
        legacy rows that predate the JWT-int convention.
        """
        result = await self._session.execute(
            text(
                "SELECT id, name, bypass_rate_limit, rate_limit_per_min, "
                "monthly_token_cap, "
                "(config->>'tenant_id_int')::int AS tenant_id_int "
                "FROM tenants WHERE id = :tid",
            ),
            {"tid": record_tenant_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "record_tenant_id": str(row[0]),
            "name": row[1],
            "bypass_rate_limit": bool(row[2]),
            "rate_limit_per_min": row[3],
            "monthly_token_cap": row[4],
            "tenant_id_int": row[5],
        }

    async def update_policy(
        self,
        record_tenant_id: UUID,
        *,
        bypass_rate_limit: bool | None = None,
        rate_limit_per_min: int | None = None,
        monthly_token_cap: int | None = None,
    ) -> dict[str, Any] | None:
        """PATCH — only writes fields explicitly passed (``None`` skips).

        Returns the post-update row dict, or ``None`` if the tenant did
        not exist. Caller is responsible for invalidating the
        ``TenantConfigCache`` after a successful write.
        """
        fields: dict[str, Any] = {}
        if bypass_rate_limit is not None:
            fields["bypass_rate_limit"] = bypass_rate_limit
        if rate_limit_per_min is not None:
            fields["rate_limit_per_min"] = rate_limit_per_min
        if monthly_token_cap is not None:
            fields["monthly_token_cap"] = monthly_token_cap

        if not fields:
            # No-op PATCH — return the current row for the response.
            return await self.get_policy(record_tenant_id)

        # ORM update().values() — column names are ORM attributes, never strings.
        # ``updated_at`` has ``onupdate=func.now()`` on TenantModel so it
        # advances automatically; no manual SET needed.
        stmt = (
            update(TenantModel)
            .where(TenantModel.id == record_tenant_id)
            .values(**fields)
            .execution_options(synchronize_session=False)
        )
        result = await self._session.execute(stmt)
        if result.rowcount == 0:
            return None
        await self._session.commit()
        return await self.get_policy(record_tenant_id)

    # ---------------------------------------------------------------
    # Admin tenant CRUD (super-admin only)
    # ---------------------------------------------------------------
    @staticmethod
    def _row_to_dict(row: TenantModel) -> dict[str, Any]:
        """Project an ORM row to the API response dict.

        Slug lives in ``config['slug']`` (no dedicated column yet). Legacy
        rows may lack the key, in which case the response carries
        ``slug=None`` rather than raising.
        """
        cfg = dict(row.config or {})
        # ``allowed_origins`` is JSONB list[str]; legacy rows with NULL
        # collapse to empty list (cached / pre-migrated reads may still
        # surface NULL).
        origins_raw = getattr(row, "allowed_origins", None)
        origins_list = list(origins_raw or [])
        return {
            "record_tenant_id": row.id,
            "name": row.name,
            "slug": cfg.get("slug"),
            "config": cfg,
            "bypass_rate_limit": bool(row.bypass_rate_limit),
            "rate_limit_per_min": row.rate_limit_per_min,
            "monthly_token_cap": row.monthly_token_cap,
            "allowed_origins": origins_list,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "deleted_at": row.deleted_at,
        }

    async def create_tenant(
        self,
        *,
        name: str,
        slug: str,
        config: dict[str, Any] | None = None,
        upstream_tenant_id: int | None = None,
    ) -> dict[str, Any]:
        """Create a new tenant row.

        Slug is stored under ``config['slug']`` (no dedicated column yet —
        see admin schema docstring). Upstream INT id, when supplied, is
        also persisted in ``config['upstream_tenant_id']`` for the
        rolling-upgrade window with legacy NestJS senders.

        Raises :class:`TenantSlugConflictError` when the slug or
        ``tenants.name`` UNIQUE collides — the route maps to HTTP 409.
        """
        merged_config: dict[str, Any] = dict(config or {})
        merged_config["slug"] = slug
        if upstream_tenant_id is not None:
            merged_config["upstream_tenant_id"] = int(upstream_tenant_id)

        # Pre-flight slug duplicate check — slug is in JSONB so the schema
        # cannot UNIQUE-enforce it at the DB level. We rely on a SELECT
        # before INSERT inside the same session/transaction. Race risk
        # is bounded by the surrounding session; concurrent creates with
        # the same slug are still defended by ``tenants.name`` UNIQUE
        # when the caller derives name == slug.
        existing_slug = await self._session.execute(
            text(
                "SELECT 1 FROM tenants "
                "WHERE config->>'slug' = :slug AND deleted_at IS NULL "
                "LIMIT 1",
            ),
            {"slug": slug},
        )
        if existing_slug.fetchone() is not None:
            raise TenantSlugConflictError(f"slug already in use: {slug}")

        row = TenantModel(
            id=uuid.uuid4(),
            name=name,
            config=merged_config,
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            # ``tenants.name`` UNIQUE collision surfaces here.
            await self._session.rollback()
            raise TenantSlugConflictError(
                f"tenant name already in use: {name}",
            ) from exc
        await self._session.commit()
        await self._session.refresh(row)
        return self._row_to_dict(row)

    async def get_tenant(
        self, record_tenant_id: UUID, *, include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """Read a single tenant by UUID PK.

        ``include_deleted=False`` (default) hides soft-deleted rows so
        admin GET responses align with the "live" view. Pass ``True`` for
        forensic / rollback flows that need the historic row.
        """
        stmt = select(TenantModel).where(TenantModel.id == record_tenant_id)
        if not include_deleted:
            stmt = stmt.where(TenantModel.deleted_at.is_(None))
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return self._row_to_dict(row)

    async def list_tenants(
        self,
        *,
        limit: int,
        offset: int,
        search: str | None = None,
        include_deleted: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Paginated list of tenants.

        Search matches ``tenants.name ILIKE %search%`` — case-insensitive
        substring. Returns ``(items, total_matching)`` so the caller can
        render correct pagination UI without firing a second COUNT().
        """
        base = select(TenantModel)
        count_base = select(func.count()).select_from(TenantModel)
        if not include_deleted:
            base = base.where(TenantModel.deleted_at.is_(None))
            count_base = count_base.where(TenantModel.deleted_at.is_(None))
        if search:
            pattern = f"%{search}%"
            base = base.where(TenantModel.name.ilike(pattern))
            count_base = count_base.where(TenantModel.name.ilike(pattern))

        total = int(
            (await self._session.execute(count_base)).scalar_one() or 0,
        )
        stmt = (
            base.order_by(TenantModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._row_to_dict(r) for r in rows], total

    async def update_tenant(
        self,
        record_tenant_id: UUID,
        *,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        bypass_rate_limit: bool | None = None,
        rate_limit_per_min: int | None = None,
        monthly_token_cap: int | None = None,
        allowed_origins: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """PATCH a tenant row — returns ``(before, after)`` snapshot pair.

        Skips fields explicitly passed as ``None``. Returns ``None`` when
        the tenant does not exist (or has been soft-deleted). Caller is
        responsible for invalidating ``TenantConfigCache`` post-write.

        ``config``, when supplied, *replaces* the whole JSONB blob (admin
        intent — config is not array-merged here). Slug rotation is not
        supported — the create-time slug stays put even if the caller
        supplies a different ``config['slug']`` value (we re-pin from the
        existing row to defend the invariant).
        """
        stmt = select(TenantModel).where(
            TenantModel.id == record_tenant_id,
            TenantModel.deleted_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        before = self._row_to_dict(row)

        if name is not None:
            row.name = name
        if config is not None:
            new_cfg = dict(config)
            # Pin slug to its create-time value to defend the slug
            # invariant — admins cannot rotate slugs via PATCH.
            existing_slug = (row.config or {}).get("slug")
            if existing_slug is not None:
                new_cfg["slug"] = existing_slug
            row.config = new_cfg
        if bypass_rate_limit is not None:
            row.bypass_rate_limit = bool(bypass_rate_limit)
        if rate_limit_per_min is not None:
            row.rate_limit_per_min = int(rate_limit_per_min)
        if monthly_token_cap is not None:
            row.monthly_token_cap = int(monthly_token_cap)
        if allowed_origins is not None:
            # Replace whole list (admin-intent overwrite).
            row.allowed_origins = [str(o) for o in allowed_origins]

        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise TenantSlugConflictError(
                "tenant name conflict on update",
            ) from exc
        await self._session.commit()
        await self._session.refresh(row)
        after = self._row_to_dict(row)
        return before, after

    async def count_active_bots_for_tenant(
        self, record_tenant_id: UUID,
    ) -> int:
        """Return the number of non-soft-deleted bots for this tenant.

        Used as the gate before soft-deleting a tenant — non-zero =>
        409 Conflict (FK ``ondelete=RESTRICT`` would have raised on
        hard delete anyway, but soft-delete needs an explicit guard).
        """
        stmt = (
            select(func.count())
            .select_from(BotModel)
            .where(
                BotModel.record_tenant_id == record_tenant_id,
                BotModel.is_deleted.is_(False),
            )
        )
        return int(
            (await self._session.execute(stmt)).scalar_one() or 0,
        )

    async def soft_delete_tenant(
        self, record_tenant_id: UUID,
    ) -> dict[str, Any] | None:
        """Soft-delete by setting ``deleted_at = now()``.

        Returns the pre-delete row snapshot for audit, or ``None`` if the
        tenant did not exist or was already soft-deleted. Raises
        :class:`TenantHasActiveBotsError` if the tenant still owns any
        active bot.
        """
        stmt = select(TenantModel).where(
            TenantModel.id == record_tenant_id,
            TenantModel.deleted_at.is_(None),
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None

        active_bot_count = await self.count_active_bots_for_tenant(
            record_tenant_id,
        )
        if active_bot_count > 0:
            raise TenantHasActiveBotsError(active_bot_count)

        before = self._row_to_dict(row)
        row.deleted_at = datetime.now(tz=timezone.utc)
        try:
            await self._session.flush()
        except SQLAlchemyError:
            await self._session.rollback()
            raise
        await self._session.commit()
        return before


__all__ = [
    "TenantHasActiveBotsError",
    "TenantRepository",
    "TenantSlugConflictError",
]
