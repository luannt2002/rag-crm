"""Token + cost instrumentation across sync, stream, and structured paths.

Regression cover for the audit finding where 99.6 % of
``model_invocations`` rows had ``prompt_tokens=0, completion_tokens=0,
cost_usd=0.0`` — the streaming branch hardcoded zeros and the structured
output helper discarded ``response.usage`` before the caller could log it.

Each test injects a stub LiteLLM module so we can verify exactly what the
router / helper extracts and forwards downstream — no network calls.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from ragbot.application.services.structured_output_helper import (
    call_with_schema,
)
from ragbot.config.logging import tenant_id_int_ctx
from ragbot.infrastructure.llm.dynamic_litellm_router import (
    DynamicLiteLLMRouter,
    compute_cost_usd,
    extract_usage_from_response,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _stub_cfg() -> Any:
    """Minimal cfg shape consumed by router + structured helper."""
    return SimpleNamespace(
        litellm_name="openai/gpt-4o-mini",
        provider=SimpleNamespace(
            code="openai",
            api_key="sk-test",  # noqa: S106
            base_url=None,
            timeout_ms=30_000,
            max_concurrent=4,
        ),
        params=SimpleNamespace(temperature=0.0, max_tokens=128),
        pricing=SimpleNamespace(
            input_per_1k_usd=Decimal("0.001"),
            cached_input_per_1k_usd=None,
            output_per_1k_usd=Decimal("0.002"),
        ),
    )


# ---------------------------------------------------------------------------
# 1. Sync path extracts usage from response (regression — was already wired
#    but the audit did not include a unit test that asserts on the dict
#    payload returned to the caller).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sync_path_extracts_usage_from_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=None,
    )
    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi there"),
                finish_reason="stop",
            ),
        ],
        usage={"prompt_tokens": 17, "completion_tokens": 9},
    )

    async def _fake_acompletion(**_kw: Any) -> Any:
        return fake_resp

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )

    out = await router.complete_runtime(
        _stub_cfg(), [{"role": "user", "content": "x"}],
    )
    assert out["prompt_tokens"] == 17
    assert out["completion_tokens"] == 9
    # 17/1000 * 0.001 + 9/1000 * 0.002 = 0.000017 + 0.000018 = 0.000035
    assert out["cost_usd"] == pytest.approx(0.000035, rel=1e-6)
    assert out["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# 2. Stream path captures last chunk usage and forwards via usage_sink
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stream_path_captures_last_chunk_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Final chunk usage is captured + sink invoked with computed cost."""
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=None,
    )

    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="he"),
                finish_reason=None,
            )],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="llo"),
                finish_reason=None,
            )],
            usage=None,
        ),
        # Final chunk — provider emits cumulative usage on stop.
        SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=""),
                finish_reason="stop",
            )],
            usage={"prompt_tokens": 21, "completion_tokens": 13},
        ),
    ]

    async def _astream(**_kw: Any) -> AsyncIterator[Any]:
        for c in chunks:
            yield c

    async def _fake_acompletion(**kw: Any) -> Any:
        # OpenAI stream path now sets stream_options={"include_usage": True}
        # — verify we propagate the flag.
        assert kw.get("stream") is True
        assert kw.get("stream_options") == {"include_usage": True}
        return _astream(**kw)

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )

    captured: dict[str, Any] = {}

    def _sink(p: int, c: int, cached: int, cost: float, fr: str | None) -> None:
        captured["prompt"] = p
        captured["completion"] = c
        captured["cached"] = cached
        captured["cost"] = cost
        captured["finish_reason"] = fr

    out: list[str] = []
    async for tok in router.complete_runtime_stream(
        _stub_cfg(),
        [{"role": "user", "content": "x"}],
        usage_sink=_sink,
    ):
        out.append(tok)

    assert "".join(out) == "hello"
    assert captured["prompt"] == 21
    assert captured["completion"] == 13
    # 21/1000 * 0.001 + 13/1000 * 0.002 = 0.000021 + 0.000026 = 0.000047
    assert captured["cost"] == pytest.approx(0.000047, rel=1e-6)
    assert captured["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# 3. Cost calc reads from the resolved Pricing row (no inlined numbers)
# ---------------------------------------------------------------------------
def test_cost_calculated_from_ai_models_pricing() -> None:
    """compute_cost_usd uses pricing fields verbatim — Decimal precision."""
    pricing = SimpleNamespace(
        input_per_1k_usd=Decimal("0.0004"),  # gpt-4.1-mini input
        output_per_1k_usd=Decimal("0.0016"),  # gpt-4.1-mini output
        cached_input_per_1k_usd=Decimal("0.0001"),  # 75% off cached
    )
    # 1000 prompt (300 cached) + 500 completion
    cost = compute_cost_usd(pricing, prompt_tokens=1000, completion_tokens=500, cached_tokens=300)
    # Non-cached input: 700/1000 * 0.0004 = 0.00028
    # Cached input:    300/1000 * 0.0001 = 0.00003
    # Output:          500/1000 * 0.0016 = 0.00080
    # Total: 0.00111
    assert cost == Decimal("0.00111")


def test_cost_calc_falls_back_when_cached_price_missing() -> None:
    """Without ``cached_input_per_1k_usd`` we use 50% of input rate."""
    pricing = SimpleNamespace(
        input_per_1k_usd=Decimal("0.001"),
        output_per_1k_usd=Decimal("0.002"),
        cached_input_per_1k_usd=None,
    )
    cost = compute_cost_usd(pricing, prompt_tokens=1000, completion_tokens=0, cached_tokens=200)
    # Non-cached: 800/1000 * 0.001 = 0.0008
    # Cached:     200/1000 * 0.0005 = 0.0001
    # Output:     0
    assert cost == Decimal("0.0009")


# ---------------------------------------------------------------------------
# 4. Structured output helper invokes usage_sink so caller can record the
#    real prompt / completion counts in invocation_logger
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_structured_output_emits_usage_to_sink() -> None:
    from pydantic import BaseModel

    class _Schema(BaseModel):
        action: str

    class _StubLitellm:
        async def acompletion(self, **_kw: Any) -> Any:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"action": "keep"}'),
                        finish_reason="stop",
                    ),
                ],
                usage={"prompt_tokens": 33, "completion_tokens": 5},
            )

    captured: dict[str, Any] = {}

    def _sink(p: int, c: int, cached: int, text: str, fr: str | None) -> None:
        captured["prompt"] = p
        captured["completion"] = c
        captured["cached"] = cached
        captured["text"] = text
        captured["finish_reason"] = fr

    parsed = await call_with_schema(
        litellm_module=_StubLitellm(),
        litellm_name="openai/gpt-4o-mini",
        provider_code="openai",
        messages=[{"role": "user", "content": "judge"}],
        schema=_Schema,
        usage_sink=_sink,
    )

    assert parsed is not None
    assert parsed.action == "keep"
    assert captured["prompt"] == 33
    assert captured["completion"] == 5
    assert captured["finish_reason"] == "stop"
    assert "keep" in captured["text"]


# ---------------------------------------------------------------------------
# 5. extract_usage_from_response is robust to dict vs pydantic-style usage
# ---------------------------------------------------------------------------
def test_extract_usage_handles_dict_and_object_shapes() -> None:
    # Dict shape (LiteLLM Anthropic path)
    resp1 = SimpleNamespace(usage={"prompt_tokens": 7, "completion_tokens": 3})
    assert extract_usage_from_response(resp1) == (7, 3, 0)
    # Pydantic-style with cached tokens nested
    resp2 = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
        ),
    )
    assert extract_usage_from_response(resp2) == (100, 20, 40)
    # Missing usage entirely (some failure modes) → zeros, no crash
    resp3 = SimpleNamespace()
    assert extract_usage_from_response(resp3) == (0, 0, 0)
    # ``None`` field values must coerce to zero, not raise
    resp4 = SimpleNamespace(usage={"prompt_tokens": None, "completion_tokens": None})
    assert extract_usage_from_response(resp4) == (0, 0, 0)


# ---------------------------------------------------------------------------
# 6. DB write path — ``InvocationLogger`` persists token counts on
#    ``ctx.record(...)``. We verify against an in-memory SQLite session so
#    the assertion runs without the integration test stack.
# ---------------------------------------------------------------------------
class _RecordingSession:
    """Tiny stand-in for ``AsyncSession`` that captures executed statements.

    ``InvocationLogger`` only needs ``execute`` + ``commit`` + the async
    context manager protocol, so we don't pull in sqlite.
    """

    def __init__(self, log: list[Any]) -> None:
        self._log = log

    async def __aenter__(self) -> "_RecordingSession":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None

    async def execute(self, stmt: Any) -> Any:
        self._log.append(stmt)
        return SimpleNamespace(rowcount=1)

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_db_write_includes_token_counts() -> None:
    """End-to-end: ctx.record(...) flows real numbers into the UPDATE values."""
    from ragbot.infrastructure.observability.invocation_logger import (
        InvocationLogger,
    )

    statements: list[Any] = []

    def _session_factory() -> _RecordingSession:
        return _RecordingSession(statements)

    inv_logger = InvocationLogger(_session_factory)  # type: ignore[arg-type]

    async with inv_logger.invoke_model(
        message_id=42,
        record_tenant_id=None,
        record_request_id=None,
        purpose="generation",
        provider="openai",
        model_id="openai/gpt-4o-mini",
        user_prompt="hello",
    ) as ctx:
        ctx.record(
            response="world",
            prompt_tokens=125,
            completion_tokens=37,
            cost_usd=0.000124,
            finish_reason="stop",
        )

    # Bug 1 P0 fix: single-session UPSERT replaced INSERT(running) +
    # UPDATE(final). Now exactly one statement carries final values.
    assert len(statements) == 1
    upsert_stmt = statements[-1]
    bound = upsert_stmt.compile().params  # type: ignore[attr-defined]
    assert bound["prompt_tokens"] == 125
    assert bound["completion_tokens"] == 37
    assert float(bound["cost_usd"]) == pytest.approx(0.000124, rel=1e-6)
    assert bound["status"] == "success"
    assert bound["finish_reason"] == "stop"


# ---------------------------------------------------------------------------
# 7. Streaming sink supports async callbacks too (caller may want to
#    persist usage to Redis / DB inside the sink)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stream_usage_sink_accepts_async_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=None,
    )

    async def _astream(**_kw: Any) -> AsyncIterator[Any]:
        yield SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content="ok"),
                finish_reason=None,
            )],
            usage=None,
        )
        yield SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=""),
                finish_reason="stop",
            )],
            usage={"prompt_tokens": 4, "completion_tokens": 1},
        )

    async def _fake_acompletion(**kw: Any) -> Any:
        return _astream(**kw)

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )

    awaited: dict[str, Any] = {}

    async def _async_sink(p: int, c: int, cached: int, cost: float, fr: str | None) -> None:
        awaited["prompt"] = p
        awaited["cost"] = cost

    tenant_id_int_ctx.set(None)
    out: list[str] = []
    async for tok in router.complete_runtime_stream(
        _stub_cfg(),
        [{"role": "user", "content": "x"}],
        usage_sink=_async_sink,
    ):
        out.append(tok)

    assert "".join(out) == "ok"
    assert awaited["prompt"] == 4
    # Async sink ran to completion — cost computed via Pricing
    assert awaited["cost"] == pytest.approx(4 / 1000 * 0.001 + 1 / 1000 * 0.002, rel=1e-6)
