"""Citation entity + validator (anti-hallucination chốt chặn cuối).

Ref: PLAN_04 §citation.py / RAGBOT_MASTER §10.5.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ragbot.shared.errors import CitationHallucinated
from ragbot.shared.types import ChunkId, DocumentId


@dataclass(frozen=True, slots=True)
class Citation:
    """Trích dẫn nguồn — liên kết câu trả lời với chunk đã truy xuất."""
    document_id: DocumentId
    chunk_id: ChunkId
    tool_name: str
    quote_span: str
    page_number: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None

    def is_in_retrieved_set(self, retrieved_chunk_ids: frozenset[ChunkId]) -> bool:
        """Kiểm tra chunk được trích dẫn có nằm trong tập đã truy xuất.
        @param retrieved_chunk_ids: tập chunk IDs đã truy xuất
        @return: True nếu chunk tồn tại trong tập
        """
        return self.chunk_id in retrieved_chunk_ids


def validate_citations(
    citations: Iterable[Citation],
    retrieved_chunk_ids: frozenset[ChunkId],
) -> None:
    """Raise CitationHallucinated if any citation references a chunk not retrieved."""
    bad: list[str] = []
    for cit in citations:
        if not cit.is_in_retrieved_set(retrieved_chunk_ids):
            bad.append(f"{cit.document_id}/{cit.chunk_id}")
    if bad:
        raise CitationHallucinated(
            "LLM cited chunks not in retrieved set",
            details={"hallucinated": bad, "retrieved_count": len(retrieved_chunk_ids)},
        )


__all__ = ["Citation", "validate_citations"]
