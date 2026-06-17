"""Unit tests — chunk quality scoring ingest gate (T2-CostPerf).

Covers:
* ``ragbot.shared.chunk_quality.HeuristicChunkQualityScorer.score``
* ``ragbot.shared.chunk_quality.score_chunk_for_ingest_gate``
* ``ragbot.shared.chunk_quality.select_passing_indices``
* ``ragbot.infrastructure.chunk_quality.registry.build_chunk_quality_scorer``
* ``ragbot.infrastructure.chunk_quality.null_chunk_quality_scorer.NullChunkQualityScorer``
* ``ChunkQualityScorerPort`` Protocol compliance (heuristic + null).

Pure-function tests — no LLM, no DB, no network. The heuristic is
deterministic given a fixed ``DetectorFactory.seed`` (set inside the
scorer); these assertions therefore stay stable across runs.

The ingest-gate scorer is DIFFERENT from the existing CleanBase one
(``test_chunk_quality_score.py``): it uses 0.3/0.2/0.2/0.3 weights vs
4×0.25 and operates in active-rejection mode rather than observability.
Tests below verify both the score arithmetic and the gating decision
shape that document_service relies on.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.chunk_quality_port import (
    ChunkQualityResult,
    ChunkQualityScorerPort,
)

try:
    from ragbot.infrastructure.chunk_quality.heuristic_chunk_quality_scorer import (
        HeuristicChunkQualityScorer as InfraHeuristicScorer,
    )
    from ragbot.infrastructure.chunk_quality.null_chunk_quality_scorer import (
        NullChunkQualityScorer,
    )
    from ragbot.infrastructure.chunk_quality.registry import (
        build_chunk_quality_scorer,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "chunk_quality infra subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.chunk_quality import (
    HeuristicChunkQualityScorer,
    score_chunk_for_ingest_gate,
    select_passing_indices,
)
from ragbot.shared.constants import (
    DEFAULT_CHUNK_QUALITY_MIN_CHARS,
    DEFAULT_CHUNK_QUALITY_MIN_SCORE,
    DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS,
    QUALITY_WEIGHT_INFO_DENSITY,
    QUALITY_WEIGHT_LANGUAGE,
    QUALITY_WEIGHT_NO_CORRUPTION,
    QUALITY_WEIGHT_TEXT_LENGTH,
)


# ── Weight invariants ────────────────────────────────────────────────────────


def test_quality_weights_sum_to_one() -> None:
    """Weights MUST sum to 1.0 so the aggregate score lives in [0,1].

    Catches accidental rebalancing that would silently expand / shrink the
    score range and break the ``score >= min_score`` gate semantics.
    """
    total = (
        QUALITY_WEIGHT_TEXT_LENGTH
        + QUALITY_WEIGHT_LANGUAGE
        + QUALITY_WEIGHT_INFO_DENSITY
        + QUALITY_WEIGHT_NO_CORRUPTION
    )
    assert abs(total - 1.0) < 1e-9, f"weights must sum to 1.0; got {total!r}"


def test_weights_match_spec() -> None:
    """The stream spec requires 0.3 / 0.2 / 0.2 / 0.3 weights."""
    assert QUALITY_WEIGHT_TEXT_LENGTH == 0.3
    assert QUALITY_WEIGHT_LANGUAGE == 0.2
    assert QUALITY_WEIGHT_INFO_DENSITY == 0.2
    assert QUALITY_WEIGHT_NO_CORRUPTION == 0.3


# ── Score range + clamps ─────────────────────────────────────────────────────


def test_empty_chunk_scores_zero() -> None:
    """Empty / whitespace-only input → aggregate exactly 0.0.

    Critical for the gate: an empty chunk must be droppable by ANY
    non-zero threshold without surprising the operator.
    """
    scorer = HeuristicChunkQualityScorer()
    assert scorer.score("").score == 0.0
    assert scorer.score("   \n\t  ").score == 0.0
    # Sub-scores also pinned to 0.0 so dashboards don't show NaN.
    result = scorer.score("")
    assert result.text_length_score == 0.0
    assert result.language_confidence == 0.0
    assert result.information_density == 0.0
    assert result.no_corruption_flag == 0.0


def test_score_clamped_to_unit_interval_for_arbitrary_text() -> None:
    """Aggregate MUST stay in [0,1] for ANY input — defends the gate's
    threshold comparison from out-of-range surprises."""
    samples = [
        "",
        "a",
        "a b c d e f g h i j",
        "x" * 10_000,  # extreme over-shoot
        "1 2 3 4 5 6 7 8 9 10",  # numeric-only
        "Word " * 100,  # repetitive (low density)
    ]
    for sample in samples:
        result = score_chunk_for_ingest_gate(sample)
        assert 0.0 <= result.score <= 1.0, (
            f"score out of range for sample[:20]={sample[:20]!r}: {result.score}"
        )


# ── High-quality cases ───────────────────────────────────────────────────────


def test_high_quality_english_paragraph_passes_default_threshold() -> None:
    """A normal English paragraph in the optimal-length band should
    comfortably clear the default 0.5 gate."""
    # ~800 char target — sit near the triangular peak.
    base = (
        "Ragbot is a multi-tenant retrieval-augmented generation platform "
        "that supports document ingest, semantic chunking, hybrid search "
        "across pgvector and BM25, contextual reranking and grounded "
        "answer generation. The platform is domain-neutral and isolates "
        "tenants by record_tenant_id. Bot owners control prompts, "
        "guardrails and refusal text. Operators tune system_config to "
        "balance recall and precision. "
    )
    chunk = base * 2
    # Bring length close to the OPTIMAL knee for max text_length_score.
    chunk = chunk[: DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS]
    result = score_chunk_for_ingest_gate(chunk)
    assert result.score >= DEFAULT_CHUNK_QUALITY_MIN_SCORE, (
        f"healthy paragraph must pass; got {result}"
    )
    assert result.no_corruption_flag == 1.0
    assert result.information_density > 0.0


def test_optimal_length_chunk_scores_max_text_length() -> None:
    """At exactly ``DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS`` the text_length
    sub-score is 1.0 (triangular peak)."""
    chunk = "x " * (DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS // 2)
    chunk = chunk[:DEFAULT_CHUNK_QUALITY_OPTIMAL_CHARS]
    result = score_chunk_for_ingest_gate(chunk)
    assert result.text_length_score == 1.0


# ── Low-quality cases (the cases the gate must drop) ─────────────────────────


def test_tiny_fragment_below_min_chars_drops_to_zero_text_length() -> None:
    """Char count below MIN_CHARS → text_length_score == 0.0."""
    chunk = "x" * (DEFAULT_CHUNK_QUALITY_MIN_CHARS - 1)
    result = score_chunk_for_ingest_gate(chunk)
    assert result.text_length_score == 0.0


def test_ocr_corruption_artefact_zeroes_no_corruption_flag() -> None:
    """Run of replacement-glyph artefacts ``????`` flips no_corruption to 0."""
    chunk = (
        "Important content present in this fragment ???? but the OCR "
        "decoder choked on a binary table region embedded in the page."
    )
    result = score_chunk_for_ingest_gate(chunk)
    assert result.no_corruption_flag == 0.0
    # And the aggregate should be dragged below the default gate.
    assert result.score < DEFAULT_CHUNK_QUALITY_MIN_SCORE


def test_hex_escape_artefact_zeroes_no_corruption_flag() -> None:
    """``<0xFE>`` raw-byte escape → corruption flag fails (parser leak)."""
    chunk = (
        "Normal sentence content goes here for some meaningful number "
        "of characters before <0xFE> the parser leaks raw bytes mid-text."
    )
    result = score_chunk_for_ingest_gate(chunk)
    assert result.no_corruption_flag == 0.0


def test_replacement_char_zeroes_no_corruption_flag() -> None:
    """U+FFFD REPLACEMENT CHARACTER signals decode failure."""
    chunk = (
        "A reasonable sentence that has plenty of characters but contains "
        "a stray replacement char � somewhere in the middle which "
        "indicates an upstream encoding failure."
    )
    result = score_chunk_for_ingest_gate(chunk)
    assert result.no_corruption_flag == 0.0


def test_pure_punctuation_zeroes_no_corruption_flag() -> None:
    """Pure-punctuation chunk (table-cell row leak) has zero info content."""
    chunk = "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"
    result = score_chunk_for_ingest_gate(chunk)
    assert result.no_corruption_flag == 0.0


def test_low_density_repetitive_tokens_zero_information_density() -> None:
    """Pure repetition (one unique token, many copies) → density 0."""
    chunk = ("alpha " * 200).strip()
    result = score_chunk_for_ingest_gate(chunk)
    # TTR = 1/200 = 0.005 — well below floor (0.2).
    assert result.information_density == 0.0


def test_high_density_diverse_tokens_full_information_density() -> None:
    """Mostly-unique tokens hit the density target → score 1.0."""
    chunk = " ".join(f"token{i}" for i in range(60))
    result = score_chunk_for_ingest_gate(chunk)
    # TTR = 60/60 = 1.0 — well above target.
    assert result.information_density == 1.0


# ── Gate decision (select_passing_indices) ───────────────────────────────────


def test_select_passing_indices_partitions_correctly() -> None:
    """``select_passing_indices`` returns disjoint passing/skipped index
    lists whose union covers the whole input range, plus per-chunk scores."""
    chunks = [
        # Healthy paragraph — should pass.
        (
            "Ragbot supports document ingest, hybrid retrieval, contextual "
            "reranking and grounded answer generation across tenants. "
            "Domain-neutral by design, all configuration is tenant-scoped."
        )
        * 3,
        # OCR corruption — must drop.
        "Random words appear here followed by ???? garbage that nobody can read.",
        # Empty — must drop.
        "",
        # Another healthy paragraph.
        (
            "Operators flip system_config keys to tune recall and precision. "
            "Bot owners control prompts and refusal text. The platform "
            "isolates tenants by record_tenant_id and workspace slug."
        )
        * 3,
        # OCR corruption + low density (one repeated artefact run) — drops on
        # corruption flag AND info-density, well below the gate.
        "aaaa ???? aaaa ???? aaaa ???? aaaa ????",
    ]
    passing, skipped, scores = select_passing_indices(chunks)
    # Length-aligned scores.
    assert len(scores) == len(chunks)
    # Disjoint + complete coverage.
    assert set(passing).isdisjoint(skipped)
    assert sorted(passing + skipped) == list(range(len(chunks)))
    # Healthy chunks pass; corrupted / empty / corrupted-and-sparse drop.
    assert 0 in passing
    assert 3 in passing
    assert 1 in skipped
    assert 2 in skipped
    assert 4 in skipped


def test_select_passing_indices_threshold_zero_passes_all() -> None:
    """``min_score=0.0`` is a degenerate "accept everything" gate — even
    empty chunks (score 0.0) clear ``>= 0.0``."""
    chunks = ["", "alpha", "beta gamma delta epsilon zeta"]
    passing, skipped, _ = select_passing_indices(chunks, min_score=0.0)
    assert sorted(passing) == [0, 1, 2]
    assert skipped == []


def test_select_passing_indices_threshold_one_skips_all_imperfect() -> None:
    """``min_score=1.0`` (degenerate strict gate) requires perfect score.
    Realistic chunks rarely hit 1.0 on text_length — they get skipped."""
    chunks = [
        "ok.",
        "alpha beta",
        "x" * 10_000,  # way over max
    ]
    passing, skipped, _ = select_passing_indices(chunks, min_score=1.0)
    # No realistic chunk hits 1.0 here; skip-set covers everything.
    assert passing == []
    assert sorted(skipped) == [0, 1, 2]


def test_select_passing_indices_uses_injected_scorer() -> None:
    """Verify the DI hook — when caller injects a scorer it's used end-to-end
    rather than the module singleton."""
    chunks = ["a", "b c d", "very corrupted ????"]
    # Null scorer returns 1.0 for every chunk → nothing is ever skipped,
    # which is precisely the platform-default behaviour.
    null_scorer = NullChunkQualityScorer()
    passing, skipped, scores = select_passing_indices(
        chunks, min_score=0.99, scorer=null_scorer,
    )
    assert sorted(passing) == [0, 1, 2]
    assert skipped == []
    assert all(r.score == 1.0 for r in scores)


# ── Registry + DI ────────────────────────────────────────────────────────────


def test_registry_returns_null_by_default() -> None:
    """No provider (or empty string) → NullChunkQualityScorer.

    Owner-opt-in baseline: ingest pipeline must never start enforcing a
    quality threshold without an explicit operator decision."""
    scorer = build_chunk_quality_scorer(None)
    assert isinstance(scorer, NullChunkQualityScorer)
    scorer = build_chunk_quality_scorer("")
    assert isinstance(scorer, NullChunkQualityScorer)


def test_registry_returns_heuristic_when_requested() -> None:
    """``"heuristic"`` resolves to the shared-module scorer."""
    scorer = build_chunk_quality_scorer("heuristic")
    # Two re-exports of the same class — accept either.
    assert isinstance(scorer, HeuristicChunkQualityScorer | InfraHeuristicScorer)
    # Functional smoke: scoring an OCR-garbage chunk drops it.
    result = scorer.score("garbage @@@@ ???? <0xFF> output")
    assert 0.0 <= result.score <= 1.0
    assert result.no_corruption_flag == 0.0


def test_registry_unknown_provider_falls_back_to_null() -> None:
    """Typo / unknown provider must not crash ingest — fail-soft to null."""
    scorer = build_chunk_quality_scorer("zzz_does_not_exist")
    assert isinstance(scorer, NullChunkQualityScorer)


def test_registry_lists_only_registered_providers() -> None:
    """``list_providers`` returns the canonical sorted set — used by ops
    to know which scorer keys are flippable in system_config."""
    providers = list_providers()
    assert providers == ["heuristic", "null"]


def test_port_compliance_for_both_strategies() -> None:
    """Both Null + Heuristic adapters MUST be runtime ``ChunkQualityScorerPort``.

    Defence against accidentally dropping the ``score`` method during a
    refactor — without Protocol compliance the registry would still build
    them but the document_service call-site would explode at runtime."""
    assert isinstance(NullChunkQualityScorer(), ChunkQualityScorerPort)
    assert isinstance(HeuristicChunkQualityScorer(), ChunkQualityScorerPort)


def test_null_scorer_returns_unit_result() -> None:
    """Null scorer yields all-1.0 result so any threshold passes — the
    documented default-OFF semantics."""
    result = NullChunkQualityScorer().score("anything at all")
    assert result == ChunkQualityResult(
        score=1.0,
        text_length_score=1.0,
        language_confidence=1.0,
        information_density=1.0,
        no_corruption_flag=1.0,
    )


# ── Result dataclass invariants ──────────────────────────────────────────────


def test_chunk_quality_result_is_frozen() -> None:
    """``ChunkQualityResult`` MUST be immutable — protects downstream
    persistence (metadata_json) from being mutated post-score."""
    result = ChunkQualityResult(
        score=0.5,
        text_length_score=0.5,
        language_confidence=0.5,
        information_density=0.5,
        no_corruption_flag=0.5,
    )
    try:
        result.score = 1.0  # type: ignore[misc]
    except (AttributeError, TypeError):
        return
    raise AssertionError("ChunkQualityResult must be frozen / immutable")


def test_module_function_matches_class_score() -> None:
    """The module-level convenience MUST be equivalent to scoring through
    a fresh class instance — no hidden state or divergence."""
    chunk = "a moderately long sentence with enough variation in tokens."
    via_class = HeuristicChunkQualityScorer().score(chunk)
    via_func = score_chunk_for_ingest_gate(chunk)
    assert via_class == via_func
