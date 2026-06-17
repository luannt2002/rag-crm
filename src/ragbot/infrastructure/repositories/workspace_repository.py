"""Workspace entity repository (ADR-W2-D2).

Slug → first-class row lookup, listing, and get-or-create. Tenant-scoped:
every query carries ``record_tenant_id`` and the session inherits the RLS
``app.tenant_id`` GUC via the bootstrap-attached hook (ADR-W1-D3), so even
a bare-session read cannot cross tenants once the app runs as the
NOBYPASSRLS role.

The entity is a reference beside the canonical ``bots.workspace_id`` slug —
it does NOT replace the 4-key identity. ``ensure`` lets the upload / bot-
create paths lazily register a workspace the first time a new slug appears,
so the entity stays in step with the slugs already in use without a
separate provisioning call.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ragbot.infrastructure.db.models import WorkspaceModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository


class WorkspaceRepository(TenantScopedRepository):
    """Tenant-scoped CRUD for ``workspaces``."""

    async def lookup(
        self, *, record_tenant_id: UUID, slug: str,
    ) -> WorkspaceModel | None:
        """Return the live workspace for ``(tenant, slug)`` or ``None``.

        Soft-deleted rows (``deleted_at`` set) are excluded — a slug whose
        workspace was offboarded resolves to ``None``.
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            row = await session.execute(
                select(WorkspaceModel).where(
                    WorkspaceModel.record_tenant_id == tid,
                    WorkspaceModel.slug == slug,
                    WorkspaceModel.deleted_at.is_(None),
                ),
            )
            return row.scalar_one_or_none()

    async def list_for_tenant(
        self, *, record_tenant_id: UUID,
    ) -> list[WorkspaceModel]:
        """Return all live workspaces for the tenant, oldest first."""
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            rows = await session.execute(
                select(WorkspaceModel)
                .where(
                    WorkspaceModel.record_tenant_id == tid,
                    WorkspaceModel.deleted_at.is_(None),
                )
                .order_by(WorkspaceModel.created_at.asc()),
            )
            return list(rows.scalars().all())

    async def ensure(
        self, *, record_tenant_id: UUID, slug: str, name: str | None = None,
    ) -> WorkspaceModel:
        """Get-or-create the workspace for ``(tenant, slug)``.

        Idempotent: a concurrent create loses the unique-constraint race and
        we re-read the winner. ``name`` defaults to the slug (owners rename
        via the control plane).
        """
        existing = await self.lookup(record_tenant_id=record_tenant_id, slug=slug)
        if existing is not None:
            return existing

        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            session.add(
                WorkspaceModel(
                    record_tenant_id=tid, slug=slug, name=name or slug,
                ),
            )
            try:
                await session.commit()
            except IntegrityError:
                # Concurrent create lost the uq_workspaces_tenant_slug race —
                # roll back and re-read the committed winner below.
                await session.rollback()
        # Re-read so the returned row reflects the committed state regardless
        # of who won the create race.
        winner = await self.lookup(record_tenant_id=record_tenant_id, slug=slug)
        if winner is None:  # pragma: no cover — only if the row vanished mid-flight
            raise RuntimeError(
                f"workspace ensure failed for tenant={tid} slug={slug}",
            )
        return winner


__all__ = ["WorkspaceRepository"]
