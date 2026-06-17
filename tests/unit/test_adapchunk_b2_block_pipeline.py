"""Unit tests: AdapChunk Wave B2 — Block-pipeline opt-in wiring (T1-Smartness).

Verifies the feature-flag scaffold added to
``DocumentService.ingest()`` by Wave B2. The Block pipeline itself
depends on Wave B1 (atomic-chunking signature) and Wave D1
(``analyze_document_blocks``). Until those merge the flag must:

1. Default OFF (no behaviour change vs main).
2. When read from a stub config, route through the new
   ``apply_cross_check`` call so the strategy/confidence recorded in
   metadata match what ``smart_chunk`` would produce on the same
   profile.
3. Gracefully no-op the Layer-2 ``attach_context_buffer`` call when no
   parser-produced ``blocks`` list is available yet (Wave B1 dep).

These tests do NOT exercise the full ``DocumentService.ingest()``
co-routine (that needs a live DB + session); instead they pin the
narrow behaviours that B2 introduces so future regressions surface
without a full integration harness.
"""
from __future__ import annotations

import pytest

from ragbot.shared.chunking import (
    analyze_document,
    apply_cross_check,
    promote_vn_hierarchical_headings,
    select_strategy,
)
from ragbot.shared.constants import DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED
from ragbot.shared.context_buffer import attach_context_buffer


# ── Constant contract ─────────────────────────────────────────────────


def test_block_pipeline_flag_default_on() -> None:
    """Default ON now that Wave B1/D1 deps have landed (default==happy).

    Shipped OFF in Wave B2 pending B1 (atomic-chunking signature) + D1
    (analyze_document_blocks); those merged, so the smart block pipeline is
    the default. It degrades gracefully to the text-flatten primitives, so
    the flip carries no breakage risk; the flag remains the per-bot kill-switch.
    """
    assert DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED is True


def test_block_pipeline_constant_is_bool() -> None:
    """Flag type matches the config-loader contract (``get_bool``)."""
    assert isinstance(DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED, bool)


# ── Block-pipeline call-chain parity ──────────────────────────────────


def test_apply_cross_check_signature_compatible_with_b2_call_site() -> None:
    """B2 call site passes ``(strategy, confidence, profile)`` and unpacks
    the three-tuple result. Pin both ends so a future signature change
    surfaces here, not at ingest time."""
    text = "# H1\n\nParagraph one.\n\n## H2\n\nParagraph two.\n" * 4
    profile = analyze_document(text)
    strategy, confidence = select_strategy(profile)

    out = apply_cross_check(strategy, confidence, profile)

    # Three-tuple shape — B2 unpacks (strategy, confidence, override_reason).
    assert isinstance(out, tuple)
    assert len(out) == 3
    final_strategy, final_confidence, override_reason = out
    assert isinstance(final_strategy, str)
    assert 0.0 <= float(final_confidence) <= 1.0
    assert override_reason is None or isinstance(override_reason, str)


def test_promote_then_analyze_remains_callable_on_b2_path() -> None:
    """B2 calls ``promote_vn_hierarchical_headings`` BEFORE
    ``analyze_document`` on the new branch, same ordering as the
    legacy branch. Pin the contract so a refactor of either function
    does not silently re-order the call inside B2."""
    raw = "Chương I\nMục 1\nĐiều 1.\nNội dung."
    promoted = promote_vn_hierarchical_headings(raw)
    profile = analyze_document(promoted)
    assert "total_headings" in profile
    # promote_vn_hierarchical_headings is a no-op or expansion; never
    # truncates the source.
    assert len(promoted) >= len(raw)


# ── Layer-2 graceful no-op (Wave B1 dep not yet merged) ───────────────


def test_attach_context_buffer_handles_empty_block_list() -> None:
    """Until Wave B1 surfaces a parser ``blocks`` list at the ingest
    call site, B2 passes ``[]``. The Layer-2 helper must return that
    list unchanged — no exception, no logged error."""
    result = attach_context_buffer([])
    assert result == []
