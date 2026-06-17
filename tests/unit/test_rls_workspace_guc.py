"""Workspace GUC binding for RLS (ADR-W1-D3 piece 2).

The 0141 policies carry a workspace clause —
``COALESCE(current_setting('app.workspace_id', true), '') = '' OR
workspace_id::text = current_setting(...)`` — which is supply-side dead
while nothing ever issues ``SET LOCAL app.workspace_id``. These tests pin
the new supply path:

* ``workspace_id_ctx`` exists and ``bind_request_context`` populates it;
* the ``after_begin`` listener emits BOTH GUCs when both are bound;
* an invalid slug is refused (never interpolated into SQL);
* no workspace bound → tenant-only behaviour unchanged (policy clause
  COALESCE('')='' keeps tenant-wide visibility — backward compat).
"""

from __future__ import annotations

from typing import Any


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, stmt: Any) -> None:
        self.executed.append(str(stmt))


def _fire_after_begin() -> _FakeConnection:
    from ragbot.infrastructure.db.session import _after_begin

    conn = _FakeConnection()
    _after_begin(session=object(), transaction=object(), connection=conn)
    return conn


def test_workspace_ctx_exists_and_bind_populates_it() -> None:
    from ragbot.config.logging import bind_request_context, workspace_id_ctx

    token = workspace_id_ctx.set("")
    try:
        bind_request_context(
            record_tenant_id="33333333-3333-3333-3333-333333333333",
            workspace_id="my-workspace",
        )
        assert workspace_id_ctx.get() == "my-workspace"
    finally:
        workspace_id_ctx.reset(token)


def test_after_begin_sets_both_gucs_when_both_bound() -> None:
    from ragbot.config.logging import tenant_id_ctx, workspace_id_ctx
    from ragbot.infrastructure.db.session import (
        TENANT_SETTING_KEY,
        WORKSPACE_SETTING_KEY,
    )

    t = tenant_id_ctx.set("44444444-4444-4444-4444-444444444444")
    w = workspace_id_ctx.set("ws-alpha")
    try:
        conn = _fire_after_begin()
    finally:
        tenant_id_ctx.reset(t)
        workspace_id_ctx.reset(w)

    joined = " | ".join(conn.executed)
    assert f"SET LOCAL {TENANT_SETTING_KEY}" in joined
    assert f"SET LOCAL {WORKSPACE_SETTING_KEY}" in joined
    assert "ws-alpha" in joined


def test_after_begin_tenant_only_when_workspace_unbound() -> None:
    from ragbot.config.logging import tenant_id_ctx, workspace_id_ctx
    from ragbot.infrastructure.db.session import WORKSPACE_SETTING_KEY

    t = tenant_id_ctx.set("44444444-4444-4444-4444-444444444444")
    w = workspace_id_ctx.set("")
    try:
        conn = _fire_after_begin()
    finally:
        tenant_id_ctx.reset(t)
        workspace_id_ctx.reset(w)

    joined = " | ".join(conn.executed)
    assert WORKSPACE_SETTING_KEY not in joined, (
        "no workspace bound → must NOT set the workspace GUC (policy "
        "COALESCE('')='' keeps tenant-wide semantics, backward compat)"
    )


def test_after_begin_refuses_invalid_workspace_slug() -> None:
    """A poisoned slug must never be interpolated into SET LOCAL."""
    from ragbot.config.logging import tenant_id_ctx, workspace_id_ctx
    from ragbot.infrastructure.db.session import WORKSPACE_SETTING_KEY

    t = tenant_id_ctx.set("44444444-4444-4444-4444-444444444444")
    w = workspace_id_ctx.set("'; DROP TABLE bots; --")
    try:
        conn = _fire_after_begin()
    finally:
        tenant_id_ctx.reset(t)
        workspace_id_ctx.reset(w)

    joined = " | ".join(conn.executed)
    assert WORKSPACE_SETTING_KEY not in joined
    assert "DROP TABLE" not in joined


def test_no_workspace_without_tenant() -> None:
    """Tenant unbound → listener stays a full no-op (ops/migration safe),
    even if a workspace slug is somehow set."""
    from ragbot.config.logging import tenant_id_ctx, workspace_id_ctx

    t = tenant_id_ctx.set("UNSET")
    w = workspace_id_ctx.set("ws-orphan")
    try:
        conn = _fire_after_begin()
    finally:
        tenant_id_ctx.reset(t)
        workspace_id_ctx.reset(w)
    assert conn.executed == []
