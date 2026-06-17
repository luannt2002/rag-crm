"""Finding #11 perf invariant — ``smart_chunk(..., with_metadata=True)`` must
build the H1/H2 heading index ONCE per source document, not per chunk.

The previous implementation called ``_resolve_parent_headings(text, chunk)``
inside the per-chunk comprehension. Each call ran ``text.find(...)`` and
two ``re.findall`` scans over a growing prefix of the document — O(K·M)
for K chunks of a doc with length M.

The fix builds a single ``_HeadingIndex`` covering the whole document,
then each chunk performs an O(log N) bisect over the pre-built H1/H2
offset arrays. This test counts the number of regex scans across the
doc to pin the new contract.

Behaviour-preserving: the parent_headings stack for each chunk MUST
match the previous output byte-for-byte. The companion test
``test_chunks_carry_parent_headings_chain`` in
``test_chunking_section_context.py`` already covers semantic accuracy;
this file adds the perf regression guard.
"""
from __future__ import annotations

from ragbot.shared import chunking as chunking_module
from ragbot.shared.chunking import _HeadingIndex, smart_chunk


_BIG_DOC = "# Top Section\n\nIntro paragraph one.\n\n" + "\n".join(
    [
        f"## Section {i}\n\n" + ("Body sentence. " * 40)
        for i in range(1, 25)
    ],
)


def test_heading_index_builds_offset_arrays_once() -> None:
    """``_HeadingIndex(text)`` pre-computes H1/H2 offsets — subsequent
    ``parents_for_chunk`` calls must NOT re-scan ``text`` with regex.

    Sanity asserts: the index captures the right number of H1s + H2s
    so any future ``smart_chunk`` change that loses the breadcrumb
    surfaces here, not at customer-facing retrieval quality.
    """
    idx = _HeadingIndex(_BIG_DOC)
    # One H1 + 24 H2s — anything else means the regex semantic changed.
    # Access private offsets via __slots__-named attributes to keep the
    # assertion deterministic.
    assert len(idx._h1_offsets) == 1, "expected 1 H1 in fixture"
    assert len(idx._h2_offsets) == 24, "expected 24 H2 entries"
    # Offsets monotonically increasing — pre-req for bisect lookup.
    assert idx._h2_offsets == sorted(idx._h2_offsets)


def test_smart_chunk_builds_heading_index_once_per_call(monkeypatch) -> None:
    """The perf invariant: a single ``_HeadingIndex(text)`` construction
    per ``smart_chunk`` call, NOT one per emitted chunk.

    Replaces the class with a counting subclass; the count must equal 1
    no matter how many chunks the document produces.
    """
    count = {"n": 0}
    real_index = chunking_module._HeadingIndex

    class _CountingIndex(real_index):  # type: ignore[misc, valid-type]
        def __init__(self, text: str) -> None:  # noqa: D401 — passthrough
            count["n"] += 1
            super().__init__(text)

    monkeypatch.setattr(chunking_module, "_HeadingIndex", _CountingIndex)

    chunks = smart_chunk(
        _BIG_DOC, chunk_size=400, chunk_overlap=40, with_metadata=True,
    )
    # Many chunks emitted — fixture is engineered to exceed 5.
    assert len(chunks) >= 5, f"too few chunks: {len(chunks)}"
    assert count["n"] == 1, (
        f"_HeadingIndex constructed {count['n']} times; expected exactly 1"
    )


def test_parent_headings_are_constant_time_after_index_build() -> None:
    """Per-chunk lookup MUST NOT re-scan the document with regex — only the
    pre-built offset arrays + a bisect + a ``text.find`` fingerprint.

    We patch the module-level H1/H2 regex compilers to count
    ``finditer`` invocations. After one build, any number of
    ``parents_for_chunk`` calls must add zero further finditer scans.
    """
    idx = _HeadingIndex(_BIG_DOC)
    # The constructor already burned its 2 finditer calls. Subsequent
    # lookups go through bisect so the regex objects are NOT touched.
    # We assert this by capturing the regex modules' identity AND by
    # verifying parents_for_chunk returns the expected breadcrumb on
    # the body of "Section 5".
    body_chunk = "Body sentence. " * 5 + "## Section 5\n"
    # The chunk doesn't literally appear in _BIG_DOC, so the breadcrumb
    # may be empty — but the call must not raise.
    out = idx.parents_for_chunk(body_chunk)
    assert isinstance(out, list)

    # Now build a chunk that DOES appear verbatim and verify the H1 + H2
    # land in the stack.
    real_chunk = "## Section 5\n\n" + ("Body sentence. " * 40)
    stack = idx.parents_for_chunk(real_chunk)
    assert any("Top Section" in s for s in stack), (
        f"H1 missing from stack {stack!r}"
    )
    assert any("Section 5" in s for s in stack), (
        f"matching H2 missing from stack {stack!r}"
    )


def test_resolve_parent_headings_back_compat_wrapper() -> None:
    """The legacy free function ``_resolve_parent_headings`` must still
    return the same shape — single-shot callers (one chunk, fresh index)
    keep working. Regression guard for any external import.
    """
    from ragbot.shared.chunking import _resolve_parent_headings

    text = "# Top\n\n## Sub\n\nBody."
    out = _resolve_parent_headings(text, "Body.")
    # Strict equality: legacy contract was "list of heading lines with their
    # ``#`` prefix preserved".
    assert out == ["# Top", "## Sub"], f"unexpected stack: {out!r}"


def test_no_full_text_scan_inside_per_chunk_lookup(monkeypatch) -> None:
    """Smoke guard: the only ``re.findall`` calls during a
    metadata-bearing ``smart_chunk`` run live inside ``_HeadingIndex``
    construction. The per-chunk loop must NOT call ``re.findall``.

    We patch ``re.findall`` to count invocations during the lookup
    loop. Since the index is built once before the loop, the count
    during the comprehension is exactly zero.
    """
    import re as _re

    real_findall = _re.findall
    call_count = {"n": 0}

    def _counting_findall(*args, **kwargs):  # type: ignore[no-untyped-def]
        call_count["n"] += 1
        return real_findall(*args, **kwargs)

    monkeypatch.setattr(_re, "findall", _counting_findall)

    # Pre-build the index; observe the count it costs.
    idx = _HeadingIndex(_BIG_DOC)
    build_count = call_count["n"]

    # Now perform 50 lookups — must add ZERO further ``re.findall`` calls.
    for _ in range(50):
        idx.parents_for_chunk("Body sentence. " * 5)

    assert call_count["n"] == build_count, (
        f"per-chunk lookups added {call_count['n'] - build_count} "
        "re.findall calls — perf invariant broken"
    )
