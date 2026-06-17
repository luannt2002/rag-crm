"""Auto-FAQ candidate generator service.

Mines REFUSE_NO_DOCS rows from ``request_logs``, embeds the raw user
question (resolved via JOIN against ``messages`` on
``record_conversation_id``), and clusters semantically-similar refused
questions via greedy cosine grouping. Output is a list of
``FAQCandidate`` rows that an operator can review + fill answers for,
then re-upload as supplementary corpus.

Closed feedback loop:
    load test → REFUSE_NO_DOCS rows in request_logs
              → this service surfaces clusters
              → operator fills answers + uploads
              → next round-test verifies PASS rate rebound

App-mindset compliance (CLAUDE.md):
- This service does NOT call any LLM for answer generation in v1.
  Operator-fill model preserves the rule that the application does not
  inject text/template/answer on behalf of the bot owner. A future
  ``FAQAnswerSuggesterPort`` strategy could plug in here without
  changing call sites.
- All thresholds are read from ``shared/constants.py`` defaults; no
  inline magic numbers.
- Embedder is injected via ``EmbeddingPort`` — Strategy + DI; tests
  swap in a deterministic fake.

Ref: feature spec — auto-FAQ candidate generator
(plans/<TBD>-auto-faq-candidates/plan.md if a plan is later authored).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

import structlog

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.ports.embedding_port import EmbeddingPort
from ragbot.shared.constants import (
    DEFAULT_FAQ_CLUSTER_SIMILARITY,
    DEFAULT_FAQ_MIN_OCCURRENCES,
)
from ragbot.shared.types import TenantId


# ----------------------------------------------------------------------------
# Data row pulled from request_logs ⨝ messages
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class RefusedQuestionRow:
    """One refused-question observation reconstructed from request_logs.

    ``question`` is the raw user-message text resolved via JOIN against
    ``messages`` (role='user', same conversation, nearest preceding
    ``created_at``). ``top_score`` is best-effort: pulled from
    ``request_logs.metadata_json.top_score`` if the upstream pipeline
    persisted it, otherwise ``None``.
    """

    request_id: UUID
    question: str
    refusal_reason: str
    started_at: datetime
    top_score: float | None = None


# ----------------------------------------------------------------------------
# Output row — what operators review
# ----------------------------------------------------------------------------
@dataclass(frozen=True)
class FAQCandidate:
    """One FAQ candidate cluster ready for operator review."""

    cluster_id: str
    sample_questions: list[str]
    occurrence_count: int
    avg_top_score: float
    representative_question: str


# ----------------------------------------------------------------------------
# Repository port (narrow — only what this service needs)
# ----------------------------------------------------------------------------
@runtime_checkable
class RefusedQuestionRepoPort(Protocol):
    """Minimal port for fetching refused-question rows.

    Kept narrow + service-local so that the implementation can use either
    raw SQL (recommended — single LATERAL JOIN) or the broader
    ``RequestLogRepository`` once a generic helper is extracted. We do
    NOT widen ``RequestLogRepository`` itself just to add this one
    use-case dependency.
    """

    async def fetch_refused(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: UUID,
        since: datetime,
    ) -> list[RefusedQuestionRow]: ...


# ----------------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------------
class FAQCandidateService:
    """Cluster refused questions into FAQ candidates."""

    def __init__(
        self,
        *,
        repo: RefusedQuestionRepoPort,
        embedder: EmbeddingPort,
        embedding_spec: EmbeddingSpec,
        logger: structlog.stdlib.BoundLogger | None = None,
    ) -> None:
        self._repo = repo
        self._embedder = embedder
        self._spec = embedding_spec
        self._logger = logger or structlog.get_logger(__name__)

    async def find_candidates(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: UUID,
        since: datetime,
        min_occurrences: int = DEFAULT_FAQ_MIN_OCCURRENCES,
        cluster_similarity: float = DEFAULT_FAQ_CLUSTER_SIMILARITY,
    ) -> list[FAQCandidate]:
        """Return candidate clusters of refused questions.

        Steps:
        1. Pull refused rows since ``since`` for the bot+tenant.
        2. Embed every question (single batched call).
        3. Greedy cluster: each new question joins the highest-similarity
           existing cluster centroid above ``cluster_similarity``,
           otherwise opens a new cluster.
        4. Drop clusters below ``min_occurrences``.
        5. Return clusters sorted by ``occurrence_count`` desc.
        """
        rows = await self._repo.fetch_refused(
            record_tenant_id=record_tenant_id,
            record_bot_id=record_bot_id,
            since=since,
        )
        if not rows:
            self._logger.info(
                "faq_candidate_no_refused_rows",
                record_bot_id=str(record_bot_id),
                since=since.isoformat(),
            )
            return []

        questions = [r.question for r in rows]
        vectors = await self._embedder.embed_batch(
            questions,
            spec=self._spec,
            record_tenant_id=record_tenant_id,
        )
        if len(vectors) != len(rows):
            # Defensive: embedder contract violation — never silent-skip.
            raise RuntimeError(
                f"embedder returned {len(vectors)} vectors for {len(rows)} questions",
            )

        clusters: list[_Cluster] = _greedy_cluster(
            rows=rows,
            vectors=vectors,
            similarity_threshold=cluster_similarity,
        )

        candidates: list[FAQCandidate] = []
        for c in clusters:
            if c.size < min_occurrences:
                continue
            candidates.append(c.to_candidate())

        candidates.sort(key=lambda c: c.occurrence_count, reverse=True)

        self._logger.info(
            "faq_candidate_clusters_built",
            record_bot_id=str(record_bot_id),
            total_refused=len(rows),
            total_clusters=len(clusters),
            surfaced_candidates=len(candidates),
            min_occurrences=min_occurrences,
            cluster_similarity=cluster_similarity,
        )
        return candidates


# ----------------------------------------------------------------------------
# Internal clustering primitives
# ----------------------------------------------------------------------------
@dataclass
class _Cluster:
    """Mutable cluster used during greedy assignment."""

    cluster_id: str
    centroid: list[float]
    rows: list[RefusedQuestionRow] = field(default_factory=list)

    @property
    def size(self) -> int:
        return len(self.rows)

    def add(self, row: RefusedQuestionRow, vector: list[float]) -> None:
        # Streaming centroid update — average of all member vectors.
        n = len(self.rows)
        new_centroid = [
            (self.centroid[i] * n + vector[i]) / (n + 1)
            for i in range(len(self.centroid))
        ]
        self.centroid = new_centroid
        self.rows.append(row)

    def to_candidate(self) -> FAQCandidate:
        scored = [r.top_score for r in self.rows if r.top_score is not None]
        avg = sum(scored) / len(scored) if scored else 0.0
        # Sample up to 3 distinct phrasings for operator review.
        seen: set[str] = set()
        samples: list[str] = []
        for r in self.rows:
            q = r.question.strip()
            if q in seen:
                continue
            seen.add(q)
            samples.append(q)
            if len(samples) >= _SAMPLE_QUESTIONS_PER_CLUSTER:
                break
        # Representative = first row in cluster (chronologically earliest
        # if ``rows`` are ingested in DB order); operator picks the final
        # phrasing on review.
        representative = self.rows[0].question.strip()
        return FAQCandidate(
            cluster_id=self.cluster_id,
            sample_questions=samples,
            occurrence_count=self.size,
            avg_top_score=avg,
            representative_question=representative,
        )


# Number of distinct sample questions surfaced per cluster for the
# operator-review CSV. 3 balances "see paraphrases" vs. CSV bloat.
_SAMPLE_QUESTIONS_PER_CLUSTER: int = 3


def _cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 if either vector is zero-norm."""
    if len(a) != len(b):
        raise ValueError(f"vector dim mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _greedy_cluster(
    *,
    rows: list[RefusedQuestionRow],
    vectors: list[list[float]],
    similarity_threshold: float,
) -> list[_Cluster]:
    """Greedy single-pass clustering.

    Each row joins the cluster whose centroid has the highest cosine
    similarity above ``similarity_threshold``; otherwise a new cluster
    opens. Deterministic given the input order.
    """
    clusters: list[_Cluster] = []
    for i, row in enumerate(rows):
        v = vectors[i]
        best_idx: int | None = None
        best_sim: float = -1.0
        for j, c in enumerate(clusters):
            sim = _cosine(v, c.centroid)
            if sim > best_sim:
                best_sim = sim
                best_idx = j
        if best_idx is not None and best_sim >= similarity_threshold:
            clusters[best_idx].add(row, v)
        else:
            clusters.append(
                _Cluster(
                    cluster_id=f"cluster_{len(clusters) + 1:04d}",
                    centroid=list(v),
                    rows=[row],
                ),
            )
    return clusters


__all__ = [
    "FAQCandidate",
    "FAQCandidateService",
    "RefusedQuestionRepoPort",
    "RefusedQuestionRow",
]


# ----------------------------------------------------------------------------
# Default SQL repository implementation (uses async session_factory).
# Kept in this file to keep the feature surface tight; can be lifted to
# infrastructure/repositories/ if other services start consuming it.
# ----------------------------------------------------------------------------
_REFUSED_SQL = """
SELECT
    rl.request_id,
    rl.refusal_reason,
    rl.started_at,
    rl.metadata_json,
    m.content AS question
FROM request_logs rl
JOIN LATERAL (
    SELECT content
    FROM messages msg
    WHERE msg.record_conversation_id = rl.record_conversation_id
      AND msg.role = 'user'
      AND msg.created_at <= rl.started_at
    ORDER BY msg.created_at DESC
    LIMIT 1
) m ON TRUE
WHERE rl.record_tenant_id = :tenant_id
  AND rl.record_bot_id = :bot_id
  AND rl.refusal_reason IS NOT NULL
  AND rl.started_at >= :since
ORDER BY rl.started_at ASC
"""


class SqlRefusedQuestionRepo(RefusedQuestionRepoPort):
    """SQL-backed implementation. Read-only, tenant-scoped."""

    def __init__(self, session_factory: Any) -> None:
        """``session_factory`` = SQLAlchemy ``async_sessionmaker``.

        Typed as ``Any`` to avoid pulling SQLAlchemy types into the
        application layer (this file lives there); concrete callers
        wire ``async_sessionmaker[AsyncSession]`` from infrastructure.
        """
        self._sf = session_factory

    async def fetch_refused(
        self,
        *,
        record_tenant_id: TenantId,
        record_bot_id: UUID,
        since: datetime,
    ) -> list[RefusedQuestionRow]:
        from sqlalchemy import text  # local import to keep app layer thin

        async with self._sf() as session:
            result = await session.execute(
                text(_REFUSED_SQL),
                {
                    "tenant_id": record_tenant_id,
                    "bot_id": record_bot_id,
                    "since": since,
                },
            )
            rows: list[RefusedQuestionRow] = []
            for r in result.mappings().all():
                meta = r.get("metadata_json") or {}
                top_score_raw = meta.get("top_score") if isinstance(meta, dict) else None
                top_score: float | None
                try:
                    top_score = float(top_score_raw) if top_score_raw is not None else None
                except (TypeError, ValueError):
                    top_score = None
                question = (r.get("question") or "").strip()
                if not question:
                    continue
                rows.append(
                    RefusedQuestionRow(
                        request_id=r["request_id"],
                        question=question,
                        refusal_reason=r.get("refusal_reason") or "",
                        started_at=r["started_at"],
                        top_score=top_score,
                    ),
                )
            return rows
