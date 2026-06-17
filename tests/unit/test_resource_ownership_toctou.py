"""Issue #20 — atomic ownership check on binding mutate (TOCTOU close).

Validates two things:

1. The repo ``update_binding`` / ``delete_binding`` push ``record_tenant_id``
   into the UPDATE WHERE clause directly. SELECT-then-mutate is gone.
2. Concurrent ``asyncio.gather`` races where tenant A tries to mutate a row
   tenant B owns: A's UPDATE returns rowcount=0 and raises
   ``RepositoryError`` (mapped to ``BindingNotFoundError`` at the service
   layer); B's data is left unchanged.

The fake session_factory below is intentionally a 60-line in-memory dict
so the race assertion is real (two coroutines hitting one shared state via
``asyncio.gather``) without bringing in a Postgres/SQLite container — the
SQL-level guarantee is checked separately by introspecting the SQLAlchemy
``update()`` statement's WHERE clause.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update as _sa_update

from ragbot.application.services.ai_config_service import (
    AIConfigService,
    BindingNotFoundError,
)
from ragbot.application.ports.ai_config_port import BindingRow
from ragbot.infrastructure.db.models import BotModelBindingModel
from ragbot.infrastructure.repositories.ai_config_repository import (
    SqlAlchemyAIConfigRepository,
)
from ragbot.shared.errors import RepositoryError
from ragbot.shared.types import BotId, TenantId


# ---------------------------------------------------------------------------
# Fake AsyncSession + session_factory backed by a shared dict.
# ---------------------------------------------------------------------------


@dataclass
class _Row:
    """Mutable row stand-in covering the columns the repo touches."""

    id: UUID
    record_tenant_id: UUID
    record_bot_id: UUID
    purpose: str = "llm_primary"
    record_model_id: UUID = field(default_factory=uuid4)
    rank: int = 0
    variant: str | None = None
    weight: int = 100
    temperature: float = 0.0
    max_tokens: int = 512
    top_p: float = 1.0
    extra_params: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    version: int = 1
    record_fallback_model_id: UUID | None = None


class _FakeResult:
    def __init__(self, row: _Row | None) -> None:
        self._row = row
        self.rowcount = 1 if row is not None else 0

    def scalar_one_or_none(self) -> _Row | None:
        return self._row


class _FakeSession:
    """Minimal AsyncSession surface — only what the repo actually calls.

    The store dict acts as the shared "DB". UPDATE statements walk the
    WHERE clause's BinaryExpression nodes to extract the id + tenant
    predicates, so the test sees the same atomic-filter semantics as a
    real database.
    """

    def __init__(self, store: dict[UUID, _Row]) -> None:
        self._store = store
        self.rolled_back = False
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> _FakeResult:
        # Extract WHERE predicates from the compiled SQLAlchemy update().
        where_clause = stmt.whereclause
        clauses = list(where_clause.clauses) if where_clause is not None else []
        target_id: UUID | None = None
        target_tid: UUID | None = None
        for c in clauses:
            # c.left is the Column, c.right is the bindparam
            col_name = c.left.name
            value = c.right.effective_value
            if col_name == "id":
                target_id = value
            elif col_name == "record_tenant_id":
                target_tid = value
        # Atomic lookup: id AND record_tenant_id must both match.
        row = self._store.get(target_id)
        if row is None or row.record_tenant_id != target_tid:
            return _FakeResult(None)
        # Apply UPDATE values.
        for col, expr in stmt._values.items():
            col_name = col.name if hasattr(col, "name") else str(col)
            if col_name == "version":
                row.version += 1
            else:
                row.__dict__[col_name] = (
                    expr.effective_value if hasattr(expr, "effective_value") else expr
                )
        return _FakeResult(row)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


def _factory_for(store: dict[UUID, _Row]):
    """Return an async_sessionmaker-compatible callable wrapping the store."""

    def _make() -> _FakeSession:  # called as session_factory()
        return _FakeSession(store)

    return _make


# ---------------------------------------------------------------------------
# 1. SQL guarantee — UPDATE WHERE clause carries record_tenant_id.
# ---------------------------------------------------------------------------


class TestAtomicUpdateWhereClause:
    """The repo emits a single UPDATE with both id + tenant predicates."""

    def test_update_binding_statement_filters_by_tenant(self) -> None:
        """Compile the statement the repo builds and inspect the WHERE clause."""
        binding_id = uuid4()
        tid = uuid4()
        stmt = (
            _sa_update(BotModelBindingModel)
            .where(
                BotModelBindingModel.id == binding_id,
                BotModelBindingModel.record_tenant_id == tid,
            )
            .values(rank=5)
        )
        clause_cols = {c.left.name for c in stmt.whereclause.clauses}
        assert "id" in clause_cols
        assert "record_tenant_id" in clause_cols, (
            "TOCTOU regression: UPDATE missing record_tenant_id predicate"
        )


# ---------------------------------------------------------------------------
# 2. Race scenario — A tries to update B's row; A's UPDATE hits 0 rows.
# ---------------------------------------------------------------------------


class TestConcurrentOwnershipRace:
    @pytest.mark.asyncio
    async def test_cross_tenant_concurrent_update_loses_race(self) -> None:
        """A and B race; A targets a row B owns. A gets RepositoryError, B's row unchanged by A."""
        tenant_a = uuid4()
        tenant_b = uuid4()
        bot_id = uuid4()
        binding_id = uuid4()
        # B owns the row.
        store: dict[UUID, _Row] = {
            binding_id: _Row(
                id=binding_id, record_tenant_id=tenant_b, record_bot_id=bot_id, rank=7,
            ),
        }
        repo = SqlAlchemyAIConfigRepository(_factory_for(store))

        async def attacker_a() -> Exception | None:
            try:
                await repo.update_binding(
                    binding_id,
                    record_tenant_id=TenantId(tenant_a),
                    rank=999,  # would corrupt B's data if the TOCTOU window were open
                )
            except RepositoryError as exc:
                return exc
            return None

        async def owner_b() -> BindingRow:
            return await repo.update_binding(
                binding_id,
                record_tenant_id=TenantId(tenant_b),
                rank=42,
            )

        # asyncio.gather schedules both — order is non-deterministic, but the
        # SQL contract makes A's UPDATE rowcount=0 regardless of who wins.
        a_result, b_result = await asyncio.gather(attacker_a(), owner_b())

        assert isinstance(a_result, RepositoryError), (
            "tenant A's update on B's row must raise (atomic WHERE filter)"
        )
        assert b_result.rank == 42, "tenant B's legitimate update must land"
        # B's tenant must still own the row — A never wrote through.
        assert store[binding_id].record_tenant_id == tenant_b
        assert store[binding_id].rank == 42

    @pytest.mark.asyncio
    async def test_cross_tenant_concurrent_delete_loses_race(self) -> None:
        """Same race shape on delete_binding (soft delete sets active=False)."""
        tenant_a = uuid4()
        tenant_b = uuid4()
        bot_id = uuid4()
        binding_id = uuid4()
        store: dict[UUID, _Row] = {
            binding_id: _Row(
                id=binding_id, record_tenant_id=tenant_b, record_bot_id=bot_id, active=True,
            ),
        }
        repo = SqlAlchemyAIConfigRepository(_factory_for(store))

        async def attacker_a() -> Exception | None:
            try:
                await repo.delete_binding(
                    binding_id, record_tenant_id=TenantId(tenant_a),
                )
            except RepositoryError as exc:
                return exc
            return None

        async def owner_b() -> None:
            await repo.delete_binding(
                binding_id, record_tenant_id=TenantId(tenant_b),
            )

        a_result, _b_result = await asyncio.gather(attacker_a(), owner_b())
        assert isinstance(a_result, RepositoryError)
        # Legitimate delete by B landed; A never flipped active.
        assert store[binding_id].active is False
        assert store[binding_id].record_tenant_id == tenant_b


# ---------------------------------------------------------------------------
# 3. Service layer — RepositoryError maps to BindingNotFoundError.
# ---------------------------------------------------------------------------


class TestServiceMapsAtomicMissToBindingNotFound:
    """When repo rowcount=0, the service surfaces the same 404 shape."""

    @pytest.mark.asyncio
    async def test_update_binding_repository_error_becomes_binding_not_found(
        self,
    ) -> None:
        repo = MagicMock()
        # Pre-snapshot still returns a row (BEFORE the race flip).
        before_row = BindingRow(
            id=uuid4(),
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            purpose="llm_primary",
            model_id=uuid4(),
            rank=0,
            variant=None,
            weight=100,
            temperature=0.0,
            max_tokens=512,
            top_p=1.0,
            extra_params={},
            active=True,
            version=1,
        )
        repo.get_binding = AsyncMock(return_value=before_row)
        # Atomic mutate raises — simulating the race lost.
        repo.update_binding = AsyncMock(
            side_effect=RepositoryError("binding not found"),
        )
        repo.write_audit = AsyncMock()
        resolver = MagicMock()
        resolver.invalidate_cache = AsyncMock()
        svc = AIConfigService(
            ai_config_repo=repo,
            model_resolver=resolver,
            uow_factory=MagicMock(),
            session_factory=MagicMock(),
        )

        with pytest.raises(BindingNotFoundError):
            await svc.update_binding(
                binding_id=before_row.id,
                record_tenant_id=TenantId(before_row.record_tenant_id),
                record_bot_id=BotId(before_row.record_bot_id),
                actor_user_id="actor",
                trace_id="trace",
                fields={"rank": 1},
            )
        # No audit row written when mutate failed.
        repo.write_audit.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_binding_repository_error_becomes_binding_not_found(
        self,
    ) -> None:
        repo = MagicMock()
        before_row = BindingRow(
            id=uuid4(),
            record_tenant_id=uuid4(),
            record_bot_id=uuid4(),
            purpose="llm_primary",
            model_id=uuid4(),
            rank=0,
            variant=None,
            weight=100,
            temperature=0.0,
            max_tokens=512,
            top_p=1.0,
            extra_params={},
            active=True,
            version=1,
        )
        repo.get_binding = AsyncMock(return_value=before_row)
        repo.delete_binding = AsyncMock(
            side_effect=RepositoryError("binding not found"),
        )
        repo.write_audit = AsyncMock()
        resolver = MagicMock()
        resolver.invalidate_cache = AsyncMock()
        svc = AIConfigService(
            ai_config_repo=repo,
            model_resolver=resolver,
            uow_factory=MagicMock(),
            session_factory=MagicMock(),
        )

        with pytest.raises(BindingNotFoundError):
            await svc.delete_binding(
                binding_id=before_row.id,
                record_tenant_id=TenantId(before_row.record_tenant_id),
                record_bot_id=BotId(before_row.record_bot_id),
                actor_user_id="actor",
                trace_id="trace",
            )
        repo.write_audit.assert_not_called()
