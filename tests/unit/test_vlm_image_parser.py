"""VlmImageParser — multimodal Phase 2 adapter.

Behavioural tests with a mock LLM (no network): the parser builds an OpenAI vision
message from image bytes, returns the model's caption as chunk content, fails loud on a
non-vision spec, and claims only image formats.
"""
from __future__ import annotations

import uuid

import pytest

from ragbot.application.dto.ai_specs import LLMSpec
from ragbot.application.ports.llm_port import LLMResponse
from ragbot.infrastructure.parser.vlm_image_parser import VlmImageParser

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32  # valid PNG magic + filler


def _spec(*, vision: bool) -> LLMSpec:
    return LLMSpec(
        binding_id=uuid.uuid4(),
        model_name="gpt-4.1-mini",
        provider="openai",
        supports_vision=vision,
    )


class _MockLLM:
    def __init__(self) -> None:
        self.captured: list = []

    async def complete(self, messages, **kwargs):  # noqa: ANN001
        self.captured = messages
        return LLMResponse(
            content="Bảng giá: Lốp A 1.000.000đ, Lốp B 2.000.000đ",
            model="gpt-4.1-mini", provider="openai",
            tokens_in=10, tokens_out=20, cost_usd=0.0, latency_ms=5,
        )

    async def health_check(self) -> bool:
        return True


def _parser(llm, *, vision: bool = True) -> VlmImageParser:
    return VlmImageParser(
        llm=llm, spec=_spec(vision=vision),
        record_tenant_id=uuid.uuid4(), trace_id="trace-1",
    )


def test_supports_image_formats_only() -> None:
    p = _parser(_MockLLM())
    assert p.supports("image/png", "png") is True
    assert p.supports("image/jpeg", "jpg") is True
    assert p.supports("", "webp") is True
    assert p.supports("application/pdf", "pdf") is False
    assert p.supports("text/plain", "txt") is False


@pytest.mark.asyncio
async def test_parse_builds_vision_message_and_returns_caption() -> None:
    llm = _MockLLM()
    p = _parser(llm)
    out = await p.parse(_PNG, file_name="price.png")
    # caption returned as the single chunk's content
    assert len(out) == 1
    assert "1.000.000" in out[0]["content"]
    assert out[0]["metadata"]["parser"] == "vlm_image"
    assert out[0]["metadata"]["source_mime"] == "image/png"
    # the message sent to the model is an OpenAI vision multipart
    content = llm.captured[0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_empty_bytes_returns_no_chunk() -> None:
    out = await _parser(_MockLLM()).parse(b"", file_name="x.png")
    assert out == []


def test_non_vision_spec_fails_loud() -> None:
    with pytest.raises(ValueError, match="vision-capable"):
        _parser(_MockLLM(), vision=False)


def test_provider_name() -> None:
    assert _parser(_MockLLM()).get_provider_name() == "vlm_image"
