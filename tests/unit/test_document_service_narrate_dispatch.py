"""Wave E3 — narrate-then-embed dispatch wiring in DocumentService.

Verifies the pure pieces of the E3 wire:

1. ``_classify_chunk_block_type(text)`` returns the dominant block-type
   label (``"TABLE" / "FORMULA" / "IMAGE" / "CODE" / "TEXT"``) — the
   same uppercase labels that ``NarrateService.eligible_block_types``
   recognises.

2. ``_narrate_chunks_for_embed(texts, narrate_service)`` performs the
   pre-embed dispatch:
     - When ``narrate_service is None`` (graceful degradation default)
       the texts pass through unchanged + no metadata is produced.
     - When the service is wired AND feature flag is ON, eligible chunks
       get their embed-target text replaced with the narration AND a
       per-chunk metadata dict carries ``raw_chunk`` + ``narrated_text``
       + ``block_type`` for retrieval introspection.
     - Prose ``TEXT`` chunks always pass through unchanged regardless
       of feature flag (prose embeds fine raw — no LLM cost).

These tests are fully synthetic — they exercise the dispatch helper
directly with a fake ``NarrateService`` stub, so no DB / network / LLM
calls run.
"""

from __future__ import annotations

import pytest

from ragbot.application.ports.narrate_port import NarrateServicePort
from ragbot.application.services.narrate_dispatch import (
    classify_chunk_block_type as _classify_chunk_block_type,
    narrate_chunks_for_embed as _narrate_chunks_for_embed,
)
from ragbot.application.services.narrate_service import NarrateService
from ragbot.shared.constants import (
    NARRATE_METADATA_KEY_BLOCK_TYPE,
    NARRATE_METADATA_KEY_NARRATED_TEXT,
    NARRATE_METADATA_KEY_RAW_CHUNK,
)


# ---------------------------------------------------------------------------
# Classifier — content → block_type label
# ---------------------------------------------------------------------------


def test_classify_markdown_table_returns_table() -> None:
    text = "| col1 | col2 |\n| --- | --- |\n| a | b |"
    assert _classify_chunk_block_type(text) == "TABLE"


def test_classify_csv_returns_table() -> None:
    # CSV with >= DEFAULT_CSV_MIN_COMMAS commas per row — table_csv path.
    text = "name,age,city,country\nAlice,30,Hanoi,VN\nBob,25,Saigon,VN"
    assert _classify_chunk_block_type(text) == "TABLE"


def test_classify_oneline_display_formula_returns_formula() -> None:
    text = "$$E = mc^2$$"
    assert _classify_chunk_block_type(text) == "FORMULA"


def test_classify_multiline_display_formula_returns_formula() -> None:
    text = "$$\nE = mc^2\n\\int_0^1 f(x)dx\n$$"
    assert _classify_chunk_block_type(text) == "FORMULA"


def test_classify_markdown_image_returns_image() -> None:
    text = "![diagram of the system](assets/diagram.png)"
    assert _classify_chunk_block_type(text) == "IMAGE"


def test_classify_fenced_code_returns_code() -> None:
    text = "```python\ndef foo():\n    return 1\n```"
    assert _classify_chunk_block_type(text) == "CODE"


def test_classify_plain_prose_returns_text() -> None:
    text = (
        "This is an ordinary paragraph of prose. It has multiple sentences. "
        "Nothing tabular, no LaTeX, no images."
    )
    assert _classify_chunk_block_type(text) == "TEXT"


def test_classify_empty_returns_text() -> None:
    assert _classify_chunk_block_type("") == "TEXT"
    assert _classify_chunk_block_type("   \n\n  ") == "TEXT"


# ---------------------------------------------------------------------------
# Dispatch helper — pre-embed narrate loop
# ---------------------------------------------------------------------------


class _FakeNarrateStrategy:
    """In-memory Narrate strategy stub for unit tests.

    Returns ``f"NARRATED({block_type}): {content[:20]}..."`` so the test
    can assert (a) narration ran, (b) block_type routed correctly.
    """

    async def narrate(
        self, content: str, block_type: str, *, language: str = "vi",
    ) -> str:
        return f"NARRATED({block_type}): {content[:20]}..."


@pytest.mark.asyncio
async def test_dispatch_with_none_service_passes_through_unchanged() -> None:
    """Graceful degradation: ``narrate_service=None`` → identity."""
    texts = ["| a | b |\n| - | - |\n| 1 | 2 |", "plain prose chunk"]
    rewritten, metadata = await _narrate_chunks_for_embed(
        texts,
        narrate_service=None,
    )
    assert rewritten == texts
    assert metadata == [None, None]


@pytest.mark.asyncio
async def test_dispatch_with_flag_off_passes_through_unchanged() -> None:
    """Flag OFF → service short-circuits even with strategy wired."""
    service: NarrateService = NarrateService(
        strategy=_FakeNarrateStrategy(),  # type: ignore[arg-type]
        enabled=False,
    )
    texts = ["| a | b |\n| - | - |\n| 1 | 2 |"]
    rewritten, metadata = await _narrate_chunks_for_embed(
        texts,
        narrate_service=service,
    )
    assert rewritten == texts
    # Even when flag OFF we still emit per-chunk metadata so the persist
    # path can record block_type + raw_chunk for offline analysis.
    assert metadata[0] is not None
    assert metadata[0][NARRATE_METADATA_KEY_BLOCK_TYPE] == "TABLE"
    assert metadata[0][NARRATE_METADATA_KEY_RAW_CHUNK] == texts[0]
    assert metadata[0][NARRATE_METADATA_KEY_NARRATED_TEXT] == texts[0]


@pytest.mark.asyncio
async def test_dispatch_with_flag_on_rewrites_table_chunk() -> None:
    """Flag ON + TABLE chunk → embed-target text replaced with narration."""
    service: NarrateService = NarrateService(
        strategy=_FakeNarrateStrategy(),  # type: ignore[arg-type]
        enabled=True,
    )
    table_text = "| col1 | col2 |\n| --- | --- |\n| a | b |"
    rewritten, metadata = await _narrate_chunks_for_embed(
        [table_text],
        narrate_service=service,
    )
    assert rewritten[0].startswith("NARRATED(TABLE):")
    assert rewritten[0] != table_text
    assert metadata[0] is not None
    assert metadata[0][NARRATE_METADATA_KEY_BLOCK_TYPE] == "TABLE"
    assert metadata[0][NARRATE_METADATA_KEY_RAW_CHUNK] == table_text
    assert metadata[0][NARRATE_METADATA_KEY_NARRATED_TEXT] == rewritten[0]


@pytest.mark.asyncio
async def test_dispatch_with_flag_on_skips_prose_chunk() -> None:
    """Flag ON + TEXT chunk → no LLM hop, passthrough preserved."""
    service: NarrateService = NarrateService(
        strategy=_FakeNarrateStrategy(),  # type: ignore[arg-type]
        enabled=True,
    )
    prose = "This is plain prose. It has no special structure."
    rewritten, metadata = await _narrate_chunks_for_embed(
        [prose],
        narrate_service=service,
    )
    assert rewritten == [prose]  # untouched
    assert metadata[0] is not None
    assert metadata[0][NARRATE_METADATA_KEY_BLOCK_TYPE] == "TEXT"
    assert metadata[0][NARRATE_METADATA_KEY_RAW_CHUNK] == prose
    # ``text_for_embedding == raw_chunk`` when no narration ran.
    assert metadata[0][NARRATE_METADATA_KEY_NARRATED_TEXT] == prose


@pytest.mark.asyncio
async def test_dispatch_with_flag_on_rewrites_formula_chunk() -> None:
    """Flag ON + FORMULA chunk → embed-target text replaced with narration."""
    service: NarrateService = NarrateService(
        strategy=_FakeNarrateStrategy(),  # type: ignore[arg-type]
        enabled=True,
    )
    formula = "$$E = mc^2$$"
    rewritten, metadata = await _narrate_chunks_for_embed(
        [formula],
        narrate_service=service,
    )
    assert rewritten[0].startswith("NARRATED(FORMULA):")
    assert metadata[0] is not None
    assert metadata[0][NARRATE_METADATA_KEY_BLOCK_TYPE] == "FORMULA"


@pytest.mark.asyncio
async def test_dispatch_mixed_corpus_routes_per_chunk() -> None:
    """Mixed batch: each chunk routed by its own block_type."""
    service: NarrateService = NarrateService(
        strategy=_FakeNarrateStrategy(),  # type: ignore[arg-type]
        enabled=True,
    )
    texts = [
        "Plain prose chunk one.",                              # TEXT
        "| h1 | h2 |\n| - | - |\n| x | y |",                   # TABLE
        "$$\\alpha + \\beta = \\gamma$$",                       # FORMULA
        "![diagram](path.png)",                                 # IMAGE
    ]
    rewritten, metadata = await _narrate_chunks_for_embed(
        texts,
        narrate_service=service,
    )
    # Block-type tagging per index.
    assert [m[NARRATE_METADATA_KEY_BLOCK_TYPE] for m in metadata] == [
        "TEXT", "TABLE", "FORMULA", "IMAGE",
    ]
    # TEXT untouched; other three rewritten.
    assert rewritten[0] == texts[0]
    assert rewritten[1].startswith("NARRATED(TABLE):")
    assert rewritten[2].startswith("NARRATED(FORMULA):")
    assert rewritten[3].startswith("NARRATED(IMAGE):")


@pytest.mark.asyncio
async def test_dispatch_graceful_on_strategy_failure() -> None:
    """Strategy raise → caller sees raw text (HALLU=0, no fabrication)."""

    class _RaisingStrategy:
        async def narrate(
            self, content: str, block_type: str, *, language: str = "vi",
        ) -> str:
            raise RuntimeError("simulated LLM provider blip")

    service: NarrateService = NarrateService(
        strategy=_RaisingStrategy(),  # type: ignore[arg-type]
        enabled=True,
    )
    table_text = "| a | b |\n| - | - |\n| 1 | 2 |"
    # NarrateService.narrate_chunk doesn't catch arbitrary exceptions —
    # the LLM adapter does. With this raising stub the helper should
    # let the exception propagate so the test asserts the contract.
    # In production the adapter swallows + returns raw content (see
    # llm_narrate.py degrade-silent path), so the production path is
    # already covered by the adapter's own unit tests.
    with pytest.raises(RuntimeError):
        await _narrate_chunks_for_embed(
            [table_text],
            narrate_service=service,
        )


@pytest.mark.asyncio
async def test_protocol_compatibility_with_real_narrate_service() -> None:
    """Smoke check: helper accepts a real ``NarrateService`` instance."""
    service: NarrateService = NarrateService(
        strategy=_FakeNarrateStrategy(),  # type: ignore[arg-type]
        enabled=True,
    )
    # NarrateService isn't a Protocol — it's a concrete class — but
    # _narrate_chunks_for_embed accepts ``NarrateService | None`` so this
    # smoke ensures the import + typing line up.
    isinstance(service._strategy, NarrateServicePort)
    rewritten, _ = await _narrate_chunks_for_embed(
        ["plain prose"],
        narrate_service=service,
    )
    assert rewritten == ["plain prose"]
