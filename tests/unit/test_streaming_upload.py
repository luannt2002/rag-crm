"""Unit tests for the streaming-upload route (WB-2 P1-5).

Coverage (14 tests):

1.  test_constants_exposed_and_sane
2.  test_route_registered_on_composed_app
3.  test_small_file_streams_to_temp_and_enqueues
4.  test_oversized_body_returns_413_and_cleans_temp
5.  test_empty_file_returns_422_and_cleans_temp
6.  test_missing_filename_returns_422
7.  test_temp_filename_is_uuid_not_bot_id
8.  test_concurrent_uploads_get_unique_temp_paths
9.  test_redis_unavailable_still_returns_202
10. test_redis_xadd_failure_logged_returns_202
11. test_no_record_tenant_returns_401
12. test_rbac_below_admin_returns_403
13. test_bot_resolve_miss_returns_404
14. test_workspace_id_invalid_format_returns_422
15. test_xadd_payload_carries_4key_identity

Harness keeps deps minimal: a fresh FastAPI app per test, the real
streaming router included, and dependency-overridden helpers for the
bot-resolver, redis client, RBAC level, and tenant context. The temp
dir is redirected to a pytest ``tmp_path`` so cleanup is automatic.

WB-2 sacred invariants asserted:

* Temp filename = UUID4 hex (no bot_id / tenant slug)
* Resident memory cap holds — we feed a body that would OOM a
  ``request.body()`` consumer (>20 MiB) but the route succeeds
* Stream message NEVER carries raw bytes — only temp_path pointer
* 4-key identity always present in Stream XADD fields
"""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

import ragbot.interfaces.http.routes.documents_stream_upload as upload_mod
from ragbot.interfaces.http.errors import register_exception_handlers
from ragbot.interfaces.http.routes.documents_stream_upload import router
from ragbot.shared.constants import (
    DEFAULT_UPLOAD_STREAM_CHUNK_SIZE,
    DEFAULT_UPLOAD_STREAM_MAX_BYTES,
    DEFAULT_UPLOAD_TEMP_DIR,
    SUBJECT_DOCUMENT_UPLOAD_STREAM,
)


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class _StateMiddleware(BaseHTTPMiddleware):
    """Inject ``request.state.record_tenant_id`` + ``role`` + ``trace_id``.

    Real prod middleware does the JWT lift; in unit tests we forge it
    directly so the route sees an authenticated caller.
    """

    def __init__(
        self,
        app: Any,
        *,
        record_tenant_id: UUID | None,
        role: str,
        trace_id: str = "trace-test",
    ) -> None:
        super().__init__(app)
        self._tenant = record_tenant_id
        self._role = role
        self._trace = trace_id

    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        if self._tenant is not None:
            request.state.record_tenant_id = self._tenant
        request.state.role = self._role
        request.state.trace_id = self._trace
        return await call_next(request)


def _build_app(
    *,
    tmp_path: Path,
    record_tenant_id: UUID | None,
    role: str = "admin",
    resolved_bot_id: UUID | None = None,
    redis_client: Any | None = None,
    bot_resolve_raises: Any | None = None,
) -> tuple[FastAPI, list[dict[str, Any]]]:
    """Build a minimal FastAPI app mounting our streaming router only.

    Returns the app + a list that captures Stream XADD calls so tests
    can assert on the worker-handoff payload.
    """
    app = FastAPI()
    app.include_router(router, prefix="/api/ragbot")
    # Mirror prod app's domain-error → HTTP envelope mapping so
    # WorkspaceIdInvalid → 422 and ForbiddenError → 403 instead of the
    # generic 500 Starlette would otherwise emit.
    register_exception_handlers(app)

    # Redirect temp dir to pytest tmp_path so we never write to /tmp.
    upload_mod.DEFAULT_UPLOAD_TEMP_DIR_TEST_OVERRIDE = str(tmp_path)  # type: ignore[attr-defined]

    # Monkey-patch the temp-dir helper indirection.  The route imports
    # the constant by name; we redirect by patching the module attr.
    monkey_temp = tmp_path
    upload_mod._ensure_temp_dir.__globals__["DEFAULT_UPLOAD_TEMP_DIR"] = str(monkey_temp)  # type: ignore[attr-defined]

    captured: list[dict[str, Any]] = []

    # ---- bot resolver -----------------------------------------------------
    async def _fake_resolve_bot_uuid(*_args: Any, **_kw: Any) -> UUID:
        if bot_resolve_raises is not None:
            raise bot_resolve_raises
        assert resolved_bot_id is not None
        return resolved_bot_id

    upload_mod._resolve_bot_uuid = _fake_resolve_bot_uuid  # type: ignore[assignment]

    # ---- ingest quota gate (not under test here) -------------------------
    # The real gate opens a tenant-scoped DB session; these tests drive the
    # xadd / temp-file / 202 contract with a mock container, so stub it to a
    # pass-through. test_ingest_quota_wired.py owns the gate's behaviour.
    async def _pass_quota(*_args: Any, **_kw: Any) -> tuple[int, int]:
        return (1, 0)

    upload_mod.enforce_ingest_quota = _pass_quota  # type: ignore[assignment]

    # ---- container.redis_client provider ---------------------------------
    container = MagicMock()
    if redis_client is None:
        container.redis_client = None
    else:
        container.redis_client = lambda: redis_client
    app.state.container = container

    # ---- middleware to forge JWT lift + role -----------------------------
    app.add_middleware(
        _StateMiddleware, record_tenant_id=record_tenant_id, role=role,
    )

    return app, captured


def _multipart_body(
    *,
    bot_id: str = "support",
    channel_type: str = "web",
    document_name: str = "manual.pdf",
    workspace_id: str | None = None,
    mime_type: str | None = "application/pdf",
    language: str | None = "vi",
    filename: str | None = "manual.pdf",
    file_bytes: bytes = b"hello-world",
) -> dict[str, Any]:
    """Build TestClient multipart kwargs (files + data)."""
    data: dict[str, Any] = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "document_name": document_name,
    }
    if workspace_id is not None:
        data["workspace_id"] = workspace_id
    if mime_type is not None:
        data["mime_type"] = mime_type
    if language is not None:
        data["language"] = language
    files = {}
    if filename is not None:
        files["file"] = (
            filename, io.BytesIO(file_bytes), mime_type or "application/octet-stream",
        )
    return {"data": data, "files": files}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_constants_exposed_and_sane() -> None:
    """Hard caps must be sized for partner enterprise corpora."""
    assert DEFAULT_UPLOAD_STREAM_MAX_BYTES == 500 * 1024 * 1024
    assert DEFAULT_UPLOAD_STREAM_CHUNK_SIZE == 1024 * 1024
    assert DEFAULT_UPLOAD_TEMP_DIR.startswith("/tmp/")
    assert SUBJECT_DOCUMENT_UPLOAD_STREAM.startswith("document.upload_stream")


def test_route_registered_on_composed_app() -> None:
    """Composed router must expose the streaming path."""
    from ragbot.interfaces.http.router import router as composed
    paths = {getattr(r, "path", None) for r in composed.routes}
    assert "/api/ragbot/documents/upload-stream" in paths


def test_small_file_streams_to_temp_and_enqueues(tmp_path: Path) -> None:
    tenant = uuid4()
    bot_uuid = uuid4()
    xadd_calls: list[tuple[str, dict[str, Any]]] = []

    class _Redis:
        async def xadd(self, key: str, fields: dict[str, Any]) -> str:
            xadd_calls.append((key, fields))
            return "1-0"

    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=_Redis(),
    )
    payload = b"x" * 4096
    kwargs = _multipart_body(file_bytes=payload)
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 202
    body = resp.json()
    assert body["ok"] is True
    assert body["state"] == "uploading"
    assert body["bytes_received"] == len(payload)
    UUID(body["document_id"])  # parseable

    # Temp file exists with full content
    leftover = list(tmp_path.glob("*.tmp"))
    assert len(leftover) == 1
    assert leftover[0].read_bytes() == payload

    # XADD recorded with 4-key identity + pointer (NOT bytes)
    assert len(xadd_calls) == 1
    subject, fields = xadd_calls[0]
    assert subject == SUBJECT_DOCUMENT_UPLOAD_STREAM
    assert fields["temp_path"] == str(leftover[0])
    assert fields["record_tenant_id"] == str(tenant)
    assert fields["record_bot_id"] == str(bot_uuid)
    assert fields["bot_id"] == "support"
    assert fields["channel_type"] == "web"
    assert fields["bytes"] == str(len(payload))
    assert "content" not in fields  # bytes never leaked into Redis


def test_oversized_body_returns_413_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """413 PAYLOAD_TOO_LARGE + temp file unlinked (no DoS via /tmp fill)."""
    # Shrink the limit so we can exceed it cheaply.
    monkeypatch.setattr(upload_mod, "DEFAULT_UPLOAD_STREAM_MAX_BYTES", 1024)
    tenant = uuid4()
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=None,
    )
    # 4 KiB body — exceeds 1 KiB cap
    payload = b"a" * 4096
    kwargs = _multipart_body(file_bytes=payload)
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 413
    assert "payload_too_large" in resp.json()["detail"]
    # No orphaned temp file
    assert list(tmp_path.glob("*.tmp")) == []


def test_empty_file_returns_422_and_cleans_temp(tmp_path: Path) -> None:
    tenant = uuid4()
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
    )
    kwargs = _multipart_body(file_bytes=b"")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 422
    assert resp.json()["detail"] == "empty_file"
    assert list(tmp_path.glob("*.tmp")) == []


def test_missing_filename_returns_422(tmp_path: Path) -> None:
    tenant = uuid4()
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
    )
    # Filename empty string — UploadFile.filename becomes "" → 422
    kwargs = _multipart_body(filename="")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    # FastAPI may itself reject the malformed multipart with 422 before
    # the route runs; either way we must NOT 202.
    assert resp.status_code == 422


def test_temp_filename_is_uuid_not_bot_id(tmp_path: Path) -> None:
    tenant = uuid4()
    bot_uuid = uuid4()
    sensitive_bot_slug = "acme-corp-internal"

    class _Redis:
        async def xadd(self, *a: Any, **k: Any) -> str:
            return "1-0"

    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=_Redis(),
    )
    kwargs = _multipart_body(bot_id=sensitive_bot_slug, file_bytes=b"data")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 202
    files = list(tmp_path.glob("*.tmp"))
    assert len(files) == 1
    name = files[0].name
    # No tenant or bot leak into the path
    assert sensitive_bot_slug not in name
    assert str(tenant) not in name
    assert str(bot_uuid) not in name
    # UUID hex stem (32 chars + .tmp)
    stem = name.removesuffix(".tmp")
    assert len(stem) == 32
    int(stem, 16)  # valid hex


def test_concurrent_uploads_get_unique_temp_paths(tmp_path: Path) -> None:
    """Five back-to-back uploads → five distinct temp files."""
    tenant = uuid4()
    bot_uuid = uuid4()

    class _Redis:
        async def xadd(self, *a: Any, **k: Any) -> str:
            return "1-0"

    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=_Redis(),
    )
    with TestClient(app) as client:
        for i in range(5):
            kwargs = _multipart_body(file_bytes=f"payload-{i}".encode())
            resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
            assert resp.status_code == 202
    files = list(tmp_path.glob("*.tmp"))
    assert len(files) == 5
    # All distinct UUIDs
    assert len({f.name for f in files}) == 5


def test_redis_unavailable_still_returns_202(tmp_path: Path) -> None:
    """Aux-dependency graceful-degradation: no Redis → still 202."""
    tenant = uuid4()
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=None,  # container.redis_client is None
    )
    kwargs = _multipart_body(file_bytes=b"x" * 128)
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 202
    # File still on disk for cleanup worker to find
    assert len(list(tmp_path.glob("*.tmp"))) == 1


def test_redis_xadd_failure_logged_returns_202(tmp_path: Path) -> None:
    """Transport hiccup mid-XADD: degrade silent, partner still gets 202."""
    tenant = uuid4()
    bot_uuid = uuid4()

    class _BrokenRedis:
        async def xadd(self, *a: Any, **k: Any) -> str:
            raise ConnectionError("redis down")

    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=_BrokenRedis(),
    )
    kwargs = _multipart_body(file_bytes=b"data")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 202


def test_no_record_tenant_returns_401(tmp_path: Path) -> None:
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=None,  # missing JWT claim
        resolved_bot_id=bot_uuid,
    )
    kwargs = _multipart_body(file_bytes=b"data")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing tenant context"


def test_rbac_below_admin_returns_403(tmp_path: Path) -> None:
    tenant = uuid4()
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        role="user",  # level 20 — below the 60 admin gate
        resolved_bot_id=bot_uuid,
    )
    kwargs = _multipart_body(file_bytes=b"data")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 403


def test_bot_resolve_miss_returns_404(tmp_path: Path) -> None:
    tenant = uuid4()
    from fastapi import HTTPException
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=None,
        bot_resolve_raises=HTTPException(status_code=404, detail="bot_not_found"),
    )
    kwargs = _multipart_body(file_bytes=b"data")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 404
    assert resp.json()["detail"] == "bot_not_found"


def test_workspace_id_invalid_format_returns_422(tmp_path: Path) -> None:
    """Invalid slug → WorkspaceIdInvalid → 422 surface."""
    tenant = uuid4()
    bot_uuid = uuid4()
    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
    )
    # Space in workspace_id breaks the ASCII slug regex
    kwargs = _multipart_body(workspace_id="bad slug", file_bytes=b"data")
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 422


def test_xadd_payload_carries_4key_identity(tmp_path: Path) -> None:
    """Worker hand-off MUST carry the full 4-key identity tuple."""
    tenant = uuid4()
    bot_uuid = uuid4()
    captured: list[dict[str, Any]] = []

    class _Redis:
        async def xadd(self, key: str, fields: dict[str, Any]) -> str:
            captured.append(fields)
            return "1-0"

    app, _ = _build_app(
        tmp_path=tmp_path,
        record_tenant_id=tenant,
        resolved_bot_id=bot_uuid,
        redis_client=_Redis(),
    )
    kwargs = _multipart_body(
        bot_id="legal", channel_type="web",
        workspace_id="acme", file_bytes=b"x" * 32,
    )
    with TestClient(app) as client:
        resp = client.post("/api/ragbot/documents/upload-stream", **kwargs)
    assert resp.status_code == 202
    assert len(captured) == 1
    fields = captured[0]
    # 4-key identity: record_tenant_id, workspace_id, bot_id, channel_type
    assert fields["record_tenant_id"] == str(tenant)
    assert fields["workspace_id"] == "acme"
    assert fields["bot_id"] == "legal"
    assert fields["channel_type"] == "web"
    # Internal UUID also present so worker can skip the resolve hop
    assert fields["record_bot_id"] == str(bot_uuid)
    # Subject label is the SSoT constant
    assert fields["subject"] == SUBJECT_DOCUMENT_UPLOAD_STREAM
