"""Unit tests for the lossless-coverage gate (P0-2).

``src/ragbot/shared/chunking/coverage.py`` is a deterministic, domain-neutral,
char-offset structural superset of the numeric-only gate: it locates each chunk
in the whitespace-normalized source and reports the uncovered char spans plus a
coverage ratio. These tests pin REAL behaviour (values + span offsets), not
``assert True`` / ``is not None``.

Cases required by the task:
  - full coverage → ok, ratio 1.0, no gaps
  - a dropped MIDDLE span is flagged (the silent-failure class)
  - overlap-tolerant (duplicate / overlapping chunks collapse, ratio stays 1.0)
  - whitespace-normalized (chunker re-strip / re-join does not count as a gap)
  - empty inputs (empty source, no chunks, whitespace-only source)
Plus offset-mapping correctness and a Vietnamese (VN/VND) happy-path that proves
the default ``vi`` pack is byte-identically lossless.
"""
from __future__ import annotations

import pytest

from ragbot.shared.chunking.coverage import (
    DEFAULT_COVERAGE_TOL,
    CoverageResult,
    check_chunk_gaps,
)


class TestFullCoverage:
    def test_contiguous_chunks_full_coverage(self) -> None:
        source = "alpha beta gamma delta epsilon"
        chunks = ["alpha beta gamma", "delta epsilon"]
        res = check_chunk_gaps(chunks, source)
        assert isinstance(res, CoverageResult)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []
        assert res.covered_chars == res.total_chars
        assert res.unlocated_chunks == 0

    def test_single_chunk_equals_source(self) -> None:
        source = "the entire document body verbatim"
        res = check_chunk_gaps([source], source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []

    def test_chunks_out_of_order_still_full_coverage(self) -> None:
        # Order need not match source; the locate retries from start when the
        # forward cursor overshoots.
        source = "first second third fourth"
        chunks = ["third fourth", "first second"]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []


class TestDroppedMiddleSpan:
    def test_dropped_middle_chunk_is_flagged(self) -> None:
        # The classic silent-failure: a middle block never made it into a chunk.
        source = "head section MIDDLE-BLOCK tail section"
        #         0123456789...
        chunks = ["head section", "tail section"]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert res.ok is False
        assert res.coverage_ratio < 1.0
        # Exactly one uncovered span, in the middle, in ORIGINAL offsets.
        assert len(res.uncovered_spans) == 1
        start, end = res.uncovered_spans[0]
        gap_text = source[start:end]
        assert "MIDDLE-BLOCK" in gap_text
        # The gap must not swallow the covered neighbours.
        assert "head" not in gap_text
        assert "tail" not in gap_text

    def test_dropped_trailing_span_is_flagged(self) -> None:
        source = "intro body conclusion-that-was-lost"
        chunks = ["intro body"]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert res.ok is False
        assert len(res.uncovered_spans) == 1
        start, end = res.uncovered_spans[0]
        assert "conclusion-that-was-lost" in source[start:end]

    def test_dropped_leading_span_is_flagged(self) -> None:
        source = "preamble-dropped then the kept body"
        chunks = ["then the kept body"]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert res.ok is False
        assert len(res.uncovered_spans) == 1
        start, end = res.uncovered_spans[0]
        assert start == 0
        assert "preamble-dropped" in source[start:end]

    def test_two_separate_gaps_both_reported(self) -> None:
        # Anchor chunks are multi-char (above the locatable floor); the two
        # GAP blocks between them were dropped.
        source = "AA GAP1 BB GAP2 CC"
        chunks = ["AA", "BB", "CC"]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert res.ok is False
        gap_texts = [source[s:e] for s, e in res.uncovered_spans]
        assert any("GAP1" in g for g in gap_texts)
        assert any("GAP2" in g for g in gap_texts)
        assert len(res.uncovered_spans) == 2

    def test_tol_absorbs_small_gap(self) -> None:
        # A 1-char gap in a long source is within the default tolerance band.
        body = "x" * 200
        source = body + " " + "y"  # the lone trailing 'y' gets dropped
        chunks = [body]
        res = check_chunk_gaps(chunks, source)  # default tol
        assert res.coverage_ratio < 1.0
        # 200/201 ≈ 0.995 >= 1 - 0.02 → ok despite the gap existing.
        assert res.ok is True
        assert res.uncovered_spans  # gap is still REPORTED for observability


class TestOverlapTolerant:
    def test_overlapping_chunks_collapse_to_full(self) -> None:
        source = "one two three four five"
        # Adjacent chunks share "three" — sliding-window overlap.
        chunks = ["one two three", "three four five"]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []
        # Covered counts the UNION, never double-counts the overlap.
        assert res.covered_chars == res.total_chars

    def test_duplicate_chunks_do_not_inflate_coverage(self) -> None:
        source = "repeated content here"
        chunks = [source, source, source]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.covered_chars == res.total_chars

    def test_fully_nested_chunk(self) -> None:
        source = "outer inner-most outer-end"
        chunks = [source, "inner-most"]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0


class TestWhitespaceNormalized:
    def test_chunker_restrip_join_not_counted_as_gap(self) -> None:
        # Source has irregular whitespace; chunker emitted clean single-spaced.
        source = "line one\n\n   line two\t\tline three\n"
        chunks = ["line one", "line two line three"]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []

    def test_leading_trailing_whitespace_ignored(self) -> None:
        source = "\n\n   core body text   \n\n"
        chunks = ["core body text"]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []

    def test_internal_whitespace_run_collapses(self) -> None:
        source = "word1      word2"  # 6 spaces between
        chunks = ["word1 word2"]  # single space
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0


class TestEmptyInputs:
    def test_empty_source_is_vacuously_lossless(self) -> None:
        res = check_chunk_gaps([], "")
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []
        assert res.total_chars == 0

    def test_whitespace_only_source_is_lossless(self) -> None:
        res = check_chunk_gaps([], "   \n\t  ")
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.total_chars == 0

    def test_no_chunks_over_nonempty_source_is_total_gap(self) -> None:
        source = "this whole body was dropped"
        res = check_chunk_gaps([], source, tol=0.0)
        assert res.ok is False
        assert res.coverage_ratio == 0.0
        assert len(res.uncovered_spans) == 1
        start, end = res.uncovered_spans[0]
        assert start == 0
        assert source[start:end] == source

    def test_empty_string_chunks_ignored(self) -> None:
        source = "real content body"
        chunks = ["", "   ", "real content body"]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0


class TestUnlocatedChunks:
    def test_synthetic_chunk_text_counted_unlocated(self) -> None:
        # A chunk whose text is NOT in the source (e.g. injected header) does
        # not contribute coverage and is surfaced for observability.
        source = "genuine source paragraph only"
        chunks = ["genuine source paragraph only", "SYNTHETIC HEADER NOT IN SRC"]
        res = check_chunk_gaps(chunks, source)
        assert res.unlocated_chunks == 1
        # The real chunk still gives full coverage of the real source.
        assert res.coverage_ratio == 1.0
        assert res.ok is True

    def test_sub_floor_fragment_not_false_matched(self) -> None:
        # A 1-char fragment must not false-match all over the source.
        source = "aaaa bbbb cccc"
        chunks = ["aaaa bbbb cccc", "a"]
        res = check_chunk_gaps(chunks, source, min_locatable_chars=2)
        # "a" is below the floor → skipped, counted unlocated, no false coverage.
        assert res.unlocated_chunks == 1
        assert res.coverage_ratio == 1.0


class TestOffsetMappingCorrectness:
    def test_uncovered_span_offsets_index_original_source(self) -> None:
        # Whitespace before the gap means normalized offsets != original offsets;
        # the returned spans MUST be original-source offsets.
        source = "AA    BBBB    CC"
        #         0123456789...      'BBBB' at original index 6..10
        chunks = ["AA", "CC"]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert len(res.uncovered_spans) == 1
        start, end = res.uncovered_spans[0]
        assert "BBBB" in source[start:end]
        # Sanity: the offsets land inside the original string range.
        assert 0 <= start < end <= len(source)

    def test_coverage_ratio_is_normalized_fraction(self) -> None:
        # half the (normalized) chars covered → ratio ~0.5.
        source = "kept1 kept2 lost1 lost2"
        chunks = ["kept1 kept2"]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert 0.0 < res.coverage_ratio < 1.0
        # normalized source length is 23; 'kept1 kept2' is 11 → 11/23.
        assert res.coverage_ratio == pytest.approx(11 / 23, abs=1e-9)


class TestDefaultsAndContract:
    def test_default_tol_constant_used(self) -> None:
        source = "body"
        res = check_chunk_gaps([source], source)
        assert res.tol == DEFAULT_COVERAGE_TOL

    def test_result_is_frozen(self) -> None:
        res = check_chunk_gaps(["x"], "x")
        with pytest.raises((AttributeError, TypeError)):
            res.ok = False  # type: ignore[misc]

    def test_deterministic_same_inputs_same_output(self) -> None:
        source = "deterministic body with GAP here"
        chunks = ["deterministic body with", "here"]
        a = check_chunk_gaps(chunks, source, tol=0.0)
        b = check_chunk_gaps(chunks, source, tol=0.0)
        assert a == b


class TestVietnameseHappyPath:
    """Byte-identical VN/VND default-pack happy-path: lossless, no gap, no
    behaviour change for Vietnamese ingest. Proves the gate does not perturb
    the default ``vi`` corpus path."""

    def test_vn_vnd_money_doc_full_coverage(self) -> None:
        source = (
            "Điều 1. Phí dịch vụ là 1.500.000 đồng mỗi tháng.\n\n"
            "Điều 2. Mức phạt tối đa là 50.000.000 VND theo quy định."
        )
        chunks = [
            "Điều 1. Phí dịch vụ là 1.500.000 đồng mỗi tháng.",
            "Điều 2. Mức phạt tối đa là 50.000.000 VND theo quy định.",
        ]
        res = check_chunk_gaps(chunks, source)
        assert res.ok is True
        assert res.coverage_ratio == 1.0
        assert res.uncovered_spans == []
        assert res.unlocated_chunks == 0

    def test_vn_dropped_money_clause_flagged(self) -> None:
        # If the chunker dropped the clause carrying the VND figure, the gate
        # flags it (numeric loss is a subset of the char gap reported here).
        source = (
            "Điều 1. Phí dịch vụ là 1.500.000 đồng mỗi tháng. "
            "Điều 2. Mức phạt tối đa là 50.000.000 VND."
        )
        chunks = ["Điều 1. Phí dịch vụ là 1.500.000 đồng mỗi tháng."]
        res = check_chunk_gaps(chunks, source, tol=0.0)
        assert res.ok is False
        gap_texts = [source[s:e] for s, e in res.uncovered_spans]
        assert any("50.000.000 VND" in g for g in gap_texts)
