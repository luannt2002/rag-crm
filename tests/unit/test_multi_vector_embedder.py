"""Unit tests for multi-vector embedding scaffold (Phase-C late-interaction).

Coverage:
1.  test_null_multi_vector_empty_input_returns_empty
2.  test_null_multi_vector_without_inner_returns_empty_list
3.  test_null_multi_vector_with_inner_emits_single_role
4.  test_null_multi_vector_batch_preserves_order
5.  test_sentence_split_decomposes_into_role_tagged_vectors
6.  test_sentence_split_requires_inner_embedder
7.  test_sentence_split_filters_short_fragments
8.  test_sentence_split_caps_max_sentences
9.  test_sentence_split_empty_input_returns_empty
10. test_sentence_split_no_boundary_falls_back_to_full_text
11. test_registry_default_is_null
12. test_registry_sentence_split_resolves
13. test_registry_unknown_provider_falls_back_to_null
14. test_registry_lists_providers_sorted
15. test_registry_filters_unsupported_kwargs
"""

from __future__ import annotations

from typing import Any

import pytest

from ragbot.application.ports.embedder_port import EmbedderPort
from ragbot.application.ports.multi_vector_embed_port import (
    MultiVectorChunk,
    MultiVectorEmbedPort,
)
try:
    from ragbot.infrastructure.embedding.multi_vector_registry import (
        build_multi_vector_embedder,
        list_providers,
    )
    from ragbot.infrastructure.embedding.null_multi_vector import (
        NullMultiVectorEmbedder,
    )
    from ragbot.infrastructure.embedding.sentence_split_multi_vector import (
        SentenceSplitMultiVectorEmbedder,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "multi_vector embedding subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _StubEmbedder:
    """Deterministic in-memory embedder — vector encodes the input length."""

    def __init__(self, dim: int = 3) -> None:
        self._dim = dim
        self.calls: list[Any] = []

    async def embed_query(self, text: str) -> list[float]:
        self.calls.append(("q", text))
        return [float(len(text))] * self._dim

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(("d", tuple(texts)))
        return [[float(len(t))] * self._dim for t in texts]

    async def health_check(self) -> bool:
        return True

    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_id(self) -> str:
        return "stub"


# Sanity: the stub satisfies the EmbedderPort protocol (runtime_checkable).
def test_stub_embedder_matches_port() -> None:
    assert isinstance(_StubEmbedder(), EmbedderPort)


# ---------------------------------------------------------------------------
# NullMultiVectorEmbedder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_multi_vector_empty_input_returns_empty() -> None:
    embedder = NullMultiVectorEmbedder(inner=_StubEmbedder())
    result = await embedder.embed_chunk("")
    assert result == []


@pytest.mark.asyncio
async def test_null_multi_vector_without_inner_returns_empty_list() -> None:
    embedder = NullMultiVectorEmbedder()
    assert embedder.provider_name == "null"
    assert embedder.dimension == 0
    # No inner embedder → degrade silent (return empty list, NOT raise).
    assert await embedder.embed_chunk("hello world") == []
    assert await embedder.embed_chunks(["a", "b"]) == [[], []]


@pytest.mark.asyncio
async def test_null_multi_vector_with_inner_emits_single_role() -> None:
    inner = _StubEmbedder(dim=4)
    embedder = NullMultiVectorEmbedder(inner=inner)
    out = await embedder.embed_chunk("hello")
    assert len(out) == 1
    assert isinstance(out[0], MultiVectorChunk)
    assert out[0].role == "chunk"
    # _StubEmbedder.embed_query returns [len(text)] * dim
    assert out[0].vector == [5.0, 5.0, 5.0, 5.0]
    assert embedder.dimension == 4


@pytest.mark.asyncio
async def test_null_multi_vector_batch_preserves_order() -> None:
    inner = _StubEmbedder(dim=2)
    embedder = NullMultiVectorEmbedder(inner=inner)
    out = await embedder.embed_chunks(["a", "bb", "", "cccc"])
    assert len(out) == 4
    assert [len(group) for group in out] == [1, 1, 0, 1]
    assert out[0][0].vector == [1.0, 1.0]
    assert out[1][0].vector == [2.0, 2.0]
    assert out[3][0].vector == [4.0, 4.0]


# ---------------------------------------------------------------------------
# SentenceSplitMultiVectorEmbedder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sentence_split_decomposes_into_role_tagged_vectors() -> None:
    inner = _StubEmbedder(dim=2)
    embedder = SentenceSplitMultiVectorEmbedder(
        inner=inner,
        min_sentence_chars=1,
        max_sentences=0,
    )
    out = await embedder.embed_chunk("Hello world. Second sentence! Third?")
    assert [c.role for c in out] == ["sentence:0", "sentence:1", "sentence:2"]
    # Each vector is from the inner stub: [len(sentence)] * dim
    assert out[0].vector == [len("Hello world.")] * 2
    assert out[1].vector == [len("Second sentence!")] * 2
    assert out[2].vector == [len("Third?")] * 2
    assert embedder.provider_name == "sentence_split"
    assert embedder.dimension == 2


def test_sentence_split_requires_inner_embedder() -> None:
    with pytest.raises(ValueError):
        SentenceSplitMultiVectorEmbedder(inner=None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_sentence_split_filters_short_fragments() -> None:
    inner = _StubEmbedder(dim=1)
    embedder = SentenceSplitMultiVectorEmbedder(
        inner=inner,
        min_sentence_chars=20,
        max_sentences=0,
    )
    # Two short + one long sentence: short ones drop.
    out = await embedder.embed_chunk(
        "Hi. Ok! This sentence is definitely long enough to keep."
    )
    assert len(out) == 1
    assert out[0].role == "sentence:0"


@pytest.mark.asyncio
async def test_sentence_split_caps_max_sentences() -> None:
    inner = _StubEmbedder(dim=1)
    embedder = SentenceSplitMultiVectorEmbedder(
        inner=inner,
        min_sentence_chars=1,
        max_sentences=2,
    )
    out = await embedder.embed_chunk("A. B. C. D.")
    assert len(out) == 2
    assert [c.role for c in out] == ["sentence:0", "sentence:1"]


@pytest.mark.asyncio
async def test_sentence_split_empty_input_returns_empty() -> None:
    inner = _StubEmbedder()
    embedder = SentenceSplitMultiVectorEmbedder(inner=inner, min_sentence_chars=1)
    assert await embedder.embed_chunk("") == []
    assert await embedder.embed_chunks([]) == []


@pytest.mark.asyncio
async def test_sentence_split_no_boundary_falls_back_to_full_text() -> None:
    inner = _StubEmbedder(dim=1)
    embedder = SentenceSplitMultiVectorEmbedder(
        inner=inner,
        min_sentence_chars=1,
        max_sentences=0,
    )
    # No terminator → splitter returns the full trimmed input as one span.
    out = await embedder.embed_chunk("a single fragment with no terminator")
    assert len(out) == 1
    assert out[0].role == "sentence:0"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_default_is_null() -> None:
    embedder = build_multi_vector_embedder()
    assert isinstance(embedder, NullMultiVectorEmbedder)
    assert isinstance(embedder, MultiVectorEmbedPort)
    assert embedder.provider_name == "null"


def test_registry_sentence_split_resolves() -> None:
    inner = _StubEmbedder()
    embedder = build_multi_vector_embedder(provider="sentence_split", inner=inner)
    assert isinstance(embedder, SentenceSplitMultiVectorEmbedder)
    assert embedder.provider_name == "sentence_split"


def test_registry_unknown_provider_falls_back_to_null() -> None:
    embedder = build_multi_vector_embedder(provider="does_not_exist")
    assert isinstance(embedder, NullMultiVectorEmbedder)


def test_registry_lists_providers_sorted() -> None:
    providers = list_providers()
    assert providers == sorted(providers)
    assert {"null", "sentence_split"} <= set(providers)


def test_registry_filters_unsupported_kwargs() -> None:
    # ``api_key`` is not in any current constructor signature; the registry's
    # filtered-kwargs forward must drop it so the build does not raise.
    embedder = build_multi_vector_embedder(provider="null", api_key="ignored")
    assert isinstance(embedder, NullMultiVectorEmbedder)
