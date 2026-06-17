"""LLM async queue consumer — Redis Stream ``chat.requested``.

Architecture
------------
The synchronous HTTP route holds a uvicorn worker for the entire pipeline
duration (typically 2-15s of LLM latency). At a default 4 uvicorn workers
this caps the API at ~0.4 RPS even though the box has spare CPU.

This worker consumes the ``chat.requested`` Redis Stream produced by the
async HTTP route (Wave-D / G26: ``POST /api/ragbot/test/chat-async``) and
writes the result to a per-job Redis hash that the caller polls. The HTTP
route returns ``job_id`` immediately, decoupling LLM latency from request
hold time. With N worker processes × M concurrency, total throughput
becomes N × M concurrent LLM calls regardless of uvicorn worker count.

Key invariants
--------------
* **No new LLM** — reuses the singleton compiled graph from
  ``ragbot.orchestration.query_graph.get_graph`` with the existing
  ``Container`` DI wiring. Model, API keys, providers all unchanged.
* **No app-side answer override** — graph output flows through to the
  result hash byte-for-byte (CLAUDE.md Quality Gate #10).
* **Graceful XACK** — only ack after the result hash is written, so a
  worker crash mid-pipeline leaves the message PEL-pending for another
  worker to XCLAIM.
* **No secrets in code** — Redis URL lifted from settings (env-driven).
* **No tenant literals / brand strings** — payload supplies all
  identifiers.

Run manually (no systemd install per project mandate)::

    .venv/bin/python -m scripts.chat_async_worker

Stop with SIGINT / SIGTERM. The block timeout caps shutdown latency.
"""
from __future__ import annotations

import asyncio
import os
import signal
from typing import Any
from uuid import UUID, uuid4

import structlog

from ragbot.bootstrap import Container
from ragbot.config.logging import setup_logging
from ragbot.config.settings import get_settings
from ragbot.orchestration.query_graph import get_graph
from ragbot.shared.constants import (
    CHAT_ASYNC_CONSUMER_GROUP,
    CHAT_ASYNC_RESULT_KEY_PREFIX,
    CHAT_ASYNC_STREAM,
    DEFAULT_CHAT_ASYNC_BATCH_COUNT,
    DEFAULT_CHAT_ASYNC_BLOCK_MS,
    DEFAULT_CHAT_ASYNC_ERROR_MAX_CHARS,
    DEFAULT_CHAT_ASYNC_RESULT_TTL_S,
)
from ragbot.shared.json_io import dumps as json_dumps, loads as json_loads

logger = structlog.get_logger(__name__)


def _coerce_uuid(value: Any) -> UUID | None:
    """Best-effort coerce payload field to UUID; return None on failure.

    The async HTTP route serialises ``record_tenant_id`` / ``record_bot_id``
    as JSON strings; the graph state schema expects UUID instances.
    """
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None


def _decode(field: Any) -> str:
    """Decode a Redis Stream field that may be bytes or str."""
    if isinstance(field, bytes):
        return field.decode()
    return str(field)


async def _build_initial_state(req: dict[str, Any]) -> dict[str, Any]:
    """Translate the API payload into a minimum viable ``GraphState`` dict.

    The async route is a thin pass-through: the payload already carries the
    resolved bot identity, conversation context, and pipeline_config bundle
    that the synchronous worker assembles. This worker is intentionally
    lighter than ``ragbot.interfaces.workers.chat_worker`` — that worker
    handles legacy ``chat.received.v1`` events, audit emission, callback
    delivery, etc. The async path is opt-in (Wave-D endpoint) and the
    route is responsible for shipping a graph-ready payload.
    """
    state: dict[str, Any] = {
        "record_tenant_id": _coerce_uuid(req.get("record_tenant_id")),
        "record_bot_id": _coerce_uuid(req.get("record_bot_id")),
        "request_id": req.get("request_id") or str(uuid4()),
        "message_id": req.get("message_id"),
        "conversation_id": _coerce_uuid(req.get("conversation_id")),
        "channel_type": req.get("channel_type", ""),
        "workspace_id": req.get("workspace_id", ""),
        "user_groups": req.get("user_groups") or [],
        "query": req.get("query", ""),
        "rewritten_query": None,
        "retrieved_chunks": [],
        "reranked_chunks": [],
        "graded_chunks": [],
        "answer": "",
        "citations": [],
        "guardrail_flags": [],
        "tokens": {"prompt": 0, "completion": 0},
        "cost_usd": 0.0,
        "model_used": "",
        "conversation_history": req.get("conversation_history") or [],
        "pipeline_config": req.get("pipeline_config") or {},
        "bot_system_prompt": req.get("bot_system_prompt", ""),
        "bot_extra_output_tokens_per_response": int(
            req.get("bot_extra_output_tokens_per_response", 0) or 0,
        ),
        "kg_service": None,
        "session_factory": None,
    }
    return state


async def _process_one(
    redis: Any,
    graph: Any,
    job_id: str,
    req: dict[str, Any],
    *,
    result_ttl_s: int,
) -> None:
    """Run one job through the graph and persist the result hash.

    The graph's answer / citations / token usage flows through verbatim —
    no app-side override (Quality Gate #10).
    """
    result_key = f"{CHAT_ASYNC_RESULT_KEY_PREFIX}{job_id}"
    try:
        initial_state = await _build_initial_state(req)
        recursion_limit = int(
            (initial_state.get("pipeline_config") or {}).get(
                "graph_recursion_limit", 0,
            ),
        )
        invoke_kwargs: dict[str, Any] = {}
        if recursion_limit > 0:
            invoke_kwargs["config"] = {"recursion_limit": recursion_limit}

        final_state = await graph.ainvoke(initial_state, **invoke_kwargs)

        answer = final_state.get("answer", "") or ""
        citations = final_state.get("citations") or []
        chunks_used = (
            len(final_state.get("graded_chunks") or [])
            or len(final_state.get("reranked_chunks") or [])
            or len(final_state.get("retrieved_chunks") or [])
        )
        duration_ms = int(final_state.get("duration_ms", 0) or 0)

        await redis.hset(
            result_key,
            mapping={
                "status": "done",
                "answer": answer,
                "citations": json_dumps(citations),
                "chunks_used": str(chunks_used),
                "duration_ms": str(duration_ms),
            },
        )
        await redis.expire(result_key, result_ttl_s)
        logger.info(
            "chat_async_job_done",
            job_id=job_id,
            chunks_used=chunks_used,
            duration_ms=duration_ms,
        )
    except Exception as exc:  # noqa: BLE001 — top-level worker handler
        logger.error(
            "chat_async_job_failed",
            job_id=job_id,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        try:
            await redis.hset(
                result_key,
                mapping={
                    "status": "error",
                    "error": str(exc)[:DEFAULT_CHAT_ASYNC_ERROR_MAX_CHARS],
                },
            )
            await redis.expire(result_key, result_ttl_s)
        except Exception:  # noqa: BLE001 — never let result-write hide root cause
            logger.error(
                "chat_async_result_write_failed",
                job_id=job_id,
                exc_info=True,
            )


async def _ensure_consumer_group(redis: Any, stream: str, group: str) -> None:
    """Idempotent consumer-group create; ignore BUSYGROUP."""
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
        logger.info("chat_async_group_created", stream=stream, group=group)
    except Exception as exc:  # noqa: BLE001 — narrow check on transport error msg
        # ``redis.exceptions.ResponseError`` is a generic transport error;
        # the only acceptable cause here is "group already exists".
        if "BUSYGROUP" not in str(exc):
            raise


async def consume_chat_requests(
    *,
    container: Container | None = None,
    stop_event: asyncio.Event | None = None,
    result_ttl_s: int = DEFAULT_CHAT_ASYNC_RESULT_TTL_S,
    block_ms: int = DEFAULT_CHAT_ASYNC_BLOCK_MS,
    batch_count: int = DEFAULT_CHAT_ASYNC_BATCH_COUNT,
) -> None:
    """Main consumer loop.

    Parameters are injectable so the integration test can drive a single
    iteration with a fake-redis + mocked graph without forking a process.
    """
    if container is None:
        container = Container()
    if stop_event is None:
        stop_event = asyncio.Event()

    redis = container.redis_client()
    graph = await get_graph(
        invocation_logger=container.invocation_logger(),
        guardrail=container.guardrail(),
        model_resolver=container.model_resolver(),
        llm=container.llm(),
        vector_store=container.vector_store(),
        lexical_retrieval=container.lexical_retrieval(),
        reranker=container.reranker(),
        reranker_resolver=container.reranker_resolver(),
        embedder=container.embedder(),
        semantic_cache=container.semantic_cache(),
        redis_client=redis,
        audit_logger=container.pipeline_audit_logger(),
        entity_extractor=container.entity_extractor(),
        metadata_filter_strategy=container.metadata_filter_strategy(),
        language_pack_service=container.language_pack_service(),
        corpus_version_service=container.corpus_version_service(),
        error_notify_hook=container.error_notify_hook(),
        understand_query_cache=container.understand_query_cache(),
    )

    consumer_name = f"worker-{os.getpid()}"
    await _ensure_consumer_group(
        redis, CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP,
    )

    logger.info(
        "chat_async_worker_started",
        stream=CHAT_ASYNC_STREAM,
        group=CHAT_ASYNC_CONSUMER_GROUP,
        consumer=consumer_name,
        result_ttl_s=result_ttl_s,
    )

    while not stop_event.is_set():
        try:
            msgs = await redis.xreadgroup(
                CHAT_ASYNC_CONSUMER_GROUP,
                consumer_name,
                {CHAT_ASYNC_STREAM: ">"},
                count=batch_count,
                block=block_ms,
            )
        except Exception:  # noqa: BLE001 — top-level loop; preserve uptime
            logger.error("chat_async_xreadgroup_failed", exc_info=True)
            await asyncio.sleep(1)
            continue

        if not msgs:
            continue

        for _stream_key, entries in msgs:
            for msg_id, fields in entries:
                # Field keys / values may be bytes (raw redis-py) or str
                # (decode_responses=True). Normalise both.
                fields_norm: dict[str, str] = {
                    _decode(k): _decode(v) for k, v in fields.items()
                }
                job_id = fields_norm.get("job_id", "")
                req_raw = fields_norm.get("req", "{}")
                if not job_id:
                    logger.warning(
                        "chat_async_msg_missing_job_id",
                        msg_id=_decode(msg_id),
                    )
                    await redis.xack(
                        CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP, msg_id,
                    )
                    continue
                try:
                    req = json_loads(req_raw)
                except (ValueError, TypeError):
                    logger.warning(
                        "chat_async_msg_malformed_req",
                        job_id=job_id,
                        msg_id=_decode(msg_id),
                    )
                    await redis.hset(
                        f"{CHAT_ASYNC_RESULT_KEY_PREFIX}{job_id}",
                        mapping={"status": "error", "error": "malformed_request"},
                    )
                    await redis.expire(
                        f"{CHAT_ASYNC_RESULT_KEY_PREFIX}{job_id}",
                        result_ttl_s,
                    )
                    await redis.xack(
                        CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP, msg_id,
                    )
                    continue

                await _process_one(
                    redis, graph, job_id, req, result_ttl_s=result_ttl_s,
                )
                # Ack only after result hash is written → crash mid-process
                # leaves message PEL-pending for XCLAIM recovery.
                await redis.xack(
                    CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP, msg_id,
                )

    logger.info("chat_async_worker_stopped")


async def main() -> None:
    settings = get_settings()
    setup_logging(level=settings.observability.log_level, json=True)
    container = Container()

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # Windows / restricted env — fall through to KeyboardInterrupt.
            pass

    await consume_chat_requests(container=container, stop_event=stop_event)


if __name__ == "__main__":
    asyncio.run(main())
