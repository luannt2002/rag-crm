"""Document repository."""

from __future__ import annotations

from datetime import datetime as _dt

from sqlalchemy import select, text

from ragbot.application.ports.repository_ports import DocumentRepositoryPort
from ragbot.domain.entities.document import Document
from ragbot.domain.value_objects.versioning import AuthorityScore
from ragbot.infrastructure.db.models import DocumentModel
from ragbot.infrastructure.repositories._base import TenantScopedRepository
from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import (
    BotId,
    DocumentId,
    DocumentState,
    TenantId,
    WorkspaceId,
)


def _row_to_document(row: DocumentModel) -> Document:
    """Chuyển đổi ORM DocumentModel sang domain Document.
    @param row: bản ghi ORM
    @return: domain entity Document
    """
    # Migration 0010: authority_score / validity_window / superseded_by
    # dropped from DB. Reconstruct domain defaults — features unused.
    return Document(
        id=DocumentId(row.id),
        record_tenant_id=TenantId(row.record_tenant_id),
        record_bot_id=BotId(row.record_bot_id),
        source_url=row.source_url,
        document_name=row.document_name,
        tool_name=row.tool_name,
        mime_type=row.mime_type,
        language=row.language,
        state=row.state,  # type: ignore[arg-type]
        version=row.version,
        content_hash=row.content_hash,
        authority_score=AuthorityScore(0.5),
        validity_window=None,
        superseded_by=None,
        acl=tuple(row.acl or ()),
        created_at=row.created_at,
        updated_at=row.updated_at,
        metadata=dict(row.metadata_json or {}),
    )


def _document_to_row(doc: Document, *, workspace_id: WorkspaceId) -> DocumentModel:
    """Chuyển đổi domain Document sang ORM DocumentModel để persist.
    @param doc: domain entity Document
    @param workspace_id: slug nhánh — caller bơm từ bot config
    @return: ORM model DocumentModel
    """
    # Migration 0010: authority_score / validity_window / superseded_by not
    # persisted. Drop from ORM insert payload.
    return DocumentModel(
        id=doc.id,
        record_tenant_id=doc.record_tenant_id,
        workspace_id=workspace_id,
        record_bot_id=doc.record_bot_id,
        source_url=doc.source_url,
        document_name=doc.document_name,
        tool_name=doc.tool_name,
        mime_type=doc.mime_type,
        language=doc.language,
        state=doc.state,
        version=doc.version,
        content_hash=doc.content_hash,
        acl=list(doc.acl),
        metadata_json=dict(doc.metadata),
    )


class SqlAlchemyDocumentRepository(TenantScopedRepository, DocumentRepositoryPort):
    """Repository cho bảng documents — CRUD tài liệu theo tenant."""

    async def save(
        self,
        document: Document,
        *,
        record_tenant_id: TenantId,
        workspace_id: WorkspaceId,
    ) -> None:
        """Lưu hoặc cập nhật document vào DB.
        @param document: domain entity Document
        @param record_tenant_id: ID tenant (kiểm tra isolation)
        @param workspace_id: slug nhánh — bắt buộc khi tạo mới; khi UPDATE
            existing row ``workspace_id`` không thay đổi để tôn trọng
            hệ FK chain (slug = bots.workspace_id).
        """
        tid = self._ensure_tenant(record_tenant_id)
        if document.record_tenant_id != tid:
            raise TenantIsolationViolation("document tenant != request tenant")
        async with self._new_session() as session:
            existing = await session.get(DocumentModel, document.id)
            if existing is None:
                session.add(_document_to_row(document, workspace_id=workspace_id))
            else:
                existing.source_url = document.source_url
                existing.document_name = document.document_name
                existing.tool_name = document.tool_name
                existing.mime_type = document.mime_type
                existing.language = document.language
                existing.state = document.state
                existing.version = document.version
                existing.content_hash = document.content_hash
                existing.acl = list(document.acl)
                existing.metadata_json = dict(document.metadata)
            await session.commit()

    async def get_by_id(
        self,
        document_id: DocumentId,
        *,
        record_tenant_id: TenantId,
    ) -> Document | None:
        """Lấy document theo UUID.
        @param document_id: UUID document
        @return: Document hoặc None
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            row = await session.scalar(
                select(DocumentModel).where(
                    DocumentModel.id == document_id,
                    DocumentModel.record_tenant_id == tid,
                ),
            )
            return _row_to_document(row) if row else None

    async def get_by_source_url(
        self,
        record_bot_id: BotId,
        source_url: str,
        *,
        record_tenant_id: TenantId,
    ) -> Document | None:
        """Tìm document theo source URL trong phạm vi bot.

        @param record_bot_id: ID bot
        @param source_url: URL nguồn tài liệu
        @return: Document hoặc None

        260525 Bug #1 guard — raise ``MultipleResultsFound`` when >1 row
        matches. Previously ``session.scalar()`` silently returned the
        first row, leading to rechunk-wrong-doc on bots with multiple
        documents sharing a single ``source_url`` (e.g. Google Sheets
        workbook tabs with the same edit URL prefix). Callers that need
        to address a specific doc should use ``get_by_id`` instead.
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            # Fetch up to 2 rows to detect ambiguity without scanning the
            # full result set. ``scalars().all()`` keeps payload tiny.
            result = await session.execute(
                select(DocumentModel)
                .where(
                    DocumentModel.record_tenant_id == tid,
                    DocumentModel.record_bot_id == record_bot_id,
                    DocumentModel.source_url == source_url,
                )
                .limit(2),
            )
            rows = result.scalars().all()
            if len(rows) > 1:
                # Defensive — leak the ambiguity to the caller. The HTTP
                # layer translates this to a 409 with guidance to use
                # the rechunk-by-id endpoint.
                from ragbot.shared.errors import InvariantViolation  # noqa: PLC0415

                raise InvariantViolation(
                    f"get_by_source_url ambiguous: ≥2 documents match "
                    f"source_url={source_url!r} for bot={record_bot_id} "
                    f"in tenant={tid}. Use get_by_id / rechunk-by-id.",
                )
            return _row_to_document(rows[0]) if rows else None

    async def get_by_tool_name(
        self,
        record_bot_id: BotId,
        tool_name: str,
        *,
        record_tenant_id: TenantId,
    ) -> Document | None:
        """Tìm document theo tool_name trong phạm vi bot.
        @param record_bot_id: ID bot
        @param tool_name: slug name tài liệu
        @return: Document hoặc None
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            row = await session.scalar(
                select(DocumentModel).where(
                    DocumentModel.record_tenant_id == tid,
                    DocumentModel.record_bot_id == record_bot_id,
                    DocumentModel.tool_name == tool_name,
                ),
            )
            return _row_to_document(row) if row else None

    async def list_by_bot(
        self,
        record_bot_id: BotId,
        *,
        record_tenant_id: TenantId,
        state_filter: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> list[Document]:
        """Keyset pagination by created_at DESC. cursor = ISO datetime of last item."""
        tid = self._ensure_tenant(record_tenant_id)
        async with self._new_session() as session:
            stmt = (
                select(DocumentModel)
                .where(DocumentModel.record_tenant_id == tid, DocumentModel.record_bot_id == record_bot_id)
                .order_by(DocumentModel.created_at.desc())
                .limit(limit)
            )
            if state_filter:
                stmt = stmt.where(DocumentModel.state == state_filter)
            if cursor:
                stmt = stmt.where(DocumentModel.created_at < _dt.fromisoformat(cursor))
            rows = (await session.execute(stmt)).scalars().all()
            return [_row_to_document(r) for r in rows]

    async def find_chunks_by_document_ids(
        self,
        document_ids: list,
        *,
        record_bot_id,
        max_chunks_per_doc: int = 10,
    ) -> list[dict]:
        """Fetch chunks for a set of documents, capped per document.

        Used by the stats-index retrieve fallback path when stats rows have
        ``record_chunk_id=NULL`` (pre-2026-05-26 ingest did not backfill
        chunk FKs). Returns the first ``max_chunks_per_doc`` chunks per
        document by ``chunk_index ASC`` so grounded context is available
        for the generate node.

        Multi-tenant scope: ``record_bot_id`` filter is mandatory.
        """
        if not document_ids:
            return []
        async with self._new_session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT chunk_id, document_id, content, chunk_index,
                           document_name, source_url
                    FROM (
                        SELECT
                            dc.id AS chunk_id,
                            dc.record_document_id AS document_id,
                            dc.content AS content,
                            dc.chunk_index AS chunk_index,
                            d.document_name AS document_name,
                            d.source_url AS source_url,
                            ROW_NUMBER() OVER (
                                PARTITION BY dc.record_document_id
                                ORDER BY dc.chunk_index ASC
                            ) AS rn
                        FROM document_chunks dc
                        JOIN documents d ON d.id = dc.record_document_id
                        WHERE dc.record_document_id = ANY(:doc_ids)
                          AND dc.record_bot_id = :record_bot_id
                          AND dc.doc_deleted_at IS NULL
                    ) ranked
                    WHERE ranked.rn <= :cap
                    ORDER BY ranked.document_id, ranked.chunk_index
                    """
                ),
                {
                    "doc_ids": document_ids,
                    "record_bot_id": record_bot_id,
                    "cap": max_chunks_per_doc,
                },
            )
            return [
                {
                    "chunk_id": str(r.chunk_id),
                    "document_id": str(r.document_id),
                    "content": r.content or "",
                    "text": r.content or "",
                    "score": 1.0,
                    "document_name": r.document_name or "",
                    "chunk_index": int(r.chunk_index) if r.chunk_index is not None else 0,
                    "payload": {
                        "document_title": r.document_name or "",
                        "source_url": r.source_url or "",
                    },
                }
                for r in result
            ]

    async def find_chunks_by_ids(
        self,
        chunk_ids: list,
        *,
        record_bot_id,
    ) -> list[dict]:
        """Fetch document_chunks rows by primary-key IDs scoped to a single bot.

        Used by the stats-index retrieve path in ``query_graph.retrieve()``
        to attach the source chunks behind a price-range SQL hit back to the
        generate node as grounded context. Without this, ``retrieved_chunks``
        is empty after a stats hit and the generate node refuses to answer
        (OOS fallback) despite the SQL path returning real entities.

        Returns list of dicts matching the vector-retrieve shape so downstream
        nodes (rerank / grade / prompt_build) treat them identically:
            {chunk_id, document_id, content, text, score, document_name,
             chunk_index, payload}

        Scoping: ``record_bot_id`` filter is mandatory to prevent cross-bot
        leak — chunks belong to documents which belong to bots; we re-assert
        the bot scope at the SQL boundary even though stats_entities were
        already filtered by bot upstream.
        """
        if not chunk_ids:
            return []
        async with self._new_session() as session:
            result = await session.execute(
                text(
                    """
                    SELECT
                        dc.id AS chunk_id,
                        dc.record_document_id AS document_id,
                        dc.content AS content,
                        dc.chunk_index AS chunk_index,
                        d.document_name AS document_name,
                        d.source_url AS source_url
                    FROM document_chunks dc
                    JOIN documents d ON d.id = dc.record_document_id
                    WHERE dc.id = ANY(:chunk_ids)
                      AND dc.record_bot_id = :record_bot_id
                      AND dc.doc_deleted_at IS NULL
                    """
                ),
                {"chunk_ids": chunk_ids, "record_bot_id": record_bot_id},
            )
            return [
                {
                    "chunk_id": str(r.chunk_id),
                    "document_id": str(r.document_id),
                    "content": r.content or "",
                    "text": r.content or "",
                    # Score sentinel — the stats path bypasses vector scoring
                    # so downstream rerank treats these as authoritative
                    # (pre-filtered) rather than near-match candidates.
                    "score": 1.0,
                    "document_name": r.document_name or "",
                    "chunk_index": int(r.chunk_index) if r.chunk_index is not None else 0,
                    "payload": {
                        "document_title": r.document_name or "",
                        "source_url": r.source_url or "",
                    },
                }
                for r in result
            ]


__all__ = ["SqlAlchemyDocumentRepository"]
