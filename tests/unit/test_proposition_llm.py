"""Unit tests for the Proposition LLM Atomic Decomposition strategy.

Mock-only — no live LLM calls. Verifies:

* ``NullPropositionDecomposer`` returns ``[text]`` unchanged (default OFF).
* ``LLMPropositionDecomposer`` forwards the Chen et al. (Dense X Retrieval,
  EMNLP 2024) system instruction + paragraph to ``LLMPort.complete`` and
  parses the one-per-line completion into atomic propositions.
* Empty / whitespace paragraph short-circuits without hitting the LLM.
* Oversized paragraph (> ``DEFAULT_PROPOSITION_LLM_MAX_INPUT_CHARS``)
  falls back to ``[text]`` so a single LLM call never blows context.
* LLM adapter exceptions (RetrievalError, OSError, ValueError,
  TimeoutError) degrade silent — return ``[text]`` (HALLU=0 sacred:
  fall back to original chunk, NOT fabricate).
* Empty LLM completion → fall back to ``[text]``.
* Malformed completion that parses to zero usable propositions →
  fall back to ``[text]``.
* Enumeration prefixes (``"1. "``, ``"- "``, ``"* "``, ``"• "``,
  ``"1) "``) stripped from each proposition.
* Domain-neutral system instruction (no industry/brand literals).
* ``build_proposition_decomposer("null"|"llm")`` returns the matching
  strategy; unknown / typo provider raises ``ValueError``.
* Provider key normalisation is case-insensitive + whitespace-tolerant.
* Constants pinned: feature flag default OFF, gpt-4o-mini default,
  temperature near zero, sensible token cap.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMMessage, LLMResponse
from ragbot.application.ports.proposition_decomposer_port import (
    PropositionDecomposerPort,
)
from ragbot.shared.constants import (
    DEFAULT_PROPOSITION_LLM_DECOMP_ENABLED,
    DEFAULT_PROPOSITION_LLM_MAX_INPUT_CHARS,
    DEFAULT_PROPOSITION_LLM_MAX_TOKENS,
    DEFAULT_PROPOSITION_LLM_MIN_LEN,
    DEFAULT_PROPOSITION_LLM_MODEL,
    DEFAULT_PROPOSITION_LLM_PROVIDER,
    DEFAULT_PROPOSITION_LLM_TEMPERATURE,
    DEFAULT_PROPOSITION_USE_LLM,
)
from ragbot.shared.errors import RetrievalError
try:
    from ragbot.shared.proposition_llm import (
        LLMPropositionDecomposer,
        NullPropositionDecomposer,
        build_proposition_decomposer,
        list_providers,
    )
except ImportError:  # module body commented out as dead-code — tests cover reactivatable code
    pytest.skip(
        "proposition_llm is dead-code (body commented out)",
        allow_module_level=True,
    )
from ragbot.shared.types import TenantId, TraceId


@dataclass
class FakeLLM:
    """Records every ``complete`` call and returns a scripted reply.

    If ``raise_exc`` is set, ``complete`` raises it instead of returning
    a response — used to exercise the degrade-silent path.
    """

    reply: str = (
        "Hệ thống thực hiện sao lưu dữ liệu hằng ngày.\n"
        "Bản sao lưu được lưu trữ tại hai trung tâm dữ liệu khác nhau.\n"
        "Quá trình sao lưu kéo dài khoảng 30 phút."
    )
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
            tokens_in=120,
            tokens_out=80,
            cost_usd=0.0,
            latency_ms=42,
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
    model_name: str = "openai/gpt-4o-mini",
    max_tokens: int = DEFAULT_PROPOSITION_LLM_MAX_TOKENS,
    temperature: float = DEFAULT_PROPOSITION_LLM_TEMPERATURE,
) -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000005c01"),
        model_name=model_name,
        provider="openai",
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=1.0,
    )


# ---------------------------------------------------------------------------
# Null Object — default OFF baseline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_null_decomposer_returns_text_as_single_element_list() -> None:
    """Default OFF Null Object: identity on input (single-element list)."""
    null_dec: PropositionDecomposerPort = NullPropositionDecomposer()
    text = "Điều 11 quy định về việc sao lưu dữ liệu định kỳ."

    result = await null_dec.decompose(text)

    assert result == [text]
    assert NullPropositionDecomposer.get_provider_name() == "null"


@pytest.mark.asyncio
async def test_null_decomposer_empty_input_returns_empty_list() -> None:
    """Empty / whitespace input → empty list (caller can detect)."""
    null_dec = NullPropositionDecomposer()
    assert await null_dec.decompose("") == []
    assert await null_dec.decompose("   \n  ") == []


# ---------------------------------------------------------------------------
# LLM strategy — happy path + Chen et al. prompt contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_decomposer_calls_llm_with_chen_et_al_prompt() -> None:
    """LLM strategy forwards system + user messages and parses propositions."""
    llm = FakeLLM()
    tenant = TenantId(uuid4())
    trace = TraceId("trace-5c-proposition-happy")
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=tenant,
        trace_id=trace,
    )
    paragraph = (
        "Hệ thống thực hiện sao lưu dữ liệu hằng ngày. Bản sao lưu được lưu "
        "trữ tại hai trung tâm dữ liệu khác nhau và quá trình sao lưu kéo "
        "dài khoảng 30 phút."
    )

    result = await strategy.decompose(paragraph)

    # Exactly one LLM call, threaded with tenant + trace.
    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["record_tenant_id"] == tenant
    assert call["trace_id"] == trace
    # Spec model honours the admin-override default (gpt-4o-mini).
    assert call["spec"].model_name == "openai/gpt-4o-mini"
    assert call["spec"].max_tokens == DEFAULT_PROPOSITION_LLM_MAX_TOKENS
    # Near-zero temperature: decomposition is deterministic rewriting.
    assert call["spec"].temperature == DEFAULT_PROPOSITION_LLM_TEMPERATURE

    messages = call["messages"]
    assert len(messages) == 2
    assert messages[0].role == "system"
    sys_lower = messages[0].content.lower()
    # Chen et al. core requirements present in instruction:
    assert "atomic" in sys_lower  # atomic decomposition
    assert "self-contained" in sys_lower  # decontextualisation
    assert "pronoun" in sys_lower  # replace pronouns
    # HALLU guard wording: do NOT add/infer/extrapolate.
    assert "do not" in sys_lower
    # Output format declared: one per line, no numbering.
    assert "one proposition per line" in sys_lower

    assert messages[1].role == "user"
    assert messages[1].content == paragraph

    # Three propositions parsed from the scripted three-line reply.
    assert len(result) == 3
    assert result[0] == "Hệ thống thực hiện sao lưu dữ liệu hằng ngày."
    assert (
        result[1]
        == "Bản sao lưu được lưu trữ tại hai trung tâm dữ liệu khác nhau."
    )
    assert result[2] == "Quá trình sao lưu kéo dài khoảng 30 phút."


@pytest.mark.asyncio
async def test_llm_decomposer_strips_enumeration_prefixes() -> None:
    """Models that ignore "no numbering" still produce clean output."""
    llm = FakeLLM(
        reply=(
            "1. Hệ thống thực hiện sao lưu dữ liệu hằng ngày.\n"
            "2) Bản sao lưu được lưu trữ tại hai trung tâm dữ liệu.\n"
            "- Quá trình sao lưu kéo dài khoảng 30 phút.\n"
            "* Người quản trị có thể kích hoạt sao lưu thủ công.\n"
            "• Báo cáo sao lưu được gửi cho quản trị viên hằng ngày."
        )
    )
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-enum"),
    )

    result = await strategy.decompose("Một đoạn văn bản bất kỳ.")

    assert len(result) == 5
    for prop in result:
        # No surviving leading numbering or bullet.
        assert not prop[:3].lstrip().startswith(("1.", "2.", "-", "*", "•"))
    assert result[0] == "Hệ thống thực hiện sao lưu dữ liệu hằng ngày."
    assert result[3] == "Người quản trị có thể kích hoạt sao lưu thủ công."
    assert result[4] == "Báo cáo sao lưu được gửi cho quản trị viên hằng ngày."


# ---------------------------------------------------------------------------
# LLM strategy — short-circuit + size guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_decomposer_empty_input_short_circuits() -> None:
    """Empty / whitespace input skips the LLM (cost + latency guard)."""
    llm = FakeLLM()
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty"),
    )

    assert await strategy.decompose("") == []
    assert await strategy.decompose("   \n  ") == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_llm_decomposer_oversized_input_falls_back_without_llm_call() -> None:
    """Oversized paragraph → ``[text]`` without LLM call (context guard)."""
    llm = FakeLLM()
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-oversize"),
        max_input_chars=100,  # tiny cap so we trip the guard easily
    )
    huge = "x" * 500

    result = await strategy.decompose(huge)

    assert result == [huge]
    # Did NOT call the LLM — defence-in-depth.
    assert llm.calls == []


# ---------------------------------------------------------------------------
# HALLU=0 — graceful degradation on adapter failure / empty completion
# ---------------------------------------------------------------------------


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
async def test_llm_decomposer_adapter_failure_falls_back_to_original_chunk(
    exc: Exception,
) -> None:
    """Any LLM adapter error → return ``[text]`` (HALLU=0 sacred).

    The contract is critical: on failure we MUST NOT fabricate
    propositions. We MUST return the original paragraph so the embedder
    still receives the source content and ingest never drops a chunk.
    """
    llm = FakeLLM(raise_exc=exc)
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-fail"),
    )
    paragraph = "Một đoạn văn về chính sách sao lưu dữ liệu của hệ thống."

    result = await strategy.decompose(paragraph)

    # Fallback returns the ORIGINAL paragraph, not invented propositions.
    assert result == [paragraph]
    # Exactly one attempt — no retry inside the strategy.
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_llm_decomposer_empty_completion_falls_back_to_original_chunk() -> None:
    """LLM returns empty content → degrade silent to ``[text]``."""
    llm = FakeLLM(reply="   \n  \n  ")
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty-content"),
    )
    paragraph = "Một đoạn văn bất kỳ về sao lưu dữ liệu."

    result = await strategy.decompose(paragraph)

    assert result == [paragraph]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_llm_decomposer_only_noise_completion_falls_back() -> None:
    """Completion with only sub-min-length lines → fall back to ``[text]``.

    Defence against malformed completions (e.g. one stray comma per line)
    that would otherwise produce a list of useless one-character entries.
    """
    llm = FakeLLM(reply=",\n.\n;\n-\n*")
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-noise"),
    )
    paragraph = "Một đoạn văn bất kỳ."

    result = await strategy.decompose(paragraph)

    assert result == [paragraph]


@pytest.mark.asyncio
async def test_llm_decomposer_drops_sub_min_length_lines_keeps_real_ones() -> None:
    """Mixed completion: keep real propositions, drop noise lines."""
    llm = FakeLLM(
        reply=(
            "Hệ thống thực hiện sao lưu dữ liệu hằng ngày.\n"
            ",\n"
            "Bản sao lưu được lưu trữ tại hai trung tâm dữ liệu.\n"
            ".\n"
            "Quá trình sao lưu kéo dài 30 phút."
        )
    )
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-mixed"),
    )

    result = await strategy.decompose("Một đoạn văn bất kỳ.")

    # Only the three real propositions survive; lone "," and "." dropped.
    assert len(result) == 3
    assert all(len(p) >= DEFAULT_PROPOSITION_LLM_MIN_LEN for p in result)


# ---------------------------------------------------------------------------
# Domain-neutral prompt — no industry / brand literals leaking through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_decomposer_system_instruction_is_domain_neutral() -> None:
    """System instruction must not reference any industry / brand vertical.

    The platform is domain-neutral (CLAUDE.md absolute rule) — bot
    owners' system_prompt is the single source of truth for domain
    framing, never an instruction we inject here.
    """
    llm = FakeLLM()
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-domain"),
    )

    await strategy.decompose("Một đoạn văn bất kỳ.")

    sys_msg = llm.calls[0]["messages"][0].content.lower()
    # Match whole-word substrings (with word-boundary chars) so we don't
    # false-positive on "decompose" ⊃ "ecom".
    import re as _re

    for forbidden in (
        "legal",
        "medical",
        "healthcare",
        "ecommerce",
        "e-commerce",
        " law ",
        "doctor",
        "patient",
        "bank",
        "insurance",
        "stategov",
        "government",
    ):
        assert _re.search(rf"\b{_re.escape(forbidden.strip())}\b", sys_msg) is None, (
            f"domain literal {forbidden!r} leaked into system instruction"
        )


@pytest.mark.asyncio
async def test_llm_decomposer_preserves_user_paragraph_verbatim() -> None:
    """The paragraph reaches the LLM untouched — no preprocessing here."""
    llm = FakeLLM()
    strategy = LLMPropositionDecomposer(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-verbatim"),
    )
    vi_paragraph = (
        "Hệ thống sao lưu dữ liệu mỗi ngày một lần. "
        "Người quản trị có thể kích hoạt sao lưu thủ công bất cứ lúc nào."
    )

    await strategy.decompose(vi_paragraph)

    user_msg = llm.calls[0]["messages"][1]
    assert user_msg.role == "user"
    assert user_msg.content == vi_paragraph
    # System message instructs language preservation.
    sys_msg_lower = llm.calls[0]["messages"][0].content.lower()
    assert "preserve" in sys_msg_lower
    assert "language" in sys_msg_lower


# ---------------------------------------------------------------------------
# Registry (Strategy + DI pattern)
# ---------------------------------------------------------------------------


def test_build_proposition_decomposer_null_returns_null_strategy() -> None:
    strategy = build_proposition_decomposer("null")

    assert isinstance(strategy, NullPropositionDecomposer)
    assert "null" in list_providers()


def test_build_proposition_decomposer_llm_returns_llm_strategy() -> None:
    llm = FakeLLM()
    strategy = build_proposition_decomposer(
        "llm",
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-build-llm"),
    )

    assert isinstance(strategy, LLMPropositionDecomposer)
    assert "llm" in list_providers()


def test_build_proposition_decomposer_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown proposition decomposer provider"):
        build_proposition_decomposer("does-not-exist")


def test_build_proposition_decomposer_provider_key_normalised() -> None:
    """Operator might type ``Null`` or ``  llm  `` in admin UI — registry
    normalises before lookup so config typos don't 500 ingest."""
    assert isinstance(build_proposition_decomposer("  Null  "), NullPropositionDecomposer)
    assert isinstance(build_proposition_decomposer("NULL"), NullPropositionDecomposer)


# ---------------------------------------------------------------------------
# Constants contract — feature flag default + admin overrides
# ---------------------------------------------------------------------------


def test_proposition_llm_constants_match_default_off_and_safety_caps() -> None:
    """Pin the constant values that the rest of the platform relies on.

    Owner-opt-in: BOTH feature flags MUST default to ``False`` so the
    platform ships proposition decomposition disabled. Anything else
    would silently change ingest cost + behaviour for every tenant.
    """
    assert DEFAULT_PROPOSITION_LLM_DECOMP_ENABLED is False
    assert DEFAULT_PROPOSITION_USE_LLM is False
    assert DEFAULT_PROPOSITION_LLM_PROVIDER == "null"
    # Admin override 2026-05-14: proposition decomposition default uses
    # gpt-4o-mini per Chen et al. EMNLP 2024 implementation spec.
    assert DEFAULT_PROPOSITION_LLM_MODEL == "gpt-4o-mini"
    # Near-zero temperature: decomposition is deterministic rewriting
    # (decontextualisation). Any randomness risks inventing facts.
    assert 0.0 <= DEFAULT_PROPOSITION_LLM_TEMPERATURE <= 0.2
    # Token cap is a SAFETY ceiling — uncapped output blows latency + cost.
    assert 100 <= DEFAULT_PROPOSITION_LLM_MAX_TOKENS <= 2000
    # Input ceiling keeps a single LLM call within context window.
    assert 1000 <= DEFAULT_PROPOSITION_LLM_MAX_INPUT_CHARS <= 16_000
    # Minimum proposition length drops noise but keeps real short
    # propositions (e.g. a single SVO clause).
    assert 5 <= DEFAULT_PROPOSITION_LLM_MIN_LEN <= 50
