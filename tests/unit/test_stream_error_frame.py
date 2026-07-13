"""#3 — a provider death mid-stream delivered a TRUNCATED answer with NO error signal.

Mechanism (verified in code): when ``graph_task`` raises (the upstream gateway
drops mid-generation), ``stream_real_llm`` logged the exception and stashed it on
``final_state_holder["error"]`` — and never told the client. Tokens were ALREADY
on the wire, so the terminal frame was a normal ``done`` with ``answer=""`` /
``answer_type="no_context"``. A client rendering incrementally showed the cut-off
text (possibly mid-number) and never learned the generation failed; no retry was
possible because the bytes were already sent.

Fix: emit an explicit ``error`` frame (and carry the reason on ``done``) so the
client can discard/flag the partial text instead of presenting it as the answer.
"""
from __future__ import annotations

import asyncio
import json

from ragbot.interfaces.http._sse_helper import _STREAM_SENTINEL, stream_real_llm


async def _drive(*, named_events: bool, boom: bool) -> list[str]:
    sink: asyncio.Queue = asyncio.Queue()
    await sink.put("Giá là 1.2")          # partial tokens ALREADY on the wire
    await sink.put("34")
    await sink.put(_STREAM_SENTINEL)

    async def _ok() -> None:
        return None

    async def _fail() -> None:
        raise RuntimeError("upstream gateway dropped mid-stream")

    graph_task = asyncio.create_task(_fail() if boom else _ok())
    holder: dict = {"state": None, "error": None, "sources": []}

    async def _on_complete(state: dict, answer: str, ms: int) -> None:
        return None

    frames: list[str] = []
    async for f in stream_real_llm(
        sink, graph_task, holder, 0.0, _on_complete, named_events=named_events,
    ):
        frames.append(f)
    return frames


def test_provider_death_emits_an_error_frame_named() -> None:
    frames = asyncio.run(_drive(named_events=True, boom=True))
    blob = "".join(frames)
    assert "event: error" in blob, (
        "provider died mid-stream but no error frame was emitted — the client "
        "silently renders the truncated answer as if it were complete"
    )


def test_error_frame_reports_the_partial_state() -> None:
    frames = asyncio.run(_drive(named_events=True, boom=True))
    err = next(f for f in frames if "event: error" in f)
    payload = json.loads(err.split("data: ", 1)[1].strip())
    assert payload["partial"] is True          # tokens already delivered
    assert payload["chars_streamed"] > 0
    assert "dropped mid-stream" in payload["error"]


def test_done_frame_carries_the_error_reason() -> None:
    """A client that only listens to ``done`` must still be able to tell."""
    frames = asyncio.run(_drive(named_events=True, boom=True))
    done = next(f for f in frames if "event: done" in f)
    payload = json.loads(done.split("data: ", 1)[1].strip())
    assert payload.get("error")


def test_legacy_framing_also_signals_the_error() -> None:
    frames = asyncio.run(_drive(named_events=False, boom=True))
    blob = "".join(frames)
    assert '"type": "error"' in blob or '"type":"error"' in blob


def test_clean_stream_emits_no_error_frame() -> None:
    """Zero-regression: a healthy stream must be byte-identical (no error frame)."""
    frames = asyncio.run(_drive(named_events=True, boom=False))
    blob = "".join(frames)
    assert "event: error" not in blob
    done = next(f for f in frames if "event: done" in f)
    payload = json.loads(done.split("data: ", 1)[1].strip())
    assert not payload.get("error")
