"""Pin tests for LLMChunkContextProvider.

Tests verify:
1. test_generate_returns_same_length_as_chunks
2. test_generate_empty_chunks_returns_empty_list
3. test_generate_per_chunk_failure_returns_empty_string_at_position
4. test_generate_logs_complete_event
5. test_get_provider_name_returns_llm_enrichment
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMResponse
from ragbot.infrastructure.llm.llm_chunk_context_provider import LLMChunkContextProvider
from ragbot.shared.types import BotId, TenantId


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_TENANT_ID = TenantId(uuid.uuid4())
_BOT_ID = BotId(uuid.uuid4())

_FAKE_SPEC = LLMSpec(
    binding_id=uuid.uuid4(),
    model_name="openai/gpt-4.1-mini",
    provider="openai",
)


def _make_llm_response(content: str = "ctx") -> LLMResponse:
    return LLMResponse(
        content=content,
        model="gpt-4.1-mini",
        provider="openai",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0,
        latency_ms=50,
    )


def _make_provider(llm_complete_side_effect=None) -> tuple[LLMChunkContextProvider, AsyncMock, MagicMock]:
    """Return (provider, mock_llm.complete, mock_resolver)."""
    mock_llm = MagicMock()
    mock_llm.complete = AsyncMock(return_value=_make_llm_response("generated context"))
    if llm_complete_side_effect is not None:
        mock_llm.complete.side_effect = llm_complete_side_effect

    mock_resolver = MagicMock()
    mock_resolver.resolve_llm = AsyncMock(return_value=_FAKE_SPEC)

    provider = LLMChunkContextProvider(
        llm=mock_llm,
        model_resolver=mock_resolver,
        record_tenant_id=_TENANT_ID,
        record_bot_id=_BOT_ID,
    )
    return provider, mock_llm.complete, mock_resolver


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_returns_same_length_as_chunks() -> None:
    """Output list length equals input chunks length."""
    provider, _, _ = _make_provider()
    chunks = ["chunk one", "chunk two", "chunk three"]

    result = await provider.generate(
        doc_full_text="full document text",
        chunks=chunks,
        max_context_tokens=50,
    )

    assert len(result) == len(chunks)
    assert all(isinstance(r, str) for r in result)


@pytest.mark.asyncio
async def test_generate_empty_chunks_returns_empty_list() -> None:
    """Empty chunk list returns empty result without calling LLM."""
    provider, mock_complete, _ = _make_provider()

    result = await provider.generate(
        doc_full_text="some document",
        chunks=[],
        max_context_tokens=50,
    )

    assert result == []
    mock_complete.assert_not_called()


@pytest.mark.asyncio
async def test_generate_per_chunk_failure_returns_empty_string_at_position() -> None:
    """When LLM raises on chunk[1], result[1] == '' and others succeed."""
    call_count = 0

    async def _side_effect(*args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ValueError("simulated rate limit")
        return _make_llm_response(f"ctx-{call_count}")

    provider, _, _ = _make_provider(llm_complete_side_effect=_side_effect)
    chunks = ["chunk A", "chunk B", "chunk C"]

    result = await provider.generate(
        doc_full_text="document body",
        chunks=chunks,
        max_context_tokens=50,
    )

    assert len(result) == 3
    # Successful positions are non-empty; failed position is empty string.
    assert result[0] != ""
    assert result[1] == ""
    assert result[2] != ""


@pytest.mark.asyncio
async def test_generate_logs_complete_event(caplog) -> None:
    """Structured event chunk_context_provider_complete is emitted."""
    import logging

    provider, _, _ = _make_provider()

    with patch(
        "ragbot.infrastructure.llm.llm_chunk_context_provider.logger"
    ) as mock_logger:
        await provider.generate(
            doc_full_text="document",
            chunks=["c1", "c2"],
            max_context_tokens=30,
        )

        mock_logger.info.assert_called_once()
        call_kwargs = mock_logger.info.call_args
        event_name = call_kwargs[0][0]
        assert event_name == "chunk_context_provider_complete"

        kw = call_kwargs[1]
        assert kw["n_chunks"] == 2
        assert kw["n_non_empty"] == 2
        assert "latency_ms" in kw


def test_get_provider_name_returns_llm_enrichment() -> None:
    """Static method returns the expected string identifier."""
    assert LLMChunkContextProvider.get_provider_name() == "llm_enrichment"
