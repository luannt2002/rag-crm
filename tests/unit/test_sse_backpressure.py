"""SSE producer→consumer backpressure (Hidden Bug Scan Round 2 — Bug 1 / P0).

Guards against the "unbounded ``asyncio.Queue()`` → OOM" regression: the
producer (LLM stream) must not buffer tokens unbounded when the SSE
consumer is slow / disconnected. Backpressure comes from the bounded
queue (``DEFAULT_SSE_SINK_MAXSIZE``) plus a hard producer timeout
(``DEFAULT_SSE_PRODUCER_TIMEOUT_S``) that cancels the stream rather
than blocking forever on a stuck consumer.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from ragbot.shared.constants import (
    DEFAULT_SSE_PRODUCER_TIMEOUT_S,
    DEFAULT_SSE_SINK_MAXSIZE,
)


# --- Constants sanity ------------------------------------------------------

def test_sse_constants_defined() -> None:
    """Constants exist and are positive — used by routes + producer guards."""
    assert isinstance(DEFAULT_SSE_SINK_MAXSIZE, int)
    assert DEFAULT_SSE_SINK_MAXSIZE > 0
    assert isinstance(DEFAULT_SSE_PRODUCER_TIMEOUT_S, float)
    assert DEFAULT_SSE_PRODUCER_TIMEOUT_S > 0.0


# --- Wiring: routes must use the constant, not bare ``asyncio.Queue()`` ---

_ROOT = Path(__file__).resolve().parents[2]
_CHAT_STREAM = _ROOT / "src/ragbot/interfaces/http/routes/chat_stream.py"
_TEST_CHAT = _ROOT / "src/ragbot/interfaces/http/routes/test_chat/chat_routes.py"


def test_sse_queue_uses_maxsize_constant_in_chat_stream() -> None:
    src = _CHAT_STREAM.read_text(encoding="utf-8")
    assert "asyncio.Queue(maxsize=DEFAULT_SSE_SINK_MAXSIZE)" in src, (
        "chat_stream.py must wire DEFAULT_SSE_SINK_MAXSIZE into asyncio.Queue"
    )
    assert "DEFAULT_SSE_SINK_MAXSIZE" in src


def test_sse_queue_uses_maxsize_constant_in_test_chat() -> None:
    src = _TEST_CHAT.read_text(encoding="utf-8")
    assert "asyncio.Queue(maxsize=DEFAULT_SSE_SINK_MAXSIZE)" in src, (
        "test_chat.py must wire DEFAULT_SSE_SINK_MAXSIZE into asyncio.Queue"
    )


def test_no_unbounded_queue_in_sse_routes() -> None:
    """Regex grep guard: no ``asyncio.Queue()`` without ``maxsize=`` in routes."""
    pattern = re.compile(r"asyncio\.Queue\s*\(\s*\)")
    for path in (_CHAT_STREAM, _TEST_CHAT):
        src = path.read_text(encoding="utf-8")
        assert not pattern.search(src), (
            f"{path.name} contains a no-maxsize asyncio.Queue() — fix to "
            f"asyncio.Queue(maxsize=DEFAULT_SSE_SINK_MAXSIZE)"
        )


# --- Behaviour: bounded queue blocks producer, timeout cancels stream -----

@pytest.mark.asyncio
async def test_bounded_queue_blocks_producer_when_full() -> None:
    """``put`` must block once ``maxsize`` deltas are buffered."""
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    await q.put("a")
    await q.put("b")
    # Third put should block since consumer hasn't drained.
    third = asyncio.create_task(q.put("c"))
    await asyncio.sleep(0)  # let the task start
    assert not third.done(), "producer must block on full queue"
    # Drain one → producer unblocks.
    assert q.get_nowait() == "a"
    await asyncio.wait_for(third, timeout=1.0)
    assert third.done()


@pytest.mark.asyncio
async def test_producer_timeout_raises_when_consumer_stuck() -> None:
    """``asyncio.wait_for(q.put, timeout)`` must raise on stuck consumer."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    await q.put("first")  # queue now full

    # Consumer never drains → put would block forever without timeout.
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(q.put("second"), timeout=0.05)


@pytest.mark.asyncio
async def test_producer_path_cancels_on_timeout() -> None:
    """Mirror the production producer path: timeout → CancelledError raised."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1)
    await q.put("first")

    async def _producer() -> None:
        try:
            await asyncio.wait_for(q.put("second"), timeout=0.05)
        except asyncio.TimeoutError as exc:
            raise asyncio.CancelledError("SSE consumer lagging > timeout") from exc

    with pytest.raises(asyncio.CancelledError):
        await _producer()


# --- Producer wiring in query_graph (token push site) ---------------------

def test_query_graph_producer_uses_timeout() -> None:
    """``query_graph._invoke_llm_node`` must wrap ``sink.put`` with timeout."""
    src = (
        _ROOT / "src/ragbot/orchestration/query_graph.py"
    ).read_text(encoding="utf-8")
    assert "DEFAULT_SSE_PRODUCER_TIMEOUT_S" in src, (
        "producer must import DEFAULT_SSE_PRODUCER_TIMEOUT_S"
    )
    # Look for ``asyncio.wait_for(sink.put(`` near the constant.
    assert (
        "asyncio.wait_for(\n                            sink.put(delta)"
        in src
        or "asyncio.wait_for(sink.put(delta)" in src
    ), "producer must wrap sink.put with asyncio.wait_for + timeout"
