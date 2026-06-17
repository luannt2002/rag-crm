"""B.6 — production /chat/stream endpoint integration tests.

These exercise the SSE helper directly (no FastAPI/TestClient bootstrap) plus
the route module's RBAC + import-shape contract. We deliberately avoid the
full TestClient path because the streaming pipeline pulls in DB / Redis /
LLM stack — out of scope for unit-level CI. End-to-end smoke is a separate
``curl`` step in the deploy validation block.

Coverage:
- ``stream_real_llm`` emits at least one ``data: {"type":"token"...}`` event
  before completion (TTFT smoke; mock yields one delta).
- ``stream_real_llm`` emits a ``replace`` event when the canonical answer
  differs from streamed buffer (math-lockdown / guardrail rewrite path).
- ``stream_real_llm`` emits ``done`` and runs ``on_complete`` even when the
  pipeline fails — no event-loss on error.
- The ``/chat/stream`` route declares the RBAC permission dependency
  ``chat:stream`` (level 50 — service token same as ``chat:submit``).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from ragbot.interfaces.http._sse_helper import (
    _STREAM_SENTINEL,
    replace_event,
    stream_real_llm,
)


def _parse_sse_data_lines(frames: list[str]) -> list[dict]:
    """Return parsed JSON payloads of every ``data: ...`` SSE line."""
    out: list[dict] = []
    for chunk in frames:
        for line in chunk.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: "):]))
    return out


@pytest.mark.asyncio
async def test_stream_emits_token_before_done() -> None:
    """TTFT smoke — one delta yields one token event, then sentinel→done."""
    sink: asyncio.Queue = asyncio.Queue()
    final_state_holder: dict = {
        "state": {"answer": "Em chào", "answer_type": "answered"},
        "error": None,
        "sources": [{"document_name": "doc.csv"}],
    }

    async def _producer() -> None:
        await sink.put("Em ")
        await sink.put("chào")
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    on_complete_called: list[bool] = []

    async def _on_complete(state, answer, dur_ms):
        on_complete_called.append(True)

    frames: list[str] = []
    async for frame in stream_real_llm(
        sink, task, final_state_holder, time.perf_counter(), _on_complete,
    ):
        frames.append(frame)
    await task

    payloads = _parse_sse_data_lines(frames)
    types = [p["type"] for p in payloads]

    # status → token(s) → done
    assert types[0] == "status"
    assert "token" in types, f"no token event emitted, got {types}"
    assert types[-1] == "done"
    assert payloads[-1]["answer"] == "Em chào"
    assert payloads[-1]["answer_type"] == "answered"
    assert on_complete_called == [True]


@pytest.mark.asyncio
async def test_stream_emits_replace_event_on_post_validation_mutation() -> None:
    """Math-lockdown / guardrail path: streamed buffer ≠ canonical answer."""
    sink: asyncio.Queue = asyncio.Queue()
    # Streamed deltas spell out an ungrounded number; the canonical answer
    # has been rewritten by post-validation to remove it.
    final_state_holder: dict = {
        "state": {
            "answer": "Giá theo bảng dịch vụ.",
            "answer_type": "answered",
            "answer_reason": "math_lockdown",
        },
        "error": None,
        "sources": [],
    }

    async def _producer() -> None:
        await sink.put("Giá ")
        await sink.put("1.234.567 đ")
        await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_args, **_kw):
        return None

    frames: list[str] = []
    async for frame in stream_real_llm(
        sink, task, final_state_holder, time.perf_counter(), _noop,
    ):
        frames.append(frame)
    await task

    payloads = _parse_sse_data_lines(frames)
    types = [p["type"] for p in payloads]

    assert "replace" in types, f"replace event missing — got {types}"
    replace = next(p for p in payloads if p["type"] == "replace")
    assert replace["answer"] == "Giá theo bảng dịch vụ."
    assert replace["reason"] == "math_lockdown"


@pytest.mark.asyncio
async def test_stream_emits_done_even_on_pipeline_error() -> None:
    """Producer raises → sentinel still pushed → done event still fires."""
    sink: asyncio.Queue = asyncio.Queue()
    final_state_holder: dict = {"state": None, "error": None, "sources": []}

    async def _producer() -> None:
        try:
            raise RuntimeError("boom")
        finally:
            await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_args, **_kw):
        return None

    frames: list[str] = []
    async for frame in stream_real_llm(
        sink, task, final_state_holder, time.perf_counter(), _noop,
    ):
        frames.append(frame)

    payloads = _parse_sse_data_lines(frames)
    types = [p["type"] for p in payloads]
    assert types[-1] == "done"
    # Pipeline error captured, no answer to stream.
    assert payloads[-1]["answer"] == ""
    assert payloads[-1]["answer_type"] == "no_context"
    assert "boom" in (final_state_holder.get("error") or "")


def test_replace_event_emits_well_formed_sse() -> None:
    frame = replace_event("Hello", reason="post_validation")
    assert frame.startswith("data: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame[len("data: "):].strip())
    assert payload == {"type": "replace", "answer": "Hello", "reason": "post_validation"}


def test_chat_stream_route_declares_rbac_permission() -> None:
    """Route module wires ``require_permission_dep('chat', 'stream')``."""
    from ragbot.interfaces.http.routes import chat_stream

    deps_seen = []
    for r in chat_stream.router.routes:
        for dep in getattr(r, "dependencies", ()) or ():
            fn = getattr(dep, "dependency", None)
            name = getattr(fn, "__name__", "")
            deps_seen.append(name)
    assert "require_chat_stream" in deps_seen, (
        f"chat_stream route missing chat:stream RBAC gate, deps={deps_seen}"
    )


def test_chat_stream_route_no_hardcoded_role_strings() -> None:
    """Domain-neutral: no inline role literals in production stream route."""
    from ragbot.interfaces.http.routes import chat_stream

    src = Path(chat_stream.__file__).read_text()
    for role in (
        "\"admin\"", "\"super_admin\"", "\"tenant\"",
        "\"operator\"", "\"viewer\"", "\"guest\"", "\"user\"",
    ):
        assert role not in src, (
            f"hardcoded role literal {role} in chat_stream.py"
        )


def test_seed_rbac_s12a_includes_chat_stream() -> None:
    """Seed script registers chat:stream at level 50 (service)."""
    from scripts.seed_rbac_permissions_s12a import CHAT_STREAM_PERMISSIONS

    perms = {(m, p): lvl for (m, p, lvl) in CHAT_STREAM_PERMISSIONS}
    assert ("chat", "stream") in perms
    assert perms[("chat", "stream")] == 50  # mirrors chat:submit service-token gate


# === P2-2 — broad-except narrowing ==========================================
def test_chat_stream_uses_narrow_db_exception_handlers() -> None:
    """Source-level audit: best-effort DB ops catch ``SQLAlchemyError``,
    not bare ``Exception``.

    Three call-sites must use narrow catches to avoid silently swallowing
    real bugs (per P20 audit rule line 4): history load, log create, log
    finalize, history save. The pipeline-execution catch in ``_run_graph``
    intentionally stays broad (any of ~20 plugin nodes can raise) but must
    use ``logger.exception`` so the traceback is preserved.
    """
    from ragbot.interfaces.http.routes import chat_stream

    src = Path(chat_stream.__file__).read_text()
    assert "from sqlalchemy.exc import SQLAlchemyError" in src
    assert "from redis.exceptions import RedisError" in src
    # The narrow catches we expect (must be present after fix).
    assert "except SQLAlchemyError as exc:" in src, (
        "history load / log create / log finalize must use narrow DB catch"
    )
    assert "except (SQLAlchemyError, RedisError) as exc:" in src, (
        "history save must catch the specific DB+cache exception types"
    )
    # The remaining broad catches must always pair with `logger.exception`
    # so failure traces are preserved (no silent swallow).
    bare_warning_swallows = src.count(
        "except Exception as exc:  # noqa: BLE001\n        logger.warning"
    )
    assert bare_warning_swallows == 0, (
        "no bare 'except Exception → logger.warning' swallow allowed"
    )


@pytest.mark.asyncio
async def test_chat_stream_pipeline_error_bubbles_via_holder() -> None:
    """Verify the ``_run_graph`` broad catch still records the error and
    re-raises so the SSE helper emits the failure path. The narrowing
    refactor must not turn the user-visible pipeline failure into a silent
    swallow (P20 rule).
    """
    sink: asyncio.Queue = asyncio.Queue()
    final_state_holder: dict = {"state": None, "error": None, "sources": []}

    captured: list[BaseException] = []

    async def _producer() -> None:
        try:
            raise ValueError("graph node X exploded")
        except ValueError as exc:
            # This mirrors the narrowed handler shape: capture into the
            # holder, then re-raise so the SSE helper sees task.exception.
            final_state_holder["error"] = f"{type(exc).__name__}: {exc}"
            captured.append(exc)
            raise
        finally:
            await sink.put(_STREAM_SENTINEL)

    task = asyncio.create_task(_producer())

    async def _noop(*_args, **_kw):
        return None

    frames: list[str] = []
    async for frame in stream_real_llm(
        sink, task, final_state_holder, time.perf_counter(), _noop,
    ):
        frames.append(frame)

    assert captured, "exception did not bubble through producer"
    assert "ValueError: graph node X exploded" in (
        final_state_holder.get("error") or ""
    )
    payloads = _parse_sse_data_lines(frames)
    types = [p["type"] for p in payloads]
    assert types[-1] == "done", f"expected done event after error, got {types}"


def test_chat_stream_uses_preview_char_constants() -> None:
    """P2-3 — magic ``[:200]`` / ``[:100]`` must come from constants.

    G15 dropped the log-preview constant: request_chunk_refs (alembic 0109)
    no longer stores a chunk preview, so ``DEFAULT_LOG_PREVIEW_CHARS`` is
    no longer referenced from chat_stream. The source-preview projection
    (UI-facing) still needs ``DEFAULT_SOURCE_PREVIEW_CHARS``.
    """
    from ragbot.interfaces.http.routes import chat_stream

    src = Path(chat_stream.__file__).read_text()
    assert "DEFAULT_SOURCE_PREVIEW_CHARS" in src
    # No raw slices remaining in the route module.
    assert "[:200]" not in src, "raw [:200] slice still present in chat_stream"
    assert "[:100]" not in src, "raw [:100] slice still present in chat_stream"
