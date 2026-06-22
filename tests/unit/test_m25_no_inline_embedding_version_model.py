"""M25 pin: no inline ``"v1"`` embedding-version / ``"gpt-4.1-nano"`` model literal.

CLAUDE.md zero-hardcode rule: the stored ``embedding_model_version`` tag and the
metadata-extraction fallback model name are config/data values that already live
in ``shared/constants`` as ``DEFAULT_EMBEDDING_FALLBACK_VERSION`` and
``DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL``. The retrieve node + query graph
MUST import those constants, never inline the literal.

This test pins:
1. Both constants exist and hold the expected values.
2. ``retrieve.py`` carries no inline ``"v1"`` for ``embedding_model_version``.
3. ``query_graph.py`` carries no inline ``"v1"`` for ``embedding_model_version``
   and no inline ``"gpt-4.1-nano"`` fallback-model literal.

Scope guard: only the M25 call-site literals are pinned. The speculative-MQ
region and the cache-version helper are owned by other agents and are NOT in the
``embedding_model_version`` / fallback-model assignment forms matched here.
"""

from __future__ import annotations

import pathlib
import re

from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_FALLBACK_VERSION,
    DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL,
)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RETRIEVE = _REPO_ROOT / "src/ragbot/orchestration/nodes/retrieve.py"
_QUERY_GRAPH = _REPO_ROOT / "src/ragbot/orchestration/query_graph.py"

# Matches the dict-entry assignment ``"embedding_model_version": "v1"`` —
# the exact M25 call-site form.
_EMB_VER_INLINE = re.compile(r'"embedding_model_version"\s*:\s*"v1"')
# Matches a fallback-model literal assignment ``= "gpt-4.1-nano"``.
_MODEL_INLINE = re.compile(r'=\s*"gpt-4\.1-nano"')


def test_constants_hold_expected_values() -> None:
    assert DEFAULT_EMBEDDING_FALLBACK_VERSION == "v1"
    assert DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL == "gpt-4.1-nano"


def test_modules_import_the_constants() -> None:
    retrieve_src = _RETRIEVE.read_text(encoding="utf-8")
    graph_src = _QUERY_GRAPH.read_text(encoding="utf-8")
    assert "DEFAULT_EMBEDDING_FALLBACK_VERSION" in retrieve_src, (
        "retrieve.py must import DEFAULT_EMBEDDING_FALLBACK_VERSION"
    )
    assert "DEFAULT_EMBEDDING_FALLBACK_VERSION" in graph_src, (
        "query_graph.py must import DEFAULT_EMBEDDING_FALLBACK_VERSION"
    )
    assert "DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL" in graph_src, (
        "query_graph.py must import DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL"
    )


def test_no_inline_embedding_version_in_retrieve() -> None:
    src = _RETRIEVE.read_text(encoding="utf-8")
    hits = [
        f"retrieve.py:{i}: {line.strip()}"
        for i, line in enumerate(src.splitlines(), start=1)
        if _EMB_VER_INLINE.search(line)
    ]
    assert not hits, (
        "Inline \"v1\" embedding_model_version in retrieve.py — use "
        "DEFAULT_EMBEDDING_FALLBACK_VERSION.\n  " + "\n  ".join(hits)
    )


def test_no_inline_embedding_version_in_query_graph() -> None:
    src = _QUERY_GRAPH.read_text(encoding="utf-8")
    hits = [
        f"query_graph.py:{i}: {line.strip()}"
        for i, line in enumerate(src.splitlines(), start=1)
        if _EMB_VER_INLINE.search(line)
    ]
    assert not hits, (
        "Inline \"v1\" embedding_model_version in query_graph.py — use "
        "DEFAULT_EMBEDDING_FALLBACK_VERSION.\n  " + "\n  ".join(hits)
    )


def test_no_inline_fallback_model_literal_in_query_graph() -> None:
    src = _QUERY_GRAPH.read_text(encoding="utf-8")
    hits = [
        f"query_graph.py:{i}: {line.strip()}"
        for i, line in enumerate(src.splitlines(), start=1)
        if _MODEL_INLINE.search(line)
    ]
    assert not hits, (
        "Inline \"gpt-4.1-nano\" fallback-model literal in query_graph.py — "
        "use DEFAULT_METADATA_EXTRACTION_FALLBACK_MODEL.\n  " + "\n  ".join(hits)
    )
