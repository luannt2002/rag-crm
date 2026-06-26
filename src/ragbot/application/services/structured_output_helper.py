"""Structured-output helper — provider-enforced JSON schema.

Bridges Pydantic schemas → LiteLLM `response_format` (OpenAI / Azure) or
Anthropic `tool_choice` so the provider enforces the contract. Callers
receive a validated Pydantic instance or ``None`` on failure; ad-hoc
regex / ``json.loads`` retry logic moves out of the orchestration layer.

Dispatch — capability-driven, NOT name-substring. When the resolver
surfaces the model's declared capabilities (``supports_json_mode`` /
``supports_tools`` from ``ai_models``), routing keys off those flags so an
OpenAI-compatible endpoint that does NOT implement strict ``json_schema``
(e.g. Qwen3 served behind an OpenAI-shape gateway) gets the looser
``response_format={"type": "json_object"}`` mode instead of a strict schema
it rejects. Order of preference:

1. ``supports_json_mode`` → ``json_object`` mode (loose; the model emits a
   JSON object, validated client-side against the Pydantic schema).
2. else OpenAI-compatible (legacy name/code match) → strict ``json_schema``.
3. else ``supports_tools`` / Anthropic → ``tool_choice`` function call.
4. else → plain completion + best-effort JSON parse.

When the capability flags are ``None`` (older callers), the legacy
name-substring routing is preserved byte-for-byte so existing behaviour is
unchanged.

A bounded single repair retry re-issues the call with an appended repair
instruction when the first response fails schema validation — one extra
round-trip (``DEFAULT_STRUCTURED_OUTPUT_REPAIR_RETRIES``), never an
unbounded loop.

Domain-neutral by design — schemas are passed in by the caller; this module
holds zero tenant or industry knowledge.
"""

from __future__ import annotations

import asyncio
import functools
import json as _json_mod
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel, ValidationError

from ragbot.shared.constants import (
    ANTHROPIC_PROVIDER_CODES,
    DEFAULT_STRUCTURED_OUTPUT_REPAIR_RETRIES,
    DEFAULT_STRUCTURED_OUTPUT_SCHEMA_CACHE_SIZE,
    OPENAI_STRUCTURED_OUTPUT_PROVIDER_CODES,
)

# Sink callback receives ``(prompt_tokens, completion_tokens, cached_tokens,
# response_text, finish_reason)`` after the LLM call returns. Used by
# ``query_graph._invoke_structured_llm_node`` so token counts make it into
# ``model_invocations`` even though the structured path swallows the
# ``ModelResponse`` object before returning the parsed schema.
StructuredUsageSink = Callable[[int, int, int, str, str | None], Awaitable[None] | None]

logger = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


def _haystack(litellm_name: str | None, provider_code: str | None) -> str:
    return f"{(litellm_name or '').lower()}|{(provider_code or '').lower()}"


def _force_additional_properties_false(schema_node: Any) -> Any:
    """Walk a JSON-schema dict and stamp ``additionalProperties: false`` on
    every object node, including nested ``$defs`` / ``properties`` / array
    ``items``. OpenAI strict ``json_schema`` mode rejects the call otherwise.

    Pydantic ``ConfigDict(extra="forbid")`` already emits the flag at the
    top-level model, but lists-of-objects via ``$defs`` and Pydantic
    ``Annotated`` re-uses can drop it on nested nodes. Doing one explicit
    walk here is cheap and survives schema-shape changes.
    """
    if isinstance(schema_node, dict):
        if schema_node.get("type") == "object" and "additionalProperties" not in schema_node:
            schema_node["additionalProperties"] = False
        for v in schema_node.values():
            _force_additional_properties_false(v)
    elif isinstance(schema_node, list):
        for item in schema_node:
            _force_additional_properties_false(item)
    return schema_node


def _force_required_all_properties(schema_node: Any) -> Any:
    """Walk a JSON-schema dict and ensure every object's ``required`` list
    enumerates every key in its ``properties``.

    OpenAI strict ``json_schema`` mode rejects the call when ``required``
    omits any property — even ones with a Pydantic default value. Strip
    out the optionality and let the model emit defaults inline; the
    caller's Pydantic model still applies its own defaults during
    ``model_validate_json`` post-processing if a value comes back empty.

    Walks nested ``$defs`` / ``properties`` / array ``items`` so list-of-
    object shapes are covered too.
    """
    if isinstance(schema_node, dict):
        if schema_node.get("type") == "object":
            properties = schema_node.get("properties")
            if isinstance(properties, dict) and properties:
                schema_node["required"] = list(properties.keys())
        for v in schema_node.values():
            _force_required_all_properties(v)
    elif isinstance(schema_node, list):
        for item in schema_node:
            _force_required_all_properties(item)
    return schema_node


@functools.lru_cache(maxsize=DEFAULT_STRUCTURED_OUTPUT_SCHEMA_CACHE_SIZE)
def _cached_hardened_schema(schema: type) -> dict:
    """Memoise the per-schema strict-JSON build — Pydantic's
    ``model_json_schema()`` walk + the recursive harden pass are
    deterministic, so a structured-output call shouldn't re-pay them
    on every request. Keyed by the Pydantic class object: identical
    classes hash equal, distinct classes get distinct cache slots.
    """
    return _harden_strict_json_schema(schema.model_json_schema())


def _harden_strict_json_schema(schema_node: Any) -> Any:
    """Apply both OpenAI strict-mode requirements: ``additionalProperties:
    false`` and ``required`` covers every property key. One walk would be
    fractionally faster but two named helpers are easier to test."""
    _force_additional_properties_false(schema_node)
    _force_required_all_properties(schema_node)
    return schema_node


def _is_openai_compatible(
    litellm_name: str | None, provider_code: str | None
) -> bool:
    """OpenAI / Azure OpenAI: ``response_format={"type": "json_schema"}`` path."""
    h = _haystack(litellm_name, provider_code)
    return any(token in h for token in OPENAI_STRUCTURED_OUTPUT_PROVIDER_CODES)


def _is_anthropic(litellm_name: str | None, provider_code: str | None) -> bool:
    """Anthropic Claude: ``tools=[…] tool_choice={…}`` path."""
    h = _haystack(litellm_name, provider_code)
    return any(token in h for token in ANTHROPIC_PROVIDER_CODES)


# Structured-output transport modes.
_MODE_JSON_OBJECT = "json_object"   # loose response_format, client-side validate
_MODE_JSON_SCHEMA = "json_schema"   # strict OpenAI provider-enforced schema
_MODE_TOOL = "tool"                 # function tool_choice (Anthropic-shape)
_MODE_PLAIN = "plain"               # no provider enforcement, parse text


def _select_mode(
    *,
    litellm_name: str | None,
    provider_code: str | None,
    supports_json_mode: bool | None,
    supports_tools: bool | None,
) -> str:
    """Pick the structured-output transport for this model.

    Capability flags (resolver-surfaced from ``ai_models``) win over the
    legacy name-substring routing. A model that advertises ``json_mode``
    but NOT strict ``json_schema`` (the Qwen3-behind-OpenAI-gateway case)
    must take the loose ``json_object`` path; forcing strict ``json_schema``
    there yields ``Extra-inputs`` / missing-field rejections.

    Precedence:

    1. ``supports_json_mode`` → ``json_object``.
    2. legacy OpenAI-compatible match → strict ``json_schema``.
    3. ``supports_tools`` OR Anthropic match → ``tool``.
    4. otherwise → ``plain``.

    When BOTH capability flags are ``None`` the function reduces to the
    pre-existing name-substring routing (json_schema for OpenAI-compatible,
    tool for Anthropic, plain otherwise) so legacy callers are unchanged.
    """
    if supports_json_mode:
        return _MODE_JSON_OBJECT
    if _is_openai_compatible(litellm_name, provider_code):
        return _MODE_JSON_SCHEMA
    if supports_tools or _is_anthropic(litellm_name, provider_code):
        return _MODE_TOOL
    return _MODE_PLAIN


def _build_repair_messages(
    messages: list[dict], schema: type[T], schema_name: str
) -> list[dict]:
    """Append a single repair instruction so the model re-emits valid JSON.

    The repair turn restates the JSON-schema contract and asks the model to
    return ONLY a JSON object/array matching it. Domain-neutral: the schema
    itself is the contract, no tenant/industry text. Appended as one extra
    ``user`` turn — the caller's original ``system_prompt`` (bot owner SoT)
    is never mutated.
    """
    schema_json = _json_mod.dumps(
        schema.model_json_schema(), ensure_ascii=False, sort_keys=True,
    )
    repair = (
        "Your previous reply did not match the required schema. "
        f"Return ONLY a single JSON value matching this JSON Schema named "
        f"{schema_name} — no prose, no markdown fences:\n{schema_json}"
    )
    return [*messages, {"role": "user", "content": repair}]


def _extract_text(response: Any) -> str:
    """Best-effort plain-text extraction from a LiteLLM ``ModelResponse``.

    Reasoning models (Qwen3 thinking, DeepSeek-R1, Gemma-thinking, etc.)
    expose two separate fields on ``choice.message``:

    * ``content`` — the final user-facing answer (may be empty when the
      ``max_tokens`` budget was consumed by the reasoning chain before
      the model could flush the answer).
    * ``reasoning_content`` — the chain-of-thought tokens.

    Some reasoning models also embed the final JSON / answer *inside*
    the reasoning block (e.g. "Let me think... here is the JSON:
    ``{...}``") instead of flushing a separate ``content`` field at all.
    We therefore fall back to ``reasoning_content`` when ``content`` is
    empty so the downstream JSON-extraction step still has bytes to
    scan — this is the 2026-05-21 fix for the Qwen3.6-35b structured
    output regression documented in
    ``plans/260521-INNOCOM-3SVC-SWAP/plan.md`` §S5.
    """
    try:
        choice = response.choices[0]
        msg = getattr(choice, "message", None)
        if msg is None:
            return ""
        content = getattr(msg, "content", None) or ""
        if content:
            return content
        # Reasoning-model fallback: prefer ``reasoning_content`` over an
        # empty ``content`` so the JSON-block scanner below can still
        # match an inline ``{...}`` payload.
        reasoning = getattr(msg, "reasoning_content", None) or ""
        return reasoning
    except (AttributeError, IndexError, TypeError):
        # ``response`` may be a stub / partial object during fallback paths.
        return ""


def _extract_anthropic_tool_args(response: Any) -> dict | None:
    """Pull the JSON arguments out of an Anthropic tool-use response.

    LiteLLM normalizes Anthropic tool-use into OpenAI-style ``tool_calls``
    on ``message`` so we read ``tool_calls[0].function.arguments``.
    """
    try:
        choice = response.choices[0]
        msg = getattr(choice, "message", None)
        if msg is None:
            return None
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return None
        first = tool_calls[0]
        fn = getattr(first, "function", None)
        if fn is None:
            return None
        raw = getattr(fn, "arguments", None)
        if raw is None:
            return None
        if isinstance(raw, dict):
            return raw
        return _json_mod.loads(raw)
    except (AttributeError, IndexError, TypeError, ValueError, _json_mod.JSONDecodeError):
        # Anthropic tool_calls may be missing / malformed when the model
        # declines to use the tool — caller treats ``None`` as "no args".
        return None


def _extract_finish_reason(response: Any) -> str | None:
    """Pull ``choices[0].finish_reason`` if available."""
    try:
        return getattr(response.choices[0], "finish_reason", None)
    except (AttributeError, IndexError):
        return None


async def _emit_usage_sink(
    sink: StructuredUsageSink | None,
    response: Any,
    response_text: str,
) -> None:
    """Best-effort forward of the response's token counts to ``sink``.

    Usage extraction lives in ``ragbot.shared.llm_usage`` — a pure
    helper with no infrastructure deps — so this application service
    stays free of an ``infrastructure.*`` import (hexagonal boundary).
    Exceptions are swallowed at debug level: a missing meter must not
    break generation.
    """
    if sink is None:
        return
    try:
        from ragbot.shared.llm_usage import extract_usage_from_response

        prompt, completion, cached = extract_usage_from_response(response)
        finish_reason = _extract_finish_reason(response)
        result = sink(
            int(prompt),
            int(completion),
            int(cached),
            response_text,
            finish_reason,
        )
        if asyncio.iscoroutine(result):
            await result
    except Exception as exc:
        logger.debug("structured_output_usage_sink_failed", err=str(exc))


def _fallback_json_parse(response: Any, schema: type[T]) -> T | None:
    """Last-resort parse: scan response text for a JSON object/array.

    Robust to 3 wire shapes:

    1. **Clean JSON** (OpenAI / Anthropic when ``response_format`` honoured):
       ``'{"key": "value"}'`` — direct ``model_validate_json``.
    2. **Fenced JSON** (some models wrap in markdown fences):
       ``'```json\\n{"key": "value"}\\n```'`` — strip the fence.
    3. **Reasoning-prefixed JSON** (Qwen3 thinking, DeepSeek-R1, etc.):
       ``'Let me think... <reasoning text>\\n\\n{"key": "value"}\\nExplanation: ...'``
       — regex-scan for the first balanced top-level JSON block.

    Pattern 3 is the 2026-05-21 addition for the Qwen3.6-35b structured
    output regression. The reasoning model's chain-of-thought lands in
    ``reasoning_content`` (now surfaced by ``_extract_text``) but the
    final JSON payload is embedded somewhere inside that block — the
    schema validator otherwise rejects the entire mixed string.
    """
    text = (_extract_text(response) or "").strip()
    if not text:
        return None
    # Accept fenced ```json ... ``` blocks too.
    if text.startswith("```"):
        # Strip first fence line + trailing fence.
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    # Try direct parse first — covers patterns 1 + 2.
    try:
        return schema.model_validate_json(text)
    except (ValidationError, ValueError):
        pass
    # Pattern 3: scan for a balanced top-level JSON object/array embedded
    # in a longer prose response. ``re.DOTALL`` lets ``.`` span newlines
    # so multi-line JSON inside reasoning text still matches.
    extracted = _scan_first_json_block(text)
    if extracted is None:
        return None
    try:
        return schema.model_validate_json(extracted)
    except (ValidationError, ValueError):
        return None


def _scan_first_json_block(text: str) -> str | None:
    """Scan ``text`` for the first balanced top-level JSON object or array.

    Returns the substring (including the opening + closing brace) when
    found, or ``None`` when no balanced block exists. Tracks brace depth
    in a single pass — cheaper than regex backtracking on long
    reasoning preambles + safer against nested structures.

    Quote-aware: skips braces that appear inside string literals so a
    payload like ``{"path": "{escaped}"}`` does not confuse the depth
    counter.
    """
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start < 0:
            continue
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"' and not escape:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
    return None


async def _safe_acompletion(
    *,
    litellm_module: Any,
    schema_name: str,
    provider_code: str | None,
    litellm_name: str,
    **call_kwargs: Any,
) -> Any | None:
    """Single guarded ``acompletion`` call shared by every transport branch.

    Returns the response object, or ``None`` when the provider call raised
    (logged once here). Centralising the broad-except keeps exactly one
    BLE001 site for the four transport modes instead of duplicating it per
    branch — fewer broad-except sites, identical degrade-silent behaviour
    (transport error → ``None`` → caller skips repair).
    """
    try:
        return await litellm_module.acompletion(**call_kwargs)
    except Exception as exc:
        logger.warning(
            "structured_output_provider_call_failed",
            schema=schema_name, provider=provider_code,
            model=litellm_name, error=str(exc),
        )
        return None


async def _attempt_json_schema(
    *,
    litellm_module: Any,
    common_kwargs: dict[str, Any],
    schema: type[T],
    schema_name: str,
    provider_code: str | None,
    litellm_name: str,
    fallback_to_json_parse: bool,
    usage_sink: StructuredUsageSink | None,
) -> tuple[T | None, bool]:
    """Strict OpenAI ``json_schema`` attempt. Returns ``(parsed, call_ok)``.

    ``call_ok`` is False only when the provider call itself raised (no usable
    response) — the caller treats that as terminal and does not repair. A
    ``None`` parse with ``call_ok=True`` means "response came back but failed
    validation", which IS repairable.
    """
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_name,
            "schema": _cached_hardened_schema(schema),
            "strict": True,
        },
    }
    resp = await _safe_acompletion(
        litellm_module=litellm_module,
        schema_name=schema_name,
        provider_code=provider_code,
        litellm_name=litellm_name,
        response_format=response_format,
        **common_kwargs,
    )
    if resp is None:
        return None, False
    text = _extract_text(resp)
    await _emit_usage_sink(usage_sink, resp, text)
    try:
        return schema.model_validate_json(text), True
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "structured_output_validation_failed",
            schema=schema_name, provider=provider_code, error=str(exc),
        )
        if fallback_to_json_parse:
            return _fallback_json_parse(resp, schema), True
        return None, True


async def _attempt_json_object(
    *,
    litellm_module: Any,
    common_kwargs: dict[str, Any],
    schema: type[T],
    schema_name: str,
    provider_code: str | None,
    litellm_name: str,
    fallback_to_json_parse: bool,
    usage_sink: StructuredUsageSink | None,
) -> tuple[T | None, bool]:
    """Loose ``response_format={"type": "json_object"}`` attempt.

    The provider only guarantees the body is *a* JSON object — the schema is
    enforced client-side via ``model_validate``. This is the path for models
    that advertise ``supports_json_mode`` but not strict ``json_schema``
    (e.g. Qwen3 behind an OpenAI-shape gateway).
    """
    resp = await _safe_acompletion(
        litellm_module=litellm_module,
        schema_name=schema_name,
        provider_code=provider_code,
        litellm_name=litellm_name,
        response_format={"type": "json_object"},
        **common_kwargs,
    )
    if resp is None:
        return None, False
    text = _extract_text(resp)
    await _emit_usage_sink(usage_sink, resp, text)
    try:
        return schema.model_validate_json(text), True
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "structured_output_validation_failed",
            schema=schema_name, provider=provider_code, error=str(exc),
        )
        if fallback_to_json_parse:
            return _fallback_json_parse(resp, schema), True
        return None, True


async def _attempt_tool(
    *,
    litellm_module: Any,
    common_kwargs: dict[str, Any],
    schema: type[T],
    schema_name: str,
    provider_code: str | None,
    litellm_name: str,
    fallback_to_json_parse: bool,
    usage_sink: StructuredUsageSink | None,
) -> tuple[T | None, bool]:
    """Function ``tool_choice`` attempt (Anthropic-shape tool-use)."""
    tool_def = {
        "type": "function",
        "function": {
            "name": "submit_response",
            "description": f"Submit {schema_name} structured response.",
            "parameters": _cached_hardened_schema(schema),
        },
    }
    # Anthropic prompt cache: stamp ephemeral cache_control on the first
    # system message so the structured-output path enjoys the same 90%
    # input-token discount the free-form path already gets via the router.
    from ragbot.shared.anthropic_cache import apply_anthropic_cache_control
    cached_kwargs = dict(common_kwargs)
    cached_kwargs["messages"] = apply_anthropic_cache_control(
        common_kwargs["messages"],
        litellm_name=litellm_name,
        provider_code=provider_code,
    )
    resp = await _safe_acompletion(
        litellm_module=litellm_module,
        schema_name=schema_name,
        provider_code=provider_code,
        litellm_name=litellm_name,
        tools=[tool_def],
        tool_choice={
            "type": "function",
            "function": {"name": "submit_response"},
        },
        **cached_kwargs,
    )
    if resp is None:
        return None, False
    args = _extract_anthropic_tool_args(resp)
    sink_text = _json_mod.dumps(args) if args is not None else _extract_text(resp)
    await _emit_usage_sink(usage_sink, resp, sink_text)
    if args is None:
        if fallback_to_json_parse:
            return _fallback_json_parse(resp, schema), True
        return None, True
    try:
        return schema.model_validate(args), True
    except (ValidationError, ValueError) as exc:
        logger.warning(
            "structured_output_validation_failed",
            schema=schema_name, provider=provider_code, error=str(exc),
        )
        if fallback_to_json_parse:
            return _fallback_json_parse(resp, schema), True
        return None, True


async def _attempt_plain(
    *,
    litellm_module: Any,
    common_kwargs: dict[str, Any],
    schema: type[T],
    schema_name: str,
    provider_code: str | None,
    litellm_name: str,
    fallback_to_json_parse: bool,
    usage_sink: StructuredUsageSink | None,
) -> tuple[T | None, bool]:
    """Plain completion + best-effort JSON parse (no provider enforcement)."""
    resp = await _safe_acompletion(
        litellm_module=litellm_module,
        schema_name=schema_name,
        provider_code=provider_code,
        litellm_name=litellm_name,
        **common_kwargs,
    )
    if resp is None:
        return None, False
    await _emit_usage_sink(usage_sink, resp, _extract_text(resp))
    if fallback_to_json_parse:
        return _fallback_json_parse(resp, schema), True
    return None, True


_MODE_DISPATCH = {
    _MODE_JSON_SCHEMA: _attempt_json_schema,
    _MODE_JSON_OBJECT: _attempt_json_object,
    _MODE_TOOL: _attempt_tool,
    _MODE_PLAIN: _attempt_plain,
}


async def call_with_schema(
    *,
    litellm_module: Any,
    litellm_name: str,
    provider_code: str | None,
    messages: list[dict],
    schema: type[T],
    api_key: str | None = None,
    api_base: str | None = None,
    timeout: float | None = None,
    fallback_to_json_parse: bool = True,
    usage_sink: StructuredUsageSink | None = None,
    supports_json_mode: bool | None = None,
    supports_tools: bool | None = None,
    repair_retries: int | None = None,
    **kwargs: Any,
) -> T | None:
    """Call an LLM and return a validated Pydantic instance, or ``None``.

    Parameters
    ----------
    litellm_module
        Injected ``litellm`` (so tests can pass a stub). Must expose
        ``acompletion``.
    litellm_name
        Fully-qualified model name as known to LiteLLM
        (provider-prefixed wire name).
    provider_code
        Provider code from the bot's resolved runtime (DB ``ai_providers.code``).
        Used together with ``litellm_name`` for legacy name routing.
    messages
        Standard OpenAI-shape ``[{"role": ..., "content": ...}]`` payload.
    schema
        Pydantic ``BaseModel`` subclass describing the expected output.
    api_key, api_base, timeout
        Forwarded as-is to LiteLLM. ``None`` lets LiteLLM use env defaults.
    fallback_to_json_parse
        When True (default), if the provider call returns text that doesn't
        validate, try a plain ``model_validate_json`` on the response body
        before giving up. Disable for strict-mode callers.
    supports_json_mode, supports_tools
        Resolver-surfaced model capabilities (``ai_models`` columns). When
        ``supports_json_mode`` is True the loose ``json_object`` transport is
        used instead of strict ``json_schema``; when both are ``None`` the
        legacy name-substring routing applies (unchanged behaviour).
    repair_retries
        Number of bounded repair retries on validation failure. ``None``
        defaults to :data:`DEFAULT_STRUCTURED_OUTPUT_REPAIR_RETRIES`. Capped
        at that constant so callers cannot request an unbounded loop.
    **kwargs
        Extra args forwarded to ``litellm.acompletion`` (temperature,
        max_tokens, etc.).

    Returns
    -------
    Validated ``schema`` instance, or ``None`` on any failure path. Callers
    are expected to handle ``None`` explicitly (e.g. fall back to the
    legacy parse path or skip the node).
    """
    base_kwargs: dict[str, Any] = {"model": litellm_name, **kwargs}
    if api_key is not None:
        base_kwargs["api_key"] = api_key
    if api_base is not None:
        base_kwargs["api_base"] = api_base
    if timeout is not None:
        base_kwargs["timeout"] = timeout

    schema_name = schema.__name__
    mode = _select_mode(
        litellm_name=litellm_name,
        provider_code=provider_code,
        supports_json_mode=supports_json_mode,
        supports_tools=supports_tools,
    )
    attempt = _MODE_DISPATCH[mode]

    # Bounded repair: 0..DEFAULT cap. None → default; negative/over-cap clamp.
    cap = DEFAULT_STRUCTURED_OUTPUT_REPAIR_RETRIES
    retries = cap if repair_retries is None else max(0, min(int(repair_retries), cap))

    attempt_messages = messages
    # total tries = 1 initial + ``retries`` repair turns
    for attempt_idx in range(retries + 1):
        common_kwargs = dict(base_kwargs)
        common_kwargs["messages"] = attempt_messages
        parsed, call_ok = await attempt(
            litellm_module=litellm_module,
            common_kwargs=common_kwargs,
            schema=schema,
            schema_name=schema_name,
            provider_code=provider_code,
            litellm_name=litellm_name,
            fallback_to_json_parse=fallback_to_json_parse,
            usage_sink=usage_sink,
        )
        if parsed is not None:
            return parsed
        # No more repair budget, or the provider call itself failed (no usable
        # response to repair) → stop.
        if attempt_idx >= retries or not call_ok:
            return None
        logger.info(
            "structured_output_repair_retry",
            schema=schema_name, provider=provider_code,
            model=litellm_name, attempt=attempt_idx + 1,
        )
        attempt_messages = _build_repair_messages(messages, schema, schema_name)
    return None


__all__ = ["StructuredUsageSink", "call_with_schema"]
