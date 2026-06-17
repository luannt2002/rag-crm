"""P33 / C.5 — DynamicLiteLLMRouter increment_tokens wiring.

Verifies that:
* every successful ``complete_runtime`` call routes the prompt /
  completion counts into the injected ``TenantTokenMeter``;
* the streaming variant emits a SINGLE meter call after the iterator
  drains (not per-chunk);
* missing tenant context is tolerated silently — system / background
  jobs that lack the contextvar must not raise.

Meter receives ``record_tenant_id`` UUID resolved from
``tenant_id_ctx`` (UUID string contextvar).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

from ragbot.config.logging import tenant_id_ctx
from ragbot.infrastructure.llm.dynamic_litellm_router import (
    DynamicLiteLLMRouter,
)
from tests.conftest import TEST_TENANT_UUID


class _RecordingMeter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def increment_tokens(
        self,
        record_tenant_id: UUID,
        *,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> dict[str, int]:
        self.calls.append(
            {
                "record_tenant_id": record_tenant_id,
                "prompt": int(prompt_tokens),
                "completion": int(completion_tokens),
            },
        )
        return {"prompt": prompt_tokens, "completion": completion_tokens, "total": 0}

    async def get_monthly_usage(
        self, _tid: Any,
    ) -> dict[str, int]:  # pragma: no cover
        return {"prompt": 0, "completion": 0, "total": 0}


def _stub_cfg() -> Any:
    return SimpleNamespace(
        litellm_name="openai/gpt-4o-mini",
        provider=SimpleNamespace(
            code="openai", api_key="sk-x", base_url=None,  # noqa: S106
            timeout_ms=30_000, max_concurrent=4,
        ),
        params=SimpleNamespace(temperature=0.0, max_tokens=128),
        pricing=SimpleNamespace(
            input_per_1k_usd=Decimal("0.001"),
            cached_input_per_1k_usd=None,
            output_per_1k_usd=Decimal("0.002"),
        ),
    )


@pytest.mark.asyncio
async def test_complete_runtime_increments_meter_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meter = _RecordingMeter()
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=meter,
    )

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi"),
                finish_reason="stop",
            ),
        ],
        usage={"prompt_tokens": 11, "completion_tokens": 7},
    )

    async def _fake_acompletion(**_kw: Any) -> Any:
        return fake_resp

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )

    tenant_id_ctx.set(str(TEST_TENANT_UUID))
    try:
        await router.complete_runtime(_stub_cfg(), [{"role": "user", "content": "x"}])
    finally:
        tenant_id_ctx.set("UNSET")

    assert meter.calls == [
        {"record_tenant_id": TEST_TENANT_UUID, "prompt": 11, "completion": 7},
    ]


@pytest.mark.asyncio
async def test_complete_runtime_without_tenant_does_not_emit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing tenant context → meter NOT called (no crash, no spurious row)."""
    meter = _RecordingMeter()
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=meter,
    )

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi"),
                finish_reason="stop",
            ),
        ],
        usage={"prompt_tokens": 5, "completion_tokens": 5},
    )

    async def _fake_acompletion(**_kw: Any) -> Any:
        return fake_resp

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )

    tenant_id_ctx.set("UNSET")
    await router.complete_runtime(_stub_cfg(), [{"role": "user", "content": "x"}])
    assert meter.calls == []


@pytest.mark.asyncio
async def test_complete_runtime_no_meter_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``token_meter=None`` keeps router functional (legacy callers)."""
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=None,
    )

    fake_resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="hi"),
                finish_reason="stop",
            ),
        ],
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )

    async def _fake_acompletion(**_kw: Any) -> Any:
        return fake_resp

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )
    tenant_id_ctx.set(str(TEST_TENANT_UUID))
    try:
        out = await router.complete_runtime(
            _stub_cfg(), [{"role": "user", "content": "x"}],
        )
    finally:
        tenant_id_ctx.set("UNSET")
    assert out["prompt_tokens"] == 1


@pytest.mark.asyncio
async def test_streaming_emits_single_meter_call_after_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Streaming generator collects usage from final chunk → ONE meter call."""
    meter = _RecordingMeter()
    router = DynamicLiteLLMRouter(
        ai_config_repo=SimpleNamespace(),
        redis_client=None,
        token_meter=meter,
    )

    chunks = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="he"))],
            usage=None,
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="llo"))],
            usage=None,
        ),
        # Final chunk — provider emits cumulative usage.
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=""))],
            usage={"prompt_tokens": 9, "completion_tokens": 3},
        ),
    ]

    async def _astream(**_kw: Any) -> AsyncIterator[Any]:
        for c in chunks:
            yield c

    async def _fake_acompletion(**kw: Any) -> Any:
        # acompletion(stream=True) returns an async generator
        return _astream(**kw)

    monkeypatch.setattr(
        "ragbot.infrastructure.llm.dynamic_litellm_router.litellm.acompletion",
        _fake_acompletion,
    )

    tenant_id_ctx.set(str(TEST_TENANT_UUID))
    try:
        out: list[str] = []
        async for tok in router.complete_runtime_stream(
            _stub_cfg(), [{"role": "user", "content": "x"}],
        ):
            out.append(tok)
    finally:
        tenant_id_ctx.set("UNSET")

    assert "".join(out) == "hello"
    # SINGLE call — not per chunk
    assert meter.calls == [
        {"record_tenant_id": TEST_TENANT_UUID, "prompt": 9, "completion": 3},
    ]
