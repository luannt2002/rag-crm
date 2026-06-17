"""Unit tests for ``smart_chunk_atomic`` — AdapChunk Layer 6 atomic chunking.

INVARIANT under test: TABLE / FORMULA / IMAGE / CODE blocks marked
``is_atomic=True`` are emitted as standalone ``Chunk`` entities and are
NEVER cut, regardless of size. The dispatched strategy applies only to
runs of non-atomic (TEXT-like) blocks.

Wave B1 of the AdapChunk reorg migration (see plans / debug doc §6.3, §18.2 Tầng 6).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from ragbot.domain.entities.document import Block, Chunk
from ragbot.shared.chunking import smart_chunk_atomic
from ragbot.shared.constants import DEFAULT_ATOMIC_BLOCK_TYPES


# ── Fixtures ────────────────────────────────────────────────────────────


def _table_block(content: str | None = None) -> Block:
    rows = content or (
        "| STT | Dịch vụ | Giá |\n"
        "|---|---|---|\n"
        "| 1 | Alpha   | 100 |\n"
        "| 2 | Bravo   | 200 |\n"
        "| 3 | Charlie | 300 |\n"
    )
    return Block(type="TABLE", content=rows, is_atomic=True)


def _formula_block() -> Block:
    return Block(
        type="FORMULA",
        content=r"E = mc^2 \quad ; \quad \int_0^\infty e^{-x^2} dx = \tfrac{\sqrt{\pi}}{2}",
        is_atomic=True,
        context_before="Định luật vật lý nổi tiếng:",
        context_after="(Albert Einstein, 1905)",
    )


def _text_block(content: str) -> Block:
    return Block(type="TEXT", content=content, is_atomic=False)


def _long_text_block() -> Block:
    paragraph = (
        "Đây là một đoạn văn dài dùng để kiểm thử chunking. "
        "Mỗi câu cố ý đủ dài để buộc bộ chunker phải cắt thành "
        "nhiều mảnh khi tổng độ dài vượt quá ngưỡng. "
    )
    return _text_block(paragraph * 40)


# ── Tests ───────────────────────────────────────────────────────────────


def test_atomic_table_block_is_never_cut() -> None:
    """A TABLE block with is_atomic=True must be emitted as one chunk."""
    block = _table_block()
    result = smart_chunk_atomic([block])

    assert len(result) == 1
    chunk = result[0]
    assert isinstance(chunk, Chunk)
    # The atomic table content must survive verbatim inside original_content.
    assert chunk.original_content == block.content
    # The chunk records its provenance: TABLE block type.
    assert "TABLE" in chunk.block_types
    # Metadata flags the chunk as atomic so downstream stages skip splitters.
    assert chunk.metadata.get("is_atomic") is True
    assert chunk.metadata.get("block_type") == "TABLE"


def test_atomic_formula_block_preserves_context_before_after() -> None:
    """FORMULA block with context_before / context_after must concatenate
    those into ``narrated_text`` (and stay atomic)."""
    block = _formula_block()
    result = smart_chunk_atomic([block])

    assert len(result) == 1
    chunk = result[0]
    # Context_before + content + context_after are present in narrated_text.
    assert block.context_before in chunk.narrated_text
    assert block.content in chunk.narrated_text
    assert block.context_after in chunk.narrated_text
    # original_content holds the raw FORMULA content alone (no surrounding text).
    assert chunk.original_content == block.content
    assert "FORMULA" in chunk.block_types


def test_long_text_blocks_are_chunked_via_strategy() -> None:
    """Two long TEXT blocks must be flattened, dispatched to the strategy,
    and produce MORE THAN ONE chunk."""
    blocks = [_long_text_block(), _long_text_block()]
    result = smart_chunk_atomic(blocks, chunk_size=400, chunk_overlap=40)

    # Long text must split into multiple chunks.
    assert len(result) >= 2
    # Each chunk must be a real Chunk entity with non-empty content.
    for chunk in result:
        assert isinstance(chunk, Chunk)
        assert chunk.original_content
        assert chunk.narrated_text
        # block_types tuple should record the TEXT provenance.
        assert "TEXT" in chunk.block_types


def test_mixed_text_table_text_emits_three_minimum_chunks() -> None:
    """A TEXT → TABLE (atomic) → TEXT sequence must produce at least 3 chunks:
    one for the leading text run, one standalone for the atomic table,
    one for the trailing text run."""
    blocks = [
        _text_block("Phần mở đầu giới thiệu nội dung. " * 10),
        _table_block(),
        _text_block("Phần kết luận sau bảng. " * 10),
    ]
    result = smart_chunk_atomic(blocks, chunk_size=400, chunk_overlap=40)

    assert len(result) >= 3
    # Verify the table chunk sits between two text chunks and stayed atomic.
    table_chunks = [c for c in result if c.metadata.get("is_atomic") is True]
    assert len(table_chunks) == 1
    assert "TABLE" in table_chunks[0].block_types
    # The table content must appear verbatim in the atomic chunk.
    assert "| STT |" in table_chunks[0].original_content


def test_empty_input_returns_empty_list() -> None:
    """Empty Block list must return empty Chunk list without raising."""
    assert smart_chunk_atomic([]) == []


# ── Identity passthrough (Wave B2 will exercise this path) ──────────────


def test_identity_kwargs_propagate_to_emitted_chunks() -> None:
    """When caller passes record_tenant_id / record_bot_id / document_id,
    each emitted Chunk must carry those exact identity values."""
    from ragbot.shared.types import (
        BotId,
        CorpusVersion,
        DocumentId,
        EmbeddingModelVersion,
        TenantId,
    )

    tenant = TenantId(uuid4())
    bot = BotId(uuid4())
    doc = DocumentId(uuid4())
    embed_ver = EmbeddingModelVersion("test-embed-1024")
    corpus_ver = CorpusVersion(7)

    blocks = [_text_block("một đoạn ngắn"), _table_block()]
    result = smart_chunk_atomic(
        blocks,
        record_tenant_id=tenant,
        record_bot_id=bot,
        document_id=doc,
        embedding_model_version=embed_ver,
        corpus_version=corpus_ver,
    )

    assert result, "Expected at least one emitted chunk"
    for chunk in result:
        assert chunk.record_tenant_id == tenant
        assert chunk.record_bot_id == bot
        assert chunk.document_id == doc
        assert chunk.embedding_model_version == embed_ver
        assert chunk.corpus_version == corpus_ver


def test_default_atomic_block_types_constant_covers_spec() -> None:
    """Regression guard for the atomic-type set: TABLE / FORMULA / IMAGE /
    CODE must all be recognised as atomic by the constant."""
    assert "TABLE" in DEFAULT_ATOMIC_BLOCK_TYPES
    assert "FORMULA" in DEFAULT_ATOMIC_BLOCK_TYPES
    assert "IMAGE" in DEFAULT_ATOMIC_BLOCK_TYPES
    assert "CODE" in DEFAULT_ATOMIC_BLOCK_TYPES
