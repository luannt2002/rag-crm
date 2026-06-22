"""M0-6: analyze_document_blocks must emit ``avg_text_length`` in WORD units.

After M0-5 wired the Block pipeline, ``_stage_u4_chunk`` feeds the
``analyze_document_blocks()`` profile straight into ``select_strategy()``,
which hard-reads ``profile["avg_text_length"]``. The block analyzer only
emitted ``avg_text_block_length`` (a CHARACTER count), so select_strategy
raised ``KeyError('avg_text_length')`` on every block-fed ingest.

A naive fix that aliases the character count into the ``avg_text_length`` key
is worse than the crash: ``select_strategy`` normalises that value against
WORD-count constants (``avg_len / DEFAULT_SEMANTIC_AVG_LEN_NORM`` = 300), so
feeding characters (~5x words for VN prose) silently saturates the signal and
flips the chosen strategy. These tests pin the contract: the key is present,
it is in WORD units (matching ``analyze_document``'s ``total_text_words /
text_blocks``), and select_strategy runs without KeyError.
"""
from __future__ import annotations

from ragbot.domain.entities.document import Block
from ragbot.shared.chunking.analyze import (
    analyze_document_blocks,
    select_strategy,
)


def _text_blocks(n_blocks: int, words_per_block: int) -> list[Block]:
    sentence = " ".join(f"w{i}" for i in range(words_per_block))
    return [
        Block(type="TEXT", content=sentence, is_atomic=False)
        for _ in range(n_blocks)
    ]


def test_block_profile_emits_avg_text_length_key() -> None:
    prof = analyze_document_blocks(_text_blocks(4, words_per_block=9))
    # The key select_strategy hard-reads MUST exist on the block profile.
    assert "avg_text_length" in prof


def test_avg_text_length_is_word_units_not_chars() -> None:
    prof = analyze_document_blocks(_text_blocks(4, words_per_block=9))
    # 9 words/block — the WORD count, NOT the character count of the block.
    assert prof["avg_text_length"] == 9.0
    assert prof["avg_text_length"] != prof["avg_text_block_length"]


def test_avg_text_length_scales_with_words() -> None:
    short = analyze_document_blocks(_text_blocks(3, words_per_block=5))
    long = analyze_document_blocks(_text_blocks(3, words_per_block=20))
    assert short["avg_text_length"] == 5.0
    assert long["avg_text_length"] == 20.0


def test_select_strategy_no_keyerror_on_block_profile() -> None:
    prof = analyze_document_blocks(_text_blocks(4, words_per_block=9))
    # Must not raise KeyError('avg_text_length') — the M0-5-activated path.
    strategy, confidence = select_strategy(prof, text="x")
    assert strategy
    assert 0.0 <= confidence <= 1.0
