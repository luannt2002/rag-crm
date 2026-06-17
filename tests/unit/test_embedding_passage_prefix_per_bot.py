"""F14-HIGH-CC1-3 — embedding_passage_prefix is per-bot.

Multi-vertical platforms ship custom asymmetric-embedding prefixes per
industry (healthcare, finance, legal, etc.). The resolution chain is:

  bots.plan_limits.embedding_passage_prefix
  > system_config.embedding_passage_prefix
  > DEFAULT_EMBEDDING_PASSAGE_PREFIX

Re-embedding required for changes to take effect.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.application.services.document_service import DocumentService
from ragbot.shared.constants import DEFAULT_EMBEDDING_PASSAGE_PREFIX


def _make_service(
    *,
    plan_limits: dict | None,
    system_config_value: str = "",
) -> DocumentService:
    """Build a DocumentService with a mocked session_factory + cfg."""
    # Mock the SQL fetch: ``session.execute(...).first()`` returns a row
    # whose first column is the JSONB plan_limits dict.
    mock_row = MagicMock()
    if plan_limits is None:
        mock_row.first.return_value = None
    else:
        mock_row.first.return_value = (plan_limits,)
    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_row)
    mock_session.close = AsyncMock()

    # session_with_tenant calls factory() directly and uses returned session
    # as a regular object (not an async context manager). Mirror that shape.
    sf = MagicMock(return_value=mock_session)

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=system_config_value)

    settings = MagicMock()
    return DocumentService(
        session_factory=sf,
        embedder=MagicMock(),
        settings=settings,
        config_service=cfg,
    )


@pytest.mark.asyncio
async def test_default_prefix_used_when_bot_unset() -> None:
    """No bot override + no system_config → DEFAULT_EMBEDDING_PASSAGE_PREFIX."""
    svc = _make_service(plan_limits={}, system_config_value="")
    out = await svc._resolve_embedding_passage_prefix(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )
    assert out == DEFAULT_EMBEDDING_PASSAGE_PREFIX


@pytest.mark.asyncio
async def test_per_bot_override_resolved() -> None:
    """bots.plan_limits.embedding_passage_prefix wins over system_config."""
    svc = _make_service(
        plan_limits={"embedding_passage_prefix": "medical_record: "},
        system_config_value="passage: ",
    )
    out = await svc._resolve_embedding_passage_prefix(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )
    assert out == "medical_record: ", (
        "F14-HIGH-CC1-3 regression — per-bot prefix must override system_config."
    )


@pytest.mark.asyncio
async def test_system_config_used_when_bot_empty() -> None:
    """Empty bot override falls through to system_config."""
    svc = _make_service(plan_limits={}, system_config_value="passage: ")
    out = await svc._resolve_embedding_passage_prefix(
        record_bot_id=uuid.uuid4(),
        record_tenant_id=uuid.uuid4(),
    )
    assert out == "passage: "


def test_constant_imported_from_shared() -> None:
    """DEFAULT_EMBEDDING_PASSAGE_PREFIX must be defined in shared.constants."""
    from ragbot.shared import constants

    assert hasattr(constants, "DEFAULT_EMBEDDING_PASSAGE_PREFIX"), (
        "F14-HIGH-CC1-3 regression — DEFAULT_EMBEDDING_PASSAGE_PREFIX missing "
        "from shared/constants.py."
    )
    # Default is opt-in (empty string).
    assert isinstance(constants.DEFAULT_EMBEDDING_PASSAGE_PREFIX, str)


def test_plan_limit_schema_includes_prefix() -> None:
    """bot_limits.PLAN_LIMIT_SCHEMA exposes embedding_passage_prefix."""
    from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA

    assert "embedding_passage_prefix" in PLAN_LIMIT_SCHEMA, (
        "F14-HIGH-CC1-3 regression — plan_limits schema missing key."
    )
    assert PLAN_LIMIT_SCHEMA["embedding_passage_prefix"]["type"] == "str"
