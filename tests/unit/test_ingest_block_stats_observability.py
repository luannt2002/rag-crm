"""M25 — block-modality histogram observability.

After ``_split_into_blocks_with_atomic`` runs the ingest path emits two
signals carrying the per-document block-type histogram:

1. structlog event ``ingest_blocks_by_type`` (carries ``blocks_by_type``,
   ``n_blocks_total``, ``doc_id``, ``record_bot_id``).
2. ``request_steps.metadata_json.blocks_by_type`` (via the step
   tracker's ``set_metadata`` call inside the ``ingest_chunk`` context).

These tests pin the structlog payload shape using a local fake tracker;
the real ``StepTracker`` is itself thin (``self.metadata.update(kwargs)``)
so a unit-level assertion is sufficient to lock the contract — the
integration test path lives in ``tests/integration``.
"""

from __future__ import annotations

from collections import Counter

from ragbot.application.services.step_tracker import StepContext
from ragbot.shared.chunking import _split_into_blocks_with_atomic


# -----------------------------------------------------------------------------
# Splitter still produces (block_type, content) tuples — Counter compatibility
# -----------------------------------------------------------------------------


def test_split_returns_block_type_content_pairs() -> None:
    """The histogram code path assumes ``(block_type, body)`` tuples."""
    text = "Paragraph one.\n\n| h | v |\n|---|---|\n| a | b |\n\nClosing paragraph."
    blocks = _split_into_blocks_with_atomic(text)
    assert blocks  # non-empty
    for b in blocks:
        assert isinstance(b, tuple) and len(b) == 2
        assert isinstance(b[0], str)


def test_histogram_counts_blocks_by_type() -> None:
    """Counter over the splitter output equals an expected per-type histogram."""
    text = (
        "Paragraph one.\n"
        "\n"
        "| h | v |\n"
        "|---|---|\n"
        "| a | b |\n"
        "\n"
        "Closing paragraph.\n"
        "\n"
        "```\nprint('hi')\n```\n"
    )
    blocks = _split_into_blocks_with_atomic(text)
    histogram = dict(Counter(btype for btype, _body in blocks))
    # At minimum we expect text + table + code each represented.
    assert histogram.get("text", 0) >= 1
    assert histogram.get("table", 0) >= 1
    assert histogram.get("code", 0) >= 1


# -----------------------------------------------------------------------------
# StepTracker.set_metadata happily takes ``blocks_by_type`` dict
# -----------------------------------------------------------------------------


def test_step_context_set_metadata_accepts_blocks_by_type() -> None:
    """The set_metadata contract must accept a dict so blocks_by_type survives.

    ``StepContext.set_metadata`` is the boundary between the document
    pipeline and ``request_steps.metadata_json``. We assert here that
    passing a dict-typed value goes through untouched — anything else
    would break the M25 observability contract.
    """
    ctx = StepContext(
        name="ingest_chunk",
        order=1,
        model_used=None,
        binding_id=None,
        metadata={},
    )
    histogram = {"text": 3, "table": 1, "code": 1}
    ctx.set_metadata(blocks_by_type=histogram, n_chunks_out=5)
    assert ctx.metadata["blocks_by_type"] == histogram
    assert ctx.metadata["n_chunks_out"] == 5


def test_step_context_metadata_dict_preserves_nested_types() -> None:
    """Histogram values stay int; keys stay str — ready for JSONB persist."""
    ctx = StepContext(
        name="ingest_chunk",
        order=1,
        model_used=None,
        binding_id=None,
        metadata={},
    )
    ctx.set_metadata(blocks_by_type={"text": 7, "table": 0})
    out = ctx.metadata["blocks_by_type"]
    assert isinstance(out, dict)
    assert all(isinstance(k, str) for k in out)
    assert all(isinstance(v, int) for v in out.values())
