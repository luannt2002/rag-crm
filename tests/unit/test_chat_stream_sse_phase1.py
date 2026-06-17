"""Wave H Phase 1 — Speculative Streaming SSE foundation (J1).

Verifies the named-event SSE wire contract added to
``ragbot.interfaces.http._sse_helper.stream_real_llm`` and the
``first_token_ms`` capture path threaded through ``query_graph`` /
``step_tracker.set_metadata``.

Phase 1 mandate (Wave H ``plans/260520-WAVE-H-SPECULATIVE-STREAMING``):

* ``event: first_token`` — fires exactly once at the first non-empty token
* ``event: chunk`` — fires per token delta (replaces legacy ``type: token``)
* ``event: citations`` — fires after generation, before ``done``
* ``event: done`` — terminal event carries ``tokens`` + ``cost_usd`` +
  ``latency_ms`` + ``first_token_ms`` so dashboards can compute TTFT +
  per-turn cost client-side.
* ``request_steps.metadata_json.first_token_ms`` — populated by
  ``query_graph.generate`` via ``step_tracker.step('generate').set_metadata``.

REAL behavioural assertions (CLAUDE.md Quality Gate item 7).
"""

from __future__ import annotations

import asyncio
import json
import re
import time

import pytest

from ragbot.interfaces.http._sse_helper import (
    _STREAM_SENTINEL,
    _sse_named,
    stream_real_llm,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_EVENT_RE = re.compile(r"^event:\s*(?P<event>[\w-]+)\s*$")
_DATA_RE = re.compile(r"^data:\s*(?P<data>.*)$", re.DOTALL)


def _parse_named_frames(frames: list[str]) -> list[dict]:
    """Decode SSE frames into ``[{'event': str|None, 'data': dict}]``.

    Each frame is either ``event: NAME\\ndata: JSON\\n\\n`` (named) or
    ``data: JSON\\n\\n`` (legacy). Frames without an ``event:`` line land
    with ``event=None`` so tests can assert framing mode side-by-side.
    """
    parsed: list[dict] = []
    for frame in frames:
        event_name: str | None = None
        data_payload: dict | None = None
        for line in frame.split("\n"):
            if not line:
                continue
            m_e = _EVENT_RE.match(line)
            if m_e:
                event_name = m_e.group("event")
                continue
            m_d = _DATA_RE.match(line)
            if m_d:
                data_payload = json.loads(m_d.group("data"))
        if data_payload is not None:
            parsed.append({"event": event_name, "data": data_payload})
    return parsed


async def _run_helper(
    deltas: list,
    state_holder: dict,
    *,
    named_events: bool,
    telemetry_extra: dict | None = None,
) -> list[str]:
    """Drive ``stream_real_llm`` with a synthesised producer + collect frames."""
    sink: asyncio.Queue = asyncio.Queue()

    async def _producer() -> None:
        for d in deltas:
            await sink.put(d)
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_a, **_kw) -> None:
        return None

    frames: list[str] = []
    async for frame in stream_real_llm(
        sink,
        task,
        state_holder,
        time.perf_counter(),
        _noop,
        named_events=named_events,
        telemetry_extra=telemetry_extra,
    ):
        frames.append(frame)
    await task
    return frames


# ---------------------------------------------------------------------------
# 1. Named-event framing — W3C EventSource compliance
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_named_events_emit_status_first_token_chunk_citations_done() -> None:
    """All five Phase 1 event types fire in order on a happy stream."""
    state_holder = {
        "state": {
            "answer": "Xin chào.",
            "answer_type": "answered",
            "answer_reason": "generated",
            "tokens": {"prompt": 11, "completion": 3, "cached": 0},
            "cost_usd": 0.0123,
            "citations": [{"chunk_id": "c1", "score": 0.91}],
        },
        "error": None,
        "sources": [{"document_name": "d.txt"}],
    }
    frames = await _run_helper(
        ["Xin ", "chào", "."],
        state_holder,
        named_events=True,
    )
    parsed = _parse_named_frames(frames)
    events = [p["event"] for p in parsed]
    # Ordering: status → first_token → chunk×3 → citations → done.
    assert events[0] == "status", events
    assert events[1] == "first_token", events
    assert events.count("chunk") == 3, events
    assert "citations" in events, events
    assert events[-1] == "done", events
    # citations precedes done
    assert events.index("citations") < events.index("done")
    # first_token precedes any chunk
    assert events.index("first_token") < events.index("chunk")


@pytest.mark.asyncio
async def test_named_events_chunk_payload_carries_content_only() -> None:
    """``chunk`` event payload = ``{"content": "..."}`` — no type discriminator
    (the event-name carries the type), and the byte content is verbatim."""
    state_holder = {
        "state": {
            "answer": "ABCDE",
            "answer_type": "answered",
            "tokens": {},
        },
        "error": None,
        "sources": [],
    }
    frames = await _run_helper(
        ["A", "B", "CDE"],
        state_holder,
        named_events=True,
    )
    parsed = _parse_named_frames(frames)
    chunks = [p["data"] for p in parsed if p["event"] == "chunk"]
    assert [c["content"] for c in chunks] == ["A", "B", "CDE"]
    # No legacy "type" key on chunk payloads.
    for c in chunks:
        assert "type" not in c, c


@pytest.mark.asyncio
async def test_named_events_first_token_event_fires_once_with_ms_field() -> None:
    """``first_token`` fires exactly once and carries ``first_token_ms: int``."""
    state_holder = {
        "state": {"answer": "ok", "answer_type": "answered", "tokens": {}},
        "error": None,
        "sources": [],
    }
    frames = await _run_helper(
        ["t1", "t2", "t3"],
        state_holder,
        named_events=True,
    )
    parsed = _parse_named_frames(frames)
    first_token_events = [p for p in parsed if p["event"] == "first_token"]
    assert len(first_token_events) == 1, first_token_events
    payload = first_token_events[0]["data"]
    assert "first_token_ms" in payload
    assert isinstance(payload["first_token_ms"], int)
    assert payload["first_token_ms"] >= 0


@pytest.mark.asyncio
async def test_named_events_first_token_skipped_on_zero_delta_stream() -> None:
    """Refuse-short-circuit / pipeline error → no token deltas →
    ``first_token`` event MUST NOT fire (would mislead TTFT SLA)."""
    state_holder = {
        "state": {"answer": "", "answer_type": "no_context", "tokens": {}},
        "error": "RefusalShortCircuit",
        "sources": [],
    }
    frames = await _run_helper([], state_holder, named_events=True)
    parsed = _parse_named_frames(frames)
    events = [p["event"] for p in parsed]
    assert "first_token" not in events, events
    # `done` still fires with first_token_ms=None so dashboards see the miss.
    done = next(p["data"] for p in parsed if p["event"] == "done")
    assert done["first_token_ms"] is None


@pytest.mark.asyncio
async def test_named_events_done_carries_tokens_cost_latency() -> None:
    """``done`` payload includes prompt/completion/cached tokens, cost_usd,
    latency_ms (alias duration_ms), and first_token_ms — the wire bundle
    dashboards parse to compute per-turn SLA."""
    state_holder = {
        "state": {
            "answer": "Em chào.",
            "answer_type": "answered",
            "tokens": {"prompt": 200, "completion": 7, "cached": 50},
            "cost_usd": 0.0042,
            "citations": [],
        },
        "error": None,
        "sources": [{"document_name": "src.md"}],
    }
    frames = await _run_helper(
        ["Em ", "chào", "."],
        state_holder,
        named_events=True,
    )
    parsed = _parse_named_frames(frames)
    done = next(p["data"] for p in parsed if p["event"] == "done")

    assert done["tokens"] == {"prompt": 200, "completion": 7, "cached": 50}
    assert done["cost_usd"] == pytest.approx(0.0042)
    assert isinstance(done["latency_ms"], int)
    assert done["latency_ms"] >= 0
    assert done["duration_ms"] == done["latency_ms"]  # legacy alias
    assert isinstance(done["first_token_ms"], int)
    assert done["sources"] == [{"document_name": "src.md"}]
    assert done["answer"] == "Em chào."


@pytest.mark.asyncio
async def test_named_events_citations_event_carries_state_citations() -> None:
    """``citations`` event surfaces ``final_state['citations']`` verbatim."""
    cits = [
        {"chunk_id": "a-1", "score": 0.99, "quote": "Q1"},
        {"chunk_id": "b-2", "score": 0.81},
    ]
    state_holder = {
        "state": {
            "answer": "ok",
            "answer_type": "answered",
            "tokens": {},
            "citations": cits,
        },
        "error": None,
        "sources": [],
    }
    frames = await _run_helper(["ok"], state_holder, named_events=True)
    parsed = _parse_named_frames(frames)
    cit_event = next(p["data"] for p in parsed if p["event"] == "citations")
    assert cit_event["citations"] == cits


@pytest.mark.asyncio
async def test_named_events_citations_empty_when_state_missing() -> None:
    """Refuse / no_context → citations event still fires with an empty list
    so the wire-contract stays predictable (clients never see a missing
    event when an answered turn produced no footnotes)."""
    state_holder = {
        "state": {"answer": "", "answer_type": "no_context", "tokens": {}},
        "error": None,
        "sources": [],
    }
    frames = await _run_helper([], state_holder, named_events=True)
    parsed = _parse_named_frames(frames)
    cit_events = [p for p in parsed if p["event"] == "citations"]
    assert len(cit_events) == 1
    assert cit_events[0]["data"]["citations"] == []


@pytest.mark.asyncio
async def test_named_events_replace_uses_named_frame_when_canonical_differs() -> None:
    """Post-validation rewrite → ``replace`` event under named framing."""
    state_holder = {
        "state": {
            "answer": "Câu trả lời đã kiểm duyệt.",
            "answer_type": "answered",
            "answer_reason": "guardrail_rewrite",
            "tokens": {},
            "citations": [],
        },
        "error": None,
        "sources": [],
    }
    frames = await _run_helper(
        ["raw "], state_holder, named_events=True,
    )
    parsed = _parse_named_frames(frames)
    repl = [p for p in parsed if p["event"] == "replace"]
    assert len(repl) == 1
    assert repl[0]["data"]["answer"] == "Câu trả lời đã kiểm duyệt."
    assert repl[0]["data"]["reason"] == "guardrail_rewrite"


# ---------------------------------------------------------------------------
# 2. Backward compat — legacy framing still works
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_legacy_framing_still_uses_bare_data_lines() -> None:
    """``named_events=False`` (default) preserves the bare ``data:`` framing
    that production ``/chat/stream`` clients rely on — no W3C event names."""
    state_holder = {
        "state": {"answer": "ok", "answer_type": "answered", "tokens": {}},
        "error": None,
        "sources": [],
    }
    frames = await _run_helper(
        ["ok"], state_holder, named_events=False,
    )
    # Default: NO ``event:`` line on any frame; every line starts with ``data:``.
    for frame in frames:
        assert "event:" not in frame, f"legacy framing must not emit event: {frame!r}"
        first_line = frame.split("\n", 1)[0]
        assert first_line.startswith("data: "), frame


# ---------------------------------------------------------------------------
# 3. first_token_ms server-side capture — request_steps metadata path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_helper_stashes_first_token_ms_on_holder_for_producer_persistence() -> None:
    """Helper writes ``first_token_ms`` to ``final_state_holder`` after the
    stream drains. Producer (graph node) reads it post-stream and calls
    ``step_tracker.set_metadata(first_token_ms=…)`` to land in
    ``request_steps.metadata_json``."""
    state_holder = {
        "state": {"answer": "ok", "answer_type": "answered", "tokens": {}},
        "error": None,
        "sources": [],
    }
    await _run_helper(["delta-1", "delta-2"], state_holder, named_events=True)
    assert "first_token_ms" in state_holder
    assert isinstance(state_holder["first_token_ms"], int)
    assert state_holder["first_token_ms"] >= 0


@pytest.mark.asyncio
async def test_helper_stashes_none_when_zero_deltas() -> None:
    """When no token deltas arrive, ``first_token_ms`` stash = None so the
    metadata field reflects the miss instead of erroneous 0."""
    state_holder = {
        "state": {"answer": "", "answer_type": "no_context", "tokens": {}},
        "error": None,
        "sources": [],
    }
    await _run_helper([], state_holder, named_events=True)
    assert state_holder["first_token_ms"] is None


# ---------------------------------------------------------------------------
# 4. _sse_named primitive
# ---------------------------------------------------------------------------
def test_sse_named_frame_format_matches_w3c() -> None:
    """``_sse_named`` produces ``event: NAME\\ndata: JSON\\n\\n``."""
    frame = _sse_named("first_token", {"first_token_ms": 350})
    lines = frame.split("\n")
    assert lines[0] == "event: first_token", lines
    assert lines[1].startswith("data: "), lines
    payload = json.loads(lines[1][len("data: "):])
    assert payload == {"first_token_ms": 350}
    assert frame.endswith("\n\n"), frame


def test_sse_named_falls_back_to_bare_data_when_event_empty() -> None:
    """Empty ``event`` arg → legacy ``data:``-only frame; lets callers
    branch on the same helper without an inline conditional."""
    frame = _sse_named("", {"hello": "world"})
    assert "event:" not in frame, frame
    assert frame.startswith("data: "), frame


def test_sse_named_preserves_unicode_diacritics() -> None:
    """ensure_ascii=False — Vietnamese / CJK content must not be escaped."""
    frame = _sse_named("chunk", {"content": "Tài liệu — 文档"})
    assert "Tài liệu — 文档" in frame
    assert "\\u" not in frame


# ---------------------------------------------------------------------------
# 5. Application-MUST-NOT-inject / override (CLAUDE.md Quality Gate #10)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_named_events_helper_does_not_modify_token_content() -> None:
    """Streaming helper relays LLM deltas verbatim — Phase 1 keeps the same
    Quality Gate #10 promise across the named-event path."""
    deltas = ["Câu ", "trả lời ", "gốc."]
    state_holder = {
        "state": {
            "answer": "".join(deltas),
            "answer_type": "answered",
            "tokens": {},
            "citations": [],
        },
        "error": None,
        "sources": [],
    }
    frames = await _run_helper(deltas, state_holder, named_events=True)
    parsed = _parse_named_frames(frames)
    streamed = [p["data"]["content"] for p in parsed if p["event"] == "chunk"]
    assert streamed == deltas
    # No replace event when canonical matches streamed buffer.
    events = [p["event"] for p in parsed]
    assert "replace" not in events


# ---------------------------------------------------------------------------
# 6. Generate-node TTFT persistence — query_graph wires set_metadata
# ---------------------------------------------------------------------------
def test_query_graph_generate_persists_first_token_ms_to_step_metadata() -> None:
    """``generate()`` MUST mirror ``state['_stream_first_token_ms']`` onto
    ``step_tracker.step('generate').set_metadata(first_token_ms=…)`` so
    the value lands in ``request_steps.metadata_json`` (SLA gate)."""
    from pathlib import Path

    # The generate node body was lifted out of build_graph into
    # orchestration/nodes/generate.py (pure relocation); _invoke_llm_node
    # stays in query_graph. Scan both.
    _orch = Path("src/ragbot/orchestration")
    src = (
        (_orch / "query_graph.py").read_text(encoding="utf-8")
        + "\n"
        + (_orch / "nodes" / "generate.py").read_text(encoding="utf-8")
    )
    # Generate step must bind ctx (`as _gen_ctx`).
    assert 'step("generate", model_used=state.get("model_used")) as _gen_ctx' in src
    # _invoke_llm_node streaming branch must stash on state.
    assert 'state["_stream_first_token_ms"] = _first_token_ms' in src
    # Generate() reads it back and persists into step metadata.
    assert "_gen_ctx.set_metadata(first_token_ms=" in src


def test_test_chat_stream_route_uses_shared_helper_with_named_events() -> None:
    """``/test/chat/stream`` (Wave H Phase 1 demo) MUST delegate to
    ``_sse_helper.stream_real_llm`` with ``named_events=True``."""
    from pathlib import Path

    src = Path(
        "src/ragbot/interfaces/http/routes/test_chat/chat_routes.py",
    ).read_text(encoding="utf-8")
    assert "from ragbot.interfaces.http._sse_helper import" in src
    assert "_shared_stream_real_llm" in src
    assert "named_events=True" in src
    # Local duplicate _stream_real_llm must be gone — single source of truth.
    assert "async def _stream_real_llm(" not in src


def test_graph_state_declares_stream_first_token_ms_field() -> None:
    """``GraphState`` MUST declare ``_stream_first_token_ms`` so the
    TypedDict carries the TTFT-stash field across the pipeline."""
    from pathlib import Path

    src = Path("src/ragbot/orchestration/state.py").read_text(encoding="utf-8")
    assert "_stream_first_token_ms" in src
