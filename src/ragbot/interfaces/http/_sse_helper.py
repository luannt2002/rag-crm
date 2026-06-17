"""Shared SSE streaming helpers for /chat/stream + /test/chat/stream.

Lifted from ``test_chat.py`` so the production endpoint and the demo route
share a single source of truth for SSE framing. Keeping this module thin —
no FastAPI / DI imports — so unit tests can drive it with plain queues.

Event format (legacy, ``named_events=False`` — bare ``data:`` lines)::

    data: {"type":"status","stage":"generating"}\n\n
    data: {"type":"token","content":"..."}\n\n
    data: {"type":"replace","answer":"...","reason":"..."}\n\n   # optional
    data: {"type":"done","answer":"...","sources":[...],"duration_ms":N}\n\n

Event format (Phase 1, ``named_events=True``) — named SSE event types per
W3C ``EventSource`` so clients can ``.addEventListener('first_token', ...)``::

    event: status\ndata: {"stage":"generating"}\n\n
    event: first_token\ndata: {"first_token_ms":N}\n\n              # one-shot
    event: chunk\ndata: {"content":"..."}\n\n                       # per delta
    event: citations\ndata: {"citations":[...]}\n\n                 # before done
    event: replace\ndata: {"answer":"...","reason":"..."}\n\n       # optional
    event: done\ndata: {"answer":"...","tokens":{...},"cost_usd":N,
                       "latency_ms":N,"first_token_ms":N|null}\n\n

The ``replace`` event fires when post-stream validation (math-lockdown,
guardrail rewrite) replaces the answer the client already saw — clients
should swap their UI buffer with the canonical text. The ``first_token``
event fires exactly once at the first non-empty token delivered to the
sink so clients can compute TTFT client-side and the helper publishes the
same value into ``final_state_holder['first_token_ms']`` for server-side
metadata persistence (``request_steps.metadata_json.first_token_ms``).
"""

from __future__ import annotations

import asyncio
import json as _json
import time
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator

import structlog

logger = structlog.get_logger(__name__)


# Sentinel pushed onto the streaming sink to signal the producer (graph
# generate node) is done emitting deltas. Singleton object; identity check.
_STREAM_SENTINEL: object = object()


def _sse(payload: dict[str, Any]) -> str:
    """Render a single SSE ``data:`` frame.

    Kept as a private helper so future heartbeat / event-name framing stays
    in one place. ``ensure_ascii=False`` so Vietnamese diacritics flow
    through unmangled.
    """
    return f"data: {_json.dumps(payload, ensure_ascii=False)}\n\n"


def _sse_named(event: str, payload: dict[str, Any]) -> str:
    """Render an SSE frame with a W3C ``event:`` name.

    Phase 1 framing: ``event: <name>\\ndata: <json>\\n\\n``. Clients use
    ``EventSource.addEventListener(event, ...)`` to route by event type
    rather than parsing a ``type`` discriminator inside the JSON.

    Empty / falsy ``event`` falls back to bare ``data:`` so callers can
    branch on the same helper.
    """
    if not event:
        return _sse(payload)
    return f"event: {event}\ndata: {_json.dumps(payload, ensure_ascii=False)}\n\n"


def replace_event(answer: str, reason: str = "post_validation") -> str:
    """Build a ``replace`` SSE frame (legacy bare-``data:`` framing).

    Used by the chat streaming endpoints when post-pipeline validation
    (math-lockdown, citation cleanup, output guardrail) has rewritten the
    answer text after streaming has already pushed the originals to the
    client. Clients swap their buffer with ``answer``.
    """
    return _sse({"type": "replace", "answer": answer, "reason": reason})


def redo_event(
    *,
    reason: str,
    overlap_pct: float,
    embedding_cosine: float,
) -> str:
    """Build a ``redo`` SSE frame for Speculative Streaming Phase 3.

    Fired when the HALLU verifier rejects the draft buffer (substring
    overlap below floor, numeric mismatch, or topic divergence). Clients
    discard the partially streamed draft and wait for the main stream
    to take over. Carries the verifier signals so the client UX can log
    why the redo happened (debug / observability only — no replace text
    yet; the main stream resumes via ``chunk`` events after this frame).
    """
    return _sse_named(
        "redo",
        {
            "reason": reason,
            "overlap_pct": overlap_pct,
            "embedding_cosine": embedding_cosine,
        },
    )


def verify_pass_event(
    *,
    overlap_pct: float,
    embedding_cosine: float,
) -> str:
    """Build a ``verify_pass`` SSE frame for Speculative Streaming Phase 3.

    Fired exactly once when the HALLU verifier accepts the draft buffer
    so the client knows the optimistic stream is now confirmed safe.
    Carries the same signals as ``redo`` so observability is symmetric.
    """
    return _sse_named(
        "verify_pass",
        {
            "overlap_pct": overlap_pct,
            "embedding_cosine": embedding_cosine,
        },
    )


async def stream_real_llm(
    sink: asyncio.Queue,
    graph_task: asyncio.Task,
    final_state_holder: dict,
    t0: float,
    on_complete: Callable[[dict, str, int], Awaitable[None]],
    *,
    telemetry_extra: dict[str, Any] | None = None,
    named_events: bool = False,
) -> AsyncIterator[str]:
    """Drain ``sink`` → emit SSE events; finalise via ``on_complete``.

    @param sink: producer pushes per-token deltas (str) and a final
        ``_STREAM_SENTINEL`` to signal completion.
    @param graph_task: the running pipeline; awaited after the sink drains
        so the final state is populated before the ``done`` event fires.
    @param final_state_holder: ``{"state": dict, "error": str|None,
        "sources": list}`` — populated by the graph_task wrapper. The
        helper additionally writes ``first_token_ms: int|None`` after the
        stream drains so the producer-side can persist into
        ``request_steps.metadata_json``.
    @param t0: ``time.perf_counter()`` recorded when the request started;
        used to compute ``duration_ms`` for the ``done`` event.
    @param on_complete: async callback to persist log / history. Runs after
        the graph completes regardless of success — failures are caught and
        logged so the stream still terminates cleanly.
    @param telemetry_extra: optional structured fields (request_id,
        record_tenant_id, record_bot_id, channel_type, feature_flag) merged
        into the ``stream_chunk_emit`` / ``streaming_response_completed``
        structlog events for cross-event correlation.
    @param named_events: when True, emit W3C-named SSE events
        (``event: first_token`` / ``event: chunk`` / ``event: citations`` /
        ``event: done``) instead of the legacy ``data:``-only frames. Wave
        H Phase 1 callers (``/test/chat/stream``) opt-in; the production
        ``/chat/stream`` route stays on the legacy framing until the
        client UI migrates to ``addEventListener``.

    Yields SSE-formatted strings the caller pipes through ``StreamingResponse``.
    """
    if named_events:
        yield _sse_named("status", {"stage": "generating"})
    else:
        yield _sse({"type": "status", "stage": "generating"})

    _tlm: dict[str, Any] = dict(telemetry_extra or {})
    streamed_chars: list[str] = []
    first_token_ms: int | None = None
    try:
        while True:
            item = await sink.get()
            if item is _STREAM_SENTINEL:
                break
            if not isinstance(item, str) or not item:
                continue
            if first_token_ms is None:
                # TTFT observability — fired exactly once per stream so the
                # event count == number of streams (cardinality stable).
                first_token_ms = int((time.perf_counter() - t0) * 1000)
                logger.info(
                    "stream_chunk_emit",
                    step_name="stream_chunk_emit",
                    first_token_ms=first_token_ms,
                    **_tlm,
                )
                if named_events:
                    # Phase 1: dedicated ``first_token`` event so clients
                    # can compute perceived-latency w/o parsing token deltas.
                    yield _sse_named(
                        "first_token",
                        {"first_token_ms": first_token_ms},
                    )
            streamed_chars.append(item)
            if named_events:
                yield _sse_named("chunk", {"content": item})
            else:
                yield _sse({"type": "token", "content": item})
    except Exception as exc:  # noqa: BLE001 — observability must not break the stream
        logger.warning("chat_stream_sink_drain_failed", error=str(exc))

    # Wait for the graph to finish (it may still be running guard_output /
    # reflect / persist after generate). Surface any pipeline error to the
    # client as a final event rather than aborting mid-stream.
    try:
        await graph_task
    except Exception as exc:  # noqa: BLE001
        logger.error("chat_stream_pipeline_failed", error=str(exc), exc_info=True)
        final_state_holder["error"] = f"{type(exc).__name__}: {exc}"

    # Stash TTFT for caller-side metadata persistence. Producer (graph
    # generate node) reads this off the holder after the SSE drain
    # completes and writes it into ``request_steps.metadata_json``.
    final_state_holder["first_token_ms"] = first_token_ms

    final_state = final_state_holder.get("state") or {}
    canonical_answer = final_state.get("answer", "") or ""
    streamed_answer = "".join(streamed_chars)
    answer_type = final_state.get(
        "answer_type",
        "answered" if canonical_answer else "no_context",
    )
    answer_reason = final_state.get("answer_reason")
    sources = final_state_holder.get("sources") or []
    citations = final_state.get("citations") or []
    duration_ms = int((time.perf_counter() - t0) * 1000)

    # Finalise hooks (log + history) — always best-effort.
    try:
        await on_complete(final_state, canonical_answer, duration_ms)
    except Exception as exc:  # noqa: BLE001
        logger.warning("chat_stream_finalize_hook_failed", error=str(exc))

    # If pipeline post-processing rewrote the answer (math-lockdown, citation
    # cleanup, guardrail) the streamed text no longer matches the canonical
    # state — emit a replace event so clients can correct UX.
    if canonical_answer and canonical_answer != streamed_answer:
        if named_events:
            yield _sse_named(
                "replace",
                {
                    "answer": canonical_answer,
                    "reason": answer_reason or "post_validation",
                },
            )
        else:
            yield replace_event(
                canonical_answer, answer_reason or "post_validation",
            )

    if named_events:
        # ``citations`` precedes ``done`` so a client can render footnotes
        # before the terminal event closes the stream. Citations may be
        # empty (no_context / refuse) — empty list still emitted so the
        # client wire contract stays predictable.
        yield _sse_named("citations", {"citations": list(citations)})
        # Phase 1 ``done`` consolidates wire-end metrics: token totals +
        # cost + latency + TTFT for SLA dashboards client-side.
        _llm_tokens = final_state.get("tokens") or {}
        yield _sse_named(
            "done",
            {
                "answer": canonical_answer,
                "answer_type": answer_type,
                "answer_reason": answer_reason,
                "sources": sources,
                "citations": list(citations),
                "tokens": {
                    "prompt": int(_llm_tokens.get("prompt", 0) or 0),
                    "completion": int(_llm_tokens.get("completion", 0) or 0),
                    "cached": int(_llm_tokens.get("cached", 0) or 0),
                },
                "cost_usd": float(final_state.get("cost_usd", 0.0) or 0.0),
                "latency_ms": duration_ms,
                "duration_ms": duration_ms,  # legacy alias
                "first_token_ms": first_token_ms,
            },
        )
    else:
        yield _sse({
            "type": "done",
            "answer": canonical_answer,
            "answer_type": answer_type,
            "answer_reason": answer_reason,
            "sources": sources,
            "duration_ms": duration_ms,
        })

    # Per spec (FILE-OWNERSHIP-MATRIX stream 2D): step_name="streaming_response"
    # logs first_token_ms + total_tokens for TTFT + completion-volume tracking.
    # Total tokens here is the count of streamed deltas (not LLM tokens — the
    # LLM token counts land in final_state.tokens). ``first_token_ms`` is None
    # when the stream produced zero deltas (refuse short-circuit / error).
    _final_state = final_state_holder.get("state") or {}
    _llm_tokens = _final_state.get("tokens") or {}
    logger.info(
        "streaming_response_completed",
        step_name="streaming_response",
        first_token_ms=first_token_ms,
        total_tokens=int(_llm_tokens.get("completion", 0) or 0),
        streamed_chunks=len(streamed_chars),
        duration_ms=duration_ms,
        answer_type=answer_type,
        had_error=bool(final_state_holder.get("error")),
        **_tlm,
    )


__all__ = [
    "_STREAM_SENTINEL",
    "_sse_named",
    "redo_event",
    "replace_event",
    "stream_real_llm",
    "verify_pass_event",
]
