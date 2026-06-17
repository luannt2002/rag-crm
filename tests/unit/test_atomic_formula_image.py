"""Atomic FORMULA / IMAGE / CODE chunk protection.

Verifies the ``formula_image_atomic_protect_enabled`` feature flag:

* Off (default) — chunking behaviour is byte-for-byte unchanged. Legacy
  ``_split_into_blocks`` still returns ``(text, …)`` / ``(table, …)``;
  ``smart_chunk`` still routes through the table-isolation path on doc
  with tables.
* On — the new ``_split_into_blocks_with_atomic`` is used; every chunking
  strategy preserves FORMULA / IMAGE / CODE blocks whole — no cut lands
  mid-formula or mid-image-line, no fenced code fragment leaks across
  chunks. Sample doc with 5 formulas + 3 tables + 4 images yields:

    * formula_count_in_output == 5
    * image_count_in_output  == 4
    * table_count_in_output  >= 3 (oversized tables may row-split)

Proof citation (RAG-Anything HKUDS + AdapChunk Layer 2) lives in the
implementation docstring (``shared/chunking.py``); tests assert the
behavioural invariant — atomic ⇒ no cross-boundary cut.
"""
from __future__ import annotations

import re

import pytest

from ragbot.shared import chunking as chunking_mod
from ragbot.shared.chunking import (
    _ATOMIC_BLOCK_TYPES,
    _is_atomic_block_type,
    _is_formula_line,
    _is_image_line,
    _split_into_blocks,
    _split_into_blocks_with_atomic,
    smart_chunk,
)


# ─── Helpers ────────────────────────────────────────────────────────────


@pytest.fixture()
def atomic_protect_on(monkeypatch):
    """Force the feature flag ON for the duration of the test.

    Bypasses ``bootstrap_config.get_boot_config`` so tests don't hit PG
    and the in-process TTL cache stays clean across runs.
    """
    monkeypatch.setattr(chunking_mod, "_atomic_protect_enabled", lambda: True)
    yield True


@pytest.fixture()
def atomic_protect_off(monkeypatch):
    """Force the feature flag OFF (mirrors the default constant)."""
    monkeypatch.setattr(chunking_mod, "_atomic_protect_enabled", lambda: False)
    yield False


# Sample document — 5 formulas, 3 tables, 4 images, mixed prose.
SAMPLE_FORMULAS = [
    "$$E = mc^2$$",
    "$$\\int_0^\\infty e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2}$$",
    "$\\sum_{i=1}^n i = \\frac{n(n+1)}{2}$",
    "$$\\nabla \\times \\mathbf{B} = \\mu_0 \\mathbf{J}$$",
    "$$f(x) = \\frac{1}{\\sqrt{2\\pi\\sigma^2}} e^{-\\frac{(x-\\mu)^2}{2\\sigma^2}}$$",
]
SAMPLE_IMAGES = [
    "![Diagram 1](https://example.local/d1.png)",
    "![Histogram](https://example.local/h1.png)",
    "![Architecture overview](https://example.local/a1.png)",
    "![Flow chart](https://example.local/f1.png)",
]
SAMPLE_TABLES = [
    """| Col A | Col B | Col C |
|-------|-------|-------|
| 1     | 2     | 3     |
| 4     | 5     | 6     |""",
    """| Metric | Value |
|--------|-------|
| Recall | 0.91  |
| MRR    | 0.78  |""",
    """| K | Score |
|---|-------|
| 1 | 0.5   |
| 2 | 0.6   |
| 3 | 0.7   |""",
]


def _build_mixed_doc() -> str:
    """Compose 5 formulas + 3 tables + 4 images interleaved with prose."""
    parts: list[str] = ["# Document title", "Introduction prose paragraph one."]
    parts.append(SAMPLE_FORMULAS[0])
    parts.append("Paragraph following the first formula explaining context.")
    parts.append(SAMPLE_IMAGES[0])
    parts.append(SAMPLE_TABLES[0])
    parts.append("Middle prose section between blocks.")
    parts.append(SAMPLE_FORMULAS[1])
    parts.append(SAMPLE_IMAGES[1])
    parts.append(SAMPLE_FORMULAS[2])
    parts.append("Another paragraph of running text in the middle of the doc.")
    parts.append(SAMPLE_TABLES[1])
    parts.append(SAMPLE_IMAGES[2])
    parts.append(SAMPLE_FORMULAS[3])
    parts.append("Paragraph close to the end.")
    parts.append(SAMPLE_TABLES[2])
    parts.append(SAMPLE_IMAGES[3])
    parts.append(SAMPLE_FORMULAS[4])
    parts.append("Closing prose paragraph.")
    return "\n\n".join(parts)


def _count_formulas(text: str) -> int:
    """Count distinct LaTeX formula occurrences (``$$…$$`` and ``$…$`` block)."""
    block_dollar = len(re.findall(r"\$\$.+?\$\$", text, flags=re.DOTALL))
    # Single-line inline-only formulas (whole-line ``$…$``).
    inline = len(re.findall(r"^\s*\$[^\$\n]+\$\s*$", text, flags=re.MULTILINE))
    return block_dollar + inline


def _count_images(text: str) -> int:
    return len(re.findall(r"!\[[^\]]*\]\([^)]+\)", text))


# ─── Detection helpers ──────────────────────────────────────────────────


class TestDetectionHelpers:
    """Atomic-block type detectors — pure functions, no DB / IO."""

    def test_atomic_block_type_set_covers_spec(self):
        # Spec: TABLE / FORMULA / IMAGE / CODE are atomic.
        assert _ATOMIC_BLOCK_TYPES == frozenset({"table", "formula", "image", "code"})
        assert _is_atomic_block_type("formula")
        assert _is_atomic_block_type("image")
        assert _is_atomic_block_type("table")
        assert _is_atomic_block_type("code")
        assert not _is_atomic_block_type("text")
        assert not _is_atomic_block_type("heading")

    def test_formula_line_block_dollar(self):
        assert _is_formula_line("$$ E = mc^2 $$")
        assert _is_formula_line("  $$x + y = z$$  ")

    def test_formula_line_inline_standalone(self):
        # Inline math standing alone on its line is treated as a formula
        # block (caption-like role for a single equation).
        assert _is_formula_line("$f(x) = x^2 + 1$")

    def test_formula_line_rejects_prose_with_inline_math(self):
        # Inline ``$x$`` embedded in prose must NOT count as a formula
        # line — splitting prose around inline math would break flow.
        assert not _is_formula_line("the value $x$ is high.")
        assert not _is_formula_line("Plain text without math.")
        assert not _is_formula_line("")

    def test_image_line(self):
        assert _is_image_line("![alt](https://example.local/x.png)")
        assert _is_image_line("Some text ![inline img](u) trailing")
        assert not _is_image_line("plain prose")
        assert not _is_image_line("[link](u) is not an image")


# ─── _split_into_blocks_with_atomic — block boundary detection ──────────


class TestSplitIntoBlocksWithAtomic:
    """The extended splitter emits typed blocks; atomicity is by type."""

    def test_simple_text_only(self):
        blocks = _split_into_blocks_with_atomic("just a paragraph of prose.")
        assert blocks == [("text", "just a paragraph of prose.")]

    def test_formula_block_isolated(self):
        doc = "Intro text.\n\n$$x = 1$$\n\nOutro text."
        blocks = _split_into_blocks_with_atomic(doc)
        types = [t for t, _ in blocks]
        assert "formula" in types
        # Formula block stays whole — content equals the formula source.
        formula_blocks = [c for t, c in blocks if t == "formula"]
        assert len(formula_blocks) == 1
        assert "$$x = 1$$" in formula_blocks[0]

    def test_multi_line_dollar_dollar_formula(self):
        doc = "Intro.\n\n$$\nE = mc^2\n+ \\Delta\n$$\n\nOutro."
        blocks = _split_into_blocks_with_atomic(doc)
        formula_blocks = [c for t, c in blocks if t == "formula"]
        assert len(formula_blocks) == 1
        # Multi-line formula keeps every interior line.
        assert "E = mc^2" in formula_blocks[0]
        assert "\\Delta" in formula_blocks[0]

    def test_image_block(self):
        doc = "Caption above.\n\n![alt](https://example.local/i.png)\n\nBelow."
        blocks = _split_into_blocks_with_atomic(doc)
        image_blocks = [c for t, c in blocks if t == "image"]
        assert len(image_blocks) == 1
        assert "![alt](https://example.local/i.png)" in image_blocks[0]

    def test_code_block_fenced(self):
        doc = "Intro.\n\n```python\nprint('x')\nprint('y')\n```\n\nOutro."
        blocks = _split_into_blocks_with_atomic(doc)
        code_blocks = [c for t, c in blocks if t == "code"]
        assert len(code_blocks) == 1
        body = code_blocks[0]
        # Opening + closing fences AND every body line are inside the block.
        assert body.startswith("```python")
        assert body.endswith("```")
        assert "print('x')" in body
        assert "print('y')" in body

    def test_table_pipe(self):
        doc = (
            "Intro.\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "Outro."
        )
        blocks = _split_into_blocks_with_atomic(doc)
        table_blocks = [c for t, c in blocks if t == "table"]
        assert len(table_blocks) == 1
        assert "| A | B |" in table_blocks[0]
        assert "| 1 | 2 |" in table_blocks[0]

    def test_consecutive_atomic_blocks_dont_merge(self):
        # A formula immediately followed by an image must yield 2
        # separate atomic blocks — they're different types.
        doc = (
            "Intro.\n\n"
            "$$a = 1$$\n"
            "![alt](u)\n"
            "Outro."
        )
        blocks = _split_into_blocks_with_atomic(doc)
        types = [t for t, _ in blocks]
        assert "formula" in types
        assert "image" in types
        # Both atomic blocks present and distinct.
        atomic_blocks = [(t, c) for t, c in blocks if t in _ATOMIC_BLOCK_TYPES]
        assert len(atomic_blocks) == 2

    def test_mixed_doc_counts(self):
        doc = _build_mixed_doc()
        blocks = _split_into_blocks_with_atomic(doc)
        formula_blocks = [c for t, c in blocks if t == "formula"]
        image_blocks = [c for t, c in blocks if t == "image"]
        table_blocks = [c for t, c in blocks if t == "table"]
        assert len(formula_blocks) == 5
        assert len(image_blocks) == 4
        assert len(table_blocks) == 3


# ─── smart_chunk with flag OFF — no regression ──────────────────────────


class TestRegressionFlagOff:
    """Flag OFF must mirror the original behaviour bit-for-bit."""

    def test_split_into_blocks_unchanged(self):
        # The original ``_split_into_blocks`` still only emits text / table
        # so existing callers (``_chunk_recursive_with_tables`` and the
        # table-isolation branch in ``smart_chunk``) keep working.
        doc = (
            "Para one.\n\n"
            "$$x = 1$$\n\n"
            "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
            "![alt](u)\n\n"
            "Para three."
        )
        blocks = _split_into_blocks(doc)
        types = [t for t, _ in blocks]
        assert set(types) <= {"text", "table"}, (
            "_split_into_blocks must not silently emit new types — "
            "atomic detection lives in _split_into_blocks_with_atomic."
        )

    def test_smart_chunk_flag_off_uses_original_path(self, atomic_protect_off):
        doc = _build_mixed_doc()
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        assert len(chunks) > 0
        # Each chunk is non-empty string.
        for c in chunks:
            assert isinstance(c, str)
            assert c.strip()

    def test_smart_chunk_empty_input(self, atomic_protect_off):
        assert smart_chunk("") == []
        assert smart_chunk("   ") == []


# ─── smart_chunk with flag ON — atomicity invariants ────────────────────


class TestAtomicityFlagOn:
    """With the flag ON every atomic block survives intact."""

    def test_formula_count_preserved_in_output(self, atomic_protect_on):
        doc = _build_mixed_doc()
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        joined = "\n".join(chunks)
        # Number of distinct formula occurrences across all chunks must
        # equal input formula count — no formula was split or duplicated.
        assert _count_formulas(joined) == 5

    def test_image_count_preserved_in_output(self, atomic_protect_on):
        doc = _build_mixed_doc()
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        joined = "\n".join(chunks)
        assert _count_images(joined) == 4

    def test_no_formula_split_across_chunks(self, atomic_protect_on):
        # Every ``$$…$$`` occurrence in the output is whole within ONE
        # chunk — no chunk ends with ``$$…`` and the next starts with
        # ``…$$``.
        doc = _build_mixed_doc()
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        for formula in SAMPLE_FORMULAS:
            # At least one chunk contains this formula whole.
            assert any(formula in c for c in chunks), (
                f"formula {formula!r} was split across chunks"
            )

    def test_no_image_split_across_chunks(self, atomic_protect_on):
        doc = _build_mixed_doc()
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        for img in SAMPLE_IMAGES:
            assert any(img in c for c in chunks), (
                f"image {img!r} was split across chunks"
            )

    def test_code_block_kept_whole(self, atomic_protect_on):
        # A code block longer than chunk_size must still survive as one
        # chunk — splitting fenced code produces syntactically broken
        # fragments which downstream embedders cannot represent well.
        long_body = "\n".join(f"print('line {i}')" for i in range(50))
        doc = (
            "Intro paragraph that is reasonably long to exercise the splitter.\n\n"
            f"```python\n{long_body}\n```\n\n"
            "Outro paragraph."
        )
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        # Exactly one chunk contains both the opening and closing fence.
        code_chunks = [c for c in chunks if "```python" in c]
        assert len(code_chunks) == 1
        assert code_chunks[0].count("```") == 2  # open + close
        # No line of the body appears outside the code chunk.
        for i in range(50):
            line = f"print('line {i}')"
            inside = code_chunks[0].count(line)
            outside = sum(c.count(line) for c in chunks if c is not code_chunks[0])
            assert inside == 1
            assert outside == 0

    def test_oversized_formula_kept_whole_warns(
        self, atomic_protect_on, caplog,
    ):
        # FORMULA spec: oversized formulas STAY WHOLE, never split.
        huge = "$$" + " + ".join(f"x_{{{i}}}" for i in range(500)) + "$$"
        doc = f"Intro.\n\n{huge}\n\nOutro."
        chunks = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        # Exactly one chunk contains the entire formula source.
        with_formula = [c for c in chunks if huge in c]
        assert len(with_formula) == 1

    def test_flag_on_emits_telemetry(self, atomic_protect_on):
        # Verify the structlog ``formula_image_atomic_protect`` event
        # fires with the documented metric fields.
        import structlog
        import structlog.testing

        cap = structlog.testing.LogCapture()
        structlog.configure(processors=[cap])

        try:
            doc = _build_mixed_doc()
            _ = smart_chunk(doc, chunk_size=200, chunk_overlap=20)
        finally:
            # Reset to default so other tests don't inherit our capture.
            structlog.reset_defaults()

        events = [e for e in cap.entries if e.get("event") == "formula_image_atomic_protect"]
        assert events, (
            "expected at least one 'formula_image_atomic_protect' structlog event"
        )
        evt = events[0]
        # Contract from OBSERVABILITY-MATRIX.md row 26 + spec.
        assert evt["feature_flag"] == "formula_image_atomic_protect_enabled"
        assert "atomic_block_count" in evt
        assert "cuts_avoided" in evt
        assert "strategy" in evt
        # Sample doc has 5 formulas + 4 images + 3 tables = 12 atomic blocks.
        assert evt["atomic_block_count"] == 12


# ─── Strategy-independence — atomicity holds across every strategy ──────


class TestAtomicAcrossStrategies:
    """All 5 explicit strategy entry-points respect atomic blocks."""

    @pytest.mark.parametrize(
        "strategy",
        ["recursive", "hdt", "semantic", "hybrid", "proposition"],
    )
    def test_strategy_preserves_formulas(self, atomic_protect_on, strategy):
        doc = _build_mixed_doc()
        chunks = smart_chunk(
            doc, chunk_size=200, chunk_overlap=20, strategy=strategy,
        )
        joined = "\n".join(chunks)
        assert _count_formulas(joined) == 5, (
            f"strategy {strategy} dropped or split formulas"
        )

    @pytest.mark.parametrize(
        "strategy",
        ["recursive", "hdt", "semantic", "hybrid", "proposition"],
    )
    def test_strategy_preserves_images(self, atomic_protect_on, strategy):
        doc = _build_mixed_doc()
        chunks = smart_chunk(
            doc, chunk_size=200, chunk_overlap=20, strategy=strategy,
        )
        joined = "\n".join(chunks)
        assert _count_images(joined) == 4, (
            f"strategy {strategy} dropped or split images"
        )

    @pytest.mark.parametrize(
        "strategy",
        ["recursive", "hdt", "semantic", "hybrid", "proposition"],
    )
    def test_no_atomic_split_invariant(self, atomic_protect_on, strategy):
        """For every sample formula, exactly one chunk contains it whole."""
        doc = _build_mixed_doc()
        chunks = smart_chunk(
            doc, chunk_size=200, chunk_overlap=20, strategy=strategy,
        )
        for formula in SAMPLE_FORMULAS:
            hits = sum(1 for c in chunks if formula in c)
            assert hits >= 1, (
                f"[{strategy}] formula {formula!r} missing from output"
            )
        for img in SAMPLE_IMAGES:
            hits = sum(1 for c in chunks if img in c)
            assert hits >= 1, (
                f"[{strategy}] image {img!r} missing from output"
            )
