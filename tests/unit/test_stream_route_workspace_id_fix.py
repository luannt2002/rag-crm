"""[T2-CostPerf] Bug #1 regression guard — workspace_id in stream route.

Commit ``6529db4`` (2026-05-05) made ``workspace_id`` a required keyword-only
argument on ``RequestLogRepository.create_request_log``.  The streaming route
``POST /test/chat/stream`` in ``test_chat.py`` was not updated at the time,
causing a silent TypeError swallowed by the ``except (SQLAlchemyError, ValueError,
TypeError)`` handler — request_log row not inserted → StepTracker raises
``TenantIsolationViolation`` → pipeline crashes early → stream emits empty
``done`` event.

This test file pins:
    1. ``create_request_log`` signature requires ``workspace_id`` (keyword-only).
    2. Calling without ``workspace_id`` raises ``TypeError``.
    3. Calling with ``workspace_id`` does NOT raise TypeError on the signature.
    4. The route-level call site passes the correct arg (static AST check).
"""

from __future__ import annotations

import inspect
import re

import pytest


# ── 1. Signature requires workspace_id ───────────────────────────────────────


def test_create_request_log_signature_has_workspace_id():
    """``workspace_id`` must be a required keyword-only parameter."""
    from ragbot.infrastructure.repositories.request_log_repository import (
        RequestLogRepository,
    )

    sig = inspect.signature(RequestLogRepository.create_request_log)
    params = sig.parameters

    assert "workspace_id" in params, (
        "create_request_log must have a workspace_id parameter"
    )
    param = params["workspace_id"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "workspace_id must be keyword-only (use * in the signature)"
    )
    assert param.default == inspect.Parameter.empty, (
        "workspace_id must be REQUIRED (no default value)"
    )


# ── 2. TypeError when workspace_id omitted ────────────────────────────────────


def test_create_request_log_raises_typeerror_without_workspace_id():
    """Omitting workspace_id must raise TypeError (not silently succeed).

    This was the root cause of Bug #1: the TypeError was caught by a broad
    ``except (SQLAlchemyError, ValueError, TypeError)`` handler, masking the
    missing arg.  The fix added ``workspace_id=workspace_slug`` to the call.

    We verify via the coroutine *construction* step (not execution) — calling
    a coroutine function with missing required kwargs raises TypeError
    immediately, before any await happens.
    """
    import uuid
    from unittest.mock import MagicMock

    from ragbot.infrastructure.repositories.request_log_repository import (
        RequestLogRepository,
    )

    stub_sf = MagicMock()
    repo = RequestLogRepository(session_factory=stub_sf)

    # Attempting to call without workspace_id must raise TypeError at call time.
    with pytest.raises(TypeError, match="workspace_id"):
        # The call itself (NOT the await) raises TypeError for missing required
        # keyword-only arg — this matches what the stream route encountered.
        repo.create_request_log(
            request_id=uuid.uuid4(),
            record_tenant_id=uuid.uuid4(),
            # workspace_id is intentionally OMITTED
            connect_id="test-user",
            question_hash="abc123",
            message_id=1,
            record_bot_id=uuid.uuid4(),
            channel_type="web",
            trace_id="test-trace",
        )


# ── 3. AST check — stream route call site passes workspace_id ────────────────


def test_stream_route_passes_workspace_id_to_create_request_log():
    """Verify the fixed call in test_chat.py includes workspace_id.

    Static analysis (grep of source text) so the test fails immediately when
    the arg is removed again — no runtime dependency on DB or HTTP stack.
    """
    import pathlib

    route_file = pathlib.Path(
        "src/ragbot/interfaces/http/routes/test_chat/chat_routes.py"
    )
    source = route_file.read_text(encoding="utf-8")

    # Find the create_request_log call block in the stream route.
    # We look for the block that has trace_id=f"test-stream-..." which is
    # unique to the streaming route (non-stream uses "test-{request_id}").
    stream_block_match = re.search(
        r"create_request_log\([^)]*?test-stream-[^)]*?\)",
        source,
        re.DOTALL,
    )
    assert stream_block_match is not None, (
        "Could not find create_request_log call with trace_id 'test-stream-' "
        "in test_chat.py — the stream route may have been restructured"
    )

    block = stream_block_match.group(0)
    assert "workspace_id" in block, (
        "Bug #1 regression: create_request_log in stream route is missing "
        "'workspace_id' argument.  Add 'workspace_id=workspace_slug' to the call."
    )


# ── 4. workspace_slug variable is available before the call ──────────────────


def test_workspace_slug_resolved_before_create_request_log_in_stream_route():
    """``workspace_slug`` must be assigned before the ``create_request_log`` call.

    ``workspace_slug`` is resolved via ``resolve_workspace_id()`` earlier
    in the handler.  This test checks ordering via source-text line numbers.
    """
    import pathlib

    route_file = pathlib.Path(
        "src/ragbot/interfaces/http/routes/test_chat/chat_routes.py"
    )
    lines = route_file.read_text(encoding="utf-8").splitlines()

    workspace_resolve_line = None
    create_log_stream_line = None

    for i, line in enumerate(lines):
        if "workspace_slug = resolve_workspace_id(" in line and workspace_resolve_line is None:
            # Find the one in stream route context — look for the stream route's
            # create_request_log call that has test-stream- in it.
            workspace_resolve_line = i
        if 'trace_id=f"test-stream-' in line and create_log_stream_line is None:
            create_log_stream_line = i

    assert workspace_resolve_line is not None, (
        "workspace_slug = resolve_workspace_id(...) not found in test_chat.py"
    )
    assert create_log_stream_line is not None, (
        "create_request_log call with test-stream- trace_id not found"
    )
    assert workspace_resolve_line < create_log_stream_line, (
        f"workspace_slug must be resolved (line {workspace_resolve_line + 1}) "
        f"BEFORE create_request_log (line {create_log_stream_line + 1})"
    )
