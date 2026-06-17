"""Chunk-quality Port — contract for ingest-time chunk quality scorers.

T2-CostPerf. Scores each chunk on four dimensions weighted to
``[0.0, 1.0]``:

* ``text_length_score``     × 0.3  — penalises tiny fragments + over-long
                                      pages parser leaks.
* ``language_confidence``   × 0.2  — langdetect (or any provider that
                                      returns a confidence in ``[0.0, 1.0]``).
* ``information_density``   × 0.2  — type/token ratio proxy.
* ``no_corruption_flag``    × 0.3  — OCR artefact detection (``??``, ``@@``,
                                      ``<0x...>`` runs of replacement chars).

When the operator turns the feature ON via
``system_config.chunk_quality_scoring_enabled``, chunks whose aggregated
score is below ``system_config.chunk_quality_min_score`` (default 0.5)
are SKIPPED before embedding — saving the dense-encoder call cost AND
keeping the retrieval corpus from being polluted by OCR garbage / parser
debris. When OFF (default) every chunk passes through; the port is still
called for observability (score logged), but no chunk is dropped.

Proof citation:
    Industry best-practice (Databricks "Quality at Ingest" 2024,
    Anthropic Contextual Retrieval blog 2024, RAG-anything paper
    https://arxiv.org/abs/2410.21943 §4 "Chunk Filtering"). Empirical
    finding: removing chunks below a hand-tuned quality floor lifts
    Hit@K by 1-3pp while shrinking the corpus 5-15% (less reranker work,
    less pgvector index pressure).

The Port deliberately does NOT carry tenant or trace identifiers — those
are bound at construction time by the implementation; the call-site in
``document_service`` stays minimal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ChunkQualityResult:
    """Per-chunk score breakdown.

    All four sub-scores are in ``[0.0, 1.0]``. The aggregate ``score`` is
    the weighted sum (weights live in
    ``ragbot.shared.chunk_quality.QUALITY_WEIGHTS``) and is also in
    ``[0.0, 1.0]`` by construction (weights sum to 1.0).

    Sub-scores are exposed so the call-site can persist them to
    ``metadata_json.chunk_quality_breakdown`` for admin dashboards +
    retrieval tuning. Pure value object — no mutation, no I/O.
    """

    score: float
    text_length_score: float
    language_confidence: float
    information_density: float
    no_corruption_flag: float


@runtime_checkable
class ChunkQualityScorerPort(Protocol):
    """Score a chunk on quality dimensions for ingest gating.

    Implementations MUST be pure (no DB / network / mutation) — the
    scorer is invoked once per chunk during ingest, on a per-document
    hot path. Failure mode: implementations that genuinely cannot score
    (empty input, encoding failure) MUST return a ``ChunkQualityResult``
    with ``score == 0.0`` rather than raise; raising would abort the
    whole document ingest, which is far worse than skipping a chunk.
    """

    def score(self, chunk: str) -> ChunkQualityResult:
        """Return the quality breakdown for ``chunk``.

        @param chunk: post-enrichment chunk text (whatever the embedder
            will eventually see — caller decides). Empty / whitespace-
            only inputs MUST yield ``score == 0.0`` so the gate's
            ``score < min`` filter drops them by default.
        @return: ``ChunkQualityResult`` with aggregate + breakdown.
        """
        ...


__all__ = ["ChunkQualityResult", "ChunkQualityScorerPort"]
