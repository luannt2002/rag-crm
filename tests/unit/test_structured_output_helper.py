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


# --- S0-C: capability-driven dispatch + repair retry --------------------------

from ragbot.application.dto.llm_schemas import UnderstandOutput  # noqa: E402


class _SequencedLiteLLM:
    """Yields a different content body per ``acompletion`` call.

    Used to verify the bounded repair retry: first call returns garbage,
    second returns valid JSON. Records every call's kwargs.
    """

    def __init__(self, bodies: list[str]) -> None:
        self._bodies = bodies
        self.calls: list[dict[str, Any]] = []

    async def acompletion(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._bodies) - 1)
        return _FakeResponse(_FakeMessage(content=self._bodies[idx]))


@pytest.mark.asyncio
async def test_supports_json_mode_uses_json_object_not_strict_schema() -> None:
    """A model advertising json_mode (e.g. Qwen3 behind OpenAI gateway) must
    take the loose json_object transport, NOT strict json_schema."""
    body = json.dumps({"grade": "yes", "reason": "ok"})
    stub = _StubLiteLLMOpenAI(body)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/some-qwen3",
        provider_code="openai",  # legacy match would force json_schema
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        supports_json_mode=True,
    )
    assert isinstance(out, GradeOutput)
    rf = stub.last_kwargs["response_format"]
    assert rf == {"type": "json_object"}
    # Strict json_schema must NOT have been used.
    assert rf.get("type") != "json_schema"


@pytest.mark.asyncio
async def test_supports_tools_uses_tool_mode_for_non_anthropic_name() -> None:
    """supports_tools=True routes to function tool_choice even when the model
    name does not match the Anthropic substring list."""
    args = {"action": "keep", "reason": "fine"}
    stub = _StubLiteLLMAnthropic(args)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="vendor/mystery-llm",
        provider_code="vendor",
        messages=[{"role": "user", "content": "q"}],
        schema=ReflectOutput,
        supports_tools=True,
    )
    assert isinstance(out, ReflectOutput)
    assert "tools" in stub.last_kwargs
    assert stub.last_kwargs["tool_choice"]["function"]["name"] == "submit_response"


@pytest.mark.asyncio
async def test_prior_routing_unchanged_when_caps_none() -> None:
    """No capability flags → legacy json_schema for OpenAI-compatible."""
    body = json.dumps({"grade": "partial", "reason": ""})
    stub = _StubLiteLLMOpenAI(body)
    await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/gpt-4.1-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
    )
    rf = stub.last_kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_repair_retry_recovers_after_bad_shape() -> None:
    """First response is wrong shape, repair turn returns valid JSON →
    helper returns the validated instance without raising."""
    bad = json.dumps({"unexpected": "field"})         # missing 'grade'
    good = json.dumps({"grade": "no", "reason": "x"})
    stub = _SequencedLiteLLM([bad, good])
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/some-qwen3",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        supports_json_mode=True,
        fallback_to_json_parse=False,  # isolate the repair path
    )
    assert isinstance(out, GradeOutput)
    assert out.grade == "no"
    # Exactly two acompletion calls: initial + one repair.
    assert len(stub.calls) == 2
    # The repair call appended an extra user turn restating the schema.
    repaired_msgs = stub.calls[1]["messages"]
    assert len(repaired_msgs) == 2
    assert repaired_msgs[-1]["role"] == "user"
    assert "GradeOutput" in repaired_msgs[-1]["content"]


@pytest.mark.asyncio
async def test_repair_retry_is_bounded_returns_none_not_raise() -> None:
    """Both attempts return bad shape → bounded retry stops at 1 repair and
    returns None (degrade), never an infinite loop, never a raise."""
    bad = json.dumps({"unexpected": "field"})
    stub = _SequencedLiteLLM([bad, bad, bad])
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/some-qwen3",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        supports_json_mode=True,
        fallback_to_json_parse=False,
    )
    assert out is None
    # 1 initial + 1 repair = 2; bounded, NOT 3.
    assert len(stub.calls) == 2


@pytest.mark.asyncio
async def test_repair_disabled_when_retries_zero() -> None:
    """repair_retries=0 → single attempt, no repair turn."""
    bad = json.dumps({"unexpected": "field"})
    good = json.dumps({"grade": "no", "reason": "x"})
    stub = _SequencedLiteLLM([bad, good])
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/some-qwen3",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        supports_json_mode=True,
        fallback_to_json_parse=False,
        repair_retries=0,
    )
    assert out is None
    assert len(stub.calls) == 1


@pytest.mark.asyncio
async def test_understand_output_condensed_query_optional() -> None:
    """qwen3 omitting condensed_query must not break understand — schema now
    defaults it to empty string instead of raising on the missing field."""
    body = json.dumps({"intent": "factoid"})  # no condensed_query, no confidence
    stub = _StubLiteLLMOpenAI(body)
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/some-qwen3",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=UnderstandOutput,
        supports_json_mode=True,
        fallback_to_json_parse=False,
    )
    assert isinstance(out, UnderstandOutput)
    assert out.condensed_query == ""
    assert out.intent == "factoid"


@pytest.mark.asyncio
async def test_no_repair_when_provider_call_raises() -> None:
    """A raising provider call (no usable response) is terminal — the helper
    returns None immediately, it does NOT spend a repair turn."""
    stub = _StubLiteLLMRaising(RuntimeError("upstream 500"))
    out = await call_with_schema(
        litellm_module=stub,
        litellm_name="openai/some-qwen3",
        provider_code="openai",
        messages=[{"role": "user", "content": "q"}],
        schema=GradeOutput,
        supports_json_mode=True,
    )
    assert out is None
