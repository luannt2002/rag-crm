"""Unit tests for `application.services.structured_output_helper`.

Mocks LiteLLM `acompletion` with stubs that emit the shapes returned by
each provider integration so we don't make real API calls.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ragbot.application.dto.llm_schemas import (
    DecomposeOutput,
    GradeOutput,
    ReflectOutput,
)
from ragbot.application.services.structured_output_helper import call_with_schema


# --- Fake LiteLLM response containers ----------------------------------------

class _FakeMessage:
    def __init__(self, content: str | None = None, tool_calls: list[Any] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message


class _FakeResponse:
    def __init__(self, message: _FakeMessage) -> None:
        self.choices = [_FakeChoice(message)]


class _FakeFunction:
    def __init__(self, arguments: str | dict) -> None:
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, fn: _FakeFunction) -> None:
        self.function = fn


# --- Stub LiteLLM modules ----------------------------------------------------

class _StubLiteLLMOpenAI:
    """Returns a clean json_schema-style content body."""

    def __init__(self, content: str) -> None:
        self._content = content
        self.last_kwargs: dict[str, Any] = {}

    async def acompletion(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        return _FakeResponse(_FakeMessage(content=self._content))


class _StubLiteLLMAnthropic:
    """Returns Anthropic tool-use response shape."""

    def __init__(self, args: dict | str) -> None:
        self._args = args
        self.last_kwargs: dict[str, Any] = {}

    async def acompletion(self, **kwargs: Any) -> _FakeResponse:
        self.last_kwargs = kwargs
        msg = _FakeMessage(
            content=None,
            tool_calls=[_FakeToolCall(_FakeFunction(self._args))],
        )
        return _FakeResponse(msg)


class _StubLiteLLMRaising:
    """Raises whatever exception the test requests."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def acompletion(self, **kwargs: Any) -> Any:
        raise self._exc


# --- Tests -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_path_returns_validated_grade() -> None:
    body = json.dumps({"grade": "yes", "reason": "match"})
    stub = _StubLiteLLMOpenAI(body)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
    )
    assert isinstance(out, GradeOutput)
    assert out.grade == "yes"
    assert out.reason == "match"
    # Verify response_format was forwarded as json_schema
    rf = stub.last_kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "GradeOutput"
    assert rf["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_anthropic_path_returns_validated_reflect() -> None:
    args = {"action": "rewrite", "reason": "answer is incomplete"}
    stub = _StubLiteLLMAnthropic(args)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="anthropic/claude-3-5-haiku",
        provider_code="anthropic",
        messages=[{"role": "user", "content": "q"}],
        schema=ReflectOutput,
    )
    assert isinstance(out, ReflectOutput)
    assert out.action == "rewrite"
    # Tools and tool_choice forwarded
    assert "tools" in stub.last_kwargs
    assert stub.last_kwargs["tool_choice"]["function"]["name"] == "submit_response"


@pytest.mark.asyncio
async def test_anthropic_arguments_can_be_json_string() -> None:
    """LiteLLM sometimes returns arguments as a JSON-encoded string."""
    args_str = json.dumps({"sub_queries": ["A?", "B?"]})
    stub = _StubLiteLLMAnthropic(args_str)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="anthropic/claude-3-5-haiku",
        provider_code="anthropic",
        messages=[{"role": "user", "content": "q"}],
        schema=DecomposeOutput,
    )
    assert isinstance(out, DecomposeOutput)
    assert out.sub_queries == ["A?", "B?"]


@pytest.mark.asyncio
async def test_unknown_provider_falls_back_to_json_parse() -> None:
    body = json.dumps({"sub_queries": ["X?", "Y?"]})
    stub = _StubLiteLLMOpenAI(body)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="cohere/command-r",
        provider_code="cohere",
        messages=[{"role": "user", "content": "q"}],
        schema=DecomposeOutput,
    )
    assert isinstance(out, DecomposeOutput)
    assert out.sub_queries == ["X?", "Y?"]
    # No response_format / no tools → pure plain call
    assert "response_format" not in stub.last_kwargs
    assert "tools" not in stub.last_kwargs


@pytest.mark.asyncio
async def test_invalid_json_returns_none_when_fallback_disabled() -> None:
    stub = _StubLiteLLMOpenAI("not json at all")
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        fallback_to_json_parse=False,
    )
    assert out is None


@pytest.mark.asyncio
async def test_fenced_json_parsed_on_fallback() -> None:
    """LLMs sometimes wrap JSON in ```json fences."""
    body = "```json\n" + json.dumps({"grade": "no", "reason": "off-topic"}) + "\n```"
    stub = _StubLiteLLMOpenAI(body)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
    )
    assert isinstance(out, GradeOutput)
    assert out.grade == "no"


@pytest.mark.asyncio
async def test_provider_call_exception_returns_none() -> None:
    stub = _StubLiteLLMRaising(RuntimeError("upstream 500"))
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
    )
    assert out is None


@pytest.mark.asyncio
async def test_anthropic_invalid_args_returns_none() -> None:
    """Anthropic returns args that don't match the schema -> None."""
    args = {"action": "totally_invalid_action", "reason": "x"}
    stub = _StubLiteLLMAnthropic(args)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="anthropic/claude-3-5-haiku",
        provider_code="anthropic",
        messages=[{"role": "user", "content": "q"}],
        schema=ReflectOutput,
    )
    assert out is None


@pytest.mark.asyncio
async def test_optional_kwargs_forwarded() -> None:
    body = json.dumps({"grade": "partial", "reason": ""})
    stub = _StubLiteLLMOpenAI(body)
    await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        api_key="sk-test",
        api_base="https://example.test",
        timeout=10.0,
        temperature=0.2,
        max_tokens=80,
    )
    assert stub.last_kwargs["api_key"] == "sk-test"
    assert stub.last_kwargs["api_base"] == "https://example.test"
    assert stub.last_kwargs["timeout"] == 10.0
    assert stub.last_kwargs["temperature"] == 0.2
    assert stub.last_kwargs["max_tokens"] == 80


@pytest.mark.asyncio
async def test_anthropic_path_applies_cache_control_to_system_message() -> None:
    """T1.5.S28 — structured-output Anthropic path now stamps cache_control.

    Without this every structured grade / reflect / understand call rebuilt
    the cache from scratch — Wave 2 ~30% input-token cost leak. The fix
    routes the messages list through ``apply_anthropic_cache_control``
    before forwarding to ``litellm.acompletion``.
    """
    args = {"action": "keep", "reason": "looks fine"}
    stub = _StubLiteLLMAnthropic(args)
    await call_with_schema(
        litellm_module=stub,
        litellm_name="anthropic/claude-3-5-haiku",
        provider_code="anthropic",
        messages=[
            {"role": "system", "content": "You are a careful judge."},
            {"role": "user", "content": "q"},
        ],
        schema=ReflectOutput,
    )
    forwarded = stub.last_kwargs["messages"]
    sys_msg = forwarded[0]
    # System content must now be a list-of-blocks payload with the
    # ephemeral cache_control marker on the text block.
    assert sys_msg["role"] == "system"
    assert isinstance(sys_msg["content"], list)
    assert sys_msg["content"][0]["type"] == "text"
    assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_openai_path_does_not_inject_anthropic_cache_control() -> None:
    """OpenAI structured path stays untouched — auto-caching is server-side."""
    body = json.dumps({"action": "keep", "reason": "ok"})
    stub = _StubLiteLLMOpenAI(body)
    await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[
            {"role": "system", "content": "You are a careful judge."},
            {"role": "user", "content": "q"},
        ],
        schema=ReflectOutput,
    )
    forwarded = stub.last_kwargs["messages"]
    # OpenAI path: messages stay as plain str-content shape.
    assert forwarded[0]["content"] == "You are a careful judge."
