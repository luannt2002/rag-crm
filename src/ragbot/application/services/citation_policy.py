"""CitationPolicyService."""

from __future__ import annotations

from collections.abc import Iterable

from ragbot.domain.entities.citation import Citation, validate_citations
from ragbot.shared.errors import CitationHallucinated
from ragbot.shared.types import ChunkId


class CitationPolicyService:
    def __init__(self, *, require_at_least_one: bool = True) -> None:
        self._require_at_least_one = require_at_least_one

    def validate(
        self,
        citations: Iterable[Citation],
        retrieved_chunk_ids: frozenset[ChunkId],
    ) -> None:
        cits = list(citations)
        if self._require_at_least_one and not cits:
            raise CitationHallucinated(
                "answer must include at least one citation",
                details={"reason": "no_citation"},
            )
        validate_citations(cits, retrieved_chunk_ids)


__all__ = ["CitationPolicyService"]
