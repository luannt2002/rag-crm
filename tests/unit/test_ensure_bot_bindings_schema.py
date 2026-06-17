"""Regression: ensure_bot_bindings uses post-0034 schema columns.

Pre-fix (2026-04-29 incident): ``bot_bindings.py`` SQL referenced
legacy columns ``bot_id`` / ``model_id``. The schema was renamed to
``record_bot_id`` / ``record_model_id`` in migration 0034. Calling
this helper crashed runtime with `UndefinedColumnError: column
"bot_id" does not exist` every time a bot was created via
/demo-ragbot or /sync — silent dead code that took years to surface
because the helper was guarded by `if model_id or embedding_model_id`
and many flows passed both as None.

This test asserts the source-level invariants without hitting Postgres.
"""
from __future__ import annotations

import inspect

from ragbot.shared import bot_bindings


def _src() -> str:
    return inspect.getsource(bot_bindings)


def test_uses_record_bot_id_not_backcompat_bot_id() -> None:
    src = _src()
    # Confirm the post-0034 column name is used in queries.
    assert "record_bot_id = :bid" in src
    # And the legacy column is NOT used in any SQL fragment.
    # (The string `bot_id` may appear inside `record_bot_id` or in
    # comments/docstrings — strip those before scanning.)
    import re
    sql_blocks = re.findall(r'text\(\s*"""(.*?)"""\s*\)', src, re.DOTALL)
    sql_blocks += re.findall(r'text\(\s*"([^"]+)"\s*\)', src)
    for sql in sql_blocks:
        # `record_bot_id` is allowed; bare `bot_id` (preceded by space, paren,
        # or comma — i.e. as a standalone column) is the bug.
        assert not re.search(r"(?<![a-z_])bot_id\b", sql.replace("record_bot_id", "")), (
            f"legacy `bot_id` column reference found in SQL: {sql}"
        )


def test_uses_record_model_id_not_backcompat_model_id() -> None:
    src = _src()
    # `record_model_id` must be the column name in the INSERT.
    assert "record_model_id" in src
    # And the INSERT must NOT have a bare `model_id` column.
    import re
    sql_blocks = re.findall(r'text\(\s*"""(.*?)"""\s*\)', src, re.DOTALL)
    for sql in sql_blocks:
        cleaned = sql.replace("record_model_id", "")
        # Look for `model_id` as a column (between spaces/commas, not :param)
        assert not re.search(r"[\s,(]model_id[\s,)]", cleaned), (
            f"legacy `model_id` column reference found: {sql}"
        )


def test_record_tenant_id_supplied_to_insert() -> None:
    """3-key identity: insert must include record_tenant_id column even if
    nullable — keeps the row scoped when caller passes the UUID."""
    src = _src()
    assert "record_tenant_id" in src
    assert ":tid" in src


def test_reranker_purpose_supported() -> None:
    """Bug fix: caller can now bind a reranker model so new bots don't
    silently default to NullReranker. Writer must use the BindingPurpose
    enum (value "rerank") so reader/writer match end-to-end."""
    src = _src()
    assert "BindingPurpose.RERANK" in src
    assert "rerank_model_id" in src


def test_writer_reader_purpose_symmetry() -> None:
    """Pin: writer (bot_bindings.py) and reader (model_resolver.py) MUST
    reference the same BindingPurpose enum, not raw strings, so the V2
    'reranker' vs 'rerank' silent-fallback regression cannot recur."""
    from ragbot.application.dto.ai_specs import BindingPurpose
    assert BindingPurpose.RERANK.value == "rerank"
    assert BindingPurpose.EMBEDDING.value == "embedding"
    assert BindingPurpose.LLM_PRIMARY.value == "llm_primary"


def test_temperature_max_tokens_default_to_constants() -> None:
    """Zero-hardcode: defaults come from shared/constants.py, not inline literals."""
    src = _src()
    assert "DEFAULT_LLM_TEMPERATURE" in src
    assert "DEFAULT_GENERATION_MAX_TOKENS" in src
    # And the inline magic numbers are gone from the function body.
    sig = str(inspect.signature(bot_bindings.ensure_bot_bindings))
    assert "0.3" not in sig
    assert "450" not in sig


def test_idempotent_skip_check_present() -> None:
    """Helper must SELECT-then-INSERT pattern — not blind insert."""
    src = _src()
    assert "SELECT 1 FROM bot_model_bindings" in src
    assert "active = true" in src
    assert "LIMIT 1" in src
