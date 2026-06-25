"""Document aggregate + Block + Chunk.

Ref: PLAN_04 §document.py / RAGBOT_MASTER §5.3 Document Lifecycle / §6.3 Block.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from uuid import uuid4

from ragbot.domain.value_objects.structural_path import StructuralPath
from ragbot.domain.value_objects.versioning import AuthorityScore, ValidityWindow
from ragbot.shared.errors import InvalidDocumentState, InvariantViolation
from ragbot.shared.types import (
    BlockType,
    BotId,
    ChunkId,
    ChunkingStrategyName,
    CorpusVersion,
    DocumentId,
    DocumentState,
    EmbeddingModelVersion,
    TenantId,
)

# State machine — allowed transitions (RAGBOT_MASTER §5.3)
_TRANSITIONS: dict[DocumentState, frozenset[DocumentState]] = {
    "active": frozenset({"PUBLISHED", "ARCHIVED", "INVALIDATED"}),  # default state from ingest
    "DRAFT": frozenset({"PUBLISHED", "ARCHIVED", "INVALIDATED"}),
    "PUBLISHED": frozenset({"UPDATED", "SUPERSEDED", "ARCHIVED", "INVALIDATED"}),
    "UPDATED": frozenset({"PUBLISHED", "SUPERSEDED", "ARCHIVED", "INVALIDATED"}),
    "SUPERSEDED": frozenset({"ARCHIVED", "PURGED"}),
    "ARCHIVED": frozenset({"PURGED"}),
    "PURGED": frozenset(),
    "INVALIDATED": frozenset({"ARCHIVED"}),
}


@dataclass(frozen=True, slots=True)
class Block:
    """Parsed block — output of OCR / structure detection (PLAN_04 / AdapChunk §6.3)."""

    type: BlockType
    content: str
    is_atomic: bool
    context_before: str = ""
    context_after: str = ""
    page_number: int | None = None
    ocr_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Chunk:
    """Indexable chunk — one row in vector store + metadata."""

    id: ChunkId
    document_id: DocumentId
    record_tenant_id: TenantId
    record_bot_id: BotId
    strategy_used: ChunkingStrategyName
    block_types: tuple[BlockType, ...]
    narrated_text: str
    contextual_prefix: str
    original_content: str | None
    structural_path: StructuralPath | None
    page_number: int | None
    content_hash: str
    embedding_model_version: EmbeddingModelVersion
    corpus_version: CorpusVersion
    ingested_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def text_for_embedding(self) -> str:
        """Tạo văn bản dùng cho embedding (prefix + narrated text).
        @return: chuỗi text sẵn sàng để embed
        """
        if self.contextual_prefix:
            return f"{self.contextual_prefix}\n\n{self.narrated_text}"
        return self.narrated_text


@dataclass(frozen=True, slots=True)
class Document:
    """Document aggregate root."""

    id: DocumentId
    record_tenant_id: TenantId
    record_bot_id: BotId
    source_url: str
    document_name: str
    tool_name: str  # slugify(document_name)
    mime_type: str
    language: str
    state: DocumentState
    version: int
    content_hash: str
    authority_score: AuthorityScore
    validity_window: ValidityWindow | None
    superseded_by: DocumentId | None
    acl: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.tool_name:
            raise InvariantViolation("Document.tool_name is required")
        # ``source_url`` is NOT required: a bytes-uploaded document legitimately
        # has no URL (its content lives in the ``raw_content`` column). Requiring
        # it here rejected every bytes-upload on entity load, breaking
        # rechunk-by-id for those docs. Whether the document has a REBUILDABLE
        # content source (source_url OR raw_content) is a rechunk precondition,
        # validated at the use-case layer (``_assert_reingestable``) which has DB
        # access — it is not a construction-time domain invariant.

    # --- State machine ------------------------------------------------------
    def _transition(self, target: DocumentState) -> Document:
        """Chuyển trạng thái tài liệu theo state machine.
        @param target: trạng thái đích
        @return: Document mới với trạng thái đã chuyển
        """
        allowed = _TRANSITIONS.get(self.state, frozenset())
        if target not in allowed:
            raise InvalidDocumentState(
                f"cannot transition {self.state} -> {target}",
                details={"current": self.state, "target": target},
            )
        return replace(self, state=target, updated_at=self.updated_at)

    def publish(self) -> Document:
        """Chuyển tài liệu sang trạng thái PUBLISHED."""
        return self._transition("PUBLISHED")

    def update_version(self, *, new_hash: str, when: datetime) -> Document:
        """Cập nhật phiên bản tài liệu nếu nội dung thay đổi.
        @param new_hash: hash nội dung mới
        @return: Document mới hoặc self nếu hash không đổi
        """
        if new_hash == self.content_hash:
            return self  # no-op
        return replace(
            self,
            content_hash=new_hash,
            version=self.version + 1,
            state="UPDATED",
            updated_at=when,
        )

    def supersede_by(self, replacement: DocumentId) -> Document:
        """Đánh dấu tài liệu bị thay thế bởi phiên bản mới.
        @param replacement: ID tài liệu thay thế
        """
        return replace(
            self._transition("SUPERSEDED"),
            superseded_by=replacement,
        )

    def archive(self) -> Document:
        """Lưu trữ tài liệu (ARCHIVED)."""
        return self._transition("ARCHIVED")

    def purge(self) -> Document:
        """Xoá vĩnh viễn tài liệu (PURGED)."""
        return self._transition("PURGED")

    def invalidate(self) -> Document:
        """Đánh dấu tài liệu không còn hợp lệ (INVALIDATED)."""
        return self._transition("INVALIDATED")

    @classmethod
    def new_draft(
        cls,
        *,
        record_tenant_id: TenantId,
        record_bot_id: BotId,
        source_url: str,
        document_name: str,
        tool_name: str,
        mime_type: str,
        language: str,
        content_hash: str,
        authority_score: AuthorityScore,
        validity_window: ValidityWindow | None,
        acl: tuple[str, ...],
        created_at: datetime,
        metadata: dict[str, Any] | None = None,
    ) -> Document:
        """Tạo tài liệu mới ở trạng thái DRAFT.
        @param source_url: URL nguồn tài liệu
        @param document_name: tên hiển thị
        @return: Document instance mới
        """
        return cls(
            id=DocumentId(uuid4()),
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            source_url=source_url,
            document_name=document_name,
            tool_name=tool_name,
            mime_type=mime_type,
            language=language,
            state="DRAFT",
            version=1,
            content_hash=content_hash,
            authority_score=authority_score,
            validity_window=validity_window,
            superseded_by=None,
            acl=acl,
            created_at=created_at,
            updated_at=created_at,
            metadata=metadata or {},
        )


__all__ = ["Block", "Chunk", "Document"]
