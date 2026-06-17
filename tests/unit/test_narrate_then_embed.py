"""Unit tests for Stream 2A Narrate-then-Embed scaffolding.

Mock-only — no live LLM calls. Verifies:
- NullNarrateGenerator returns raw content unchanged (default OFF).
- LLMNarrateGenerator forwards the correct block-type prompt to LLMPort
  and returns the LLM's narration (stripped).
- Empty / whitespace content short-circuits without hitting the LLM.
- Unsupported block_type (HEADING / TEXT / CODE / LIST) bypasses the LLM.
- LLM adapter exceptions (RetrievalError, OSError, ValueError,
  TimeoutError) degrade silent — return the original content (HALLU=0
  fallback: never embed empty / fabricated text in place of source).
- Empty LLM content also degrades silent.
- Domain-neutral system instruction (no industry/brand literals).
- NarrateService applies feature flag + dual-content metadata storage.
- NarrateService.to_metadata writes both raw_chunk + narrated_text.
- build_narrate("null"|"llm") returns the matching strategy.
- Unknown provider key raises ValueError (owner-opt-in surfaces loud).
- Constants pin sane defaults (default OFF, low temperature, capped tokens).
- NullAnthropicHaikuBatchClient honours the 100-item cap + cost estimator.
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
from ragbot.application.ports.narrate_port import NarrateServicePort
from ragbot.application.services.narrate_service import (
    NarrateResult,
    NarrateService,
)
from ragbot.infrastructure.llm.anthropic_haiku_batch import (
    NarrateBatchItem,
    NullAnthropicHaikuBatchClient,
    estimate_batch_cost_usd,
)
from ragbot.infrastructure.narrate.llm_narrate import LLMNarrateGenerator
from ragbot.infrastructure.narrate.null_narrate import NullNarrateGenerator
from ragbot.infrastructure.narrate.registry import build_narrate, list_providers
from ragbot.shared.constants import (
    DEFAULT_NARRATE_BATCH_DISCOUNT_FACTOR,
    DEFAULT_NARRATE_BATCH_SIZE,
    DEFAULT_NARRATE_BATCH_USE,
    DEFAULT_NARRATE_MAX_TOKENS,
    DEFAULT_NARRATE_MODEL,
    DEFAULT_NARRATE_PROVIDER,
    DEFAULT_NARRATE_TEMPERATURE,
    DEFAULT_NARRATE_THEN_EMBED_ENABLED,
    NARRATE_BLOCK_TYPES_DEFAULT,
    NARRATE_METADATA_KEY_BLOCK_TYPE,
    NARRATE_METADATA_KEY_NARRATED_TEXT,
    NARRATE_METADATA_KEY_RAW_CHUNK,
)
from ragbot.shared.errors import RetrievalError
from ragbot.shared.types import BlockType, TenantId, TraceId


@dataclass
class FakeLLM:
    """Records every ``complete`` call and returns a scripted reply.

    If ``raise_exc`` is set, ``complete`` raises it instead of returning
    a response — used to exercise the degrade-silent path.
    """

    reply: str = "Bảng liệt kê đơn giá ba dịch vụ chính theo tháng."
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
            tokens_in=42,
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
    model_name: str = "anthropic/claude-haiku-4-5",
    max_tokens: int = DEFAULT_NARRATE_MAX_TOKENS,
    temperature: float = DEFAULT_NARRATE_TEMPERATURE,
) -> LLMSpec:
    return LLMSpec(
        binding_id=UUID("00000000-0000-0000-0000-000000000d01"),
        model_name=model_name,
        provider="anthropic",
        temperature=temperature,
        max_tokens=max_tokens,
        top_p=1.0,
    )


_TABLE_MARKDOWN = (
    "| Dịch vụ | Đơn giá |\n"
    "|---|---|\n"
    "| Cơ bản | 100k |\n"
    "| Tiêu chuẩn | 200k |\n"
    "| Cao cấp | 350k |\n"
)
_FORMULA_LATEX = r"$\bar{x} = \frac{1}{n}\sum_{i=1}^{n} x_i$"
_IMAGE_CAPTION = "Sơ đồ luồng xử lý gồm ba bước: nhập, xử lý, xuất."


@pytest.mark.asyncio
async def test_null_narrate_returns_content_unchanged_for_all_block_types() -> None:
    """Default OFF Null Object: identity function across every block type."""
    null_narrate: NarrateServicePort = NullNarrateGenerator()
    block_types: tuple[BlockType, ...] = (
        "TABLE", "FORMULA", "IMAGE", "TEXT", "HEADING", "CODE", "LIST",
    )
    for bt in block_types:
        result = await null_narrate.narrate("payload", bt)
        assert result == "payload", f"Null narrate altered {bt} content"

    assert NullNarrateGenerator.get_provider_name() == "null"


@pytest.mark.asyncio
async def test_llm_narrate_table_uses_table_prompt_and_returns_stripped_reply() -> None:
    """TABLE block routes through the table-linearisation prompt template."""
    llm = FakeLLM(reply="   Bảng liệt kê đơn giá ba gói dịch vụ.   ")
    tenant = TenantId(uuid4())
    trace = TraceId("trace-narrate-table")
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=tenant,
        trace_id=trace,
    )

    result = await strategy.narrate(_TABLE_MARKDOWN, "TABLE")

    assert len(llm.calls) == 1
    call = llm.calls[0]
    assert call["record_tenant_id"] == tenant
    assert call["trace_id"] == trace
    assert call["spec"].model_name == "anthropic/claude-haiku-4-5"
    assert call["spec"].max_tokens == DEFAULT_NARRATE_MAX_TOKENS

    messages = call["messages"]
    assert len(messages) == 2
    assert messages[0].role == "system"
    sys_lower = messages[0].content.lower()
    # Declarative-style + grounding rules present.
    assert "declarative" in sys_lower
    assert "do not invent" in sys_lower or "do not translate" in sys_lower
    # Domain-neutral: no industry literals leaking.
    for forbidden in ("legal", "medical", "ecom", "ecommerce", "law", "doctor", "fintech"):
        assert forbidden not in sys_lower

    # User prompt is the TABLE-specific template + verbatim source.
    user = messages[1].content
    assert "Diễn giải bảng" in user  # template localised VI (llm_narrate.py TABLE scaffold)
    assert _TABLE_MARKDOWN in user

    # Reply stripped of surrounding whitespace.
    assert result == "Bảng liệt kê đơn giá ba gói dịch vụ."


@pytest.mark.asyncio
async def test_llm_narrate_formula_uses_formula_prompt() -> None:
    """FORMULA block routes through the formula-description prompt."""
    llm = FakeLLM(reply="Công thức tính trung bình cộng của n giá trị.")
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-narrate-formula"),
    )

    result = await strategy.narrate(_FORMULA_LATEX, "FORMULA")

    user = llm.calls[0]["messages"][1].content
    assert "Diễn giải công thức" in user  # template localised VI (llm_narrate.py FORMULA scaffold)
    assert _FORMULA_LATEX in user
    assert result == "Công thức tính trung bình cộng của n giá trị."


@pytest.mark.asyncio
async def test_llm_narrate_image_uses_image_prompt() -> None:
    """IMAGE block routes through the image-caption prompt."""
    llm = FakeLLM(reply="Sơ đồ ba bước: nhập, xử lý, xuất kết quả.")
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-narrate-image"),
    )

    result = await strategy.narrate(_IMAGE_CAPTION, "IMAGE")

    user = llm.calls[0]["messages"][1].content
    assert "image caption" in user.lower() or "ocr" in user.lower()
    assert _IMAGE_CAPTION in user
    assert result == "Sơ đồ ba bước: nhập, xử lý, xuất kết quả."


@pytest.mark.asyncio
@pytest.mark.parametrize("block_type", ["HEADING", "TEXT", "CODE", "LIST"])
async def test_llm_narrate_skips_prose_block_types_without_llm_call(
    block_type: BlockType,
) -> None:
    """Prose blocks embed fine raw — strategy must NOT call the LLM."""
    llm = FakeLLM()
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-skip"),
    )

    raw = "Một đoạn văn bình thường, không cần linearise."
    result = await strategy.narrate(raw, block_type)

    assert result == raw
    assert llm.calls == []


@pytest.mark.asyncio
async def test_llm_narrate_empty_content_short_circuits_without_llm_call() -> None:
    """Empty / whitespace input skips the LLM entirely (cost + latency guard)."""
    llm = FakeLLM()
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty"),
    )

    assert await strategy.narrate("", "TABLE") == ""
    assert await strategy.narrate("   ", "TABLE") == "   "
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
async def test_llm_narrate_adapter_failure_falls_back_to_raw_content(
    exc: Exception,
) -> None:
    """Any LLM adapter error → return original content (degrade silent, HALLU=0)."""
    llm = FakeLLM(raise_exc=exc)
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-fail"),
    )

    result = await strategy.narrate(_TABLE_MARKDOWN, "TABLE")

    # Fallback to raw — we never embed empty / fabricated content.
    assert result == _TABLE_MARKDOWN
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_llm_narrate_empty_completion_falls_back_to_raw_content() -> None:
    """LLM returns empty content → degrade silent to raw content."""
    llm = FakeLLM(reply="   \n  ")
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-empty-content"),
    )

    result = await strategy.narrate(_TABLE_MARKDOWN, "TABLE")

    assert result == _TABLE_MARKDOWN
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_narrate_service_feature_flag_off_is_identity() -> None:
    """Feature flag OFF: service short-circuits even with a real LLM strategy."""
    llm = FakeLLM(reply="should not be called")
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-off"),
    )
    service = NarrateService(strategy=strategy, enabled=False)

    result = await service.narrate_chunk(_TABLE_MARKDOWN, "TABLE")

    assert service.enabled is False
    assert result.narrated is False
    assert result.text_for_embedding == _TABLE_MARKDOWN
    assert result.raw_chunk == _TABLE_MARKDOWN
    assert result.narrated_text == _TABLE_MARKDOWN
    assert result.block_type == "TABLE"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_narrate_service_feature_flag_on_enriches_table() -> None:
    """Feature flag ON: service replaces text_for_embedding with the narration."""
    narration = "Bảng liệt kê đơn giá ba gói dịch vụ theo tháng."
    llm = FakeLLM(reply=narration)
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-on"),
    )
    service = NarrateService(strategy=strategy, enabled=True)

    result = await service.narrate_chunk(_TABLE_MARKDOWN, "TABLE")

    assert service.enabled is True
    assert result.narrated is True
    assert result.text_for_embedding == narration
    assert result.narrated_text == narration
    # Raw chunk preserved verbatim for downstream LLM consumption.
    assert result.raw_chunk == _TABLE_MARKDOWN
    assert result.block_type == "TABLE"
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_narrate_service_unsupported_block_type_returns_raw() -> None:
    """Service honours eligible_block_types: TEXT bypasses even when enabled."""
    llm = FakeLLM(reply="should not be called")
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-text"),
    )
    service = NarrateService(strategy=strategy, enabled=True)

    raw = "Một đoạn văn bình thường."
    result = await service.narrate_chunk(raw, "TEXT")

    assert result.narrated is False
    assert result.text_for_embedding == raw
    assert result.raw_chunk == raw
    assert llm.calls == []


@pytest.mark.asyncio
async def test_narrate_service_falls_back_to_raw_on_strategy_failure() -> None:
    """HALLU=0 sacred — strategy raises → service returns raw chunk + narrated=False."""
    llm = FakeLLM(raise_exc=RetrievalError("backend down"))
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-strategy-fail"),
    )
    service = NarrateService(strategy=strategy, enabled=True)

    result = await service.narrate_chunk(_TABLE_MARKDOWN, "TABLE")

    assert result.narrated is False
    assert result.text_for_embedding == _TABLE_MARKDOWN
    assert result.raw_chunk == _TABLE_MARKDOWN
    assert result.narrated_text == _TABLE_MARKDOWN


@pytest.mark.asyncio
async def test_narrate_service_treats_unchanged_strategy_output_as_no_op() -> None:
    """When LLM echoes back the raw content unchanged, narrated=False so the
    metadata layer knows nothing was enriched."""
    llm = FakeLLM(reply=_TABLE_MARKDOWN)
    strategy = LLMNarrateGenerator(
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-echo"),
    )
    service = NarrateService(strategy=strategy, enabled=True)

    result = await service.narrate_chunk(_TABLE_MARKDOWN, "TABLE")

    assert result.narrated is False
    assert result.text_for_embedding == _TABLE_MARKDOWN


def test_narrate_service_to_metadata_serialises_dual_content() -> None:
    """to_metadata exposes raw_chunk + narrated_text + block_type for persistence."""
    result = NarrateResult(
        text_for_embedding="natural-language summary",
        raw_chunk=_TABLE_MARKDOWN,
        narrated_text="natural-language summary",
        block_type="TABLE",
        narrated=True,
    )

    meta = NarrateService.to_metadata(result)

    assert meta[NARRATE_METADATA_KEY_RAW_CHUNK] == _TABLE_MARKDOWN
    assert meta[NARRATE_METADATA_KEY_NARRATED_TEXT] == "natural-language summary"
    assert meta[NARRATE_METADATA_KEY_BLOCK_TYPE] == "TABLE"
    # The metadata dict carries exactly the three documented keys — no
    # accidental leakage of internal fields (text_for_embedding /
    # narrated bool) into chunk metadata.
    assert set(meta.keys()) == {
        NARRATE_METADATA_KEY_RAW_CHUNK,
        NARRATE_METADATA_KEY_NARRATED_TEXT,
        NARRATE_METADATA_KEY_BLOCK_TYPE,
    }


def test_build_narrate_null_returns_null_strategy() -> None:
    strategy = build_narrate("null")

    assert isinstance(strategy, NullNarrateGenerator)
    assert "null" in list_providers()


def test_build_narrate_llm_returns_llm_strategy() -> None:
    llm = FakeLLM()
    strategy = build_narrate(
        "llm",
        llm=llm,
        spec=_make_spec(),
        record_tenant_id=TenantId(uuid4()),
        trace_id=TraceId("trace-build"),
    )

    assert isinstance(strategy, LLMNarrateGenerator)
    assert "llm" in list_providers()


def test_build_narrate_unknown_provider_raises_value_error() -> None:
    with pytest.raises(ValueError, match="unknown narrate provider"):
        build_narrate("does-not-exist")


def test_build_narrate_provider_key_case_insensitive_and_whitespace_tolerant() -> None:
    """Operator might type ``Null`` or ``  llm  `` in admin UI — registry
    normalises before lookup so config typos don't 500 the ingest worker."""
    assert isinstance(build_narrate("  Null  "), NullNarrateGenerator)
    assert isinstance(build_narrate("NULL"), NullNarrateGenerator)


def test_narrate_constants_match_safety_caps_and_defaults() -> None:
    """Pin the constant values that the rest of the platform relies on."""
    # AdapChunk Tầng 6 — Narrate-then-Embed default OFF (2026-06-17 Jina
    # migration): narrate is per-table-block nano (the spreadsheet ingest storm).
    # The deterministic csv_chunker key:value rendering + Jina late_chunking cover
    # table retrievability with 0 LLM. Opt IN via system_config.
    assert DEFAULT_NARRATE_THEN_EMBED_ENABLED is False
    assert DEFAULT_NARRATE_PROVIDER == "llm"
    # Catalog locked to gpt-4.1 (alembic 0216); narration default = gpt-4.1-mini.
    assert DEFAULT_NARRATE_MODEL == "gpt-4.1-mini"
    # Token cap safety ceiling — uncapped output blows latency + cost.
    assert 50 <= DEFAULT_NARRATE_MAX_TOKENS <= 300
    # Low-but-not-zero temperature: stays grounded without verbatim echo.
    assert 0.0 < DEFAULT_NARRATE_TEMPERATURE < 0.5
    # Only non-prose blocks are eligible — prose embeds fine raw.
    assert set(NARRATE_BLOCK_TYPES_DEFAULT) == {"TABLE", "FORMULA", "IMAGE"}
    # Anthropic Batch API per-batch cap.
    assert DEFAULT_NARRATE_BATCH_SIZE == 100
    # Batch discount factor must be a proper discount (0 < f <= 1).
    assert 0.0 < DEFAULT_NARRATE_BATCH_DISCOUNT_FACTOR <= 1.0
    # Default OFF for paid Batch API path.
    assert DEFAULT_NARRATE_BATCH_USE is False


@pytest.mark.asyncio
async def test_null_batch_client_honours_size_cap() -> None:
    """Null batch client rejects oversized submissions early."""
    client = NullAnthropicHaikuBatchClient(batch_size=DEFAULT_NARRATE_BATCH_SIZE)

    assert client.get_provider_name() == "null"
    assert client.batch_size == DEFAULT_NARRATE_BATCH_SIZE

    items = [
        NarrateBatchItem(custom_id=f"c{i}", block_type="TABLE", content="x")
        for i in range(DEFAULT_NARRATE_BATCH_SIZE)
    ]
    batch_id = await client.submit(items)
    assert batch_id.startswith("null:")

    status = await client.poll(batch_id)
    assert status.ended is True
    assert status.processing_status == "ended"
    assert status.succeeded_count == 0
    assert status.errored_count == 0

    fetched = [r async for r in client.fetch_results(batch_id)]
    assert fetched == []

    too_many = items + [
        NarrateBatchItem(custom_id="overflow", block_type="TABLE", content="x"),
    ]
    with pytest.raises(ValueError, match="batch exceeds size cap"):
        await client.submit(too_many)


def test_null_batch_client_rejects_invalid_batch_size() -> None:
    with pytest.raises(ValueError, match="batch_size must be positive"):
        NullAnthropicHaikuBatchClient(batch_size=0)
    with pytest.raises(ValueError, match="batch_size must be positive"):
        NullAnthropicHaikuBatchClient(batch_size=-5)


def test_estimate_batch_cost_applies_discount_when_enabled() -> None:
    """Cost estimator multiplies gross by discount factor when Batch is on."""
    # Synthetic prices: $1/M input, $2/M output. 1M input tokens + 1M output
    # tokens = $1 + $2 = $3 gross. With 50% discount → $1.50.
    gross = estimate_batch_cost_usd(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        input_price_per_million_usd=1.0,
        output_price_per_million_usd=2.0,
        use_batch_discount=False,
    )
    assert gross == pytest.approx(3.0)

    discounted = estimate_batch_cost_usd(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        input_price_per_million_usd=1.0,
        output_price_per_million_usd=2.0,
        use_batch_discount=True,
        discount_factor=0.5,
    )
    assert discounted == pytest.approx(1.5)


def test_estimate_batch_cost_rejects_negative_inputs() -> None:
    with pytest.raises(ValueError, match="token counts"):
        estimate_batch_cost_usd(
            input_tokens=-1,
            output_tokens=10,
            input_price_per_million_usd=1.0,
            output_price_per_million_usd=2.0,
        )
    with pytest.raises(ValueError, match="prices"):
        estimate_batch_cost_usd(
            input_tokens=10,
            output_tokens=10,
            input_price_per_million_usd=-1.0,
            output_price_per_million_usd=2.0,
        )
    with pytest.raises(ValueError, match="discount_factor"):
        estimate_batch_cost_usd(
            input_tokens=10,
            output_tokens=10,
            input_price_per_million_usd=1.0,
            output_price_per_million_usd=2.0,
            discount_factor=0.0,
        )
    with pytest.raises(ValueError, match="discount_factor"):
        estimate_batch_cost_usd(
            input_tokens=10,
            output_tokens=10,
            input_price_per_million_usd=1.0,
            output_price_per_million_usd=2.0,
            discount_factor=1.5,
        )
