"""NullLexicalRetrieval adapter tests.

Coverage:
- search() returns [] regardless of inputs (Null Object contract).
- search() never raises (default-OFF must be bulletproof).
- health_check() is True (no external dependency).
- get_provider_name() == "null" and ``mode`` attr == "null"
  (signals to the orchestrator's _is_null_lexical probe).
- Constructor accepts arbitrary kwargs (bootstrap-friendly).
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from ragbot.infrastructure.retrieval.null_lexical_retrieval import NullLexicalRetrieval


@pytest.mark.asyncio
async def test_search_returns_empty_list() -> None:
    adapter = NullLexicalRetrieval()
    out = await adapter.search("anything", uuid4(), top_k=20)
    assert out == []


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty() -> None:
    # Same contract as a non-empty query — Null Object never errors.
    adapter = NullLexicalRetrieval()
    assert (await adapter.search("", uuid4(), 5)) == []
    assert (await adapter.search("   ", uuid4(), 5)) == []


@pytest.mark.asyncio
async def test_search_none_record_bot_id_returns_empty() -> None:
    adapter = NullLexicalRetrieval()
    # Even with garbage inputs the null adapter must stay quiet.
    assert (await adapter.search("q", None, 5)) == []  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_health_check_always_true() -> None:
    adapter = NullLexicalRetrieval()
    assert (await adapter.health_check()) is True


def test_get_provider_name_returns_null() -> None:
    assert NullLexicalRetrieval.get_provider_name() == "null"


def test_mode_attribute_is_null() -> None:
    adapter = NullLexicalRetrieval()
    assert adapter.mode == "null"


def test_constructor_accepts_arbitrary_kwargs() -> None:
    # Bootstrap factory forwards ``session_factory=`` to every adapter;
    # the Null Object must not crash on extras.
    NullLexicalRetrieval(session_factory=lambda: None, foo=1, bar="x")
