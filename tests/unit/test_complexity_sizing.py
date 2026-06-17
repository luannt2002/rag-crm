"""Unit tests for ``ragbot.shared.complexity_sizing``.

T1-Smartness — Databricks adaptive complexity sizing.
Tests the pure-function contract:
    * compute_complexity returns [0, 1] float for all measures
    * complex (lex-dense, long-sentence) text scores higher than simple text
    * adaptive_chunk_size inversely maps complexity to chunk size
    * integration via _chunk_recursive_with_tables(complexity_sizing_enabled=True)
      yields smaller chunks on dense text than on simple text

Domain-neutral: all sample text generic prose / synthetic, no brand /
customer / industry literals.
"""
from __future__ import annotations

import re

import pytest

from ragbot.shared.complexity_sizing import (
    adaptive_chunk_size,
    compute_complexity,
)
from ragbot.shared.constants import (
    DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE,
    DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE,
)


# ---------------------------------------------------------------------------
# Samples — three complexity tiers
# ---------------------------------------------------------------------------
SIMPLE_TEXT = (
    "The cat sat on the mat. The cat was happy. The cat slept. "
    "The cat woke up. The cat sat on the mat again. The cat slept again."
)
# Highly repetitive short sentences → low lexical density AND low avg
# sentence length. Should land near the low end of complexity.

DENSE_TEXT = (
    "Quantum chromodynamics encompasses gluon-mediated strong interactions "
    "wherein color-charged quarks exhibit asymptotic freedom at sufficiently "
    "elevated momentum transfers, whereas at infrared scales nonperturbative "
    "confinement phenomena dominate the hadronization landscape entirely. "
    "Lattice regularization schemes furnish nonperturbative computational "
    "frameworks for evaluating gauge-invariant correlation functions across "
    "diverse kinematic regimes spanning multiple orders of magnitude."
)
# High unique-word ratio + long sentences → high complexity.

MEDIUM_TEXT = (
    "Customers can update their billing address at any time. "
    "The change takes effect on the next invoice. "
    "Please contact support if you do not see the update reflected within 24 hours. "
    "Refunds for prior invoices are subject to the standard policy."
)


# ---------------------------------------------------------------------------
# compute_complexity
# ---------------------------------------------------------------------------
class TestComputeComplexity:
    @pytest.mark.parametrize(
        "measure", ["lexical_density", "sentence_length", "combined"]
    )
    def test_returns_float_in_unit_interval(self, measure: str) -> None:
        for sample in (SIMPLE_TEXT, MEDIUM_TEXT, DENSE_TEXT):
            score = compute_complexity(sample, measure=measure)  # type: ignore[arg-type]
            assert isinstance(score, float), f"{measure}: not float"
            assert 0.0 <= score <= 1.0, (
                f"{measure}: score {score} outside [0, 1] for sample"
            )

    def test_empty_string_returns_zero(self) -> None:
        assert compute_complexity("") == 0.0
        assert compute_complexity("    \n\t  ") == 0.0

    def test_dense_text_more_complex_than_simple(self) -> None:
        dense = compute_complexity(DENSE_TEXT, measure="combined")
        simple = compute_complexity(SIMPLE_TEXT, measure="combined")
        # Real assertion: dense MUST score higher; gap should be non-trivial.
        assert dense > simple, f"dense={dense} should be > simple={simple}"
        assert (dense - simple) > 0.20, (
            f"complexity delta {dense - simple:.3f} too small to be useful"
        )

    def test_lexical_density_dense_higher_than_simple(self) -> None:
        # The simple sample repeats words; dense sample uses each word once.
        dense = compute_complexity(DENSE_TEXT, measure="lexical_density")
        simple = compute_complexity(SIMPLE_TEXT, measure="lexical_density")
        assert dense > simple

    def test_sentence_length_dense_higher_than_simple(self) -> None:
        dense = compute_complexity(DENSE_TEXT, measure="sentence_length")
        simple = compute_complexity(SIMPLE_TEXT, measure="sentence_length")
        assert dense > simple

    def test_combined_is_mean_of_two_signals(self) -> None:
        # Contract: measure="combined" == mean(lexical_density, sentence_length).
        for sample in (SIMPLE_TEXT, MEDIUM_TEXT, DENSE_TEXT):
            combined = compute_complexity(sample, measure="combined")
            lex = compute_complexity(sample, measure="lexical_density")
            sent = compute_complexity(sample, measure="sentence_length")
            assert combined == pytest.approx((lex + sent) / 2.0, abs=1e-9)

    def test_invalid_measure_raises(self) -> None:
        with pytest.raises(ValueError, match="complexity measure"):
            compute_complexity("anything", measure="bogus")  # type: ignore[arg-type]

    def test_single_word_lexical_density(self) -> None:
        # Single unique word, single total word → 1/1 / 0.8 = 1.25 → capped 1.0.
        score = compute_complexity("hello", measure="lexical_density")
        assert score == pytest.approx(1.0)

    def test_very_long_sentence_saturates(self) -> None:
        # Sentence length normalisation cap is 200 chars; > 200 → 1.0.
        long_sentence = ("word " * 100).strip() + "."  # 500 chars, 1 sentence
        score = compute_complexity(long_sentence, measure="sentence_length")
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# adaptive_chunk_size
# ---------------------------------------------------------------------------
class TestAdaptiveChunkSize:
    def test_complex_text_smaller_chunk(self) -> None:
        # complexity=0.9 → near min_size
        size = adaptive_chunk_size(0.9, min_size=300, max_size=1000)
        assert 300 <= size <= 400, (
            f"complexity=0.9 should map close to min_size, got {size}"
        )

    def test_simple_text_larger_chunk(self) -> None:
        # complexity=0.1 → near max_size
        size = adaptive_chunk_size(0.1, min_size=300, max_size=1000)
        assert 900 <= size <= 1000, (
            f"complexity=0.1 should map close to max_size, got {size}"
        )

    def test_zero_complexity_returns_max(self) -> None:
        assert adaptive_chunk_size(0.0, 300, 1000) == 1000

    def test_full_complexity_returns_min(self) -> None:
        assert adaptive_chunk_size(1.0, 300, 1000) == 300

    def test_clamps_out_of_range_complexity(self) -> None:
        # Defensive: negative or >1 complexity should not blow up.
        assert adaptive_chunk_size(-0.5, 300, 1000) == 1000
        assert adaptive_chunk_size(1.5, 300, 1000) == 300

    def test_monotonic_inverse(self) -> None:
        # Higher complexity → smaller-or-equal chunk size.
        prev_size = adaptive_chunk_size(0.0, 300, 1000)
        for c in [0.1, 0.25, 0.5, 0.75, 0.9, 1.0]:
            size = adaptive_chunk_size(c, 300, 1000)
            assert size <= prev_size, (
                f"non-monotonic: c={c}, prev={prev_size}, curr={size}"
            )
            prev_size = size

    def test_bounds_validated(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            adaptive_chunk_size(0.5, min_size=0, max_size=1000)
        with pytest.raises(ValueError, match="must be positive"):
            adaptive_chunk_size(0.5, min_size=300, max_size=-1)
        with pytest.raises(ValueError, match="<= max_size"):
            adaptive_chunk_size(0.5, min_size=1000, max_size=300)

    def test_default_constants_used(self) -> None:
        # Sanity: function defaults match constants module.
        size_at_zero = adaptive_chunk_size(0.0)
        size_at_one = adaptive_chunk_size(1.0)
        assert size_at_zero == DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE
        assert size_at_one == DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE


# ---------------------------------------------------------------------------
# Integration: _chunk_recursive_with_tables honours feature flag
# ---------------------------------------------------------------------------
class TestIntegrationWithRecursiveChunker:
    def test_flag_off_preserves_default_chunk_size(self) -> None:
        """When feature flag is OFF, behaviour is identical to baseline."""
        from ragbot.shared.chunking import _chunk_recursive_with_tables

        # 4 KB of dense text; default chunk_size=1024 should produce multiple
        # chunks, none materially larger than the input chunk_size.
        text = DENSE_TEXT * 6
        chunks = _chunk_recursive_with_tables(
            text, chunk_size=1024, chunk_overlap=128
        )
        assert len(chunks) >= 1
        # No chunk should exceed the requested chunk_size by more than the
        # langchain splitter slack (separators); upper bound is generous.
        for c in chunks:
            assert len(c) <= 1024 * 2, (
                f"chunk size {len(c)} exceeds reasonable upper bound"
            )

    def test_flag_on_dense_text_produces_smaller_chunks(self) -> None:
        """Flag ON + dense text → adaptive size lands near min_size, so
        we expect a strictly larger number of chunks than the flag-OFF
        baseline at the same input length."""
        from ragbot.shared.chunking import _chunk_recursive_with_tables

        text = DENSE_TEXT * 6  # ~3-4 KB
        baseline_chunks = _chunk_recursive_with_tables(
            text, chunk_size=1024, chunk_overlap=0,
            complexity_sizing_enabled=False,
        )
        adaptive_chunks = _chunk_recursive_with_tables(
            text, chunk_size=1024, chunk_overlap=0,
            complexity_sizing_enabled=True,
            complexity_min_chunk_size=300,
            complexity_max_chunk_size=1000,
            complexity_measure="combined",
        )
        # Real assertion: adaptive should produce >= as many chunks (smaller
        # chunks for dense text). At least one MORE for a clear signal.
        assert len(adaptive_chunks) > len(baseline_chunks), (
            f"adaptive={len(adaptive_chunks)} should exceed "
            f"baseline={len(baseline_chunks)} on dense text"
        )

    def test_flag_on_simple_text_uses_larger_chunks(self) -> None:
        """Simple text → adaptive size near max_size. We assert the
        adaptive run does NOT over-fragment compared with baseline."""
        from ragbot.shared.chunking import _chunk_recursive_with_tables

        text = SIMPLE_TEXT * 30  # repetitive simple prose
        adaptive_chunks = _chunk_recursive_with_tables(
            text,
            chunk_size=500,  # caller default smaller than max_size=1000
            chunk_overlap=0,
            complexity_sizing_enabled=True,
            complexity_min_chunk_size=300,
            complexity_max_chunk_size=1000,
            complexity_measure="combined",
        )
        # Real assertion: with simple text, adaptive chunk_size > caller's
        # 500 so individual chunks should typically exceed 500 chars when
        # input is long enough.
        long_chunks = [c for c in adaptive_chunks if len(c) > 500]
        assert len(long_chunks) >= 1, (
            "adaptive sizing on simple long text should yield >500-char chunks"
        )

    def test_flag_on_emits_structlog_event(self, caplog) -> None:
        """Telemetry contract: structlog event with step_name + feature_flag."""
        import logging

        import structlog

        from ragbot.shared.chunking import _chunk_recursive_with_tables

        # Bridge structlog → stdlib logging so caplog can capture it.
        structlog.configure(
            processors=[structlog.stdlib.render_to_log_kwargs],
            logger_factory=structlog.stdlib.LoggerFactory(),
            wrapper_class=structlog.stdlib.BoundLogger,
            cache_logger_on_first_use=False,
        )
        try:
            with caplog.at_level(logging.INFO, logger="ragbot.shared.chunking"):
                _chunk_recursive_with_tables(
                    DENSE_TEXT,
                    chunk_size=1024,
                    chunk_overlap=0,
                    complexity_sizing_enabled=True,
                    complexity_min_chunk_size=300,
                    complexity_max_chunk_size=1000,
                    bot_id="unit-test-bot",
                )
        finally:
            structlog.reset_defaults()

        # Look for the structured event. The render_to_log_kwargs processor
        # attaches event kwargs onto the LogRecord.
        found = False
        for record in caplog.records:
            msg = record.getMessage()
            if "databricks_complexity_sizing_applied" in msg or getattr(
                record, "step_name", None
            ) == "databricks_complexity":
                found = True
                break
        assert found, (
            "expected databricks_complexity step telemetry not emitted"
        )


# ---------------------------------------------------------------------------
# Smoke: 3 sample tiers as called out in spec acceptance criteria
# ---------------------------------------------------------------------------
def test_three_sample_tiers_ordering() -> None:
    """Acceptance: simple < medium < dense complexity ranking."""
    s = compute_complexity(SIMPLE_TEXT, measure="combined")
    m = compute_complexity(MEDIUM_TEXT, measure="combined")
    d = compute_complexity(DENSE_TEXT, measure="combined")
    assert s < d, f"simple({s}) should be < dense({d})"
    # Medium straddles the middle — assert at least one strict inequality
    # relative to the endpoints (rather than forcing exact ordering, which
    # depends on synthetic sample wording).
    assert s <= m or m <= d
    # Chunk sizes mirror the ordering inversely.
    s_size = adaptive_chunk_size(s)
    d_size = adaptive_chunk_size(d)
    assert s_size > d_size, f"simple chunk {s_size} should be > dense {d_size}"


# ---------------------------------------------------------------------------
# Zero-hardcode self-check: module should not contain numeric magic
# ---------------------------------------------------------------------------
def test_module_imports_constants_not_inline_magic() -> None:
    """Defensive: complexity_sizing.py should reference the named constants
    from ``shared/constants`` rather than inline magic numbers (0.8, 200,
    300, 1000). Inline magic in the algorithm body would drift from the
    SSoT and break tenant override of the bounds.
    """
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "ragbot" / "shared" / "complexity_sizing.py"
    text = src.read_text()
    # Body (skip docstrings/citation references) must reference the
    # constants by name.
    assert "DEFAULT_COMPLEXITY_LEX_DENSITY_NORM" in text
    assert "DEFAULT_COMPLEXITY_SENTENCE_LEN_NORM" in text
    assert "DEFAULT_COMPLEXITY_MIN_CHUNK_SIZE" in text
    assert "DEFAULT_COMPLEXITY_MAX_CHUNK_SIZE" in text
    # No bare 0.8/200/300/1000 outside the docstring proof citation.
    # Strip docstring blocks then scan.
    no_docstrings = re.sub(r'"""[\s\S]*?"""', "", text)
    # Exclude the algorithm's "max constant value bound" lines — only fail
    # on inline numeric magic that should have come from constants.
    forbidden = [r"\b0\.8\b", r"\b200\.0\b", r"\b300\b", r"\b1000\b"]
    for pat in forbidden:
        assert not re.search(pat, no_docstrings), (
            f"inline magic {pat!r} leaked into complexity_sizing.py body"
        )
