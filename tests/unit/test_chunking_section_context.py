"""Stream A Phase 3 — recursive chunker must propagate section context.

Bug (G3): recursive chunking flattens markdown headings into ordinary
prose. A chunk extracted from "## Triệt lông" loses its parent heading,
so retrieval for "giá triệt lông nách" never sees that the chunk belongs
to the pricing section.

Phase 3 stamps a ``parent_headings`` chain into each chunk's metadata
so the downstream embedder + reranker can use the section ancestry.

These tests are RED today — current chunker emits no metadata field.
"""
from __future__ import annotations

from ragbot.shared.chunking import smart_chunk

MD_FIXTURE = """# Bảng giá Dr. Medispa

## Triệt lông Diode Laser

Combo 10 buổi cho từng vùng cơ thể, cách nhau 4-6 tuần.

- Mép: 899.000đ combo / 129.000đ buổi lẻ
- Mặt: 1.499.000đ combo / 219.000đ buổi lẻ
- Nách: 1.199.000đ combo / 199.000đ buổi lẻ

## Chăm sóc da chuyên sâu

Cấp ẩm cơ bản 60 phút giá 700.000đ, ưu đãi khách mới 199.000đ.

# Quy trình tư vấn

## Bước 1 — Chào khách

Nhân viên gọi tên khách + giới thiệu Dr. Medispa.
"""


def _metadata_dict(chunk: object) -> dict:
    """smart_chunk may return list[str] today; Phase 3 will move to list[dict]."""
    if isinstance(chunk, dict):
        return chunk.get("metadata") or {}
    return getattr(chunk, "metadata", {}) or {}


import pytest


def test_chunks_carry_parent_headings_chain() -> None:
    chunks = smart_chunk(MD_FIXTURE, chunk_size=400, chunk_overlap=40, with_metadata=True)
    assert chunks, "chunker emitted nothing on multi-section markdown fixture"

    saw_pricing_heading = False
    saw_workflow_heading = False
    for chunk in chunks:
        meta = _metadata_dict(chunk)
        parents = meta.get("parent_headings") or []
        text = chunk["content"] if isinstance(chunk, dict) else str(chunk)
        if "899.000" in text or "Mép" in text:
            assert parents, (
                "pricing chunk has no parent_headings — section context lost"
            )
            assert any("Bảng giá" in p for p in parents), (
                f"expected '# Bảng giá' ancestor on pricing chunk; got {parents!r}"
            )
            assert any("Triệt lông" in p for p in parents), (
                f"expected '## Triệt lông Diode Laser' ancestor on pricing chunk; got {parents!r}"
            )
            saw_pricing_heading = True
        if "Nhân viên gọi tên" in text:
            assert any("Quy trình tư vấn" in p for p in parents), (
                f"expected '# Quy trình tư vấn' on workflow chunk; got {parents!r}"
            )
            saw_workflow_heading = True

    assert saw_pricing_heading, "no pricing chunk surfaced; fixture / chunking changed"
    assert saw_workflow_heading, "no workflow chunk surfaced; fixture / chunking changed"


def test_chunk_break_on_h1_boundary() -> None:
    """Two H1 sections must not share a chunk."""
    chunks = smart_chunk(MD_FIXTURE, chunk_size=4000, chunk_overlap=0)
    for chunk in chunks:
        text = chunk["content"] if isinstance(chunk, dict) else str(chunk)
        # No single chunk should straddle both H1 sections.
        assert not (
            "Bảng giá Dr. Medispa" in text and "Quy trình tư vấn" in text
        ), "chunk crosses an H1 boundary — break point missing"
