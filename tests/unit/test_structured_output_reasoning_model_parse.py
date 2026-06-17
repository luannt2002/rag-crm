"""Pin tests for ``structured_output_helper`` — reasoning-model parse path.

Before 2026-05-21, ``_extract_text`` only read ``message.content`` and
``_fallback_json_parse`` only stripped markdown fences. That made every
structured-output call against a reasoning model (Qwen3 thinking,
DeepSeek-R1, Qwen3.6-35b-kimi, Gemma-thinking) return ``None`` because:

1. Reasoning models often emit an empty ``content`` field — the answer
   tokens were eaten by the chain-of-thought before ``max_tokens``
   could land on a flushed answer.
2. The chain-of-thought (``reasoning_content``) frequently contains the
   final JSON object *inline*, prefixed with prose like "Let me think...".
   The naked ``model_validate_json`` call rejected the entire blob.

This test file pins the 3 supported wire shapes so a future refactor
that drops the reasoning-content fallback or the embedded-JSON scanner
re-introduces the same silent regression.
"""

from __future__ import annotations

from types import SimpleNamespace

from pydantic import BaseModel

from ragbot.application.services.structured_output_helper import (
    _extract_text,
    _fallback_json_parse,
    _scan_first_json_block,
)


class _Out(BaseModel):
    answer: str
    confidence: float


def _mk_response(content: str | None, reasoning: str | None = None) -> object:
    """Build a minimal LiteLLM ``ModelResponse`` stand-in."""
    msg = SimpleNamespace(content=content, reasoning_content=reasoning)
    choice = SimpleNamespace(message=msg)
    return SimpleNamespace(choices=[choice])


# ---------------------------------------------------------------------------
# _extract_text — reasoning fallback
# ---------------------------------------------------------------------------


def test_extract_text_prefers_content_when_present() -> None:
    """Non-reasoning models populate ``content`` — must not regress."""
    resp = _mk_response("Hello world", reasoning="Let me think...")
    assert _extract_text(resp) == "Hello world"


def test_extract_text_falls_back_to_reasoning_when_content_empty() -> None:
    """Reasoning models with empty ``content`` — surface ``reasoning_content``."""
    resp = _mk_response("", reasoning="The answer is {'answer': 'x'}")
    assert _extract_text(resp) == "The answer is {'answer': 'x'}"


def test_extract_text_handles_both_none() -> None:
    """Defensive — both fields ``None`` returns empty string, not crash."""
    resp = _mk_response(None, reasoning=None)
    assert _extract_text(resp) == ""


def test_extract_text_handles_missing_reasoning_attr() -> None:
    """Older / non-reasoning models don't have ``reasoning_content``."""
    msg = SimpleNamespace(content="ok")
    choice = SimpleNamespace(message=msg)
    resp = SimpleNamespace(choices=[choice])
    assert _extract_text(resp) == "ok"


# ---------------------------------------------------------------------------
# _scan_first_json_block — quote-aware brace counter
# ---------------------------------------------------------------------------


def test_scan_extracts_clean_json_object() -> None:
    assert _scan_first_json_block('{"x": 1}') == '{"x": 1}'


def test_scan_extracts_json_after_prose() -> None:
    text = 'Let me think. Here is the answer:\n{"answer": "yes", "confidence": 0.9}\nDone.'
    assert _scan_first_json_block(text) == '{"answer": "yes", "confidence": 0.9}'


def test_scan_handles_nested_braces() -> None:
    text = '{"outer": {"inner": "v"}}'
    assert _scan_first_json_block(text) == text


def test_scan_handles_braces_inside_strings() -> None:
    """Quote-aware — braces inside string literals do not increment depth."""
    text = '{"path": "/api/{id}", "ok": true}'
    assert _scan_first_json_block(text) == text


def test_scan_handles_escaped_quotes_inside_strings() -> None:
    text = r'{"msg": "He said \"hi\""}'
    assert _scan_first_json_block(text) == text


def test_scan_returns_none_when_unbalanced() -> None:
    text = 'No JSON here, just words'
    assert _scan_first_json_block(text) is None


def test_scan_returns_none_when_truncated() -> None:
    text = '{"answer": "yes", '  # missing closing brace
    assert _scan_first_json_block(text) is None


def test_scan_prefers_object_when_both_present() -> None:
    """Object should win over array per registry iteration order."""
    text = 'Prose [1, 2, 3] more {"x": 1} end'
    assert _scan_first_json_block(text) == '{"x": 1}'


# ---------------------------------------------------------------------------
# _fallback_json_parse — end-to-end across response shapes
# ---------------------------------------------------------------------------


def test_fallback_parse_clean_json_in_content() -> None:
    """OpenAI / Anthropic happy path — content already valid JSON."""
    resp = _mk_response('{"answer": "yes", "confidence": 0.9}')
    out = _fallback_json_parse(resp, _Out)
    assert out is not None
    assert out.answer == "yes"
    assert out.confidence == 0.9


def test_fallback_parse_fenced_markdown_json() -> None:
    """Some models wrap JSON in ```json ... ``` fences."""
    resp = _mk_response('```json\n{"answer": "yes", "confidence": 0.5}\n```')
    out = _fallback_json_parse(resp, _Out)
    assert out is not None and out.answer == "yes"


def test_fallback_parse_reasoning_model_inline_json() -> None:
    """Reasoning model — content empty, JSON embedded inline in reasoning."""
    resp = _mk_response(
        content="",
        reasoning=(
            "Let me analyse this question step by step. The user asked X. "
            "Based on the document, the answer should be 'yes' with high "
            'confidence. Final answer:\n{"answer": "yes", "confidence": 0.95}\n'
            "Note: this is my reasoning."
        ),
    )
    out = _fallback_json_parse(resp, _Out)
    assert out is not None, (
        "Reasoning model output with inline JSON must be parseable. "
        "Regression of the 2026-05-21 Qwen3.6-35b fix."
    )
    assert out.answer == "yes"
    assert out.confidence == 0.95


def test_fallback_parse_returns_none_on_empty_response() -> None:
    resp = _mk_response("", reasoning="")
    assert _fallback_json_parse(resp, _Out) is None


def test_fallback_parse_returns_none_on_unparseable_text() -> None:
    resp = _mk_response("I don't know the answer.")
    assert _fallback_json_parse(resp, _Out) is None
