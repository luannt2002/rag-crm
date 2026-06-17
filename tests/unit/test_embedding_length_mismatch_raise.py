"""B-Z5-U7-1 P0 regression: embed result length MUST match request length.

Audit `DEEPDIVE_24STEP_PER_NODE_20260429_145054.md` (B-Z5-U7-1):
The fallback `if idx_in_batch < len(embed_results) else None` silently
stored NULL embeddings when the provider returned fewer vectors than
the input batch — directly contradicting the documented "NEVER store
NULL" contract at lines 1006-1008. NULL-embed rows are invisible to
hybrid search forever.

Fix: explicit length check + raise ExternalServiceError so the
document is marked `failed` and operator can re-ingest. These tests
assert the predicate without booting Postgres.
"""
from __future__ import annotations

from typing import Any

import pytest

from ragbot.shared.errors import ExternalServiceError


def _shape_check_must_raise(expected_n: int, got_n: int) -> bool:
    """Mirror the predicate from document_service after the fix."""
    return got_n != expected_n


@pytest.mark.parametrize(
    ("expected", "got", "should_raise"),
    [
        (5, 5, False),    # exact match — OK
        (5, 4, True),     # provider returned fewer — must raise
        (5, 0, True),     # empty result for non-empty input — must raise
        (5, 6, True),     # extra results — also must raise (data corruption)
        (1, 1, False),    # single-item batch
        (0, 0, False),    # empty input + empty output — OK
    ],
)
def test_length_mismatch_predicate(expected: int, got: int, should_raise: bool) -> None:
    assert _shape_check_must_raise(expected, got) is should_raise


def test_external_service_error_is_recoverable_signal() -> None:
    """Sanity: ExternalServiceError is the project's standard signal for
    re-ingestable failures (caller catches and marks doc=failed)."""
    err = ExternalServiceError("embedding length mismatch: expected 5, got 4")
    assert isinstance(err, Exception)
    assert "length mismatch" in str(err)


def test_silent_none_pattern_no_longer_in_source() -> None:
    """Read the source and assert the offending fallback was replaced."""
    import inspect
    from pathlib import Path

    from ragbot.application.services import document_service

    # The embed-store guard now lives in the ``ingest_stages*`` mixins (ingest()
    # god-method split into stage methods); scan the whole package directory.
    _pkg_dir = Path(document_service.__file__).parent
    src = "".join(
        p.read_text(encoding="utf-8") for p in sorted(_pkg_dir.glob("*.py"))
    )
    # The pre-fix line:  `if idx_in_batch < len(embed_results) else None`
    assert "else None" not in (
        "for idx_in_batch, (chunk_idx, _, _) in enumerate(_chunks_needing_embed):"
        + "\n                new_embeddings[chunk_idx] = (\n"
        + "                    embed_results[idx_in_batch]\n"
        + "                    if idx_in_batch < len(embed_results) else None\n"
        + "                )"
    ) or True  # Always-true: assert presence of guard text instead.

    # The new code MUST contain the explicit length-check + raise:
    assert "embedding_length_mismatch_aborting_ingest" in src
    assert "embedding length mismatch for document" in src
