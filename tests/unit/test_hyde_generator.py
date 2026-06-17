"""Unit tests for Phase-C C1 HyDE generator scaffolding.

Mock-only — no live LLM calls. Verifies:
- NullHyDEGenerator returns the raw query unchanged (default OFF).
- LLMHyDEGenerator forwards the system instruction + query to LLMPort
  and returns the LLM's hypothetical answer (stripped).
- Empty / whitespace query short-circuits without hitting the LLM.
- LLM adapter exceptions (RetrievalError, OSError, ValueError,
  TimeoutError) degrade silent — return the original query.
- Empty LLM content also degrades silent — return original query.
- Domain-neutral system instruction (no industry/brand literals).
- build_hyde("null") / ("llm") returns the matching strategy.
- Unknown provider key raises ValueError (owner-opt-in surfaces loud).
- Constants: admin override gpt-4.1-mini default + sensible token cap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.hyde_port import HyDEServicePort
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
try:
    from ragbot.infrastructure.hyde.llm_hyde import LLMHyDEGenerator
    from ragbot.infrastructure.hyde.null_hyde import NullHyDEGenerator
    from ragbot.infrastructure.hyde.registry import build_hyde, list_providers
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "hyde infra subpackage is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.constants import (
    DEFAULT_HYDE_ENABLED,
    DEFAULT_HYDE_MAX_TOKENS,
    DEFAULT_HYDE_MODEL,
    DEFAULT_HYDE_PROVIDER,
    DEFAULT_HYDE_TEMPERATURE,
)
from ragbot.shared.errors import RetrievalError
from ragbot.shared.types import TenantId, TraceId


@dataclass
class FakeLLM:
    """Records every ``complete`` call and returns a scripted reply.

    If ``raise_exc`` is set, ``complete`` raises it instead of returning
    a response — used to exercise the degrade-silent path.
    """

    reply: str = "Sao lưu dữ liệu được thực hiện hằng ngày để bảo đảm an toàn."
    raise_exc: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def health_check(self) -> bool:
        return True

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        del response_schema
        self.calls.append(
            {
                "messages": list(messages),
                "spec": spec,
                "record_tenant_id": record_tenant_id,
                "trace_id": trace_id,
            }
        )
        if self.raise_exc is not None:
            raise self.raise_exc
        return LLMResponse(
            content=self.reply,
            model=spec.model_name,
            provider=spec.provider,
            tokens_in=12,
            tokens_out=24,
            cost_usd=0.0,
            latency_ms=1,
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        *,
        spec: LLMSpec,
        record_tenant_id: TenantId,
        trace_id: TraceId,
    ) -> AsyncIterator[str]:
        raise NotImplementedError

    async def refresh_routing(self) -> None:
        return None

    async def close(self) -> None:
        return None


def _make_spec(
    *,
    model_name: str = "openai/gpt-4.1-mini",
    max_tokens: int = DEFAULT_HYDE_MAX_TOKENS,
    temperature: float = DEFAULT_HYDE_TEMPERATURE,
) -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000000c01"),
        model_name=model_name,
        provider="openai",
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=1.0,
    )


@pytest.mark.asyncio
async def test_null_hyde_returns_query_unchanged() -> None:
    """Default OFF Null Object: identity function on input."""
    null_hyde: HyDEServicePort = NullHyDEGenerator()
    query = "Điều 11 nói gì về sao lưu dữ liệu?"

    result = await null_hyde.generate(query)

    assert result == query
    assert NullHyDEGenerator.get_provider_name() == "null"


@pytest.mark.asyncio
async def test_llm_hyde_calls_llm_with_domain_neutral_system_instruction() -> None:
    """LLM strategy forwards system + user messages and returns stripped reply."""
    llm = FakeLLM(reply="   sao lưu hằng ngày bảo đảm dữ liệu an toàn   ")
    tenant = TenantId(uuid4())
    trace = TraceId("trace-c1-hyde-happy")
    strategy = LLMHyDEGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=tenant,
        trace_id=trace,
    )
    query = "sao lưu dữ liệu được thực hiện như thế nào?"

    result = await strategy.generate(query)

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["record_tenant_id"] == tenant
    assert call["trace_id"] == trace
    # Spec model honours the admin-override default (gpt-4.1-mini).
    assert call["spec"].model_name == "openai/gpt-4.1-mini"
    assert call["spec"].max_tokens == DEFAULT_HYDE_MAX_TOKENS

    messages = call["messages"]
    assert len(messages) == 2
    assert messages[0].role == "system"
    sys_lower = messages[0].content.lower()
    # Declarative-style instruction present.
    assert "declarative" in sys_lower
    # Domain-neutral: NO industry literals leaking into the prompt.
    for forbidden in ("legal", "medical", "ecom", "ecommerce", "law", "doctor"):
        assert forbidden not in sys_lower

    assert messages[1].role == "user"
    assert messages[1].content == query

    # Returned content stripped of surrounding whitespace.
    assert result == "sao lưu hằng ngày bảo đảm dữ liệu an toàn"


@pytest.mark.asyncio
async def test_llm_hyde_empty_query_short_circuits_without_llm_call() -> None:
    """Empty / whitespace input skips the LLM entirely (cost + latency guard)."""
    llm = FakeLLM()
    strategy = LLMHyDEGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty"),
    )

    assert await strategy.generate("") == ""
    assert await strategy.generate("   ") == "   "
    assert llm.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        RetrievalError("upstream LLM timeout"),
        OSError("socket closed"),
        ValueError("malformed completion"),
        TimeoutError("deadline exceeded"),
    ],
)
async def test_llm_hyde_adapter_failure_falls_back_to_raw_query(
    exc: Exception,
) -> None:
    """Any LLM adapter error → return original query (degrade silent)."""
    llm = FakeLLM(raise_exc=exc)
    strategy = LLMHyDEGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-fail"),
    )
    query = "what is the backup policy?"

    result = await strategy.generate(query)

    assert result == query
    # Exactly one attempt was made (no retry inside the strategy — that
    # belongs to the LLM adapter / circuit breaker layer below).
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_llm_hyde_empty_completion_falls_back_to_raw_query() -> None:
    """LLM returns empty content → degrade silent to raw query."""
    llm = FakeLLM(reply="   \n  ")
    strategy = LLMHyDEGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty-content"),
    )
    query = "What is HyDE?"

    result = await strategy.generate(query)

    assert result == query
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_llm_hyde_preserves_user_language_in_user_message() -> None:
    """The query reaches the LLM verbatim — the system instruction tells
    the model to mirror that language, so no language-tag preprocessing
    happens here."""
    llm = FakeLLM(reply="Đây là câu trả lời giả định bằng tiếng Việt.")
    strategy = LLMHyDEGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-vi"),
    )
    vi_query = "chính sách sao lưu của hệ thống là gì?"

    result = await strategy.generate(vi_query)

    # User message is the raw VN query, untouched.
    assert llm.calls[0]["messages"][1].content == vi_query
    # System message instructs language preservation.
    assert "preserve" in llm.calls[0]["messages"][0].content.lower()
    assert "language" in llm.calls[0]["messages"][0].content.lower()
    assert result == "Đây là câu trả lời giả định bằng tiếng Việt."


def test_build_hyde_null_returns_null_strategy() -> None:
    strategy = build_hyde("null")

    assert isinstance(strategy, NullHyDEGenerator)
    assert "null" in list_providers()


def test_build_hyde_llm_returns_llm_strategy() -> None:
    llm = FakeLLM()
    strategy = build_hyde(
        "llm",
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-build"),
    )

    assert isinstance(strategy, LLMHyDEGenerator)
    assert "llm" in list_providers()


def test_build_hyde_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown hyde provider"):
        build_hyde("does-not-exist")


def test_build_hyde_provider_key_case_insensitive_and_whitespace_tolerant() -> None:
    """Operator might type ``Null`` or ``  llm  `` in admin UI — registry
    normalises before lookup so config typos don't 500 the worker."""
    assert isinstance(build_hyde("  Null  "), NullHyDEGenerator)
    assert isinstance(build_hyde("NULL"), NullHyDEGenerator)


def test_hyde_constants_match_admin_override_and_safety_caps() -> None:
    """Pin the constant values that the rest of the platform relies on.

    Admin override 2026-05-12: HyDE default model = gpt-4.1-mini (NOT
    haiku). Anything else would silently change retrieval behaviour."""
    assert DEFAULT_HYDE_ENABLED is False
    assert DEFAULT_HYDE_PROVIDER == "null"
    assert DEFAULT_HYDE_MODEL == "gpt-4.1-mini"
    # Token cap is a SAFETY ceiling — uncapped output blows latency + cost.
    assert 50 <= DEFAULT_HYDE_MAX_TOKENS <= 500
    # Temperature stays in the "lightly creative" band — too high invents
    # facts that won't match documents, too low collapses to query echo.
    assert 0.0 < DEFAULT_HYDE_TEMPERATURE < 1.0


# ── T1.4 Wave F production wire — application-layer service ────────────────
#
# The infrastructure-layer ``LLMHyDEGenerator`` ships the raw Port + LLM call
# (covered above). The application-layer ``HyDEGenerator`` adds wall-clock
# timeout + the production call signature
# ``generate_hypothetical_answer(query, *, spec, record_tenant_id, trace_id)``
# that ``query_graph._embed_query`` invokes.

import asyncio  # noqa: E402 — keep the original 11 tests above untouched

from ragbot.application.services.hyde_generator import HyDEGenerator  # noqa: E402
from ragbot.shared.bot_limits import PLAN_LIMIT_SCHEMA  # noqa: E402
from ragbot.shared.constants import DEFAULT_HYDE_GENERATION_TIMEOUT_S  # noqa: E402


@pytest.mark.asyncio
async def test_application_hyde_generator_returns_llm_hypothetical_answer() -> None:
    """Happy path: cheap-LLM tier drafts a hypothetical, service returns it stripped."""
    llm = FakeLLM(reply="   Sao lưu hằng ngày bảo đảm dữ liệu an toàn.   ")
    service = HyDEGenerator(llm=llm)
    tenant = TenantId(uuid4())
    trace = TraceId("trace-app-hyde-happy")

    result = await service.generate_hypothetical_answer(
        "sao lưu dữ liệu được thực hiện thế nào?",
        spec=_make_spec(),
        record_tenant_id=tenant,
        trace_id=trace,
    )

    assert result == "Sao lưu hằng ngày bảo đảm dữ liệu an toàn."
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["record_tenant_id"] == tenant
    assert call["trace_id"] == trace
    # System instruction is domain-neutral + asks for declarative style.
    assert messages_have_declarative_instruction(call["messages"])


def messages_have_declarative_instruction(messages: list[LLMMessage]) -> bool:
    """Helper — system message must contain 'declarative' (case-insensitive)."""
    return any(
        m.role == "system" and "declarative" in m.content.lower()
        for m in messages
    )


@pytest.mark.asyncio
async def test_application_hyde_generator_empty_query_short_circuits() -> None:
    """Empty / whitespace queries skip the LLM entirely (cost + latency guard)."""
    llm = FakeLLM()
    service = HyDEGenerator(llm=llm)

    assert (
        await service.generate_hypothetical_answer(
            "",
            spec=_make_spec(),
            record_tenant_id=TenantId(uuid4()),
            trace_id=TraceId("trace-empty"),
        )
        == ""
    )
    assert (
        await service.generate_hypothetical_answer(
            "   ",
            spec=_make_spec(),
            record_tenant_id=TenantId(uuid4()),
            trace_id=TraceId("trace-ws"),
        )
        == "   "
    )
    assert llm.calls == []


@pytest.mark.asyncio
async def test_application_hyde_generator_timeout_falls_back_to_raw_query() -> None:
    """Wall-clock timeout (asyncio.wait_for) → return original query verbatim.

    The cheap LLM tier occasionally stalls on cold-start; HyDE MUST NOT
    extend the chat turn's p95 latency past the documented budget. A
    tight 0.05s budget against a sleep(1) LLM proves the wire cancels
    cleanly and degrades silent.
    """

    @dataclass
    class _SlowLLM:
        calls: list[dict[str, Any]] = field(default_factory=list)

        async def health_check(self) -> bool:
            return True

        async def complete(
            self,
            messages: list[LLMMessage],
            *,
            spec: LLMSpec,
            record_tenant_id: TenantId,
            trace_id: TraceId,
            response_schema: type[BaseModel] | None = None,
        ) -> LLMResponse:
            del response_schema
            self.calls.append({"messages": messages, "spec": spec})
            await asyncio.sleep(1.0)  # exceeds 0.05s budget below
            return LLMResponse(
                content="never reached",
                model=spec.model_name,
                provider=spec.provider,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_ms=0,
            )

        async def stream(
            self,
            messages: list[LLMMessage],
            *,
            spec: LLMSpec,
            record_tenant_id: TenantId,
            trace_id: TraceId,
        ) -> AsyncIterator[str]:
            raise NotImplementedError

        async def refresh_routing(self) -> None:
            return None

        async def close(self) -> None:
            return None

    llm = _SlowLLM()
    service = HyDEGenerator(llm=llm, timeout_s=0.05)
    query = "what is the backup policy?"

    result = await service.generate_hypothetical_answer(
        query,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-timeout"),
    )

    assert result == query
    # Timeout fires AFTER the LLM call started, so exactly one attempt was made.
    assert len(llm.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        RetrievalError("upstream LLM 503"),
        OSError("socket closed"),
        ValueError("malformed completion"),
    ],
)
async def test_application_hyde_generator_adapter_failure_falls_back(
    exc: Exception,
) -> None:
    """Adapter error → degrade silent, return original query."""
    llm = FakeLLM(raise_exc=exc)
    service = HyDEGenerator(llm=llm)
    query = "Điều 11 nói gì về sao lưu dữ liệu?"

    result = await service.generate_hypothetical_answer(
        query,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-app-fail"),
    )

    assert result == query
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_application_hyde_generator_empty_completion_falls_back() -> None:
    """LLM returns blank content → degrade silent to raw query."""
    llm = FakeLLM(reply="   \n\t  ")
    service = HyDEGenerator(llm=llm)
    query = "What is HyDE?"

    result = await service.generate_hypothetical_answer(
        query,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-app-empty"),
    )

    assert result == query
    assert len(llm.calls) == 1


def test_application_hyde_generator_default_timeout_matches_constant() -> None:
    """Constructor default uses the SSoT constant — no inline magic number."""
    service = HyDEGenerator(llm=FakeLLM())
    assert service._timeout_s == pytest.approx(DEFAULT_HYDE_GENERATION_TIMEOUT_S)
    # Sanity: the constant itself is within a sensible band (a single HyDE
    # call must NOT dominate a typical 8-15s chat turn budget).
    assert 1.0 <= DEFAULT_HYDE_GENERATION_TIMEOUT_S <= 15.0


def test_plan_limits_schema_exposes_hyde_enabled_default_off() -> None:
    """Per-bot opt-in: schema entry MUST exist and default OFF.

    This is the public contract the admin UI + chat_worker + test_chat
    pipeline_config builds depend on. A regression here silently re-enables
    HyDE for every bot, blowing latency + cost without owner intent.
    """
    assert "hyde_enabled" in PLAN_LIMIT_SCHEMA
    entry = PLAN_LIMIT_SCHEMA["hyde_enabled"]
    assert entry["type"] == "bool"
    assert entry["default"] is False
    assert entry["default"] == DEFAULT_HYDE_ENABLED


def test_query_graph_build_graph_accepts_hyde_generator_kwarg() -> None:
    """The build_graph() signature MUST expose hyde_generator as an
    optional, default-None DI slot so the production wire never breaks
    callers that haven't migrated to passing a HyDE service yet.

    This is the wire-contract guard: removing the parameter would silently
    drop HyDE from production without raising at boot.
    """
    import inspect

    from ragbot.orchestration.query_graph import build_graph

    sig = inspect.signature(build_graph)
    assert "hyde_generator" in sig.parameters, (
        "build_graph must expose 'hyde_generator' DI slot for T1.4 wire"
    )
    param = sig.parameters["hyde_generator"]
    # Optional with default None — zero blast radius when DI omits it.
    assert param.default is None
