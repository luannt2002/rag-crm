"""Unit tests — query intent extractor.

The extractor turns a user query into a small filter dict that the
retrieve node feeds into ``hybrid_search`` as ``metadata_filter``.
Vocabulary + system prompt come from operator-supplied config; empty
prompt means skip the call. Every failure mode degrades to ``{}``.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

_litellm_stub = types.ModuleType("litellm")


async def _default_acompletion(**_kwargs: Any) -> Any:  # noqa: ANN401
    raise RuntimeError("acompletion was not patched in test")


_litellm_stub.acompletion = _default_acompletion  # type: ignore[attr-defined]
sys.modules.setdefault("litellm", _litellm_stub)

from ragbot.application.services import query_intent_extractor as qie  # noqa: E402

_DEFAULT_PROMPT = "Label the query. Return JSON with document_type and entity."
_DEFAULT_VOCAB = frozenset({"price_list", "policy", "info", "guide", "faq", "other"})


def _make_response(text: str) -> Any:
    class _Msg:
        def __init__(self, content: str) -> None:
            self.content = content

    class _Choice:
        def __init__(self, content: str) -> None:
            self.message = _Msg(content)

    class _Resp:
        def __init__(self, content: str) -> None:
            self.choices = [_Choice(content)]

    return _Resp(text)


@pytest.mark.asyncio
async def test_returns_dict_for_valid_doc_type(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_response('{"document_type": "price_list"}')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "Bao nhiêu tiền dịch vụ A?",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {"document_type": "price_list"}
    assert captured["temperature"] == 0.0
    assert captured["model"]
    assert captured["max_tokens"] > 0


@pytest.mark.asyncio
async def test_returns_empty_for_unknown_doc_type(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> Any:
        return _make_response('{"document_type": "spaceship_manual"}')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "anything",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {}


@pytest.mark.asyncio
async def test_keeps_entity_when_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> Any:
        return _make_response(
            '{"document_type": "policy", "entity": "refund window"}'
        )

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "how do refunds work?",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {"document_type": "policy", "entity": "refund window"}


@pytest.mark.asyncio
async def test_strips_markdown_fences(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> Any:
        return _make_response('```json\n{"document_type": "guide"}\n```')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "how to ...",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {"document_type": "guide"}


@pytest.mark.asyncio
async def test_invalid_json_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> Any:
        return _make_response("not json at all { broken")

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "anything",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {}


@pytest.mark.asyncio
async def test_llm_exception_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> Any:
        raise TimeoutError("provider down")

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "Giá A?",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {}


@pytest.mark.asyncio
async def test_blank_query_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, bool] = {"hit": False}

    async def fake(**_kwargs: Any) -> Any:
        called["hit"] = True
        return _make_response('{"document_type":"info"}')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "   ",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {}
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_empty_prompt_short_circuits(monkeypatch: pytest.MonkeyPatch) -> None:
    """No operator-supplied system prompt → skip LLM, return {}."""
    called: dict[str, bool] = {"hit": False}

    async def fake(**_kwargs: Any) -> Any:
        called["hit"] = True
        return _make_response('{"document_type":"info"}')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "anything",
        system_prompt="",
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {}
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_non_dict_payload_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(**_kwargs: Any) -> Any:
        return _make_response('["price_list"]')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "anything",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert out == {}


@pytest.mark.asyncio
async def test_model_id_override_is_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return _make_response("{}")

    monkeypatch.setattr("litellm.acompletion", fake)
    await qie.extract_intent(
        "anything",
        model_id="claude-haiku-test",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=_DEFAULT_VOCAB,
    )
    assert captured["model"] == "claude-haiku-test"


@pytest.mark.asyncio
async def test_empty_vocabulary_drops_doc_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator did not seed vocabulary → document_type field is dropped."""

    async def fake(**_kwargs: Any) -> Any:
        return _make_response('{"document_type": "price_list", "entity": "X"}')

    monkeypatch.setattr("litellm.acompletion", fake)
    out = await qie.extract_intent(
        "Giá A?",
        system_prompt=_DEFAULT_PROMPT,
        allowed_doc_types=frozenset(),
    )
    assert out == {"entity": "X"}
