"""Unit tests for the Speculative Streaming Phase 3 HALLU verifier."""

from __future__ import annotations

import math
import uuid
from typing import Any

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.hallu_verifier import (
    HALLUVerdict,
    HALLUVerifier,
    REASON_EMPTY_BUFFER,
    REASON_NUMERIC_MISMATCH,
    REASON_OVERLAP_BELOW_FLOOR,
    REASON_SAFE,
    REASON_TOPIC_DIVERGENCE,
    REASON_WAIT,
    verdict_to_payload,
)
from ragbot.shared.constants import (
    DEFAULT_HALLU_VERIFIER_BUFFER_TOKENS,
    DEFAULT_HALLU_VERIFIER_EMBEDDING_THRESHOLD,
    DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD,
)
from ragbot.shared.types import TenantId


# ---------------------------------------------------------------------------
# Fixtures + fakes
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    """Embedder mock that returns one of two configurable vectors.

    The first text routed to ``embed_batch`` gets ``vec_a``, the second
    gets ``vec_b`` — driver controls the cosine the verifier sees.
    """

    def __init__(self, vec_a: list[float], vec_b: list[float]) -> None:
        self.vec_a = vec_a
        self.vec_b = vec_b
        self.calls: list[list[str]] = []

    async def health_check(self) -> bool:
        return True

    async def embed_batch(
        self, texts: list[str], *, spec: Any, record_tenant_id: Any,
    ) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vec_a, self.vec_b]

    async def embed_one(self, text: str, *, spec: Any, record_tenant_id: Any) -> list[float]:
        return self.vec_a

    async def close(self) -> None:
        return None


def _make_spec() -> EmbeddingSpec:
    return EmbeddingSpec(
        binding_id=uuid.uuid4(),
        model_name="mock/embed",
        provider="mock",
        dimension=8,
        model_version="mock-v1",
    )


def _tenant() -> TenantId:
    return TenantId(uuid.uuid4())


def _aligned_unit(dim: int = 8) -> list[float]:
    """Unit vector ``[1, 0, ...]`` — cosine 1.0 with itself."""
    out = [0.0] * dim
    out[0] = 1.0
    return out


def _orthogonal_unit(dim: int = 8) -> list[float]:
    """Unit vector ``[0, 1, 0, ...]`` — cosine 0.0 with ``_aligned_unit``."""
    out = [0.0] * dim
    out[1] = 1.0
    return out


def _verifier(
    embedder: _FakeEmbedder | None = None,
    *,
    overlap: float = DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD,
    embedding: float = DEFAULT_HALLU_VERIFIER_EMBEDDING_THRESHOLD,
    shingle: int = 4,
) -> HALLUVerifier:
    embedder = embedder or _FakeEmbedder(_aligned_unit(), _aligned_unit())
    return HALLUVerifier(
        embedder=embedder,
        overlap_threshold=overlap,
        embedding_threshold=embedding,
        shingle_size=shingle,
    )


# ---------------------------------------------------------------------------
# 1. Empty buffer + wait edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_draft_buffer_returns_safe_empty() -> None:
    """Nothing to verify → safe + reason=empty_buffer (no SSE redo)."""
    verifier = _verifier()
    verdict = await verifier.verify_draft_vs_main([], "main answer text here")
    assert verdict.safe is True
    assert verdict.reason == REASON_EMPTY_BUFFER


@pytest.mark.asyncio
async def test_whitespace_only_draft_returns_safe_empty() -> None:
    """Whitespace draft is functionally empty — must NOT trigger redo."""
    verifier = _verifier()
    verdict = await verifier.verify_draft_vs_main(["   ", "\n\t"], "real main text content")
    assert verdict.safe is True
    assert verdict.reason == REASON_EMPTY_BUFFER


@pytest.mark.asyncio
async def test_short_main_returns_wait() -> None:
    """Main first chunk shorter than one shingle window → caller must wait."""
    verifier = _verifier(shingle=6)
    verdict = await verifier.verify_draft_vs_main(
        ["the answer is forty two percent of the total"], "only three words",
    )
    assert verdict.safe is False
    assert verdict.reason == REASON_WAIT


# ---------------------------------------------------------------------------
# 2. Substring overlap gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_overlap_passes_substring_gate() -> None:
    """Draft fully contained in main → overlap = 1.0 → safe."""
    text = "the policy allows refunds within thirty days of purchase"
    verifier = _verifier()
    verdict = await verifier.verify_draft_vs_main([text], text + " no questions asked")
    assert verdict.safe is True
    assert verdict.reason == REASON_SAFE
    assert verdict.overlap_pct == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_partial_overlap_above_floor_passes() -> None:
    """Draft 80%+ shingle overlap with main → safe."""
    draft = "the policy allows refunds within thirty days of purchase complete"
    main = "the policy allows refunds within thirty days of purchase complete"
    verifier = _verifier(overlap=0.75)
    verdict = await verifier.verify_draft_vs_main([draft], main)
    assert verdict.safe is True
    assert verdict.overlap_pct >= 0.75


@pytest.mark.asyncio
async def test_disjoint_text_below_floor_aborts() -> None:
    """Draft talks about apples, main talks about cars → abort."""
    draft = "apples are red and grow on tall green trees"
    main = "automobiles require gasoline to drive on paved roads"
    verifier = _verifier()
    verdict = await verifier.verify_draft_vs_main([draft], main)
    assert verdict.safe is False
    assert verdict.reason == REASON_OVERLAP_BELOW_FLOOR
    assert verdict.overlap_pct < DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD


# ---------------------------------------------------------------------------
# 3. Numeric mismatch gate (HALLU sacred anti-fabricate-numbers)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_numeric_mismatch_5m_vs_50m_aborts() -> None:
    """Classic fabrication: draft says 5 million, main says 50 million.

    The textual surround is identical so word-shingle overlap stays near
    1.0 and the gate ordering still reaches the numeric check.
    """
    draft = "the report cites the figure 5 in its preface"
    main = "the report cites the figure 50 in its preface"
    verifier = _verifier(shingle=3, overlap=0.5)
    verdict = await verifier.verify_draft_vs_main([draft], main)
    assert verdict.safe is False
    assert verdict.reason == REASON_NUMERIC_MISMATCH
    # draft's "5" must be in the missing-numbers list
    assert "5" in verdict.numeric_mismatch


@pytest.mark.asyncio
async def test_numeric_mismatch_percent_fabrication_aborts() -> None:
    """Draft invents an 80 improvement metric not present in main.

    Surface text reuses main verbatim plus the inserted "80" token so
    shingle overlap stays high; the verifier should still flag the
    fabricated number on the dedicated numeric gate.
    """
    main = "the system delivers measurable gains over the prior baseline release"
    draft = "the system delivers 80 measurable gains over the prior baseline release"
    verifier = _verifier(shingle=3, overlap=0.5)
    verdict = await verifier.verify_draft_vs_main([draft], main)
    assert verdict.safe is False
    assert verdict.reason == REASON_NUMERIC_MISMATCH
    assert "80" in verdict.numeric_mismatch


@pytest.mark.asyncio
async def test_numbers_match_passes() -> None:
    """Same numbers in draft + main → numeric gate passes."""
    draft = "the policy refunds within 30 days for orders over 100 dollars"
    main = "the policy refunds within 30 days for orders over 100 dollars exactly"
    verifier = _verifier()
    verdict = await verifier.verify_draft_vs_main([draft], main)
    assert verdict.safe is True
    assert verdict.numeric_mismatch == []


# ---------------------------------------------------------------------------
# 4. Topic divergence (sentence-embedding cosine) gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_topic_divergence_low_cosine_aborts() -> None:
    """High word overlap + zero cosine (orthogonal embeddings) → abort."""
    # Use identical surface text so shingles + numerics agree; embedder
    # mock supplies the orthogonal vectors that should trip gate 3.
    text = "the customer support team is available every weekday morning"
    embedder = _FakeEmbedder(_aligned_unit(), _orthogonal_unit())
    verifier = _verifier(embedder=embedder, overlap=0.0)
    verdict = await verifier.verify_draft_vs_main(
        [text], text, spec=_make_spec(), record_tenant_id=_tenant(),
    )
    assert verdict.safe is False
    assert verdict.reason == REASON_TOPIC_DIVERGENCE
    assert verdict.embedding_cosine == pytest.approx(0.0, abs=1e-9)


@pytest.mark.asyncio
async def test_topic_match_high_cosine_passes() -> None:
    """Same vector pair → cosine 1.0 → safe."""
    text = "the customer support team is available every weekday morning"
    embedder = _FakeEmbedder(_aligned_unit(), _aligned_unit())
    verifier = _verifier(embedder=embedder, overlap=0.0)
    verdict = await verifier.verify_draft_vs_main(
        [text], text, spec=_make_spec(), record_tenant_id=_tenant(),
    )
    assert verdict.safe is True
    assert verdict.embedding_cosine == pytest.approx(1.0, abs=1e-9)


@pytest.mark.asyncio
async def test_spec_none_skips_embedding_gate() -> None:
    """When spec=None, embed gate is bypassed (cosine reported as 1.0)."""
    embedder = _FakeEmbedder(_aligned_unit(), _orthogonal_unit())
    text = "the customer support team is available every weekday morning"
    verifier = _verifier(embedder=embedder, overlap=0.0)
    verdict = await verifier.verify_draft_vs_main([text], text)  # no spec
    assert verdict.safe is True
    assert verdict.embedding_cosine == pytest.approx(1.0)
    # Embedder NEVER called when spec omitted (perf — saves embed RTT).
    assert embedder.calls == []


# ---------------------------------------------------------------------------
# 5. Gate ordering + payload helpers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_overlap_failure_short_circuits_before_numeric_check() -> None:
    """Overlap fails first → numeric_mismatch list stays empty for that reason."""
    draft = "apples are red and grow on tall green trees with 99 leaves"
    main = "automobiles require gasoline to drive on paved roads"
    verifier = _verifier()
    verdict = await verifier.verify_draft_vs_main([draft], main)
    assert verdict.reason == REASON_OVERLAP_BELOW_FLOOR
    # Numeric scan deferred — list is empty even though draft has "99".
    assert verdict.numeric_mismatch == []


def test_verdict_to_payload_round_trip() -> None:
    """Dataclass → wire JSON dict carries every field on the SSE schema."""
    v = HALLUVerdict(
        safe=False,
        reason=REASON_NUMERIC_MISMATCH,
        overlap_pct=0.9,
        numeric_mismatch=["5"],
        embedding_cosine=0.0,
    )
    payload = verdict_to_payload(v)
    assert payload == {
        "safe": False,
        "reason": REASON_NUMERIC_MISMATCH,
        "overlap_pct": 0.9,
        "numeric_mismatch": ["5"],
        "embedding_cosine": 0.0,
    }


def test_buffer_tokens_default_matches_constant() -> None:
    """Verifier exposes buffer_tokens so caller knows how much to accumulate."""
    verifier = _verifier()
    assert verifier.buffer_tokens == DEFAULT_HALLU_VERIFIER_BUFFER_TOKENS


def test_constants_are_in_safe_ranges() -> None:
    """HALLU=0 sacred: defaults must NOT be permissive enough to ship a fabricated draft."""
    assert 0.5 <= DEFAULT_HALLU_VERIFIER_OVERLAP_THRESHOLD <= 1.0
    assert 0.5 <= DEFAULT_HALLU_VERIFIER_EMBEDDING_THRESHOLD <= 1.0
    assert DEFAULT_HALLU_VERIFIER_BUFFER_TOKENS >= 10
