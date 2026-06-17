"""Chunk Quality Scoring — ingest gate (T2-CostPerf).

Heuristic 0.0-1.0 quality score per ingest chunk, gated by
``system_config.chunk_quality_scoring_enabled``. Score formula:

    quality_score = (
          QUALITY_WEIGHT_TEXT_LENGTH       * text_length_score
        + QUALITY_WEIGHT_LANGUAGE          * language_confidence
        + QUALITY_WEIGHT_INFO_DENSITY      * information_density
        + QUALITY_WEIGHT_NO_CORRUPTION     * no_corruption_flag
    )

Weights live in ``ragbot.shared.constants`` and sum to 1.0 by construction
so the aggregate ``score`` is itself in ``[0.0, 1.0]``. Chunks whose
score falls below ``system_config.chunk_quality_min_score`` are SKIPPED
before embedding (saves dense-encoder cost + keeps OCR debris out of
the retrieval corpus). When the feature flag is OFF (default), the
score is still computed for observability but no chunk is dropped.

Proof citation:
    Databricks "Quality at Ingest" 2024 + Anthropic Contextual
    Retrieval blog 2024 + RAG-anything paper (https://arxiv.org/abs/2410.21943
    §4 "Chunk Filtering"). Removing chunks below a tuned quality floor
    lifts Hit@K by 1-3pp while shrinking the corpus 5-15% (less reranker
    work, less pgvector index pressure).

Pure module — no DB, no network, no I/O. Domain-neutral: no brand /
industry / language assumptions baked into the heuristic. The optional
``langdetect`` dependency is consulted lazily; absence is treated as a
neutral language confidence of 1.0 so the score remains computable.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from ragbot.application.ports.chunk_quality_port import (
    ChunkQualityResult,
    ChunkQualityScorerPort,
)
from ragbot.shared.constants import (
    DEFAULT_CHUNK_QUALITY_CORRUPTION_RATIO_MAX,
    DEFAULT_CHUNK_QUALITY_INFO_DENSITY_FLOOR,
    DEFAULT_CHUNK_QUALITY_INFO_DENSITY_TARGET,
    DEFAULT_CHUNK_QUALITY_MAX_CHARS,
    DEFAULT_CHUNK_QUALITY_MIN_CHARS,
    DEFAULT_CHUNK_QUALITY_MIN_SCORE,
    DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS,
    QUALITY_WEIGHT_INFO_DENSITY,
    QUALITY_WEIGHT_LANGUAGE,
    QUALITY_WEIGHT_NO_CORRUPTION,
    QUALITY_WEIGHT_TEXT_LENGTH,
)


# OCR / parser corruption markers — heuristic glob of artefacts commonly
# left behind by Tesseract / PDF text extractors when they hit binary
# tables, embedded fonts, scanned figures, or RTL/mixed-encoding pages.
#
# * ``?{3,}``  — run of replacement chars (Tesseract fallback for
#                unrecognised glyphs).
# * ``@{2,}``  — bullet / list-marker corruption common in legacy PDFs.
# * ``<0x``    — raw hex byte escapes leaking into text (``<0xFE>``).
# * ``\\u00`` / ``\\x``  — escaped control / replacement codepoints.
# * U+FFFD    — Unicode REPLACEMENT CHARACTER (canonical decode failure).
#
# Domain-neutral: matches ARTEFACT shapes, not language content.
_CORRUPTION_PATTERN: re.Pattern[str] = re.compile(
    r"(\?{3,}|@{2,}|<0x[0-9A-Fa-f]{2,}>|\\u00[0-9A-Fa-f]{2}|\\x[0-9A-Fa-f]{2}|�)",
)

# Non-alphanumeric ratio guard — pure number/punct-only chunks (e.g. a
# leaked table cell row of ``1.2 | 3.4 | 5.6 | ...``) have no semantic
# content the embedder can usefully encode. Threshold is loose so
# legitimate prose with normal punctuation passes through.
_ALPHANUMERIC_PATTERN: re.Pattern[str] = re.compile(r"[\w]", flags=re.UNICODE)


def _score_text_length(chunk_chars: int) -> float:
    """Triangular score peaking at ``OPTIMAL_CHARS``.

    * ``< MIN_CHARS``                 → 0.0    (too sparse to be informative)
    * ``[MIN_CHARS, OPTIMAL_CHARS]``  → ramp up linearly to 1.0
    * ``[OPTIMAL_CHARS, MAX_CHARS]``  → ramp down linearly to 0.0
    * ``> MAX_CHARS``                 → 0.0    (parser leak / unsplit page)

    The shape rewards the sweet spot a chunker should target without
    being brittle around the boundaries (linear, not step).
    """
    if chunk_chars < DEFAULT_CHUNK_QUALITY_MIN_CHARS:
        return 0.0
    if chunk_chars > DEFAULT_CHUNK_QUALITY_MAX_CHARS:
        return 0.0
    if chunk_chars <= DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS:
        span = DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS - DEFAULT_CHUNK_QUALITY_MIN_CHARS
        if span <= 0:
            return 1.0
        return (chunk_chars - DEFAULT_CHUNK_QUALITY_MIN_CHARS) / span
    span = DEFAULT_CHUNK_QUALITY_MAX_CHARS - DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS
    if span <= 0:
        return 1.0
    return (DEFAULT_CHUNK_QUALITY_MAX_CHARS - chunk_chars) / span


def _score_language_confidence(chunk: str) -> float:
    """Language-detector confidence ∈ ``[0.0, 1.0]``.

    Delegates to ``langdetect.detect_langs`` when available. The library
    is an optional dependency — when absent we treat the language signal
    as NEUTRAL (1.0) so the aggregate score is unaffected; an operator
    that wants language confidence enforced can ``pip install langdetect``
    and the gate immediately starts demoting mixed-script / garbled chunks.

    Empty / whitespace-only chunks return 0.0 so the gate trivially drops
    them.
    """
    if not chunk or not chunk.strip():
        return 0.0
    try:
        from langdetect import (  # noqa: PLC0415 — optional dep, lazy import
            DetectorFactory,
            detect_langs,
        )
    except ImportError:
        # Optional dep absent → neutral signal. Documented in module docstring.
        return 1.0
    # Deterministic detection — without seeding, langdetect's underlying
    # n-gram model produces non-reproducible scores across calls.
    DetectorFactory.seed = 0
    try:
        candidates = detect_langs(chunk)
    except Exception:  # noqa: BLE001 — langdetect raises generic LangDetectException
        # Treat detection failure as low confidence (often signals
        # encoding garbage / mixed-script content the embedder will
        # struggle with anyway).
        return 0.0
    if not candidates:
        return 0.0
    # First candidate is the most probable language by definition.
    top = candidates[0]
    prob = float(getattr(top, "prob", 0.0))
    if prob < 0.0:
        return 0.0
    if prob > 1.0:
        return 1.0
    return prob


def _score_information_density(chunk: str) -> float:
    """Type/token ratio proxy clamped to ``[0.0, 1.0]``.

    Whitespace-tokenised — exact tokenizer doesn't matter at heuristic
    resolution and avoids dragging a heavyweight NLP dep into the ingest
    hot-path. Maps:

    * ttr <= FLOOR     → 0.0   (boilerplate / repeated tokens)
    * ttr >= TARGET    → 1.0   (rich vocab — what we want)
    * linear in between

    Empty / single-token chunks return 0.0.
    """
    if not chunk or not chunk.strip():
        return 0.0
    tokens = chunk.split()
    if len(tokens) < 2:
        return 0.0
    unique = len(set(tokens))
    ttr = unique / len(tokens)
    floor = DEFAULT_CHUNK_QUALITY_INFO_DENSITY_FLOOR
    target = DEFAULT_CHUNK_QUALITY_INFO_DENSITY_TARGET
    if ttr <= floor:
        return 0.0
    if ttr >= target:
        return 1.0
    span = target - floor
    if span <= 0:
        return 1.0
    return (ttr - floor) / span


def _score_no_corruption(chunk: str) -> float:
    """Binary-ish corruption flag.

    Returns 1.0 (good) when:
    * No OCR/parser artefact pattern matches.
    * At least one alphanumeric char exists.
    * The artefact-char ratio (replacement / total) is below
      ``DEFAULT_CHUNK_QUALITY_CORRUPTION_RATIO_MAX``.

    Returns 0.0 (bad) when ANY of the above fails. The "binary-ish"
    design is deliberate — corruption is qualitatively different from
    "short text" / "low density"; once it's there at all, we want the
    weight (0.3) to deliver a decisive penalty rather than a gradient.

    Empty input → 0.0 (nothing to verify).
    """
    if not chunk or not chunk.strip():
        return 0.0
    # Any direct artefact pattern → fail immediately.
    if _CORRUPTION_PATTERN.search(chunk):
        return 0.0
    total = len(chunk)
    if total == 0:
        return 0.0
    alnum_hits = _ALPHANUMERIC_PATTERN.findall(chunk)
    alnum_count = len(alnum_hits)
    if alnum_count == 0:
        # Pure punctuation / digit-table garbage → fail.
        return 0.0
    non_alnum_ratio = 1.0 - (alnum_count / total)
    if non_alnum_ratio > DEFAULT_CHUNK_QUALITY_CORRUPTION_RATIO_MAX:
        return 0.0
    return 1.0


class HeuristicChunkQualityScorer:
    """Default :class:`ChunkQualityScorerPort` — pure stdlib heuristic.

    Composes the four sub-scores (text length, language confidence,
    information density, no-corruption flag) into an aggregate using
    weights from ``ragbot.shared.constants``. Pure / deterministic /
    no external I/O — safe to call on the ingest hot-path.

    The class is constructor-arg-free so the DI Registry can build it
    with ``HeuristicChunkQualityScorer()`` for every adapter caller.
    """

    @staticmethod
    def get_provider_name() -> str:
        return "heuristic"

    def score(self, chunk: str) -> ChunkQualityResult:
        if not chunk or not chunk.strip():
            return ChunkQualityResult(
                score=0.0,
                text_length_score=0.0,
                language_confidence=0.0,
                information_density=0.0,
                no_corruption_flag=0.0,
            )
        chunk_chars = len(chunk)
        text_length = _score_text_length(chunk_chars)
        lang_conf = _score_language_confidence(chunk)
        info_density = _score_information_density(chunk)
        no_corruption = _score_no_corruption(chunk)
        aggregate = (
            QUALITY_WEIGHT_TEXT_LENGTH * text_length
            + QUALITY_WEIGHT_LANGUAGE * lang_conf
            + QUALITY_WEIGHT_INFO_DENSITY * info_density
            + QUALITY_WEIGHT_NO_CORRUPTION * no_corruption
        )
        # Defensive clamp — weights sum to 1.0 by construction (verified
        # in tests) so the aggregate is already in [0,1], but the clamp
        # makes the contract explicit for readers + mypy.
        if aggregate < 0.0:
            aggregate = 0.0
        if aggregate > 1.0:
            aggregate = 1.0
        return ChunkQualityResult(
            score=aggregate,
            text_length_score=text_length,
            language_confidence=lang_conf,
            information_density=info_density,
            no_corruption_flag=no_corruption,
        )


def score_chunk_for_ingest_gate(chunk: str) -> ChunkQualityResult:
    """Module-level convenience — equivalent to ``HeuristicChunkQualityScorer().score(chunk)``.

    Provided so callers that don't go through DI (tests, scripts, ad-hoc
    diagnostics) can grade a chunk with a single import. Production
    callers SHOULD inject :class:`ChunkQualityScorerPort` via the DI
    container so an operator can swap to a future ML-based scorer
    without code change.
    """
    return _SCORER_SINGLETON.score(chunk)


def select_passing_indices(
    chunks: Sequence[str],
    *,
    min_score: float = DEFAULT_CHUNK_QUALITY_MIN_SCORE,
    scorer: ChunkQualityScorerPort | None = None,
) -> tuple[list[int], list[int], list[ChunkQualityResult]]:
    """Partition chunk indices by quality threshold.

    @param chunks: ordered chunk texts (typically post-enrichment).
    @param min_score: aggregate score below which a chunk is dropped.
    @param scorer: optional injected scorer; defaults to the module
        singleton :class:`HeuristicChunkQualityScorer`.
    @return: ``(passing_indices, skipped_indices, all_scores)`` where:
        * ``passing_indices``  — sorted indices with ``score >= min_score``
        * ``skipped_indices``  — sorted indices with ``score <  min_score``
        * ``all_scores``       — ``ChunkQualityResult`` per chunk, index-
                                  aligned with the input list. Caller can
                                  persist these to ``metadata_json`` or
                                  log score histograms regardless of
                                  whether the gate skipped a chunk.

    Pure function — no logging, no DB. The caller decides whether to
    actually filter (feature flag) and what telemetry to emit.
    """
    scorer = scorer or _SCORER_SINGLETON
    passing: list[int] = []
    skipped: list[int] = []
    scores: list[ChunkQualityResult] = []
    for i, chunk in enumerate(chunks):
        result = scorer.score(chunk)
        scores.append(result)
        if result.score >= min_score:
            passing.append(i)
        else:
            skipped.append(i)
    return passing, skipped, scores


# Module singleton — constructor-arg-free, stateless, safe to share across
# requests / threads. Reuse avoids ~µs of class-instantiation overhead per
# chunk on documents with thousands of chunks.
_SCORER_SINGLETON: ChunkQualityScorerPort = HeuristicChunkQualityScorer()


__all__ = [
    "HeuristicChunkQualityScorer",
    "score_chunk_for_ingest_gate",
    "select_passing_indices",
]
