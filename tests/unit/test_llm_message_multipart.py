"""LLMMessage multipart (vision) content — Phase 1 of the multimodal track.

Asserts the enabling contract: LLMMessage.content accepts BOTH a plain str (every
existing caller — backward-compat) AND an OpenAI-style content-part list (an image),
and that the router's message-build pattern forwards either shape to LiteLLM verbatim.

The build pattern under test is the exact one in
``dynamic_litellm_router.complete_runtime`` (``{"role": m.role, "content": m.content}``,
router lines 1023 + 1151) — LiteLLM accepts both str and list[dict] content natively.
"""
from __future__ import annotations

from ragbot.application.ports.llm_port import LLMMessage


def _build_litellm_messages(messages: list[LLMMessage]) -> list[dict]:
    # Mirror of the router's forward pattern (dynamic_litellm_router.py:1023/1151).
    return [{"role": m.role, "content": m.content} for m in messages]


def test_str_content_unchanged_backward_compat() -> None:
    msg = LLMMessage(role="user", content="giá lốp 205/55R16?")
    built = _build_litellm_messages([msg])
    assert built == [{"role": "user", "content": "giá lốp 205/55R16?"}]
    # content stays a plain str — no wrapping, no regression for text turns.
    assert isinstance(built[0]["content"], str)


def test_vision_multipart_content_round_trips() -> None:
    parts = [
        {"type": "text", "text": "Mô tả bảng giá trong ảnh."},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAAA"},
        },
    ]
    msg = LLMMessage(role="user", content=parts)
    built = _build_litellm_messages([msg])
    content = built[0]["content"]
    # The OpenAI vision shape is preserved verbatim for LiteLLM to forward.
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "Mô tả bảng giá trong ảnh."}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_mixed_batch_text_then_vision() -> None:
    msgs = [
        LLMMessage(role="system", content="Bạn là trợ lý đọc ảnh."),
        LLMMessage(
            role="user",
            content=[{"type": "image_url", "image_url": {"url": "data:image/png;base64,Zg=="}}],
        ),
    ]
    built = _build_litellm_messages(msgs)
    assert isinstance(built[0]["content"], str)  # system turn stays text
    assert isinstance(built[1]["content"], list)  # user turn carries the image
