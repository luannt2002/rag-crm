"""Unit tests for FAQCandidateService — clustering + threshold logic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.faq_candidate_service import (
    FAQCandidateService,
    RefusedQuestionRepoPort,
    RefusedQuestionRow,
)
from ragbot.shared.constants import (
    DEFAULT_FAQ_CLUSTER_SIMILARITY,
    DEFAULT_FAQ_MIN_OCCURRENCES,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
@dataclass
class _FakeRepo(RefusedQuestionRepoPort):
    rows: list[RefusedQuestionRow]

    async def fetch_refused(
        self,
        *,
        record_tenant_id: Any,
        record_bot_id: Any,
        since: Any,
    ) -> list[RefusedQuestionRow]:
        del record_tenant_id, record_bot_id, since
        return list(self.rows)


class _FakeEmbedder:
    """Deterministic embedder.

    ``mapping`` keyed by full question string → unit vector. Tests build
    the mapping so cosine similarity has known values.
    """

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping
        self.batch_calls = 0

    async def health_check(self) -> bool:
        return True

    async def embed_batch(
        self,
        texts: list[str],
        *,
        spec: Any,
        record_tenant_id: Any,
    ) -> list[list[float]]:
        del spec, record_tenant_id
        self.batch_calls += 1
        out: list[list[float]] = []
        for t in texts:
            if t not in self._mapping:
                raise KeyError(f"no fake embedding for: {t!r}")
            out.append(list(self._mapping[t]))
        return out

    async def embed_one(self, text: str, *, spec: Any, record_tenant_id: Any) -> list[float]:
        return (await self.embed_batch([text], spec=spec, record_tenant_id=record_tenant_id))[0]

    async def close(self) -> None:
        return None


def _row(question: str, top_score: float | None = 0.4) -> RefusedQuestionRow:
    return RefusedQuestionRow(
        request_id=uuid4(),
        question=question,
        refusal_reason="REFUSE_NO_DOCS",
        started_at=datetime.now(tz=timezone.utc),
        top_score=top_score,
    )


def _spec() -> EmbeddingSpec:
    return EmbeddingSpec(
        binding_id=UUID(int=0),
        model_name="fake-embedder",
        provider="fake",
        dimension=2,
        model_version="fake-v1",
    )


def _make_service(
    rows: list[RefusedQuestionRow],
    mapping: dict[str, list[float]],
) -> tuple[FAQCandidateService, _FakeEmbedder]:
    repo = _FakeRepo(rows=rows)
    emb = _FakeEmbedder(mapping=mapping)
    svc = FAQCandidateService(repo=repo, embedder=emb, embedding_spec=_spec())
    return svc, emb


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cluster_five_similar_questions_yields_one_candidate() -> None:
    """5 near-paraphrase questions → 1 cluster of 5; threshold 3 surfaces it."""
    similar = [
        "có chỗ gửi xe không",
        "ở đó có gửi xe không",
        "có bãi gửi xe không",
        "chỗ gửi xe ở đâu",
        "spa có gửi xe không",
    ]
    rows = [_row(q) for q in similar]
    # All 5 vectors near-identical (cosine ≈ 1.0).
    mapping = {q: [1.0, 0.001 * i] for i, q in enumerate(similar)}

    svc, _emb = _make_service(rows, mapping)
    candidates = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
    )

    assert len(candidates) == 1
    c = candidates[0]
    assert c.occurrence_count == 5
    # avg_top_score is the mean of the per-row top_score (all 0.4 in fixture)
    assert c.avg_top_score == pytest.approx(0.4)
    # Sample list capped at 3 for operator review.
    assert len(c.sample_questions) == 3
    assert c.representative_question == similar[0]


@pytest.mark.asyncio
async def test_below_min_occurrences_not_surfaced() -> None:
    """A 2-row cluster is dropped when min_occurrences=3 (default)."""
    qs = ["có chỗ gửi xe không", "ở đó có gửi xe không"]
    rows = [_row(q) for q in qs]
    mapping = {q: [1.0, 0.0] for q in qs}

    svc, _emb = _make_service(rows, mapping)
    candidates = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
    )

    assert candidates == []
    # Sanity-check: lowering the floor to 2 surfaces the same cluster.
    candidates2 = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
        min_occurrences=2,
    )
    assert len(candidates2) == 1
    assert candidates2[0].occurrence_count == 2


@pytest.mark.asyncio
async def test_below_similarity_threshold_yields_separate_clusters() -> None:
    """Two distinct topics each repeated 3× → 2 clusters surface, not 1."""
    parking = [
        "có chỗ gửi xe không",
        "ở đó có gửi xe không",
        "spa có gửi xe không",
    ]
    hours = [
        "chủ nhật có mở cửa không",
        "cuối tuần spa có làm không",
        "chủ nhật có làm việc không",
    ]
    rows = [_row(q) for q in parking + hours]
    # parking vectors point along x axis, hours along y axis → cosine = 0.
    mapping: dict[str, list[float]] = {}
    for i, q in enumerate(parking):
        mapping[q] = [1.0, 0.001 * i]
    for i, q in enumerate(hours):
        mapping[q] = [0.001 * i, 1.0]

    svc, _emb = _make_service(rows, mapping)
    candidates = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
    )

    assert len(candidates) == 2
    counts = sorted(c.occurrence_count for c in candidates)
    assert counts == [3, 3]
    # Representative questions come from different topics (cosine ≈ 0
    # between the two clusters, well below DEFAULT_FAQ_CLUSTER_SIMILARITY).
    reps = {c.representative_question for c in candidates}
    assert reps == {parking[0], hours[0]}


@pytest.mark.asyncio
async def test_empty_input_yields_empty_output() -> None:
    """No refused rows → empty list, embedder never called."""
    svc, emb = _make_service(rows=[], mapping={})
    candidates = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
    )
    assert candidates == []
    assert emb.batch_calls == 0


@pytest.mark.asyncio
async def test_candidates_sorted_by_occurrence_desc() -> None:
    """Larger clusters come first regardless of input order."""
    small = ["chủ nhật có mở cửa không", "cuối tuần có làm không", "cn có làm không"]
    big = [
        "có chỗ gửi xe không",
        "có bãi gửi xe không",
        "spa có gửi xe không",
        "ở đó có gửi xe không",
        "chỗ gửi xe ở đâu",
    ]
    # Feed small first to prove sorting kicks in.
    rows = [_row(q) for q in small + big]
    mapping: dict[str, list[float]] = {}
    for i, q in enumerate(small):
        mapping[q] = [0.001 * i, 1.0]
    for i, q in enumerate(big):
        mapping[q] = [1.0, 0.001 * i]

    svc, _emb = _make_service(rows, mapping)
    candidates = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
    )
    assert [c.occurrence_count for c in candidates] == [5, 3]


@pytest.mark.asyncio
async def test_constants_drive_defaults() -> None:
    """Default kwargs come from shared/constants.py — no inline magic.

    Regression guard for the zero-hardcode rule on this module: confirms
    that the service's default thresholds match the canonical constants
    so a future refactor can't silently fork them.
    """
    # Build a 3-row cluster that sits exactly at the default threshold.
    qs = ["có chỗ gửi xe không", "ở đó có gửi xe không", "spa có gửi xe không"]
    rows = [_row(q) for q in qs]
    mapping = {q: [1.0, 0.0] for q in qs}

    svc, _emb = _make_service(rows, mapping)
    # Call with NO kwargs — must fall through to constants.
    candidates = await svc.find_candidates(
        record_tenant_id=uuid4(),  # type: ignore[arg-type]
        record_bot_id=uuid4(),
        since=datetime.now(tz=timezone.utc),
    )
    assert DEFAULT_FAQ_MIN_OCCURRENCES == 3
    assert 0.0 < DEFAULT_FAQ_CLUSTER_SIMILARITY <= 1.0
    assert len(candidates) == 1
    assert candidates[0].occurrence_count == DEFAULT_FAQ_MIN_OCCURRENCES
