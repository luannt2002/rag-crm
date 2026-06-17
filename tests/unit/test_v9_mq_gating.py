"""Multi-query gating refinement.

Covers:

* ``dedup_variants`` cosine path drops near-duplicate paraphrases when
  an embedder closure is supplied.
* ``dedup_variants`` keeps diverse variants intact (low cosine).
* ``dedup_variants`` falls back to token-set Jaccard when no embedder
  closure is provided.
* Single-variant input returns unchanged.
* Empty input returns ``([], 0)``.
* Embedder failure on a single variant downgrades to Jaccard rather
  than aborting (fail-soft contract).
* ``DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD`` /
  ``DEFAULT_MQ_ENTITY_CONFIDENCE_GATE`` exposed in ``shared/constants``.
"""

from __future__ import annotations

import pytest

from ragbot.application.services.multi_query_expansion import dedup_variants
from ragbot.shared.constants import (
    DEFAULT_MQ_ENTITY_CONFIDENCE_GATE,
    DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD,
)


def test_constants_exposed() -> None:
    assert 0.0 < DEFAULT_MQ_VARIANT_SIMILARITY_DEDUP_THRESHOLD <= 1.0
    assert 0.0 < DEFAULT_MQ_ENTITY_CONFIDENCE_GATE <= 1.0


@pytest.mark.asyncio
async def test_dedup_drops_similar_variants_via_cosine() -> None:
    """When embed_one_fn returns near-identical vectors, the second + third
    variants must be dropped (cosine ≥ threshold)."""
    canonical = [1.0, 0.0, 0.0, 0.0]
    near_dup = [0.999, 0.001, 0.0, 0.0]

    embeddings = {
        "alpha": canonical,
        "alpha-two": near_dup,
        "alpha-three": near_dup,
    }

    async def _embed_one(text: str) -> list[float]:
        return embeddings[text]

    kept, dropped = await dedup_variants(
        ["alpha", "alpha-two", "alpha-three"],
        embed_one_fn=_embed_one,
        threshold=0.9,
    )
    assert kept == ["alpha"]
    assert dropped == 2


@pytest.mark.asyncio
async def test_dedup_keeps_diverse_variants() -> None:
    """Cosine well below threshold → all variants kept."""
    embeddings = {
        "v1": [1.0, 0.0, 0.0, 0.0],
        "v2": [0.0, 1.0, 0.0, 0.0],
        "v3": [0.0, 0.0, 1.0, 0.0],
    }

    async def _embed_one(text: str) -> list[float]:
        return embeddings[text]

    kept, dropped = await dedup_variants(
        ["v1", "v2", "v3"],
        embed_one_fn=_embed_one,
        threshold=0.95,
    )
    assert kept == ["v1", "v2", "v3"]
    assert dropped == 0


@pytest.mark.asyncio
async def test_dedup_falls_back_to_jaccard_when_no_embedder() -> None:
    """Without ``embed_one_fn`` the helper uses token-set Jaccard."""
    # "giá gói A bao nhiêu" vs "giá gói A là bao nhiêu" → 5/6 ≈ 0.83
    # Pick threshold low enough that Jaccard catches the duplicate.
    kept, dropped = await dedup_variants(
        [
            "giá gói A bao nhiêu",
            "giá gói A là bao nhiêu",
            "thời gian mở cửa quán",
        ],
        threshold=0.7,
    )
    assert "giá gói A bao nhiêu" in kept
    assert "thời gian mở cửa quán" in kept
    assert dropped == 1


@pytest.mark.asyncio
async def test_dedup_single_variant_returns_unchanged() -> None:
    kept, dropped = await dedup_variants(["only one"])
    assert kept == ["only one"]
    assert dropped == 0


@pytest.mark.asyncio
async def test_dedup_empty_input_returns_empty() -> None:
    kept, dropped = await dedup_variants([])
    assert kept == []
    assert dropped == 0


@pytest.mark.asyncio
async def test_dedup_failsoft_when_embedder_raises() -> None:
    """Embedder raising on one variant must not crash; that variant
    silently falls back to Jaccard against the kept set."""

    embeddings = {
        "alpha":               [1.0, 0.0, 0.0],
        "diverse text content": [0.0, 0.0, 1.0],
    }

    async def _embed_one(text: str) -> list[float]:
        if text == "boom":
            raise ValueError("provider down")
        return embeddings[text]

    kept, dropped = await dedup_variants(
        ["alpha", "boom", "diverse text content"],
        embed_one_fn=_embed_one,
        threshold=0.95,
    )
    # "alpha" is the first kept; "boom" embed failed so Jaccard("alpha","boom")=0
    # → kept; "diverse text content" cosine vs "alpha" is 0 (orthogonal) and
    # Jaccard vs "boom" is 0 → kept.
    assert "alpha" in kept
    assert "boom" in kept
    assert "diverse text content" in kept
    assert dropped == 0
