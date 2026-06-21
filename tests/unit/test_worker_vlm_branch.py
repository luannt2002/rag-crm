"""Worker VLM image-MIME branch (multimodal Phase 2 A1) — gate logic.

Tests `_try_build_vlm_image_parser`: it returns a VlmImageParser only when the upload
is an image AND vlm_provider is enabled AND a vision model resolves; otherwise None
(legacy OCR fallback), never crashing the ingest.
"""
from __future__ import annotations

import uuid

import pytest

import ragbot.interfaces.workers.document_worker as dw
from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.infrastructure.parser.vlm_image_parser import VlmImageParser


def _spec(*, vision: bool) -> LLMSpec:
    return LLMSpec(
        binding_id=uuid.uuid4(), model_name="gpt-4.1-mini",
        provider="openai", supports_vision=vision,
    )


class _FakeCfg:
    def __init__(self, provider: str) -> None:
        self._p = provider

    async def get(self, key: str, default=None):  # noqa: ANN001
        return self._p if key == "vlm_provider" else default


class _FakeContainer:
    def __init__(self, *, provider: str, vision: bool) -> None:
        self._spec = _spec(vision=vision)
        self._provider = provider

    def session_factory(self):
        return object()

    def redis_client(self):
        return object()

    def llm(self):
        return object()

    def model_resolver(self):
        spec = self._spec
        class _R:
            async def resolve_llm(self, *a, **k):  # noqa: ANN001, ANN002, ANN003
                return spec
        return _R()


def _patch_cfg(monkeypatch, provider: str) -> None:
    monkeypatch.setattr(dw, "SystemConfigService", lambda **kw: _FakeCfg(provider))


@pytest.mark.asyncio
async def test_non_image_mime_returns_none(monkeypatch) -> None:  # noqa: ANN001
    _patch_cfg(monkeypatch, "vlm_image")
    out = await dw._try_build_vlm_image_parser(
        _FakeContainer(provider="vlm_image", vision=True),
        bot_id=uuid.uuid4(), tenant_id=uuid.uuid4(), trace_id="t", mime_type="application/pdf",
    )
    assert out is None


@pytest.mark.asyncio
async def test_vlm_provider_off_returns_none(monkeypatch) -> None:  # noqa: ANN001
    _patch_cfg(monkeypatch, "null")
    out = await dw._try_build_vlm_image_parser(
        _FakeContainer(provider="null", vision=True),
        bot_id=uuid.uuid4(), tenant_id=uuid.uuid4(), trace_id="t", mime_type="image/png",
    )
    assert out is None


@pytest.mark.asyncio
async def test_image_with_vision_model_builds_parser(monkeypatch) -> None:  # noqa: ANN001
    _patch_cfg(monkeypatch, "vlm_image")
    out = await dw._try_build_vlm_image_parser(
        _FakeContainer(provider="vlm_image", vision=True),
        bot_id=uuid.uuid4(), tenant_id=uuid.uuid4(), trace_id="t", mime_type="image/png",
    )
    assert isinstance(out, VlmImageParser)
    assert out.get_provider_name() == "vlm_image"


@pytest.mark.asyncio
async def test_non_vision_model_degrades_to_none(monkeypatch) -> None:  # noqa: ANN001
    # vlm enabled but resolved model lacks vision → graceful OCR fallback, no crash.
    _patch_cfg(monkeypatch, "vlm_image")
    out = await dw._try_build_vlm_image_parser(
        _FakeContainer(provider="vlm_image", vision=False),
        bot_id=uuid.uuid4(), tenant_id=uuid.uuid4(), trace_id="t", mime_type="image/jpeg",
    )
    assert out is None
