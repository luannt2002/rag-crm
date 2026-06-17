"""Verify prompt-compression + preview-truncation constants are sourced
from `shared/constants.py` instead of inline magic literals in
`orchestration/query_graph.py`.

Background (CLAUDE.md zero-hardcode rule):
    F4 audit finding flagged the inline `500` char threshold and the
    inline `True` default in the prompt_compression block of query_graph.
    These tests pin the contract:
      1. The exported constants exist with the documented types/values.
      2. ``_pcfg`` honours the new defaults when ``pipeline_config`` is
         empty (i.e. no per-bot override).
      3. ``_pcfg`` returns the per-bot override when ``pipeline_config``
         supplies one (so the constant is a default, not a hard-cap).
"""

from __future__ import annotations

import pytest

from ragbot.shared import constants as C
from ragbot.orchestration.query_graph import _pcfg


# ---------------------------------------------------------------------------
# 1. Constants exist with correct types + sentinel values
# ---------------------------------------------------------------------------


def test_prompt_compression_threshold_constant_is_int_500() -> None:
    """Default per-chunk compression budget is the int 500 pulled from
    constants — not a magic literal in query_graph.
    """
    assert isinstance(
        C.DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK, int
    ), "char threshold must be int"
    assert C.DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK == 500, (
        "value pinned at 500 — change here on purpose, not by accident"
    )


def test_prompt_compression_enabled_default_is_true_bool() -> None:
    """Default for `prompt_compression_enabled` is a real bool True
    (not None, not 1 — bool to keep `_pcfg(...)` truthy semantics
    explicit and JSON-roundtrip safe).
    """
    val = C.DEFAULT_PROMPT_COMPRESSION_ENABLED
    assert isinstance(val, bool), "must be bool, not int truthy"
    assert val is True


# ---------------------------------------------------------------------------
# 2. _pcfg honours the new defaults when no pipeline override is given
# ---------------------------------------------------------------------------


def test_pcfg_uses_compression_default_when_pipeline_empty() -> None:
    """Empty pipeline_config → _pcfg returns the constant (the default
    we pass in), simulating how query_graph reads it on a fresh state.
    """
    state = {"pipeline_config": {}}

    enabled = _pcfg(
        state,
        "prompt_compression_enabled",
        C.DEFAULT_PROMPT_COMPRESSION_ENABLED,
    )
    threshold = _pcfg(
        state,
        "prompt_compression_max_chars_per_chunk",
        C.DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    )

    assert enabled is True
    assert threshold == 500


def test_pcfg_uses_compression_default_when_pipeline_missing_key() -> None:
    """pipeline_config missing entirely (None) → still returns default."""
    state: dict = {}  # no pipeline_config key at all
    threshold = _pcfg(
        state,
        "prompt_compression_max_chars_per_chunk",
        C.DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    )
    assert threshold == C.DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK


# ---------------------------------------------------------------------------
# 3. pipeline_config override beats the default (per-bot tuning preserved)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("override", [123, 800, 2000])
def test_pcfg_pipeline_override_beats_compression_threshold_default(
    override: int,
) -> None:
    """A bot owner can still tune the threshold via pipeline_config —
    the constant is a default, not a hard-cap.
    """
    state = {
        "pipeline_config": {"prompt_compression_max_chars_per_chunk": override}
    }
    got = _pcfg(
        state,
        "prompt_compression_max_chars_per_chunk",
        C.DEFAULT_PROMPT_COMPRESSION_MAX_CHARS_PER_CHUNK,
    )
    assert got == override


def test_pcfg_pipeline_override_can_disable_prompt_compression() -> None:
    """Per-bot override `False` must beat the default `True` — bot owner
    needs to be able to turn the node OFF without a code change.
    """
    state = {"pipeline_config": {"prompt_compression_enabled": False}}
    got = _pcfg(
        state,
        "prompt_compression_enabled",
        C.DEFAULT_PROMPT_COMPRESSION_ENABLED,
    )
    assert got is False
