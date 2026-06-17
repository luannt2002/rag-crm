"""Pure domain unit tests — no IO."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ragbot.domain.entities.citation import Citation, validate_citations
from ragbot.domain.entities.conversation import Conversation
from ragbot.domain.entities.document import Document
from ragbot.domain.entities.message import Message
from ragbot.domain.value_objects.idempotency_key import (
    build_idempotency_key,
    for_chat_message,
)
from ragbot.domain.value_objects.tenant_scope import TenantScope
from ragbot.domain.value_objects.versioning import (
    AuthorityScore,
    ValidityWindow,
    compute_freshness,
)
from ragbot.shared.errors import (
    CitationHallucinated,
    InvalidDocumentState,
    InvariantViolation,
    TenantIsolationViolation,
)
from ragbot.shared.types import ChunkId


# --- Tenant scope -----------------------------------------------------------
def test_tenant_scope_requires_tenant() -> None:
    with pytest.raises(TenantIsolationViolation):
        TenantScope(record_tenant_id=None)  # type: ignore[arg-type]


def test_tenant_scope_cache_prefix(tenant_id, bot_id):
    scope = TenantScope(record_tenant_id=tenant_id, record_bot_id=bot_id)
    prefix = scope.cache_key_prefix()
    assert str(tenant_id) in prefix
    assert str(bot_id) in prefix


# --- Idempotency ------------------------------------------------------------
def test_idempotency_key_deterministic() -> None:
    a = build_idempotency_key("a", "b", "c")
    b = build_idempotency_key("a", "b", "c")
    assert a == b
    assert len(a) == 64


def test_idempotency_for_chat_includes_user(tenant_id, bot_id) -> None:
    k1 = for_chat_message(
        record_tenant_id=str(tenant_id), record_bot_id=str(bot_id), user_id="u1", external_message_id="x",
    )
    k2 = for_chat_message(
        record_tenant_id=str(tenant_id), record_bot_id=str(bot_id), user_id="u2", external_message_id="x",
    )
    assert k1 != k2


# --- Versioning -------------------------------------------------------------
def test_authority_score_bounds() -> None:
    AuthorityScore(0.5)
    with pytest.raises(InvariantViolation):
        AuthorityScore(1.5)


def test_validity_window_validation() -> None:
    a = datetime(2026, 1, 1, tzinfo=UTC)
    b = datetime(2026, 1, 2, tzinfo=UTC)
    ValidityWindow(valid_from=a, valid_until=b)
    with pytest.raises(InvariantViolation):
        ValidityWindow(valid_from=b, valid_until=a)


def test_freshness_decay() -> None:
    fresh = compute_freshness(age_days=0)
    old = compute_freshness(age_days=180, half_life_days=90)
    assert fresh == 1.0
    assert old < fresh


# --- Document state machine -------------------------------------------------
def test_document_state_transition(tenant_id, bot_id) -> None:
    when = datetime(2026, 1, 1, tzinfo=UTC)
    doc = Document.new_draft(
        record_tenant_id=tenant_id,
        record_bot_id=bot_id,
        source_url="https://example.com/x.pdf",
        document_name="X",
        tool_name="x",
        mime_type="application/pdf",
        language="vi",
        content_hash="hash1",
        authority_score=AuthorityScore(0.5),
        validity_window=None,
        acl=(),
        created_at=when,
    )
    assert doc.state == "DRAFT"
    pub = doc.publish()
    assert pub.state == "PUBLISHED"
    arch = pub.archive()
    assert arch.state == "ARCHIVED"
    with pytest.raises(InvalidDocumentState):
        arch.publish()  # cannot go back


# --- Conversation merge -----------------------------------------------------
def test_conversation_merge_consecutive_users(
    tenant_id, bot_id, user_id, clock,
) -> None:
    conv = Conversation.new(
        record_tenant_id=tenant_id, record_bot_id=bot_id, connect_id=user_id,
        channel="api", when=clock.now(),
    )
    for content in ["Bao giờ", "giao ngay", "còn hàng?"]:
        msg = Message.new_user_message(
            conversation_id=conv.id,
            record_tenant_id=tenant_id,
            record_bot_id=bot_id,
            content=content,
            channel="api",
            created_at=clock.now(),
        )
        conv = conv.add_message(msg)

    history = conv.history_for_llm()
    assert len(history) == 1
    assert "Bao giờ" in history[0].content
    assert "giao ngay" in history[0].content


# --- Citation guard ---------------------------------------------------------
def test_citation_validation_blocks_hallucination() -> None:
    from uuid import uuid4
    real_chunk = ChunkId(uuid4())
    fake_chunk = ChunkId(uuid4())
    doc_id = uuid4()

    cit_real = Citation(
        document_id=doc_id, chunk_id=real_chunk, tool_name="x", quote_span="q",
    )
    cit_fake = Citation(
        document_id=doc_id, chunk_id=fake_chunk, tool_name="x", quote_span="q",
    )

    validate_citations([cit_real], frozenset({real_chunk}))
    with pytest.raises(CitationHallucinated):
        validate_citations([cit_fake], frozenset({real_chunk}))
