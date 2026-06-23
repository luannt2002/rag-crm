"""F-1 IDOR-write fence: document/conversation ``save()`` must mutate ONLY a
row owned by the request tenant.

Root cause (pre-fix): ``save()`` resolved the existing row by primary key alone
(``session.get(Model, id)``) and then mutated the attributes of the in-memory
object. With RLS inert in production (app on a BYPASSRLS DSN), nothing fenced a
cross-tenant overwrite: a known document/conversation UUID belonging to tenant B
could be addressed by tenant A and have its content columns clobbered.

Post-fix: the UPDATE branch is a single statement filtered on BOTH the primary
key AND ``record_tenant_id`` (``RETURNING ...``). A foreign-tenant id matches
zero rows → falls through to INSERT under the request tenant → no clobber.

These tests drive ``save()`` with a fake AsyncSession that records the compiled
UPDATE and simulates the "0 rows / 1 row" RETURNING outcomes, so the real
branch logic in the repository is exercised without a live database.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.document import Document
from ragbot.domain.entities.message import Message
from ragbot.domain.value_objects.versioning import AuthorityScore
from ragbot.infrastructure.db.models import (
    ConversationModel,
    DocumentModel,
    MessageModel,
)
from ragbot.infrastructure.repositories.conversation_repository import (
    SqlAlchemyConversationRepository,
)
from ragbot.infrastructure.repositories.document_repository import (
    SqlAlchemyDocumentRepository,
)


class _FakeResult:
    """Mimics the slice of SQLAlchemy ``Result`` the repos use."""

    def __init__(self, scalar: Any = None, rows: list[Any] | None = None) -> None:
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self) -> Any:
        return self._scalar

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """Records executed statements and the objects added.

    ``update_returns`` controls what the fenced UPDATE's RETURNING yields:
    ``None`` → 0 rows (absent / foreign tenant), a value → 1 row (updated).
    SELECT statements (existing-message-id probe) return an empty set.
    """

    def __init__(self, *, update_returns: Any) -> None:
        self._update_returns = update_returns
        self.executed: list[Any] = []
        self.added: list[Any] = []
        self.committed = False

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, statement: Any, *_a: object, **_k: object) -> _FakeResult:
        self.executed.append(statement)
        verb = statement.__visit_name__  # "update" | "select"
        if verb == "update":
            return _FakeResult(scalar=self._update_returns)
        return _FakeResult(rows=[])

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


def _factory(session: _FakeSession):
    """Return a zero-arg callable matching ``async_sessionmaker`` usage."""

    def _make() -> _FakeSession:
        return session

    return _make


def _compiled_where(statement: Any) -> str:
    """Render the WHERE clause of an UPDATE for substring assertions."""
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": False},
        )
    )


def _make_document(*, doc_id: uuid.UUID, tenant_id: uuid.UUID) -> Document:
    return Document(
        id=doc_id,
        record_tenant_id=tenant_id,
        record_bot_id=uuid.uuid4(),
        source_url="http://example/doc",
        document_name="d",
        tool_name="t",
        mime_type="text/plain",
        language="vi",
        state="active",
        version=1,
        content_hash="h",
        authority_score=AuthorityScore(0.5),
        validity_window=None,
        superseded_by=None,
        acl=(),
        created_at=datetime.now(tz=UTC),
        updated_at=datetime.now(tz=UTC),
        metadata={},
    )


@pytest.mark.asyncio
async def test_document_save_update_is_tenant_fenced() -> None:
    """The UPDATE statement filters on id AND record_tenant_id (not PK only)."""
    tenant = uuid.uuid4()
    doc = _make_document(doc_id=uuid.uuid4(), tenant_id=tenant)
    session = _FakeSession(update_returns=doc.id)  # 1 row → "updated"
    repo = SqlAlchemyDocumentRepository(_factory(session))  # type: ignore[arg-type]

    await repo.save(doc, record_tenant_id=tenant, workspace_id="ws")

    updates = [s for s in session.executed if s.__visit_name__ == "update"]
    assert len(updates) == 1, "save must issue exactly one fenced UPDATE"
    where_sql = _compiled_where(updates[0])
    assert "documents.id" in where_sql
    assert "record_tenant_id" in where_sql, "UPDATE missing tenant fence"
    # Row existed for this tenant → updated in place, no INSERT.
    assert not any(isinstance(o, DocumentModel) for o in session.added)
    assert session.committed


@pytest.mark.asyncio
async def test_document_save_foreign_tenant_inserts_no_clobber() -> None:
    """A foreign-tenant id (UPDATE returns 0 rows) must INSERT a fresh row
    under the request tenant rather than overwrite the foreign row."""
    tenant = uuid.uuid4()
    doc = _make_document(doc_id=uuid.uuid4(), tenant_id=tenant)
    session = _FakeSession(update_returns=None)  # 0 rows → absent/foreign
    repo = SqlAlchemyDocumentRepository(_factory(session))  # type: ignore[arg-type]

    await repo.save(doc, record_tenant_id=tenant, workspace_id="ws")

    inserted = [o for o in session.added if isinstance(o, DocumentModel)]
    assert len(inserted) == 1, "0-row UPDATE must fall through to INSERT"
    assert inserted[0].record_tenant_id == tenant
    assert session.committed


def _make_conversation(
    *, conv_id: uuid.UUID, tenant_id: uuid.UUID, msg_tenant_id: uuid.UUID
) -> Conversation:
    msg = Message(
        id=uuid.uuid4(),
        conversation_id=conv_id,
        record_tenant_id=msg_tenant_id,
        record_bot_id=uuid.uuid4(),
        role="user",
        content="hi",
        channel="api",
        created_at=datetime.now(tz=UTC),
        citations=(),
        metadata={},
    )
    return Conversation(
        id=conv_id,
        record_tenant_id=tenant_id,
        record_bot_id=uuid.uuid4(),
        connect_id="u1",
        channel="api",
        messages=(msg,),
        rolling_summary="",
        turn_count=1,
        created_at=datetime.now(tz=UTC),
        last_message_at=datetime.now(tz=UTC),
        metadata={},
    )


@pytest.mark.asyncio
async def test_conversation_save_update_is_tenant_fenced() -> None:
    """Conversation UPDATE filters on id AND record_tenant_id."""
    tenant = uuid.uuid4()
    conv = _make_conversation(
        conv_id=uuid.uuid4(), tenant_id=tenant, msg_tenant_id=tenant
    )
    session = _FakeSession(update_returns="parent-ws")  # existing row, slug
    repo = SqlAlchemyConversationRepository(_factory(session))  # type: ignore[arg-type]

    await repo.save(conv, record_tenant_id=tenant, workspace_id="ws")

    updates = [s for s in session.executed if s.__visit_name__ == "update"]
    assert len(updates) == 1
    where_sql = _compiled_where(updates[0])
    assert "conversations.id" in where_sql
    assert "record_tenant_id" in where_sql, "UPDATE missing tenant fence"


@pytest.mark.asyncio
async def test_conversation_save_forces_request_tenant_on_message_insert() -> None:
    """An inserted message row carries the REQUEST tenant, never the message
    entity's own (possibly cross-tenant) record_tenant_id."""
    request_tenant = uuid.uuid4()
    foreign_tenant = uuid.uuid4()
    assert request_tenant != foreign_tenant
    conv = _make_conversation(
        conv_id=uuid.uuid4(),
        tenant_id=request_tenant,
        msg_tenant_id=foreign_tenant,  # smuggled cross-tenant message
    )
    session = _FakeSession(update_returns="parent-ws")  # existing parent
    repo = SqlAlchemyConversationRepository(_factory(session))  # type: ignore[arg-type]

    await repo.save(conv, record_tenant_id=request_tenant, workspace_id="ws")

    msgs = [o for o in session.added if isinstance(o, MessageModel)]
    assert len(msgs) == 1
    assert msgs[0].record_tenant_id == request_tenant, (
        "message INSERT must be forced to the request tenant, not the "
        "message entity's own tenant"
    )
    assert msgs[0].record_tenant_id != foreign_tenant


@pytest.mark.asyncio
async def test_conversation_save_foreign_tenant_inserts_no_clobber() -> None:
    """Foreign-tenant conversation id (UPDATE 0 rows) → INSERT new, no clobber."""
    tenant = uuid.uuid4()
    conv = _make_conversation(
        conv_id=uuid.uuid4(), tenant_id=tenant, msg_tenant_id=tenant
    )
    session = _FakeSession(update_returns=None)  # absent / foreign
    repo = SqlAlchemyConversationRepository(_factory(session))  # type: ignore[arg-type]

    await repo.save(conv, record_tenant_id=tenant, workspace_id="ws")

    convs = [o for o in session.added if isinstance(o, ConversationModel)]
    assert len(convs) == 1
    assert convs[0].record_tenant_id == tenant
