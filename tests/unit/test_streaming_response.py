"""Stream 08-streaming — Streaming Response (SSE) unit tests.

T2 UX. Verifies the SSE contract surfaced by
``ragbot.interfaces.http._sse_helper.stream_real_llm`` plus the
``/chat/stream`` route module's structural invariants (RBAC gate, 4-key
identity preservation, feature-flag resolution, domain-neutral source).

These tests drive the helper with a plain ``asyncio.Queue`` so they do not
require DB, Redis, or LiteLLM. The end-to-end smoke is covered by the
integration suite ``tests/integration/test_chat_stream_production.py``;
this module is the unit-level Quality-Gate guard.

REAL behavioural assertions (CLAUDE.md Quality Gate item 7) — no
``assert True`` placeholders.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from ragbot.interfaces.http._sse_helper import (
    _STREAM_SENTINEL,
    replace_event,
    stream_real_llm,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _parse_sse(frames: list[str]) -> list[dict]:
    """Decode the JSON payload of every ``data: ...`` line in ``frames``."""
    payloads: list[dict] = []
    for chunk in frames:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[len("data: "):]))
    return payloads


# ---------------------------------------------------------------------------
# SSE event format / contract
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sse_event_sequence_status_token_done() -> None:
    """SSE protocol: ``status`` → one-or-more ``token`` → ``done``.

    Stream 08 acceptance: client receives first-token frame before the
    full pipeline finishes, then a terminal ``done`` event with the
    canonical answer + sources.
    """
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {
        "state": {
            "answer": "Xin chào bạn.",
            "answer_type": "answered",
            "answer_reason": "generated",
            "tokens": {"prompt": 12, "completion": 5, "cached": 0},
        },
        "error": None,
        "sources": [{"document_name": "doc1.txt"}],
    }

    async def _producer() -> None:
        await sink.put("Xin ")
        await sink.put("chào ")
        await sink.put("bạn.")
        await sink.put(_STREAM_SENTINEL)

    on_complete_args: list[tuple] = []

    async def _on_complete(state, answer, dur_ms):
        on_complete_args.append((state, answer, dur_ms))

    task = asyncio.create_task(_producer())
    frames: list[str] = []
    async for f in stream_real_llm(
        sink, task, state_holder, time.perf_counter(), _on_complete,
    ):
        frames.append(f)
    await task

    # Every frame must be a well-formed SSE data line ending in \n\n.
    for f in frames:
        assert f.startswith("data: ") and f.endswith("\n\n"), f"malformed SSE: {f!r}"

    payloads = _parse_sse(frames)
    types = [p["type"] for p in payloads]
    assert types[0] == "status", types
    assert types[-1] == "done", types
    token_idxs = [i for i, t in enumerate(types) if t == "token"]
    assert len(token_idxs) == 3, types
    # Tokens arrive in order; concatenation matches what the producer pushed.
    assert "".join(p["content"] for p in payloads if p["type"] == "token") == "Xin chào bạn."

    done = payloads[-1]
    assert done["answer"] == "Xin chào bạn."
    assert done["answer_type"] == "answered"
    assert done["sources"] == [{"document_name": "doc1.txt"}]
    assert isinstance(done["duration_ms"], int) and done["duration_ms"] >= 0

    # on_complete must run exactly once with canonical answer + non-negative duration.
    assert len(on_complete_args) == 1
    _, ans, dur = on_complete_args[0]
    assert ans == "Xin chào bạn."
    assert dur >= 0


@pytest.mark.asyncio
async def test_sse_emits_replace_event_when_canonical_differs() -> None:
    """Post-validation rewrite (guardrail / math-lockdown) → ``replace`` event.

    The helper MUST surface the canonical answer to the client when it
    diverges from the streamed buffer, so the UI can swap text. The
    streaming path itself never injects/overrides — that decision lives in
    the pipeline (CLAUDE.md Quality Gate item 10). The helper just relays.
    """
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {
        "state": {
            "answer": "Vui lòng tham khảo tài liệu chính thức.",
            "answer_type": "answered",
            "answer_reason": "guardrail_rewrite",
            "tokens": {"prompt": 0, "completion": 0, "cached": 0},
        },
        "error": None,
        "sources": [],
    }

    async def _producer() -> None:
        await sink.put("Số liệu ")
        await sink.put("không có trong tài liệu")
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw):
        return None

    frames: list[str] = []
    async for f in stream_real_llm(
        sink, task, state_holder, time.perf_counter(), _noop,
    ):
        frames.append(f)
    await task

    payloads = _parse_sse(frames)
    types = [p["type"] for p in payloads]
    assert "replace" in types, types
    rep = next(p for p in payloads if p["type"] == "replace")
    assert rep["answer"] == "Vui lòng tham khảo tài liệu chính thức."
    assert rep["reason"] == "guardrail_rewrite"


@pytest.mark.asyncio
async def test_sse_emits_done_when_producer_raises_mid_stream() -> None:
    """Producer error → sentinel still arrives via ``finally`` → ``done`` fires.

    SSE clients must never be left hanging — even when the pipeline
    explodes, the helper terminates the response cleanly with a final
    ``done`` event so the connection closes.
    """
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {"state": None, "error": None, "sources": []}

    async def _producer() -> None:
        try:
            await sink.put("partial ")
            raise RuntimeError("simulated pipeline node failure")
        finally:
            await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw):
        return None

    frames: list[str] = []
    async for f in stream_real_llm(
        sink, task, state_holder, time.perf_counter(), _noop,
    ):
        frames.append(f)

    payloads = _parse_sse(frames)
    types = [p["type"] for p in payloads]
    assert types[-1] == "done", types
    # No canonical answer set when graph errored before final state populated.
    assert payloads[-1]["answer"] == ""
    assert payloads[-1]["answer_type"] == "no_context"
    assert "simulated pipeline node failure" in (state_holder.get("error") or "")


@pytest.mark.asyncio
async def test_sse_filters_non_string_and_empty_payloads() -> None:
    """Producer might enqueue empty strings / wrong types — drop silently."""
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {
        "state": {"answer": "ok", "answer_type": "answered", "tokens": {}},
        "error": None,
        "sources": [],
    }

    async def _producer() -> None:
        await sink.put("")          # empty string — dropped
        await sink.put(None)        # type: ignore[arg-type] — wrong type, dropped
        await sink.put(42)          # type: ignore[arg-type] — wrong type, dropped
        await sink.put("real")
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw):
        return None

    frames: list[str] = []
    async for f in stream_real_llm(
        sink, task, state_holder, time.perf_counter(), _noop,
    ):
        frames.append(f)
    await task

    types = [p["type"] for p in _parse_sse(frames)]
    # Exactly one token frame (the "real" delta) — empties + wrong types skipped.
    assert types.count("token") == 1, types


# ---------------------------------------------------------------------------
# Telemetry (FILE-OWNERSHIP-MATRIX 2D spec: first_token_ms + total_tokens)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_telemetry_first_token_and_completion_events() -> None:
    """Stream emits ``stream_chunk_emit`` once on first token and
    ``streaming_response_completed`` once at end of stream, both with
    spec-mandated fields and any caller-supplied identity tags."""
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {
        "state": {
            "answer": "Em chào anh",
            "answer_type": "answered",
            "tokens": {"prompt": 100, "completion": 42, "cached": 5},
        },
        "error": None,
        "sources": [],
    }

    async def _producer() -> None:
        # Two tokens — first_token_ms fires on token #1 only (not #2).
        await sink.put("Em ")
        await sink.put("chào anh")
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw):
        return None

    telemetry = {
        "request_id": "11111111-1111-1111-1111-111111111111",
        "record_tenant_id": "22222222-2222-2222-2222-222222222222",
        "record_bot_id": "33333333-3333-3333-3333-333333333333",
        "workspace_id": "ws-alpha",
        "bot_id": "demo-bot",
        "channel_type": "web",
        "feature_flag": "streaming_response_enabled",
    }

    with capture_logs() as cap:
        async for _ in stream_real_llm(
            sink,
            task,
            state_holder,
            time.perf_counter(),
            _noop,
            telemetry_extra=telemetry,
        ):
            pass
        await task

    by_event = {e["event"]: e for e in cap}

    # 1) stream_chunk_emit fires exactly once.
    chunk_events = [e for e in cap if e["event"] == "stream_chunk_emit"]
    assert len(chunk_events) == 1, chunk_events
    ce = chunk_events[0]
    assert ce.get("step_name") == "stream_chunk_emit"
    assert isinstance(ce.get("first_token_ms"), int)
    assert ce.get("first_token_ms") >= 0
    # 4-key identity propagated.
    assert ce.get("record_tenant_id") == telemetry["record_tenant_id"]
    assert ce.get("workspace_id") == telemetry["workspace_id"]
    assert ce.get("bot_id") == telemetry["bot_id"]
    assert ce.get("channel_type") == telemetry["channel_type"]
    assert ce.get("feature_flag") == "streaming_response_enabled"

    # 2) streaming_response_completed fires exactly once with TTFT + totals.
    assert "streaming_response_completed" in by_event
    se = by_event["streaming_response_completed"]
    assert se.get("step_name") == "streaming_response"
    assert se.get("first_token_ms") == ce["first_token_ms"]
    # total_tokens is wired to LLM completion token count, not delta count.
    assert se.get("total_tokens") == 42
    # streamed_chunks reflects the producer delta count (2 deltas).
    assert se.get("streamed_chunks") == 2
    assert se.get("had_error") is False
    assert se.get("answer_type") == "answered"


@pytest.mark.asyncio
async def test_telemetry_first_token_absent_on_zero_delta_stream() -> None:
    """Refuse short-circuit / pipeline error → no token deltas → no
    stream_chunk_emit event, but the completion event still fires with
    ``first_token_ms=None`` so monitoring can compute miss-rate."""
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {
        "state": {"answer": "", "answer_type": "no_context", "tokens": {}},
        "error": "PipelineError: refused",
        "sources": [],
    }

    async def _producer() -> None:
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw):
        return None

    with capture_logs() as cap:
        async for _ in stream_real_llm(
            sink, task, state_holder, time.perf_counter(), _noop,
        ):
            pass
        await task

    chunk_events = [e for e in cap if e["event"] == "stream_chunk_emit"]
    completed = [e for e in cap if e["event"] == "streaming_response_completed"]
    assert chunk_events == []
    assert len(completed) == 1
    assert completed[0].get("first_token_ms") is None
    assert completed[0].get("streamed_chunks") == 0
    assert completed[0].get("had_error") is True


# ---------------------------------------------------------------------------
# Replace-event helper standalone
# ---------------------------------------------------------------------------
def test_replace_event_frame_is_valid_sse_data_line() -> None:
    frame = replace_event("Hello world", reason="math_lockdown")
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame[len("data: "):].strip())
    assert payload == {
        "type": "replace",
        "answer": "Hello world",
        "reason": "math_lockdown",
    }


def test_replace_event_preserves_unicode_diacritics() -> None:
    """ensure_ascii=False — Vietnamese/CJK text must not get \\uXXXX escaped."""
    frame = replace_event("Tài liệu hướng dẫn 中文")
    assert "Tài liệu hướng dẫn 中文" in frame
    assert "\\u" not in frame


# ---------------------------------------------------------------------------
# /chat/stream route — structural invariants
# ---------------------------------------------------------------------------
def test_chat_stream_route_declares_rbac_chat_stream_dep() -> None:
    """RBAC (CLAUDE.md Quality Gate item 5): every route attaches
    ``require_permission_dep('chat', 'stream')`` — never anonymous."""
    from ragbot.interfaces.http.routes import chat_stream

    deps_per_route: list[list[str]] = []
    for r in chat_stream.router.routes:
        names = []
        for dep in getattr(r, "dependencies", ()) or ():
            fn = getattr(dep, "dependency", None)
            names.append(getattr(fn, "__name__", ""))
        deps_per_route.append(names)
    # At least one route exists and declares require_chat_stream.
    assert deps_per_route, "chat_stream router has no routes"
    assert any(
        "require_chat_stream" in names for names in deps_per_route
    ), f"chat_stream missing chat:stream RBAC, deps={deps_per_route}"


def test_chat_stream_route_resolves_four_key_identity() -> None:
    """4-key identity (CLAUDE.md IDENTITY RULE): the route reads
    ``record_tenant_id`` from JWT (request.state) and resolves
    (workspace_id, bot_id, channel_type) via ``BotRegistryService.lookup``
    — never trusts a body-supplied tenant UUID."""
    from ragbot.interfaces.http.routes import chat_stream as cs

    src = Path(cs.__file__).read_text()
    # JWT-derived tenant: middleware lifts request.state.record_tenant_id.
    assert "request.state.record_tenant_id" in src
    # Workspace resolves via the shared validator (body fallback).
    assert "resolve_workspace_id" in src
    # The registry receives all 4 keys for lookup.
    assert "bot_registry_service" in src
    assert re.search(
        r"registry\.lookup\(\s*record_tenant_id=", src,
    ) is not None, "registry.lookup must accept named record_tenant_id"
    # Body must not be allowed to set record_tenant_id directly.
    assert "req.record_tenant_id" not in src
    assert "req.tenant_id" not in src


def test_chat_stream_honors_streaming_response_enabled_flag() -> None:
    """Feature flag (stream 08 spec): ``streaming_response_enabled``
    is read from system_config so ops can flip the kill-switch without
    redeploy. Backward compat: legacy ``streaming_enabled`` is still
    honored — disabling either kills SSE."""
    from ragbot.interfaces.http.routes import chat_stream as cs

    src = Path(cs.__file__).read_text()
    assert '"streaming_response_enabled"' in src or "streaming_response_enabled" in src
    assert '"streaming_enabled"' in src or "streaming_enabled" in src
    # The route must return 403 (not 500) when streaming is disabled.
    assert "status_code=403" in src


def test_chat_stream_is_domain_neutral() -> None:
    """CLAUDE.md domain-neutral rule: no brand / customer / role literals
    in the streaming route module."""
    from ragbot.interfaces.http.routes import chat_stream as cs

    src = Path(cs.__file__).read_text()
    # Role literals (handled by numeric RBAC level, not strings).
    for lit in ('"admin"', '"super_admin"', '"operator"', '"viewer"', '"guest"'):
        assert lit not in src, f"chat_stream has role literal {lit}"
    # No customer / brand identifiers — generic placeholder check.
    forbidden_brands = ("vntour.vn", "innocom.vn", "vinfast")
    lower = src.lower()
    for brand in forbidden_brands:
        assert brand not in lower, f"chat_stream leaks brand literal {brand}"


def test_chat_stream_zero_hardcode_uses_constants() -> None:
    """CLAUDE.md zero-hardcode rule: SSE chunk size / timeout / sink size
    come from ``shared.constants``, not inline literals.

    G15 dropped the ``DEFAULT_LOG_PREVIEW_CHARS`` use in chat_stream --
    request_chunk_refs (alembic 0109) stores ONLY (chunk_id, rank, score)
    so no preview-truncation constant is needed at the finalize-log call
    site.
    """
    from ragbot.interfaces.http.routes import chat_stream as cs

    src = Path(cs.__file__).read_text()
    # Required constant imports.
    for const in (
        "DEFAULT_CHAT_STREAM_TIMEOUT_S",
        "DEFAULT_SSE_SINK_MAXSIZE",
        "DEFAULT_SOURCE_PREVIEW_CHARS",
    ):
        assert const in src, f"chat_stream must import {const}"


def test_chat_stream_preserves_non_streaming_endpoint_isolation() -> None:
    """The legacy ``/chat`` (202 + worker outbox) endpoint must not be
    affected by this stream. ``chat_stream`` lives in its own module and
    its router does not register ``/chat`` (only ``/chat/stream``)."""
    from ragbot.interfaces.http.routes import chat_stream as cs

    paths = {
        getattr(r, "path", "") for r in cs.router.routes
    }
    assert "/chat/stream" in paths
    # The non-streaming "/chat" is owned by ``chat.py``, not chat_stream.
    assert "/chat" not in paths


def test_stream_real_llm_signature_supports_telemetry_extra() -> None:
    """Telemetry contract: caller can pass identity tags into the SSE
    pump so structlog events carry 4-key identity correlation."""
    sig = inspect.signature(stream_real_llm)
    params = sig.parameters
    assert "telemetry_extra" in params, list(params)
    # Keyword-only so positional callers don't break.
    assert params["telemetry_extra"].kind == inspect.Parameter.KEYWORD_ONLY


# ---------------------------------------------------------------------------
# Application-MUST-NOT-inject / override (CLAUDE.md Quality Gate item 10)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_streaming_helper_does_not_modify_token_content() -> None:
    """The SSE helper relays LLM deltas verbatim — never rewrites,
    never injects platform text, never strips guardrail-output. Any
    answer mutation is the pipeline's responsibility and surfaces via
    the ``replace`` event (covered above)."""
    deltas = ["Câu trả lời ", "gốc ", "từ LLM."]
    sink: asyncio.Queue = asyncio.Queue()
    state_holder = {
        "state": {
            "answer": "".join(deltas),
            "answer_type": "answered",
            "tokens": {},
        },
        "error": None,
        "sources": [],
    }

    async def _producer() -> None:
        for d in deltas:
            await sink.put(d)
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw):
        return None

    frames: list[str] = []
    async for f in stream_real_llm(
        sink, task, state_holder, time.perf_counter(), _noop,
    ):
        frames.append(f)
    await task

    payloads = _parse_sse(frames)
    streamed = [p["content"] for p in payloads if p["type"] == "token"]
    # Byte-for-byte fidelity — helper does not edit deltas.
    assert streamed == deltas
    # And canonical answer matches streamed buffer → no replace event.
    types = [p["type"] for p in payloads]
    assert "replace" not in types, types
