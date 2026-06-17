"""HARN-3 tests — opt-in `debug=full` payload + auditor uptake.

Scope:
  1. TestChatRequest Pydantic model accepts the new `debug` field
     without breaking existing callers (default "").
  2. When the handler builds its response and `debug=="full"`, the
     final dict includes `retrieved_chunks_content` with the expected
     per-chunk shape (chunk_id, content, source, score).
     Absent by default (debug="").
  3. `_judge_one` in scripts/audit_harness_run.py switches from legacy
     source-names prompt to chunk-content prompt when the turn carries
     `retrieved_chunks_content`.

We do NOT boot uvicorn or hit the live route here — that's what the
smoke test does. These unit tests exercise request-schema and the
response-assembly BRANCH directly so they run in ~0.1s with no DB.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from ragbot.interfaces.http.routes.test_chat import TestChatRequest

# ---------------------------------------------------------------------------
# Part 1 — TestChatRequest pydantic model
# ---------------------------------------------------------------------------

def test_debug_default_empty_string():
    """Legacy callers send no debug field → default "" → off."""
    req = TestChatRequest(
        bot_id="b1", channel_type="web", question="hello",
    )
    assert req.debug == ""


def test_debug_full_accepted():
    """New callers may pass debug='full' (case-insensitive consumed later)."""
    req = TestChatRequest(
        bot_id="b1", channel_type="web", question="hello", debug="full",
    )
    assert req.debug == "full"


def test_test_chat_request_omits_tenant_id_lifted_from_jwt():
    """Body carries 2-key bot identity; tenant is lifted from the JWT
    bearer (record_tenant_id UUID on request.state).
    The schema therefore has NO ``tenant_id`` field; supplying one is
    silently dropped (Pydantic default is non-strict on extra=allow).
    """
    req = TestChatRequest(bot_id="b1", channel_type="web", question="hello")
    assert not hasattr(req, "tenant_id"), (
        "TestChatRequest must NOT carry tenant_id in body — JWT bearer is canonical."
    )


def test_test_chat_request_rejects_missing_bot_id():
    """Hard-cut: missing bot_id → ValidationError (2-key bot identity)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        TestChatRequest(channel_type="web", question="hello")
    errs = exc_info.value.errors()
    assert any(e["loc"] == ("bot_id",) and e["type"] == "missing" for e in errs)


def test_test_chat_request_rejects_missing_channel_type():
    """Hard-cut: missing channel_type → ValidationError (2-key bot identity)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError) as exc_info:
        TestChatRequest(bot_id="b1", question="hello")
    errs = exc_info.value.errors()
    assert any(e["loc"] == ("channel_type",) and e["type"] == "missing" for e in errs)


def test_bypass_cache_defaults_false():
    """bypass_cache defaults to False so existing callers are not affected."""
    req = TestChatRequest(bot_id="b1", channel_type="web", question="hello")
    assert req.bypass_cache is False


def test_bypass_cache_true_accepted():
    """bypass_cache=True is accepted by the schema for test-mode use."""
    req = TestChatRequest(
        bot_id="b1", channel_type="web", question="hello",
        bypass_cache=True,
    )
    assert req.bypass_cache is True


# ---------------------------------------------------------------------------
# Part 2 — response assembly branch reproduction
#
# We mirror the exact code shape used inside test_chat() so we can verify
# the chunk-content payload without booting the full handler (which needs
# DB + container + LLM). If the handler's branch ever drifts from this,
# the integration harness smoke step catches it.
# ---------------------------------------------------------------------------

def _build_retrieved_chunks_content(debug_param: str, graded_chunks, retrieved_chunks):
    """Minimal replay of the `debug=="full"` branch in test_chat()."""
    response: dict = {"answer": "ok"}
    if (debug_param or "").lower() == "full":
        _src = graded_chunks or retrieved_chunks
        response["retrieved_chunks_content"] = [
            {
                "chunk_id": (
                    (c.get("chunk_id") if isinstance(c, dict) else None)
                    or (c.get("id") if isinstance(c, dict) else None)
                ),
                "content": ((c.get("content") if isinstance(c, dict) else None)
                            or (c.get("text") if isinstance(c, dict) else None)
                            or "")[:3000],
                "source": (
                    (c.get("document_name") if isinstance(c, dict) else None)
                    or (c.get("source") if isinstance(c, dict) else None)
                    or ((c.get("metadata") or {}).get("document_title") if isinstance(c, dict) else None)
                ),
                "score": float(c.get("score", 0)) if isinstance(c, dict) else None,
            }
            for c in _src
        ]
    return response


def test_debug_full_not_default():
    """Omitting debug (or sending "") must NOT attach chunk content."""
    chunks = [
        {"chunk_id": "c1", "content": "giá 200k", "document_name": "Bảng giá", "score": 0.9},
    ]
    resp = _build_retrieved_chunks_content("", chunks, [])
    assert "retrieved_chunks_content" not in resp
    resp2 = _build_retrieved_chunks_content("", [], [])
    assert "retrieved_chunks_content" not in resp2


def test_debug_full_includes_chunks():
    """debug='full' → list of {chunk_id, content, source, score}."""
    chunks = [
        {"chunk_id": "c1", "content": "giá gội đầu 200k",
         "document_name": "Bảng giá dịch vụ gội đầu", "score": 0.87},
        {"id": "c2", "text": "massage 500k", "source": "Bảng giá massage", "score": 0.72},
    ]
    resp = _build_retrieved_chunks_content("FULL", chunks, [])
    payload = resp.get("retrieved_chunks_content")
    assert isinstance(payload, list) and len(payload) == 2
    assert payload[0]["chunk_id"] == "c1"
    assert "200k" in payload[0]["content"]
    assert payload[0]["source"] == "Bảng giá dịch vụ gội đầu"
    assert payload[0]["score"] == pytest.approx(0.87)
    # Second chunk uses the `id` / `text` / `source` fallback keys.
    assert payload[1]["chunk_id"] == "c2"
    assert payload[1]["content"] == "massage 500k"
    assert payload[1]["source"] == "Bảng giá massage"


def test_debug_full_falls_back_to_retrieved_when_no_graded():
    """If graded_chunks is empty (grading filtered all out), fall back
    to retrieved_chunks so the judge still sees something."""
    retrieved = [{"chunk_id": "r1", "content": "raw", "document_name": "Doc", "score": 0.3}]
    resp = _build_retrieved_chunks_content("full", [], retrieved)
    assert len(resp["retrieved_chunks_content"]) == 1
    assert resp["retrieved_chunks_content"][0]["chunk_id"] == "r1"


def test_debug_full_truncates_content_3000_chars():
    long = "x" * 5000
    chunks = [{"chunk_id": "c1", "content": long, "document_name": "D", "score": 0.5}]
    resp = _build_retrieved_chunks_content("full", chunks, [])
    assert len(resp["retrieved_chunks_content"][0]["content"]) == 3000


# ---------------------------------------------------------------------------
# Part 3 — auditor _judge_one consumes chunks when present
# ---------------------------------------------------------------------------

_AUDIT_SCRIPT = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__), os.pardir, os.pardir,
        "scripts", "audit_harness_run.py",
    )
)


def _load_audit_mod() -> Any:
    """Load ``scripts/audit_harness_run`` for in-process testing.

    The script resolves its judge model from ``system_config`` at import
    time, which requires a live Postgres. Stub the resolver so the unit
    test runs without DB credentials. Function-scope (not module-scope)
    so the stub is isolated per test — module-scope would let an early
    error mask later tests' setup.
    """
    spec = importlib.util.spec_from_file_location("audit_harness_run", _AUDIT_SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit_harness_run"] = mod
    _patch_db_for_audit_module_load()
    try:
        spec.loader.exec_module(mod)
    finally:
        _unpatch_db_after_audit_module_load()
    return mod


_AUDIT_DB_PATCHES: list[tuple[Any, Any]] = []
_AUDIT_ENV_PATCHES: list[tuple[str, str | None]] = []


def _patch_db_for_audit_module_load() -> None:
    import psycopg2  # noqa: PLC0415 — only when test-module fixture loads

    _orig_connect = psycopg2.connect

    class _StubCur:
        def execute(self, *_a: Any, **_k: Any) -> None:
            return None

        def fetchone(self) -> tuple[str]:
            return ("stub-judge-model",)

        def __enter__(self) -> _StubCur:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    class _StubConn:
        def cursor(self) -> _StubCur:
            return _StubCur()

        def __enter__(self) -> _StubConn:
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

    def _stub_connect(*_a: Any, **_k: Any) -> _StubConn:
        return _StubConn()

    psycopg2.connect = _stub_connect
    _AUDIT_ENV_PATCHES.append(("DATABASE_URL", os.environ.get("DATABASE_URL")))
    os.environ["DATABASE_URL"] = "postgresql://stub:stub@stub/stub"
    _AUDIT_DB_PATCHES.append((psycopg2, _orig_connect))


def _unpatch_db_after_audit_module_load() -> None:
    while _AUDIT_DB_PATCHES:
        mod, orig = _AUDIT_DB_PATCHES.pop()
        mod.connect = orig
    while _AUDIT_ENV_PATCHES:
        key, prev = _AUDIT_ENV_PATCHES.pop()
        if prev is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = prev


@pytest.fixture(scope="function")
def audit_mod() -> Any:
    return _load_audit_mod()


def _fake_openai_client(capture: dict) -> MagicMock:
    """AsyncOpenAI stub that records the user message we built."""
    client = MagicMock()
    client.chat = MagicMock()
    client.chat.completions = MagicMock()

    async def _create(**kwargs):
        # Capture the user-role message for assertions.
        for m in kwargs.get("messages", []):
            if m["role"] == "user":
                capture["user_msg"] = m["content"]
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message = MagicMock()
        resp.choices[0].message.content = (
            '{"answered": true, "grounded": true, "correct": true, '
            '"hallucinated": false, "reason": "ok"}'
        )
        return resp

    client.chat.completions.create = AsyncMock(side_effect=_create)
    return client


def test_auditor_uses_chunks_when_available(audit_mod):
    capture: dict = {}
    client = _fake_openai_client(capture)
    turn = {
        "_question": "giá gội đầu?",
        "answer": "200k ạ.",
        "sources": ["Bảng giá dịch vụ gội đầu"],
        "retrieved_chunks_content": [
            {"chunk_id": "c1", "source": "Bảng giá",
             "content": "gội đầu cơ bản: 200,000đ", "score": 0.9},
        ],
    }
    verdict = asyncio.run(audit_mod._judge_one(client, turn))
    assert verdict["answered"] is True
    assert "Nội dung chunks" in capture["user_msg"]
    # Real chunk content is injected, not just doc names.
    assert "200,000đ" in capture["user_msg"]


def test_auditor_falls_back_to_sources_when_no_chunks(audit_mod):
    """Pre-HARN-3 run files (no retrieved_chunks_content) must still work."""
    capture: dict = {}
    client = _fake_openai_client(capture)
    turn = {
        "_question": "giá gội đầu?",
        "answer": "200k.",
        "sources": ["Bảng giá dịch vụ gội đầu"],
    }
    asyncio.run(audit_mod._judge_one(client, turn))
    assert "Nguồn đã retrieve" in capture["user_msg"]
    assert "Bảng giá dịch vụ gội đầu" in capture["user_msg"]
    assert "Nội dung chunks" not in capture["user_msg"]
