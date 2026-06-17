"""Integration test for ``scripts/chat_async_worker.py``.

End-to-end: push a message to ``chat.requested`` via fakeredis → worker
consumes via XREADGROUP → result lands in ``chat:result:{job_id}`` hash
within the configured TTL. The graph is mocked (no real LLM call) so the
test isolates worker plumbing — the LLM path is exercised by the
synchronous chat_worker tests.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import fakeredis.aioredis
import pytest

from ragbot.shared.constants import (
    CHAT_ASYNC_CONSUMER_GROUP,
    CHAT_ASYNC_RESULT_KEY_PREFIX,
    CHAT_ASYNC_STREAM,
)
from ragbot.shared.json_io import dumps as json_dumps, loads as json_loads
from scripts import chat_async_worker as worker_mod


class _StubContainer:
    """Minimal stand-in for ``ragbot.bootstrap.Container``.

    Only ``redis_client()`` is consulted by the test path because we patch
    ``scripts.chat_async_worker.get_graph`` to return a mock graph; the
    rest of the container providers are never called.
    """

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    def redis_client(self) -> Any:
        return self._redis

    # All other providers funnel into get_graph(...) which is patched.
    def __getattr__(self, name: str) -> Any:  # pragma: no cover - defensive
        return lambda *a, **kw: None


async def _drive_one_iteration(redis: Any, mock_graph: Any) -> None:
    """Run the consumer loop just long enough to drain a single message.

    The ``stop_event`` is set in a helper task right after the worker
    publishes its 'started' line so the next ``XREADGROUP`` returns within
    the small ``block_ms`` window and the loop exits cleanly.
    """
    container = _StubContainer(redis)
    stop = asyncio.Event()

    async def _stopper() -> None:
        # Allow the worker enough wall-clock to: ensure group, XREADGROUP,
        # process one msg, write hash, XACK. fakeredis is in-process so
        # this is well under a second in practice.
        await asyncio.sleep(0.5)
        stop.set()

    with patch.object(worker_mod, "get_graph", AsyncMock(return_value=mock_graph)):
        stopper_task = asyncio.create_task(_stopper())
        try:
            await worker_mod.consume_chat_requests(
                container=container,  # type: ignore[arg-type]
                stop_event=stop,
                result_ttl_s=60,
                block_ms=100,
                batch_count=1,
            )
        finally:
            stopper_task.cancel()
            try:
                await stopper_task
            except asyncio.CancelledError:
                pass


@pytest.mark.asyncio
async def test_worker_consumes_stream_and_writes_result_hash() -> None:
    """Push a job → worker XREADGROUPs → result hash populated."""
    redis = fakeredis.aioredis.FakeRedis()

    job_id = "job-" + uuid4().hex[:8]
    record_tenant_id = uuid4()
    record_bot_id = uuid4()
    request_payload = {
        "record_tenant_id": str(record_tenant_id),
        "record_bot_id": str(record_bot_id),
        "channel_type": "web",
        "workspace_id": "ws-test",
        "query": "hello",
        "pipeline_config": {"graph_recursion_limit": 25},
        "bot_system_prompt": "",
    }

    # Pre-populate the stream BEFORE the worker starts so the first
    # XREADGROUP returns immediately.
    await redis.xadd(
        CHAT_ASYNC_STREAM,
        {"job_id": job_id, "req": json_dumps(request_payload)},
    )

    # Mock graph returns a final state mimicking the LangGraph output shape.
    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "hi there",
        "citations": [{"chunk_id": "c1", "snippet": "snip"}],
        "graded_chunks": [{"id": "c1"}, {"id": "c2"}],
        "duration_ms": 42,
    })

    await _drive_one_iteration(redis, mock_graph)

    # Result hash exists, status=done, answer flows through verbatim.
    result_key = f"{CHAT_ASYNC_RESULT_KEY_PREFIX}{job_id}"
    raw = await redis.hgetall(result_key)
    assert raw, "worker did not write result hash"

    decoded = {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }
    assert decoded["status"] == "done"
    assert decoded["answer"] == "hi there"
    assert decoded["chunks_used"] == "2"
    assert decoded["duration_ms"] == "42"
    assert json_loads(decoded["citations"]) == [
        {"chunk_id": "c1", "snippet": "snip"},
    ]

    # Graph was invoked exactly once with state carrying the request fields.
    assert mock_graph.ainvoke.await_count == 1
    state_arg = mock_graph.ainvoke.await_args.args[0]
    assert state_arg["record_tenant_id"] == record_tenant_id
    assert state_arg["record_bot_id"] == record_bot_id
    assert state_arg["channel_type"] == "web"
    assert state_arg["workspace_id"] == "ws-test"
    assert state_arg["query"] == "hello"

    # TTL set on the result hash.
    ttl = await redis.ttl(result_key)
    assert 0 < ttl <= 60

    # Message acknowledged → consumer group has zero pending. ``XPENDING``
    # summary form: redis-py returns a 4-tuple (count, min, max, consumers);
    # fakeredis returns a dict with the same keys. Accept both shapes.
    pending = await redis.xpending(
        CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP,
    )
    pending_count = (
        pending["pending"] if isinstance(pending, dict) else pending[0]
    )
    assert pending_count == 0


@pytest.mark.asyncio
async def test_worker_writes_error_status_on_graph_exception() -> None:
    """Graph raises → result hash status=error, exception message captured."""
    redis = fakeredis.aioredis.FakeRedis()

    job_id = "job-err-" + uuid4().hex[:8]
    await redis.xadd(
        CHAT_ASYNC_STREAM,
        {"job_id": job_id, "req": json_dumps({"query": "x"})},
    )

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("synthetic boom"))

    await _drive_one_iteration(redis, mock_graph)

    raw = await redis.hgetall(f"{CHAT_ASYNC_RESULT_KEY_PREFIX}{job_id}")
    decoded = {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }
    assert decoded["status"] == "error"
    assert "synthetic boom" in decoded["error"]

    # Even on exception the message is XACK'd → no PEL leak (the worker
    # has already persisted the failure to the result hash; retry would be
    # caller-driven via a fresh job_id). Accept dict (fakeredis) or tuple
    # (redis-py) summary shape.
    pending = await redis.xpending(
        CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP,
    )
    pending_count = (
        pending["pending"] if isinstance(pending, dict) else pending[0]
    )
    assert pending_count == 0


@pytest.mark.asyncio
async def test_worker_handles_malformed_json_payload() -> None:
    """Bad JSON in ``req`` field → status=error('malformed_request') + XACK."""
    redis = fakeredis.aioredis.FakeRedis()

    job_id = "job-bad-" + uuid4().hex[:8]
    await redis.xadd(
        CHAT_ASYNC_STREAM,
        {"job_id": job_id, "req": "this is not json {"},
    )

    mock_graph = AsyncMock()
    mock_graph.ainvoke = AsyncMock(return_value={"answer": "should not run"})

    await _drive_one_iteration(redis, mock_graph)

    raw = await redis.hgetall(f"{CHAT_ASYNC_RESULT_KEY_PREFIX}{job_id}")
    decoded = {
        (k.decode() if isinstance(k, bytes) else k):
        (v.decode() if isinstance(v, bytes) else v)
        for k, v in raw.items()
    }
    assert decoded["status"] == "error"
    assert decoded["error"] == "malformed_request"
    # Graph never invoked for malformed payload.
    assert mock_graph.ainvoke.await_count == 0


@pytest.mark.asyncio
async def test_consumer_group_is_idempotent_on_restart() -> None:
    """Second worker start with existing group does not raise BUSYGROUP."""
    redis = fakeredis.aioredis.FakeRedis()

    # Pre-create the group so the first call hits the BUSYGROUP path.
    await redis.xadd(CHAT_ASYNC_STREAM, {"_seed": "1"})
    await redis.xgroup_create(
        CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP, id="0", mkstream=True,
    )

    # Should not raise.
    await worker_mod._ensure_consumer_group(
        redis, CHAT_ASYNC_STREAM, CHAT_ASYNC_CONSUMER_GROUP,
    )
