"""Unit tests for ``application.services.citation_policy``.

Anti-hallucination final guard:
- Empty citations + ``require_at_least_one`` -> CitationHallucinated.
- Empty citations + ``require_at_least_one=False`` -> no raise.
- Citation referencing a chunk NOT in retrieved set -> CitationHallucinated.
- All citations in retrieved set -> no raise.
- Mixed in/out citations: error lists exactly the foreign chunk ids.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ragbot.application.services.citation_policy import CitationPolicyService
from ragbot.domain.entities.citation import Citation
from ragbot.shared.errors import CitationHallucinated
from ragbot.shared.types import ChunkId, DocumentId


def _cit(chunk_id: ChunkId, *, doc_id: DocumentId | None = None) -> Citation:
    return Citation(
        document_id=doc_id or DocumentId(uuid4()),
        chunk_id=chunk_id,
        tool_name="vector_search",
        quote_span="snippet",
    )


def test_empty_citations_default_raises() -> None:
    policy = CitationPolicyService()
    retrieved = frozenset[ChunkId]()

    with pytest.raises(CitationHallucinated) as exc:
        policy.validate([], retrieved)

    assert exc.value.details.get("reason") == "no_citation"


def test_empty_citations_when_not_required_passes() -> None:
    policy = CitationPolicyService(require_at_least_one=False)
    policy.validate([], frozenset())


def test_citation_in_retrieved_set_passes() -> None:
    cid = ChunkId(uuid4())
    policy = CitationPolicyService()
    policy.validate([_cit(cid)], frozenset({cid}))


def test_citation_outside_retrieved_set_raises() -> None:
    retrieved_only = ChunkId(uuid4())
    hallucinated = ChunkId(uuid4())

    policy = CitationPolicyService()
    with pytest.raises(CitationHallucinated) as exc:
        policy.validate([_cit(hallucinated)], frozenset({retrieved_only}))

    assert exc.value.details["retrieved_count"] == 1
    assert any(str(hallucinated) in s for s in exc.value.details["hallucinated"])


def test_mixed_in_and_out_lists_only_foreign_ids() -> None:
    in_set = ChunkId(uuid4())
    foreign = ChunkId(uuid4())
    retrieved = frozenset({in_set})

    policy = CitationPolicyService()
    with pytest.raises(CitationHallucinated) as exc:
        policy.validate([_cit(in_set), _cit(foreign)], retrieved)

    bad = exc.value.details["hallucinated"]
    assert len(bad) == 1
    assert str(foreign) in bad[0]
    assert str(in_set) not in bad[0]
