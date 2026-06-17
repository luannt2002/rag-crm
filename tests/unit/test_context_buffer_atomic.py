"""Tests — Context Buffer for Atomic Blocks (AdapChunk Layer 2).

Verifies that ``attach_context_buffer`` populates ``Block.context_before``
+ ``Block.context_after`` for atomic blocks using 1-2 sentences from
neighbouring TEXT blocks. Domain-neutral fixtures (no brand / industry
literal).
"""

from __future__ import annotations

import pytest

from ragbot.domain.entities.document import Block
from ragbot.shared.context_buffer import (
    ENV_CONTEXT_BUFFER_ENABLED,
    ENV_CONTEXT_BUFFER_WINDOW,
    _split_sentences,
    attach_context_buffer,
)


# ── _split_sentences ──────────────────────────────────────────────────────


def test_split_sentences_basic_terminators() -> None:
    """Splits on . ! ? while preserving sentence order."""
    text = "This is one. This is two! Is this three? Final."
    sents = _split_sentences(text)
    assert sents == [
        "This is one.",
        "This is two!",
        "Is this three?",
        "Final.",
    ]


def test_split_sentences_vietnamese_terminator() -> None:
    """Supports VN/CJK full stop 。 in addition to ASCII terminators."""
    text = "Câu một。 Câu hai. Câu ba!"
    sents = _split_sentences(text)
    assert "Câu một。" in sents
    assert "Câu hai." in sents
    assert "Câu ba!" in sents
    assert len(sents) == 3


def test_split_sentences_empty_input() -> None:
    """Empty / whitespace-only → empty list (no crash)."""
    assert _split_sentences("") == []
    assert _split_sentences("   \n  ") == []


def test_split_sentences_no_terminator_returns_single() -> None:
    """Text without terminator returns as a single sentence."""
    text = "Just one fragment with no punctuation"
    sents = _split_sentences(text)
    assert sents == [text]


# ── attach_context_buffer — flag OFF (default) ────────────────────────────


def test_enabled_by_default_attaches_context(monkeypatch) -> None:
    """Default ON (env unset): atomic blocks get neighbouring context.

    DEFAULT_CONTEXT_BUFFER_ATOMIC_ENABLED flipped to True (default==happy —
    atomic chunks carry their introducing/trailing prose into retrieval).
    An explicit ``enabled=False`` (next test) still disables it.
    """
    monkeypatch.delenv(ENV_CONTEXT_BUFFER_ENABLED, raising=False)
    monkeypatch.delenv(ENV_CONTEXT_BUFFER_WINDOW, raising=False)

    blocks = [
        Block(type="TEXT", content="Intro sentence one. Intro two.", is_atomic=False),
        Block(type="TABLE", content="| a | b |", is_atomic=True),
        Block(type="TEXT", content="Outro one. Outro two.", is_atomic=False),
    ]
    result = attach_context_buffer(blocks)
    # Enabled by default → atomic block carries neighbour prose.
    assert result[1].context_before != ""
    assert result[1].context_after != ""


def test_explicit_enabled_false_overrides_env(monkeypatch) -> None:
    """Explicit ``enabled=False`` wins over env var."""
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_ENABLED, "true")
    blocks = [
        Block(type="TEXT", content="Before.", is_atomic=False),
        Block(type="TABLE", content="| x |", is_atomic=True),
    ]
    result = attach_context_buffer(blocks, enabled=False)
    assert result[1].context_before == ""


# ── attach_context_buffer — flag ON ───────────────────────────────────────


def test_atomic_block_populated_with_context_before_and_after() -> None:
    """Atomic block between two TEXT blocks gets both context sides."""
    blocks = [
        Block(
            type="TEXT",
            content="The formula derives from Bayes. Theo định lý Bayes, ta có:",
            is_atomic=False,
        ),
        Block(type="FORMULA", content="P(A|B) = P(B|A)*P(A)/P(B)", is_atomic=True),
        Block(
            type="TEXT",
            content="Trong đó P(A) là xác suất tiên nghiệm. Result interpretation follows.",
            is_atomic=False,
        ),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)

    # FORMULA payload itself is unchanged.
    assert result[1].content == "P(A|B) = P(B|A)*P(A)/P(B)"
    assert result[1].is_atomic is True

    # context_before should carry the 2 sentences from previous TEXT block.
    assert "Theo định lý Bayes, ta có:" in result[1].context_before
    assert "The formula derives from Bayes." in result[1].context_before

    # context_after should carry the first 2 sentences from next TEXT block.
    assert "Trong đó P(A) là xác suất tiên nghiệm." in result[1].context_after
    assert "Result interpretation follows." in result[1].context_after


def test_window_one_takes_only_one_sentence() -> None:
    """``window=1`` keeps only the last/first sentence on each side."""
    blocks = [
        Block(type="TEXT", content="First. Second. Third.", is_atomic=False),
        Block(type="TABLE", content="| x | y |", is_atomic=True),
        Block(type="TEXT", content="Next-one. Next-two. Next-three.", is_atomic=False),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=1)

    assert result[1].context_before == "Third."
    assert result[1].context_after == "Next-one."


def test_atomic_at_first_index_has_no_context_before() -> None:
    """No previous block → context_before stays empty."""
    blocks = [
        Block(type="TABLE", content="| a |", is_atomic=True),
        Block(type="TEXT", content="Trailing sentence one. Trailing two.", is_atomic=False),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)
    assert result[0].context_before == ""
    assert "Trailing sentence one." in result[0].context_after
    assert "Trailing two." in result[0].context_after


def test_atomic_at_last_index_has_no_context_after() -> None:
    """No following block → context_after stays empty."""
    blocks = [
        Block(type="TEXT", content="Leading sentence one. Leading two.", is_atomic=False),
        Block(type="IMAGE", content="figure-ref", is_atomic=True),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)
    assert "Leading sentence one." in result[1].context_before
    assert "Leading two." in result[1].context_before
    assert result[1].context_after == ""


def test_neighbour_not_text_skipped() -> None:
    """Adjacent atomic block (e.g. two TABLEs) is NOT pulled into context."""
    blocks = [
        Block(type="TABLE", content="| a | b |", is_atomic=True),
        Block(type="TABLE", content="| c | d |", is_atomic=True),
        Block(type="TABLE", content="| e | f |", is_atomic=True),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)
    # Middle table: neighbours are TABLE (not TEXT) → both context sides empty.
    assert result[1].context_before == ""
    assert result[1].context_after == ""


def test_non_atomic_block_not_modified() -> None:
    """Plain TEXT block (is_atomic=False) is returned unchanged."""
    blocks = [
        Block(type="TEXT", content="Before.", is_atomic=False),
        Block(type="TEXT", content="Middle sentence.", is_atomic=False),
        Block(type="TEXT", content="After.", is_atomic=False),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)
    # Middle TEXT is not atomic → context fields stay empty.
    assert result[1].context_before == ""
    assert result[1].context_after == ""
    # Same identity returned for non-atomic blocks (no reallocation).
    assert result[1] is blocks[1]


def test_existing_context_preserved_not_overwritten() -> None:
    """If a block already has context_before set (upstream), don't overwrite."""
    blocks = [
        Block(type="TEXT", content="Old neighbour sentence.", is_atomic=False),
        Block(
            type="TABLE",
            content="| a |",
            is_atomic=True,
            context_before="Pre-set context preserved.",
            context_after="",
        ),
        Block(type="TEXT", content="New trailing sentence.", is_atomic=False),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)
    # context_before kept as-is.
    assert result[1].context_before == "Pre-set context preserved."
    # context_after still gets populated (it was empty).
    assert "New trailing sentence." in result[1].context_after


def test_empty_input_returns_empty() -> None:
    """Empty list in → empty list out (no crash)."""
    assert attach_context_buffer([], enabled=True) == []


def test_block_frozen_dataclass_replace_not_mutate() -> None:
    """Original block instances must NOT be mutated (frozen dataclass)."""
    table = Block(type="TABLE", content="| x |", is_atomic=True)
    blocks = [
        Block(type="TEXT", content="Lead-in sentence.", is_atomic=False),
        table,
        Block(type="TEXT", content="Trail-out sentence.", is_atomic=False),
    ]
    result = attach_context_buffer(blocks, enabled=True, window=2)
    # Original table object unchanged.
    assert table.context_before == ""
    assert table.context_after == ""
    # New block in result has the context populated.
    assert result[1] is not table
    assert result[1].context_before != ""
    assert result[1].context_after != ""


# ── env var resolution ────────────────────────────────────────────────────


def test_env_var_enables_feature(monkeypatch) -> None:
    """``RAGBOT_CONTEXT_BUFFER_ATOMIC_ENABLED=true`` flips the flag."""
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_ENABLED, "true")
    blocks = [
        Block(type="TEXT", content="Intro sentence.", is_atomic=False),
        Block(type="TABLE", content="| x |", is_atomic=True),
    ]
    result = attach_context_buffer(blocks)
    assert "Intro sentence." in result[1].context_before


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("True", True), ("TRUE", True),
    ("1", True), ("yes", True), ("on", True),
    ("false", False), ("0", False), ("no", False),
    ("", False), ("garbage", False),
])
def test_env_var_truthy_values(monkeypatch, raw: str, expected: bool) -> None:
    """Boolean env coercion handles common truthy/falsy spellings."""
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_ENABLED, raw)
    blocks = [
        Block(type="TEXT", content="Lead.", is_atomic=False),
        Block(type="TABLE", content="| x |", is_atomic=True),
    ]
    result = attach_context_buffer(blocks)
    if expected:
        assert result[1].context_before != ""
    else:
        assert result[1].context_before == ""


def test_env_var_window_override(monkeypatch) -> None:
    """``RAGBOT_CONTEXT_BUFFER_SENTENCE_WINDOW`` overrides the default."""
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_ENABLED, "true")
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_WINDOW, "1")
    blocks = [
        Block(type="TEXT", content="One. Two. Three.", is_atomic=False),
        Block(type="TABLE", content="| x |", is_atomic=True),
    ]
    result = attach_context_buffer(blocks)
    # With window=1, only "Three." should land in context_before.
    assert result[1].context_before == "Three."


def test_env_var_window_invalid_falls_back_to_default(monkeypatch) -> None:
    """Malformed window env var doesn't crash; falls back to constant default."""
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_ENABLED, "true")
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_WINDOW, "not-a-number")
    blocks = [
        Block(type="TEXT", content="A. B. C.", is_atomic=False),
        Block(type="TABLE", content="| x |", is_atomic=True),
    ]
    # Should NOT raise.
    result = attach_context_buffer(blocks)
    # Default window=2 → expect "B. C." in context_before.
    assert "B." in result[1].context_before
    assert "C." in result[1].context_before


# ── Parser integration smoke test ─────────────────────────────────────────


def test_simple_text_parser_attaches_context_when_enabled(monkeypatch) -> None:
    """SimpleTextParser._build_blocks output passes through attach_context_buffer."""
    monkeypatch.setenv(ENV_CONTEXT_BUFFER_ENABLED, "true")
    from ragbot.infrastructure.ocr.simple_text_parser import SimpleTextParser

    parser = SimpleTextParser()
    # Use _build_blocks directly to skip HTTP/IO.
    text = (
        "Introduction paragraph. Theo bảng dưới đây ta thấy:\n"
        "\n"
        "| Header A | Header B |\n"
        "| --- | --- |\n"
        "| v1 | v2 |\n"
        "\n"
        "Bảng 1 cho thấy phân bố. Phần tiếp theo phân tích kết quả."
    )
    blocks = parser._build_blocks(text, is_markdown=True)
    # Apply context buffer manually (mirrors what parse() does).
    from ragbot.shared.context_buffer import attach_context_buffer as _att

    result = _att(blocks)

    # Find the TABLE block in the output.
    tables = [b for b in result if b.type == "TABLE"]
    assert len(tables) >= 1, "expected at least one TABLE block"
    table = tables[0]
    assert table.is_atomic is True
    # Context should include the intro sentence preceding the table.
    assert "Theo bảng dưới đây ta thấy:" in table.context_before
    # And the trailing reference sentence.
    assert "Bảng 1 cho thấy phân bố." in table.context_after
