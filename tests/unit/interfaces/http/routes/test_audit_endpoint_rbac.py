"""Regression test — /audit endpoint requires admin level.

Bug: ``/bots/{bot_id}/{channel_type}/audit`` (handler ``bot_audit_stats``)
ran arbitrary SELECTs over ``request_logs`` / ``model_invocations`` for
the bot, but had NO RBAC gate. Any authenticated caller with level >= 0
(viewer / user / guest) could read cross-user audit traffic.

Fix: first line of the handler body now calls
``require_min_level(request, DEFAULT_ADMIN_LEVEL)`` (admin), matching the
policy used by sibling /admin/audit routes (admin_audit.py). The level is
the shared constant, not a magic number.

Pre-fix: a level-20 ``user`` token reaches ``_find_bot_uuid`` (no raise).
Post-fix: ``ForbiddenError`` (HTTP 403) raised before any DB work.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest

from ragbot.interfaces.http.routes import test_chat as chat_routes
from ragbot.shared.errors import ForbiddenError


def test_handler_calls_require_admin_level_first() -> None:
    """Static guard: gate must be the first executable statement.

    Verifies that ``require_min_level(request, DEFAULT_ADMIN_LEVEL)`` appears
    in the handler source AND is the first statement after the docstring, so
    cheap/expensive prep work can never run for a non-admin caller. The level
    is the shared constant (zero-hardcode), not a literal.
    """
    gate = "require_min_level(request, DEFAULT_ADMIN_LEVEL)"
    src = inspect.getsource(chat_routes.bot_audit_stats)
    assert gate in src, (
        f"bot_audit_stats must call {gate} — "
        "audit endpoint reads cross-user request_logs."
    )
    # Strip the leading docstring lines and check the first non-blank
    # executable line is the gate. The handler signature ends with `:`
    # then ``"""..."""`` triple-quoted docstring then the body.
    body_lines = src.split('"""')[2].splitlines()
    first_exec = next(
        (line.strip() for line in body_lines if line.strip()),
        "",
    )
    assert first_exec.startswith(gate), (
        f"require_min_level must be the FIRST executable statement, got: {first_exec!r}"
    )


@pytest.mark.asyncio
async def test_user_level_token_gets_403() -> None:
    """Behavioural: caller with role='user' (level 20) → ForbiddenError.

    ForbiddenError is the platform's 403 envelope (http_status=403,
    code='FORBIDDEN'); the FastAPI exception handler maps it to a
    standard 403 response.
    """
    request = MagicMock()
    request.state = MagicMock(spec=["role"])
    request.state.role = "user"  # level 20 per ROLE_LEVELS

    with pytest.raises(ForbiddenError) as exc:
        await chat_routes.bot_audit_stats(
            bot_id="some-bot",
            channel_type="web",
            request=request,
        )
    # 403 envelope semantics — admin-only gate
    assert exc.value.http_status == 403
    assert exc.value.code == "FORBIDDEN"


@pytest.mark.asyncio
async def test_guest_level_token_gets_403() -> None:
    """Behavioural: anonymous caller (no role attr) → ForbiddenError.

    ``request.state.role`` defaults to 'guest' (level 0) when missing,
    so even pre-auth callers cannot enumerate audit data.
    """
    request = MagicMock()
    request.state = MagicMock(spec=[])  # no role attr at all

    with pytest.raises(ForbiddenError) as exc:
        await chat_routes.bot_audit_stats(
            bot_id="some-bot",
            channel_type="web",
            request=request,
        )
    assert exc.value.http_status == 403
