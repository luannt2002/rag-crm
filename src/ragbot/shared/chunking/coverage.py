"""[T1-Smartness] Lossless-coverage gate — char-offset structural superset.

Deterministic, no LLM, domain-neutral.

WHY this exists
---------------
After a chunking strategy runs we must guarantee NO source text was silently
dropped on the floor — a dropped middle span is how a corpus loses the one
sentence that carries the answer (silent-failure class, the opposite of
HALLU=0 honesty). The pre-existing numeric gate checks only that every NUMBER
survives chunking. This module is the **structural superset**: it checks that
every CHARACTER of the source is covered by at least one chunk (numbers are a
subset of characters, so full char-coverage implies full numeric-coverage).

HOW it works
------------
Chunking strategies in this package emit chunk TEXT (not char spans), and they
freely re-strip / re-join whitespace (``smart_chunk`` returns
``[c.strip() for c in chunks]``). So we cannot diff char offsets directly. We
instead:

1. Whitespace-normalize the source (collapse every run of whitespace to a
   single space) AND remember, for each normalized position, the ORIGINAL
   source offset it came from.
2. Whitespace-normalize each chunk the same way and locate it as a substring of
   the normalized source (left-to-right, advancing a cursor so repeated chunks
   match successive occurrences).
3. Union the matched intervals (overlap-tolerant — overlapping/duplicated chunks
   collapse to one covered interval), then report the complement as the
   UNCOVERED spans, mapped back to ORIGINAL source offsets for repair/reporting.

``coverage_ratio`` is computed in normalized space (covered normalized chars /
total normalized chars) so leading/trailing/inter-block whitespace the chunker
legitimately discards does not count against coverage.

This is a PURE function: same inputs → same output, no I/O, no global state.
It is OBSERVE-only at the call site (the caller logs / emits a metric and does
NOT raise) — this module never raises on a gap; it returns a structured result.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Final

# Tunables live in the constants SSoT (CLAUDE.md zero-hardcode) — caller may still
# override per-invocation via the ``tol`` / ``min_locatable_chars`` arguments.
from ragbot.shared.constants import (
    DEFAULT_COVERAGE_TOL,
    DEFAULT_MIN_LOCATABLE_CHARS,
)

# Collapse any run of unicode whitespace to a single space.
_WS_RUN: Final[re.Pattern[str]] = re.compile(r"\s+")


@dataclass(frozen=True)
class CoverageResult:
    """Outcome of a lossless-coverage check.

    Attributes
    ----------
    ok:
        ``coverage_ratio >= 1.0 - tol``. ``True`` means lossless within tol.
    coverage_ratio:
        Covered normalized chars / total normalized chars, in ``[0.0, 1.0]``.
        ``1.0`` for an empty (no non-whitespace) source.
    uncovered_spans:
        Sorted, non-overlapping ``(start_char, end_char)`` half-open ranges in
        ORIGINAL source offsets that no chunk covered. Empty when fully covered.
    covered_chars / total_chars:
        Counts in NORMALIZED space (the ratio numerator / denominator).
    unlocated_chunks:
        Count of chunks that could not be located as a substring of the
        normalized source (e.g. chunker injected synthetic header text). These
        do not contribute coverage; surfaced for observability.
    tol:
        The tolerance used for the ``ok`` verdict.
    """

    ok: bool
    coverage_ratio: float
    uncovered_spans: list[tuple[int, int]] = field(default_factory=list)
    covered_chars: int = 0
    total_chars: int = 0
    unlocated_chunks: int = 0
    tol: float = DEFAULT_COVERAGE_TOL


def _normalize_with_offsets(source: str) -> tuple[str, list[int]]:
    """Whitespace-normalize ``source``; return normalized text + offset map.

    The returned ``offsets`` list is parallel to the normalized string:
    ``offsets[i]`` is the ORIGINAL source index of normalized char ``i``.
    Runs of whitespace collapse to a single space whose mapped offset is the
    first whitespace char of the run. Leading/trailing whitespace is dropped.
    """
    norm_chars: list[str] = []
    offsets: list[int] = []
    prev_ws = True  # treat string start as preceding-whitespace → strip leading
    for idx, ch in enumerate(source):
        if ch.isspace():
            if not prev_ws:
                norm_chars.append(" ")
                offsets.append(idx)
            prev_ws = True
        else:
            norm_chars.append(ch)
            offsets.append(idx)
            prev_ws = False
    # Strip a single trailing collapsed space (run at end of source).
    if norm_chars and norm_chars[-1] == " ":
        norm_chars.pop()
        offsets.pop()
    return "".join(norm_chars), offsets


def _normalize_chunk(text: str) -> str:
    """Whitespace-normalize a chunk the same way as the source."""
    return _WS_RUN.sub(" ", text).strip()


def _merge_intervals(
    intervals: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """Union half-open ``[start, end)`` intervals; overlap/adjacency-tolerant."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:  # overlap OR touch → extend
            if end > last_end:
                merged[-1] = (last_start, end)
        else:
            merged.append((start, end))
    return merged


def check_chunk_gaps(
    chunk_texts: list[str],
    source_text: str,
    tol: float = DEFAULT_COVERAGE_TOL,
    *,
    min_locatable_chars: int = DEFAULT_MIN_LOCATABLE_CHARS,
) -> CoverageResult:
    """Compute character COVERAGE of ``source_text`` by ``chunk_texts``.

    Locates each chunk in the whitespace-normalized source, unions the matched
    intervals (overlap-tolerant), and reports the uncovered complement mapped
    back to ORIGINAL source char offsets plus a coverage ratio. A structural
    superset of the numeric-only gate: full char-coverage ⇒ full numeric-coverage.

    Deterministic, no LLM, domain-neutral. Never raises on a gap — returns a
    :class:`CoverageResult`. OBSERVE-only: the caller decides what to do.

    Parameters
    ----------
    chunk_texts:
        The chunk strings a strategy emitted (order need not match the source;
        located left-to-right with a forward cursor so duplicate chunks map to
        successive occurrences).
    source_text:
        The pre-chunk source markdown / text.
    tol:
        Coverage slack for the ``ok`` verdict; ``ok`` iff
        ``coverage_ratio >= 1.0 - tol``.
    min_locatable_chars:
        Chunks whose normalized length is below this are skipped for locating
        (too short to position reliably). ``0`` disables the floor.

    Returns
    -------
    CoverageResult
    """
    norm_source, offsets = _normalize_with_offsets(source_text)
    total = len(norm_source)

    # Empty / whitespace-only source: nothing to cover → vacuously lossless.
    if total == 0:
        return CoverageResult(
            ok=True,
            coverage_ratio=1.0,
            uncovered_spans=[],
            covered_chars=0,
            total_chars=0,
            unlocated_chunks=0,
            tol=tol,
        )

    intervals: list[tuple[int, int]] = []
    unlocated = 0
    cursor = 0  # forward search cursor in normalized source
    for raw in chunk_texts:
        norm_chunk = _normalize_chunk(raw)
        if len(norm_chunk) < min_locatable_chars:
            # Empty or sub-floor fragment carries no positional signal.
            if norm_chunk:
                unlocated += 1
            continue
        pos = norm_source.find(norm_chunk, cursor)
        if pos == -1:
            # Not found ahead of the cursor — retry from the start (chunk order
            # may differ from source order). Forward-cursor is only an
            # optimisation for the common in-order case.
            pos = norm_source.find(norm_chunk)
        if pos == -1:
            unlocated += 1
            continue
        end = pos + len(norm_chunk)
        intervals.append((pos, end))
        cursor = end

    merged = _merge_intervals(intervals)

    # Complement of covered intervals → candidate uncovered spans in NORMALIZED
    # space. A gap that is whitespace-only is NOT dropped content: it is the
    # single normalized space sitting at a chunk boundary (a chunker that splits
    # "...gamma | delta..." owns neither the joining space). Bridge those so a
    # benign boundary does not register as a gap and does not depress coverage.
    uncovered_norm: list[tuple[int, int]] = []
    prev_end = 0
    for start, end in merged:
        if start > prev_end and norm_source[prev_end:start].strip():
            uncovered_norm.append((prev_end, start))
        prev_end = end
    if prev_end < total and norm_source[prev_end:total].strip():
        uncovered_norm.append((prev_end, total))

    # Covered = total minus the GENUINE (non-whitespace) gaps, so boundary
    # whitespace counts as covered. coverage_ratio is then 1.0 for a clean
    # split and < 1.0 only when real content is missing.
    gap_chars = sum(n_end - n_start for n_start, n_end in uncovered_norm)
    covered = total - gap_chars
    coverage_ratio = covered / total

    uncovered_spans: list[tuple[int, int]] = []
    for n_start, n_end in uncovered_norm:
        # offsets[i] = original index of normalized char i. The half-open
        # normalized span [n_start, n_end) maps to original
        # [offsets[n_start], offsets[n_end-1] + 1).
        orig_start = offsets[n_start]
        orig_end = offsets[n_end - 1] + 1
        uncovered_spans.append((orig_start, orig_end))

    return CoverageResult(
        ok=coverage_ratio >= 1.0 - tol,
        coverage_ratio=coverage_ratio,
        uncovered_spans=uncovered_spans,
        covered_chars=covered,
        total_chars=total,
        unlocated_chunks=unlocated,
        tol=tol,
    )
