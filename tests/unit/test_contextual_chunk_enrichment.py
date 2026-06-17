"""Unit tests —  Contextual Retrieval (Anthropic 2024-09).

Covers ``ragbot.application.services.contextual_chunk_enrichment``:

* Happy path: LLM returns context → chunk wrapped in canonical
  ``<chunk_context>...</chunk_context>`` envelope.
* LLM failure → original chunk returned unchanged + WARN log.
* Empty / whitespace doc or chunk → original chunk returned (no LLM call).
* Doc above ``max_doc_chars`` cost guard → CR skipped (no LLM call).
* Empty LLM response → original chunk returned + WARN log.
* Anthropic prompt-cache: ``cache_control: ephemeral`` attached to the
  system block when ``prompt_cache_enabled=True`` and provider routes to
  Anthropic; no-op for OpenAI.

Tests inject a fake litellm module via ``litellm_module=...`` to avoid any
real network call.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.services.contextual_chunk_enrichment import (
    enrich_chunk_with_context,
)
from ragbot.shared.constants import (
    DEFAULT_CR_CONTEXT_MAX_TOKENS,
    DEFAULT_CR_MAX_DOC_CHARS,
)


# A model id placeholder that is descriptive but cfg-driven in real callers.
_FAKE_OPENAI_MODEL = "openai/gpt-4.1-mini-test"
_FAKE_ANTHROPIC_MODEL = "anthropic/claude-test"


def _build_response(text: str) -> Any:
    """Mimic the litellm response shape — only the bits the service reads."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
    )


class _FakeLitellm:
    """Records every ``acompletion`` call and returns canned responses."""

    def __init__(self, response_text: str = "Section 2.1 — refund policy.") -> None:
        self.calls: list[dict[str, Any]] = []
        self._response_text = response_text
        self._raise: BaseException | None = None

    def fail_next(self, exc: BaseException) -> None:
        self._raise = exc

    async def acompletion(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raise is not None:
            exc, self._raise = self._raise, None
            raise exc
        return _build_response(self._response_text)


# ── Happy path ──────────────────────────────────────────────────────────────


class TestEnrichmentSuccess:
    @pytest.mark.asyncio
    async def test_returns_canonical_envelope_on_success(self) -> None:
        fake = _FakeLitellm(response_text="Section about pricing tiers.")
        chunk = "Plan A costs ten units per month."
        full_doc = "Pricing chapter. " + chunk + " Plan B is free."

        out = await enrich_chunk_with_context(
            chunk,
            full_doc,
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            litellm_module=fake,
        )

        # Canonical envelope: <chunk_context>{ctx}</chunk_context>\n\n{chunk}
        assert out.startswith("<chunk_context>")
        assert "</chunk_context>" in out
        assert "Section about pricing tiers." in out
        # Original chunk preserved verbatim after the envelope close.
        assert out.endswith("\n\n" + chunk)
        # LLM was called exactly once with the right model + max_tokens cap.
        assert len(fake.calls) == 1
        call = fake.calls[0]
        assert call["model"] == _FAKE_OPENAI_MODEL
        assert call["max_tokens"] == DEFAULT_CR_CONTEXT_MAX_TOKENS

    @pytest.mark.asyncio
    async def test_llm_receives_full_doc_as_system_block(self) -> None:
        fake = _FakeLitellm()
        full_doc = "DOC_MARKER_SENTINEL — full document body."

        await enrich_chunk_with_context(
            "Some chunk.",
            full_doc,
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            litellm_module=fake,
        )

        messages = fake.calls[0]["messages"]
        assert messages[0]["role"] == "system"
        # Cache disabled → system content stays a plain string.
        assert isinstance(messages[0]["content"], str)
        assert "DOC_MARKER_SENTINEL" in messages[0]["content"]
        assert messages[1]["role"] == "user"
        assert "Some chunk." in messages[1]["content"]


# ── Failure / fallback paths ───────────────────────────────────────────────


class TestEnrichmentFallback:
    @pytest.mark.asyncio
    async def test_llm_failure_returns_original_chunk(self, caplog: Any) -> None:
        fake = _FakeLitellm()
        fake.fail_next(RuntimeError("provider rate limited"))
        chunk = "Original chunk content."

        out = await enrich_chunk_with_context(
            chunk,
            "Full document body.",
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            litellm_module=fake,
        )

        # Non-fatal: original chunk back unchanged.
        assert out == chunk
        # Verify the LLM was actually invoked (so we hit the failure branch).
        assert len(fake.calls) == 1

    @pytest.mark.asyncio
    async def test_empty_doc_skips_llm(self) -> None:
        fake = _FakeLitellm()
        chunk = "A chunk."
        out = await enrich_chunk_with_context(
            chunk,
            "",  # empty doc → bail out before any LLM call
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            litellm_module=fake,
        )
        assert out == chunk
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_empty_chunk_skips_llm(self) -> None:
        fake = _FakeLitellm()
        out = await enrich_chunk_with_context(
            "   ",
            "Full doc body.",
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            litellm_module=fake,
        )
        assert out == "   "
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_doc_over_cost_guard_skips_llm(self) -> None:
        fake = _FakeLitellm()
        chunk = "A chunk."
        # Build doc strictly larger than the (tiny) cost-guard cap.
        full_doc = "x" * (len(chunk) + 7)

        out = await enrich_chunk_with_context(
            chunk,
            full_doc,
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            max_doc_chars=len(chunk),  # tiny cap → guard trips
            litellm_module=fake,
        )

        # Returned unchanged + LLM never called.
        assert out == chunk
        assert fake.calls == []

    @pytest.mark.asyncio
    async def test_empty_llm_response_returns_original(self) -> None:
        fake = _FakeLitellm(response_text="   ")  # whitespace-only
        chunk = "Original."
        out = await enrich_chunk_with_context(
            chunk,
            "Full doc body.",
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,
            litellm_module=fake,
        )
        # Service refuses to wrap an empty context → original chunk preserved.
        assert out == chunk

    @pytest.mark.asyncio
    async def test_default_cost_guard_constant_unchanged(self) -> None:
        # Sanity check: constant export must stay positive (zero would block
        # CR for every doc — a non-default behaviour change should be visible).
        assert DEFAULT_CR_MAX_DOC_CHARS > 0


# ── Anthropic prompt cache ──────────────────────────────────────────────────


class TestAnthropicPromptCache:
    @pytest.mark.asyncio
    async def test_cache_control_attached_for_anthropic(self) -> None:
        fake = _FakeLitellm()
        await enrich_chunk_with_context(
            "Chunk.",
            "Full document body.",
            model_id=_FAKE_ANTHROPIC_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=True,
            litellm_module=fake,
        )

        system_msg = fake.calls[0]["messages"][0]
        assert system_msg["role"] == "system"
        # Anthropic path → system content is a list of blocks with cache_control.
        assert isinstance(system_msg["content"], list)
        first_block = system_msg["content"][0]
        assert first_block["type"] == "text"
        assert first_block["cache_control"] == {"type": "ephemeral"}
        assert "Full document body." in first_block["text"]

    @pytest.mark.asyncio
    async def test_cache_control_noop_for_openai(self) -> None:
        fake = _FakeLitellm()
        await enrich_chunk_with_context(
            "Chunk.",
            "Full document body.",
            model_id=_FAKE_OPENAI_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=True,
            litellm_module=fake,
        )

        system_msg = fake.calls[0]["messages"][0]
        # OpenAI: cache helper is a no-op → content stays a plain string.
        assert isinstance(system_msg["content"], str)
        assert "cache_control" not in system_msg["content"]

    @pytest.mark.asyncio
    async def test_cache_disabled_keeps_string_content(self) -> None:
        fake = _FakeLitellm()
        await enrich_chunk_with_context(
            "Chunk.",
            "Full document body.",
            model_id=_FAKE_ANTHROPIC_MODEL,
            max_context_tokens=DEFAULT_CR_CONTEXT_MAX_TOKENS,
            prompt_cache_enabled=False,  # explicit opt-out
            litellm_module=fake,
        )

        system_msg = fake.calls[0]["messages"][0]
        assert isinstance(system_msg["content"], str)
