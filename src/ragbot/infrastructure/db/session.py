"""Per-request RLS binding for the runtime (non-superuser) DB role.

Background
----------
Tenant isolation has three independent layers that must ALL be live for
row-level security to actually enforce:

1. ``ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY`` + ``CREATE POLICY``
   that filters on a GUC — installed by alembic ``0069`` / ``0141`` and
   re-asserted by ``0187``. The canonical GUC is ``app.tenant_id`` (the
   policies read ``current_setting('app.tenant_id', true)::uuid``).
2. A **non-superuser, NOBYPASSRLS** login role — provisioned by alembic
   ``0073`` and re-grant-asserted by ``0186``. A superuser /
   ``rolbypassrls`` connection ignores every policy, so until the app
   connects as this role the 23 policies are dead.
3. Every transaction the app opens must bind the GUC with ``SET LOCAL``
   so the policy has a tenant to compare against.

Layer 3 was only wired at *explicit* call sites (``session_with_tenant``
in ``engine.py``). Repositories that open the plain ``session_factory``
session never issued ``SET LOCAL`` — so even with a NOBYPASSRLS role they
would see *zero* rows (fail-closed) or, before the role switch, every row
(fail-open). This module closes layer 3 generically: it installs a
SQLAlchemy ``after_begin`` hook that, when a per-request tenant id is
present on the contextvar, issues ``SET LOCAL app.tenant_id = '<uuid>'``
inside the just-started transaction.

Design constraints honoured
---------------------------
* **Default OFF (rule #0)** — ``attach_rls_session_hook`` is opt-in. Until
  a coordinator attaches it (and the runtime DSN points at the
  NOBYPASSRLS role), behaviour is byte-for-byte unchanged. The contextvar
  read is also a no-op when no tenant is bound (ops / migration /
  background jobs that never set the contextvar), so attaching the hook on
  a superuser connection is harmless.
* **Async rule 7** — ``SET LOCAL`` is only valid inside a transaction; we
  bind on ``after_begin`` (transaction just opened) rather than on
  ``connect`` (no txn yet).
* **No bind params** — PostgreSQL ``SET LOCAL`` does not accept bind
  parameters, so the uuid is interpolated. We validate it parses as a
  ``UUID`` first (defence against SQL injection via a poisoned
  contextvar).
* **Constants local, not in shared/constants.py** — that module is owned
  by another stream; the GUC name + role name live here as module-level
  ``Final`` so this file is self-contained.
"""

from __future__ import annotations

import re
from typing import Any, Final
from uuid import UUID

import structlog
from sqlalchemy import event, text
from sqlalchemy.engine import Connection

from ragbot.config.logging import tenant_id_ctx, workspace_id_ctx

_log = structlog.get_logger(__name__)

# --- Module-level configuration constants -----------------------------------
# Kept local (NOT in shared/constants.py — that file is owned by another
# stream). Names reflect PURPOSE, not version.

# The runtime role the application connects as. NOSUPERUSER + NOBYPASSRLS so
# the RLS policies actually apply. Login/credential handled by ops via the
# DATABASE_URL_APP DSN; this name only documents the contract.
RUNTIME_DB_ROLE: Final[str] = "ragbot_app"

# The role the trusted cross-tenant background workers connect as. NOSUPERUSER
# but BYPASSRLS — outbox publisher / recovery scan / cache GC / cost-cap
# aggregate legitimately read across every tenant and have no single tenant
# context, so they must NOT be fail-closed by RLS. Login/credential handled by
# ops via the DATABASE_URL_SYSTEM DSN; provisioned by the
# ``rls_system_role_grants`` migration (whose role literal is pinned to this).
SYSTEM_DB_ROLE: Final[str] = "ragbot_system"

# Canonical GUC the RLS policies compare against. MUST match the policy
# definitions in alembic 0069 / 0141 / 0187 — diverging here would make the
# SET LOCAL bind a setting no policy reads, leaving RLS dead.
TENANT_SETTING_KEY: Final[str] = "app.tenant_id"

# Workspace GUC the 0141 workspace-aware policies read. When never SET the
# policy clause ``COALESCE(current_setting('app.workspace_id', true), '')=''``
# short-circuits TRUE → tenant-only semantics (backward compat).
WORKSPACE_SETTING_KEY: Final[str] = "app.workspace_id"

# Workspace slug shape — mirrors the 4-key identity contract
# (``^[a-zA-Z0-9-]+$``, length 1-64). Anything else is refused before SQL
# interpolation (SET LOCAL takes no bind params).
_WORKSPACE_SLUG_RE: Final[re.Pattern[str]] = re.compile(r"^[a-zA-Z0-9-]{1,64}$")

# Contextvar sentinel for "no tenant bound" — mirrors logging.tenant_id_ctx
# default. Treated as no-op (ops / migration / background tasks).
_UNSET_TENANT: Final[str] = "UNSET"


def _current_tenant_uuid() -> str | None:
    """Return the bound tenant UUID string, or ``None`` when unset/invalid.

    Reads the request-scoped ``tenant_id_ctx`` populated by
    ``TenantContextMiddleware`` via ``bind_request_context()``. Returns
    ``None`` (→ hook is a no-op) when:

      * the contextvar is at its ``"UNSET"`` sentinel (no HTTP request /
        ops shell / migration), or
      * the value does not parse as a UUID (poisoned contextvar — we refuse
        to interpolate it into raw SQL).
    """
    raw = tenant_id_ctx.get()
    if not raw or raw == _UNSET_TENANT:
        return None
    try:
        return str(UUID(raw))
    except (TypeError, ValueError):
        _log.warning("rls_session_hook_bad_tenant_ctx", value=raw)
        return None


def _current_workspace_slug() -> str | None:
    """Return the bound workspace slug, or ``None`` when unset/invalid.

    Invalid slugs (shape outside the 4-key identity contract) are refused
    with a warning — never interpolated into raw SQL. Unbound (empty
    default) is silent: most flows are tenant-only and that is correct.
    """
    raw = workspace_id_ctx.get()
    if not raw:
        return None
    if not _WORKSPACE_SLUG_RE.match(raw):
        _log.warning("rls_session_hook_bad_workspace_ctx", value=raw)
        return None
    return raw


def _set_local_tenant(connection: Connection, tenant_uuid: str) -> None:
    """Issue ``SET LOCAL app.tenant_id = '<uuid>'`` on a sync Connection.

    Separated for unit-test reach. ``tenant_uuid`` is assumed pre-validated
    by ``_current_tenant_uuid`` (UUID-shaped) — we still build the literal
    via interpolation because ``SET LOCAL`` rejects bind params.
    """
    connection.execute(
        text(f"SET LOCAL {TENANT_SETTING_KEY} = '{tenant_uuid}'"),
    )


def _after_begin(
    session: Any,
    transaction: Any,  # noqa: ARG001 — required by SQLAlchemy event signature
    connection: Connection,
) -> None:
    """``after_begin`` listener: bind the tenant GUC for this transaction.

    No-op when no tenant context is bound — ops / migration / background
    sessions keep their current (superuser or unscoped) behaviour, so the
    hook is safe to attach unconditionally.
    """
    tenant_uuid = _current_tenant_uuid()
    if tenant_uuid is None:
        return
    _set_local_tenant(connection, tenant_uuid)
    # Workspace dimension (0141 policies) — only meaningful under a bound
    # tenant; unbound/invalid slug keeps tenant-only semantics.
    workspace_slug = _current_workspace_slug()
    if workspace_slug is not None:
        connection.execute(
            text(f"SET LOCAL {WORKSPACE_SETTING_KEY} = '{workspace_slug}'"),
        )


def _session_event_target(target: Any) -> Any:
    """Resolve the SQLAlchemy target that accepts the ``after_begin`` event.

    ``after_begin`` is a ``SessionEvents`` event, so it must be registered
    on a ``Session`` (class), a ``sessionmaker``, or — for the async stack —
    the sync ``Session`` class that an ``async_sessionmaker`` proxies. An
    ``async_sessionmaker`` itself does NOT accept session events directly;
    its ``class_`` is the ``AsyncSession`` whose ``sync_session_class`` is
    the real listen target.

    Accepts:
      * a ``Session`` (sub)class or a ``sessionmaker`` — used as-is;
      * an ``async_sessionmaker`` — resolved to its underlying sync
        ``Session`` class via the ``AsyncSession.sync_session_class``.
    """
    class_ = getattr(target, "class_", None)
    if class_ is not None:
        sync_cls = getattr(class_, "sync_session_class", None)
        if sync_cls is not None:
            return sync_cls
    return target


def attach_rls_session_hook(target: Any) -> None:
    """Attach the per-transaction RLS binder to a session factory / class.

    OPT-IN (rule #0 — default OFF). The coordinator calls this only when the
    runtime DSN points at the NOBYPASSRLS ``ragbot_app`` role; until then the
    application is unchanged. Attaching it on a superuser connection is a
    harmless no-op (policies are bypassed regardless).

    ``target`` is the ``async_sessionmaker`` (or sync ``sessionmaker`` /
    ``Session`` class) that the application opens sessions from. Idempotent —
    re-attaching does not double-register.
    """
    tgt = _session_event_target(target)
    if event.contains(tgt, "after_begin", _after_begin):
        return
    event.listen(tgt, "after_begin", _after_begin)


def detach_rls_session_hook(target: Any) -> None:
    """Remove the hook (test teardown / explicit rollback to superuser DSN)."""
    tgt = _session_event_target(target)
    if event.contains(tgt, "after_begin", _after_begin):
        event.remove(tgt, "after_begin", _after_begin)


def create_rls_session_factory(*, engine: Any) -> Any:
    """Build the app session factory WITH the RLS binder attached.

    The production composition root (``bootstrap.Container.session_factory``)
    routes through this wrapper so layer 3 of the RLS stack (per-transaction
    ``SET LOCAL``) is wired for every repo session — bare ``_new_session``
    callsites included. Under the superuser DSN the hook is a behavioural
    no-op (policies bypassed regardless); it starts enforcing the moment
    ops points ``DATABASE_URL_APP`` at the ``ragbot_app`` role (ADR-W1-D3).
    """
    from ragbot.infrastructure.db.engine import create_session_factory  # noqa: PLC0415 — avoid module cycle

    factory = create_session_factory(engine)
    attach_rls_session_hook(factory)
    return factory


__all__ = [
    "RUNTIME_DB_ROLE",
    "SYSTEM_DB_ROLE",
    "TENANT_SETTING_KEY",
    "WORKSPACE_SETTING_KEY",
    "attach_rls_session_hook",
    "create_rls_session_factory",
    "detach_rls_session_hook",
]
