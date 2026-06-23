"""Ledger emit-coverage gate for the LLM router (COST-LOG/CRM expert fixes).

Locks the three previously-divergent / missing emit sites in
``DynamicLiteLLMRouter`` onto the shared ``_emit_llm_ledger`` helper:

  * G-1 — the streaming answer path (``complete_runtime_stream``) MUST emit
    exactly one ``action='llm'`` row after the stream drains, with non-zero
    duration_ms + the call's ``purpose`` set + ``status='success'``.
  * G-4 — every LLM emit carries ``purpose`` and a real wall-clock
    ``started_at``/``finished_at``/``duration_ms`` (not started==finished).
  * G-6 — every emitted row snapshots ``request_id`` from ``request_id_ctx``.

These are behavioural assertions on the emitted ``TokenLedgerEntry`` captured
through a spy ledger injected at construction — no live DB / network.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from ragbot.application.ports.token_ledger_port import TokenLedgerEntry
from ragbot.config.logging import request_id_ctx
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter


class _SpyLedger:
    """Captures every emitted entry for assertions."""

    def __init__(self) -> None:
        self.entries: list[TokenLedgerEntry] = []

    def emit(self, entry: TokenLedgerEntry) -> None:
        self.entries.append(entry)


def _make_router(ledger: _SpyLedger) -> DynamicLiteLLMRouter:
    return DynamicLiteLLMRouter(ai_config_repo=AsyncMock(), ledger=ledger)


def _make_cfg(provider_code: str = "openai", litellm_name: str = "openai/gpt-4.1-mini"):
    return SimpleNamespace(
        litellm_name=litellm_name,
        params=SimpleNamespace(temperature=0.0, max_tokens=128),
        provider=SimpleNamespace(
            code=provider_code,
            api_key="sk-test",
            base_url=None,
            timeout_ms=30000,
            max_concurrent=4,
        ),
        pricing=SimpleNamespace(
            input_per_1k_usd=Decimal("0.0004"),
            output_per_1k_usd=Decimal("0.0016"),
            cached_input_per_1k_usd=Decimal("0.0001"),
        ),
    )


def _delta_chunk(text: str | None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text), finish_reason=None)],
        usage=None,
    )


def _final_usage_chunk(prompt: int, completion: int, cached: int = 0):
    """A terminal chunk that carries cumulative usage + finish_reason (OpenAI shape)."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(delta=SimpleNamespace(content=None), finish_reason="stop"),
        ],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            # extract_usage_from_response reads cached via prompt_tokens_details
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


@pytest.mark.asyncio
async def test_stream_emits_exactly_one_llm_ledger_row(monkeypatch):
    """G-1: a streamed completion inserts exactly 1 action='llm' row."""
    ledger = _SpyLedger()
    router = _make_router(ledger)
    cfg = _make_cfg()
    token = request_id_ctx.set(str(uuid.uuid4()))
    try:
        stream = _FakeStream([
            _delta_chunk("Hel"),
            _delta_chunk("lo"),
            _final_usage_chunk(prompt=120, completion=8),
        ])
        with patch("litellm.acompletion", AsyncMock(return_value=stream)):
            out = [
                t
                async for t in router.complete_runtime_stream(
                    cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
                )
            ]
        assert "".join(out) == "Hello"
        llm_rows = [e for e in ledger.entries if e.action == "llm"]
        assert len(llm_rows) == 1
    finally:
        request_id_ctx.reset(token)


@pytest.mark.asyncio
async def test_stream_ledger_row_has_purpose_duration_status_and_request_id():
    """G-1 + G-4 + G-6: the streamed row carries the full schema."""
    ledger = _SpyLedger()
    router = _make_router(ledger)
    cfg = _make_cfg()
    rid = str(uuid.uuid4())
    token = request_id_ctx.set(rid)
    try:
        stream = _FakeStream([
            _delta_chunk("x"),
            _final_usage_chunk(prompt=200, completion=40),
        ])
        with patch("litellm.acompletion", AsyncMock(return_value=stream)):
            async for _ in router.complete_runtime_stream(
                cfg, [{"role": "user", "content": "Hi"}], purpose="generation",
            ):
                pass
    finally:
        request_id_ctx.reset(token)

    row = next(e for e in ledger.entries if e.action == "llm")
    assert row.purpose == "generation"
    assert row.status == "success"
    assert row.input_tokens == 200
    assert row.output_tokens == 40
    assert row.total_tokens == 240
    # G-4: real wall-clock window — started strictly before finished.
    assert row.started_at is not None and row.finished_at is not None
    assert row.started_at < row.finished_at
    assert row.duration_ms is not None and row.duration_ms >= 0
    # G-6: request id snapshot from the contextvar.
    assert str(row.request_id) == rid
    # cost snapshot present (unit prices from cfg.pricing).
    assert row.cost_usd is not None and row.cost_usd > 0


@pytest.mark.asyncio
async def test_non_stream_runtime_emit_carries_purpose_and_status_success():
    """G-4: the non-streaming runtime emit now sets purpose + status='success'."""
    ledger = _SpyLedger()
    router = _make_router(ledger)
    cfg = _make_cfg()

    resp = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="answer"),
                finish_reason="stop",
            ),
        ],
        usage=SimpleNamespace(
            prompt_tokens=50,
            completion_tokens=10,
            prompt_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )
    with patch("litellm.acompletion", AsyncMock(return_value=resp)):
        await router._complete_runtime_one(
            cfg, [{"role": "user", "content": "Hi"}], purpose="grounding",
        )

    row = next(e for e in ledger.entries if e.action == "llm")
    assert row.purpose == "grounding"
    assert row.status == "success"
