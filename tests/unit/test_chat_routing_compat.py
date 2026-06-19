"""Routing-compat regression test for mega-sprint G27 (D3 retry).

Pre-fix: D3 original test asserted ``chat_async.router`` exposed POST at
``route.path == "" or "/"`` assuming D2 used ``APIRouter(prefix="/chat-async")``.
D2 actually uses ``APIRouter(tags=["test-async"])`` (no prefix) with
``@router.post("/chat-async")`` (full sub-path). D3 was reverted at
``8b85be5`` because of this contract drift.

Post-fix (this file): assertions match the ACTUAL repo convention:
  * All chat routers (`chat`, `chat_stream`, `chat_async`) declare
    ``APIRouter(tags=[...])`` with NO ``prefix=`` arg.
  * Prefix is applied centrally in ``router.py`` via ``include_router(
    ..., prefix=BASE)`` or ``include_router(..., prefix=f"{BASE}/test")``.
  * Inside the router file, ``@router.post("/<sub-path>")`` uses the
    full sub-path (e.g. ``/chat-async``, ``/chat/stream``, ``/chat``).

The test PROTECTS BACKWARDS COMPAT (G27): the sync path
``POST /api/ragbot/chat`` and the SSE streaming path
``POST /api/ragbot/chat/stream`` MUST coexist next to the new async path
``POST /api/ragbot/test/chat-async`` (G26) so existing clients keep
working.
"""
from __future__ import annotations

from fastapi.routing import APIRoute

from ragbot.interfaces.http.routes import chat, chat_async, chat_stream


# ---------- helpers ----------------------------------------------------


def _route_paths(router_module, method: str) -> set[str]:
    """Return the set of declared paths on ``router_module.router`` for ``method``."""
    return {
        r.path
        for r in router_module.router.routes
        if isinstance(r, APIRoute) and method.upper() in r.methods
    }


# ---------- sync chat (G27 backwards-compat preservation) --------------


def test_sync_chat_router_preserves_post_chat_path() -> None:
    """G27: POST /chat sync path MUST still exist (existing client compat)."""
    posts = _route_paths(chat, "POST")
    assert "/chat" in posts, (
        f"Sync POST /chat path missing from chat.router; got {posts!r}"
    )


def test_sync_chat_router_uses_no_internal_prefix() -> None:
    """Convention: chat.router carries no prefix; router.py applies BASE."""
    prefix = chat.router.prefix
    assert prefix == "", (
        f"chat.router.prefix must be empty (prefix applied in router.py), "
        f"got {prefix!r}"
    )


def test_chat_stream_router_preserves_sse_path() -> None:
    """G27: POST /chat/stream sync SSE path MUST still exist."""
    posts = _route_paths(chat_stream, "POST")
    assert "/chat/stream" in posts, (
        f"SSE POST /chat/stream missing from chat_stream.router; got {posts!r}"
    )


def test_chat_stream_router_uses_no_internal_prefix() -> None:
    """Convention: chat_stream.router carries no prefix; router.py applies BASE."""
    prefix = chat_stream.router.prefix
    assert prefix == "", (
        f"chat_stream.router.prefix must be empty, got {prefix!r}"
    )


# ---------- async chat (G26 new) ---------------------------------------


def test_chat_async_router_has_post_enqueue() -> None:
    """G26: POST /chat-async enqueues a job and returns job_id."""
    posts = _route_paths(chat_async, "POST")
    assert "/chat-async" in posts, (
        f"Async POST /chat-async missing from chat_async.router; got {posts!r}"
    )


def test_chat_async_router_has_get_polling() -> None:
    """G26: GET /chat-async/{job_id} returns status/result."""
    gets = _route_paths(chat_async, "GET")
    assert "/chat-async/{job_id}" in gets, (
        f"Async GET /chat-async/{{job_id}} missing from chat_async.router; "
        f"got {gets!r}"
    )


def test_chat_async_router_uses_no_internal_prefix() -> None:
    """Convention: chat_async.router carries no prefix; router.py applies BASE/test."""
    prefix = chat_async.router.prefix
    assert prefix == "", (
        f"chat_async.router.prefix must be empty (router.py applies prefix), "
        f"got {prefix!r}"
    )


# ---------- mounted full paths (verify router.py wiring) ---------------


def test_app_router_mounts_sync_async_streaming_at_distinct_paths() -> None:
    """All three chat flavors mount at distinct full paths (no collision).

    Verifies the central wiring in ``router.py``:
      * chat.router          -> prefix BASE        -> /api/ragbot/chat
      * chat_stream.router   -> prefix BASE        -> /api/ragbot/chat/stream
      * chat_async.router    -> prefix BASE/test   -> /api/ragbot/test/chat-async
    """
    from ragbot.config.settings import get_settings
    from ragbot.interfaces.http.router import router as app_router

    from tests.unit._helpers_routes import iter_leaf_routes

    base = get_settings().app.api_base_path
    # FastAPI lazy-composes include_router(...) as _IncludedRouter wrappers;
    # flatten to the real leaf routes the live app serves.
    full_paths: set[str] = {
        lr.path for lr in iter_leaf_routes(app_router.routes) if "POST" in lr.methods
    }

    assert f"{base}/chat" in full_paths, (
        f"Sync chat path missing; full POST paths: {sorted(full_paths)!r}"
    )
    assert f"{base}/chat/stream" in full_paths, (
        f"SSE stream path missing; full POST paths: {sorted(full_paths)!r}"
    )
    assert f"{base}/test/chat-async" in full_paths, (
        f"Async chat path missing; full POST paths: {sorted(full_paths)!r}"
    )


def test_app_router_async_polling_get_is_wired_at_full_path() -> None:
    """GET /api/ragbot/test/chat-async/{job_id} is reachable from app router."""
    from ragbot.config.settings import get_settings
    from ragbot.interfaces.http.router import router as app_router

    from tests.unit._helpers_routes import iter_leaf_routes

    base = get_settings().app.api_base_path
    full_get_paths: set[str] = {
        lr.path for lr in iter_leaf_routes(app_router.routes) if "GET" in lr.methods
    }

    assert f"{base}/test/chat-async/{{job_id}}" in full_get_paths, (
        f"Async polling GET path missing; full GET paths: "
        f"{sorted(full_get_paths)!r}"
    )
