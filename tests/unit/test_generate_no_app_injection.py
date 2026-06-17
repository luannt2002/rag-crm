"""Regression: `generate` node MUST NOT inject application-side text into LLM messages.

CLAUDE.md "Application MINDSET" rule (User explicit 2026-04-29 night):

    Application KHÔNG inject text vào LLM prompt:
        - KHÔNG prepend platform_rule_docs_only
        - KHÔNG prepend docs_only_strict_rule
        - KHÔNG inject "context tag" instructions
        - KHÔNG inject citation format hint
        - Bot owner's `system_prompt` là SINGLE source of truth.

The `generate` node at `src/ragbot/orchestration/query_graph.py:2455-2533` is the
single point where the final prompt is composed before the LLM call. It builds:

    messages = [
        {"role": "system",    "content": bot_system_prompt},      # exactly bot_system_prompt
        *history_messages,                                          # passed through
        {"role": "user",      "content":
            "<documents>\n{ctx}\n</documents>\n\n<question>{q}</question>"},
    ]

This file invokes the closure with controlled inputs and captures the
`messages` list received by the mock LLM. Any future refactor that re-introduces
hardcoded "rule:" / "always cite:" / "do not hallucinate:" / docs-only template
text will fail here.

Approach B: extract the closure from the compiled LangGraph
(`compiled.nodes['generate'].bound.afunc`) and call it directly with a state
dict + minimal mocks (capturing LLM messages via `AsyncMock.call_args`).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


# ---------------------------------------------------------------------------
# Minimal port doubles
# ---------------------------------------------------------------------------
class _FakeInvocationLogger:
    """Yields a ctx with a `record(**kw)` method matching the real surface."""

    @asynccontextmanager
    async def invoke_model(self, **_kw):
        ctx = MagicMock()

        def _record(**_rec_kw):
            return None

        ctx.record = _record
        yield ctx


class _FakeStepTracker:
    @asynccontextmanager
    async def step(self, _name, **_kw):
        ctx = MagicMock()
        ctx.set_metadata = lambda **_a: None
        ctx.add_tokens = lambda **_a: None
        yield ctx


class _FakeGuardrail:
    async def check_input(self, *_a, **_kw):
        return []

    async def check_output(self, *_a, **_kw):
        return []


def _make_fakes(answer_text: str = "stub answer") -> tuple[MagicMock, MagicMock]:
    """Return (model_resolver, llm) AsyncMock pair.

    `llm.complete` records the `messages=` kwarg in `call_args` so the test
    can assert the exact shape after invoking `generate`.
    """
    resolver = MagicMock()
    cfg = MagicMock()
    cfg.litellm_name = "mock/model"
    cfg.provider = MagicMock(name="mock-provider")
    cfg.provider.name = "mock"
    # `cfg.params.max_tokens` path — _invoke_llm_node does
    # `getattr(getattr(cfg, "params", None), "max_tokens", None)` then int().
    # Use None so the int(...) branch is skipped.
    cfg.params = MagicMock()
    cfg.params.max_tokens = None
    resolver.resolve_runtime = AsyncMock(return_value=cfg)

    llm = MagicMock()
    llm.complete = AsyncMock(return_value={
        "text": answer_text,
        "prompt_tokens": 1,
        "completion_tokens": 1,
        "cached_tokens": 0,
        "cost_usd": 0.0,
        "finish_reason": "stop",
    })
    # No streaming branch in tests
    llm.complete_runtime_stream = None
    return resolver, llm


def _extract_generate_closure(*, bot_system_prompt: str):
    """Build the graph and return the `generate` async callable."""
    from ragbot.orchestration.query_graph import build_graph

    resolver, llm = _make_fakes()
    compiled = build_graph(
        invocation_logger=_FakeInvocationLogger(),
        guardrail=_FakeGuardrail(),
        model_resolver=resolver,
        llm=llm,
    )
    node = compiled.nodes["generate"].bound
    return node.afunc, llm


def _make_state(
    *,
    question: str = "What is the price?",
    graded_chunks: list[dict] | None = None,
    history: list[dict] | None = None,
    language: str = "vi",
    bot_system_prompt: str = "",
) -> dict:
    """Construct a minimal GraphState dict that drives the `generate` node
    through the plain-text path (structured output disabled).
    """
    return {
        "tenant_id": uuid4(),
        "record_tenant_id": uuid4(),
        "request_id": uuid4(),
        "message_id": 1,
        "conversation_id": uuid4(),
        "bot_id": uuid4(),
        "record_bot_id": uuid4(),
        "channel_type": "api",
        "language": language,
        "query": question,
        "rewritten_query": None,
        "graded_chunks": graded_chunks or [],
        "conversation_history": history or [],
        "answer": "",
        "model_used": "mock/model",
        "step_tracker": _FakeStepTracker(),
        "bot_system_prompt": bot_system_prompt,
        "kg_service": None,
        "session_factory": None,
        # Force the plain-text path (deterministic message capture) and
        # disable optional re-ordering / compression so chunks pass through.
        # F1a refuse-short-circuit DISABLED here: these tests assert on
        # the LLM-prompt SHAPE that fires on the regular generate path —
        # F1a coverage lives in test_refuse_short_circuit_chunks_zero.py.
        "pipeline_config": {
            "structured_output_enabled": False,
            "generate_use_structured_output": False,
            "prompt_compression_enabled": False,
            "lost_in_middle_reorder_enabled": False,
            "condense_history_limit": 6,
            "refuse_short_circuit_enabled": False,
        },
    }


def _run_generate(
    bot_system_prompt: str,
    *,
    question: str = "What is the price?",
    graded_chunks: list[dict] | None = None,
    history: list[dict] | None = None,
    language: str = "vi",
) -> tuple[list[dict], dict]:
    """Drive `generate` once; return (captured_messages, generate_output)."""
    afunc, llm = _extract_generate_closure(bot_system_prompt=bot_system_prompt)
    state = _make_state(
        question=question,
        graded_chunks=graded_chunks,
        history=history,
        language=language,
        bot_system_prompt=bot_system_prompt,
    )
    out = asyncio.run(afunc(state))
    # llm.complete was awaited with kwargs including messages=
    assert llm.complete.await_count >= 1, "generate did not call llm.complete"
    call = llm.complete.await_args
    captured = call.kwargs.get("messages")
    assert isinstance(captured, list), "messages kwarg missing from llm.complete call"
    return captured, out


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_messages_first_role_is_system_with_exact_bot_prompt() -> None:
    bot_prompt = "X test prompt"
    messages, _ = _run_generate(bot_prompt)
    assert messages[0]["role"] == "system"
    # EXACT equality — application MUST NOT prepend / append anything.
    assert messages[0]["content"] == bot_prompt, (
        f"system content was modified: {messages[0]['content']!r}"
    )


def test_no_extra_system_text_when_bot_prompt_nonempty() -> None:
    """Application MUST NOT add a 2nd system message (e.g. docs-only rule)."""
    messages, _ = _run_generate("Bot owner system prompt here")
    sys_msgs = [m for m in messages if m.get("role") == "system"]
    assert len(sys_msgs) == 1, (
        f"expected exactly 1 system msg, got {len(sys_msgs)}: {sys_msgs!r}"
    )


def test_user_message_is_documents_question_wrapper() -> None:
    """Final user message wraps context + question in <documents>/<question>
    tags only — no extra `rule:` / `always:` / `must:` prefix. Use a long
    factoid-shaped question so the chitchat-skip-docs branch does not fire.
    """
    chunk = {
        "chunk_id": "00000000-0000-0000-0000-000000000001",
        "text": "ABC Spa offers facial treatments.",
        "document_name": "service-catalog.pdf",
        "chunk_index": 0,
    }
    messages, _ = _run_generate(
        "You are a helpful assistant.",
        question=(
            "Vui lòng cho biết chi tiết bảng giá toàn bộ dịch vụ chăm sóc "
            "da mặt và liệu trình trẻ hóa hiện đang áp dụng tại spa, "
            "kèm theo thời gian thực hiện cụ thể của từng gói"
        ),
        graded_chunks=[chunk],
    )
    last = messages[-1]
    assert last["role"] == "user"
    content = last["content"]
    assert "<documents>" in content and "</documents>" in content
    assert "<question>" in content and "</question>" in content
    assert "bảng giá" in content
    # No application-injected directive prefixes
    forbidden_lower = (
        "rule:",
        "must always",
        "you must always cite",
        "promote our",
        "upsell",
        "docs only",
        "docs-only",
        "citation:",
    )
    cl = content.lower()
    for token in forbidden_lower:
        assert token not in cl, f"forbidden prefix {token!r} injected into user msg"


def test_chitchat_query_skips_documents_block() -> None:
    """Chitchat queries (intent classifier sets intent='chitchat') get only
    <question>; bot's sysprompt branch handles tone. Pattern-only heuristic
    (token-count + trap keyword) was REMOVED — short factoid queries must keep
    the documents block. This test now drives via explicit intent.
    """
    chunk = {
        "chunk_id": "00000000-0000-0000-0000-000000000099",
        "text": "Spa skincare brochure paragraph.",
        "document_name": "brochure.pdf",
        "chunk_index": 0,
    }
    state = _make_state(question="khoẻ không", graded_chunks=[chunk])
    state["intent"] = "chitchat"
    afunc, llm = _extract_generate_closure(
        bot_system_prompt="Bot owner sysprompt with chitchat branch.",
    )
    asyncio.run(afunc(state))
    captured = llm.complete.await_args.kwargs.get("messages")
    last = captured[-1]
    content = last["content"]
    assert "<question>khoẻ không</question>" == content.strip()
    assert "<documents>" not in content


def test_short_factoid_keeps_documents_block() -> None:
    """Regression for the removed pattern-chitchat OR-clause: a short query
    classified as factoid (no chitchat intent) MUST receive the documents block.
    """
    chunk = {
        "chunk_id": "00000000-0000-0000-0000-000000000088",
        "text": "Spa offers facial treatment X for face care.",
        "document_name": "catalog.pdf",
        "chunk_index": 0,
    }
    state = _make_state(question="có gì cho mặt", graded_chunks=[chunk])
    state["intent"] = "factoid"
    afunc, llm = _extract_generate_closure(
        bot_system_prompt="Bot owner sysprompt.",
    )
    asyncio.run(afunc(state))
    captured = llm.complete.await_args.kwargs.get("messages")
    last = captured[-1]
    content = last["content"]
    assert "<documents>" in content
    assert "<question>có gì cho mặt</question>" in content


def test_history_messages_passed_through_unchanged() -> None:
    history = [
        {"role": "user", "content": "prev1"},
        {"role": "assistant", "content": "prev2"},
    ]
    messages, _ = _run_generate("system X", history=history)
    # System first, user wrapper last; history sandwiched between.
    middle = messages[1:-1]
    assert len(middle) == 2, f"expected 2 history msgs, got {len(middle)}: {middle!r}"
    assert middle[0]["role"] == "user" and middle[0]["content"] == "prev1"
    assert middle[1]["role"] == "assistant" and middle[1]["content"] == "prev2"


def test_no_application_keywords_anywhere() -> None:
    """None of the CLAUDE.md "application MUST NOT inject" tokens should
    appear in any message content. Application is a passive carrier; the
    bot owner's system_prompt is the single source of truth for behavior.
    """
    history = [
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    chunks = [{
        "chunk_id": "00000000-0000-0000-0000-0000000000aa",
        "text": "Service info.",
        "document_name": "doc.pdf",
        "chunk_index": 1,
    }]
    messages, _ = _run_generate(
        "Bot owner prompt — answer politely.",
        question="Hello?",
        graded_chunks=chunks,
        history=history,
    )
    banned = [
        "docs_only",
        "platform_rule",
        "always upsell",
        "tự kiểm tra",
        "hallucinate",  # case-insensitive check below
        "math_lockdown",
        "oos_template",
    ]
    for msg in messages:
        content = (msg.get("content") or "").lower()
        for token in banned:
            assert token.lower() not in content, (
                f"banned application-injected token {token!r} found in "
                f"role={msg.get('role')!r} content={content[:120]!r}"
            )


def test_empty_bot_prompt_falls_back_to_i18n_generic_no_promo() -> None:
    """When bot_system_prompt is empty, fallback equals the i18n LanguagePack
    `prompt_generator` value (currently lives in `src/ragbot/shared/i18n.py`
    around line 35 (vi) / 123 (en)). Assert the fallback is the i18n string
    verbatim and contains zero promo / upsell verbiage.
    """
    from ragbot.shared.i18n import get_pack

    pack_vi = get_pack("vi")
    expected = pack_vi.prompt_generator

    messages, _ = _run_generate("", language="vi")
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == expected, (
        f"fallback drifted from i18n.prompt_generator: "
        f"got={messages[0]['content']!r} expected={expected!r}"
    )

    # Fallback must not contain promo / upsell / "always cite" verbiage
    promo_tokens = ("upsell", "promote", "advertis", "always recommend", "buy now")
    cl = expected.lower()
    for tok in promo_tokens:
        assert tok not in cl, f"i18n fallback leaks promo verbiage {tok!r}"


@pytest.mark.parametrize("bot_prompt", [
    "ABC Spa hỗ trợ chăm sóc da",          # vendor-name in bot's own prompt — bot owner's choice
    "Test Corp — internal helpdesk bot",
    "你好，我是客服机器人",                  # multilingual passthrough
])
def test_system_prompt_passes_through_verbatim(bot_prompt: str) -> None:
    """Even when the bot owner puts a brand / vendor name in their system
    prompt, the application MUST forward it verbatim — no filtering, no
    augmentation. Application is domain-neutral; bot config is the source.
    """
    messages, _ = _run_generate(bot_prompt)
    assert messages[0]["content"] == bot_prompt


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
