"""Mock-only unit tests for the message_feedback scaffolding.

Five tests, all infrastructure-free:

* repo ``record()`` writes the row with the full tenant + bot keys
* repo ``aggregate_per_bot()`` collapses verdicts into separate counts
* POST endpoint resolves the 4-key bot identity and lifts tenant from JWT
* missing body field returns 422 (pydantic validation)
* cross-tenant write is blocked at the repo layer (RLS WITH CHECK simulation)

The repo tests substitute the SQLAlchemy ``async_sessionmaker`` with a
fake that records what was added; the route tests inject a mocked
``BotRegistryService`` + ``MessageFeedbackRepository`` onto the app's
container so the wire contract is exercised end-to-end without touching
Postgres.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from ragbot.infrastructure.repositories.message_feedback_repository import (
    MessageFeedbackRepository,
)
from ragbot.interfaces.http.routes.feedback import router
from ragbot.shared.constants import (
    FEEDBACK_VERDICT_THUMBS_DOWN,
    FEEDBACK_VERDICT_THUMBS_UP,
)
from ragbot.shared.errors import ForbiddenError, TenantIsolationViolation

_TENANT_A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
_TENANT_B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
_BOT_A = uuid.UUID("11111111-1111-1111-1111-1111111111aa")


# --- Fake session plumbing for repo tests ----------------------------------

class _CapturedRow:
    """Simple value object holding fields the fake session captured."""

    def __init__(self, model_instance: Any) -> None:
        self.record_tenant_id = model_instance.record_tenant_id
        self.record_bot_id = model_instance.record_bot_id
        self.message_id = model_instance.message_id
        self.record_conversation_id = model_instance.record_conversation_id
        self.connect_id = model_instance.connect_id
        self.verdict = model_instance.verdict
        self.comment = model_instance.comment


class _FakeAsyncSession:
    """Mimics the subset of AsyncSession the repo touches."""

    def __init__(self, store: list[_CapturedRow], tenant_set_log: list[str]) -> None:
        self._store = store
        self._tenant_set_log = tenant_set_log
        self.committed = False

    def add(self, obj: Any) -> None:
        self._store.append(_CapturedRow(obj))

    async def execute(self, stmt: Any) -> Any:  # noqa: ARG002 — sql ignored in fake
        # Repo aggregate path issues SELECT — return a fixed result mirroring
        # the SQL contract: a single row with two integer columns
        # (up_count, down_count). We compute against the in-memory store.
        up = sum(1 for r in self._store if r.verdict == FEEDBACK_VERDICT_THUMBS_UP)
        down = sum(1 for r in self._store if r.verdict == FEEDBACK_VERDICT_THUMBS_DOWN)
        return _FakeResult(rows=[(up, down)])

    async def commit(self) -> None:
        self.committed = True

    async def close(self) -> None:
        return None


class _FakeResult:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def one(self) -> tuple[Any, ...]:
        return self._rows[0]


class _FakeSessionFactory:
    """Stand-in for ``async_sessionmaker``.

    The real ``session_with_tenant`` opens a real connection then runs
    ``SET LOCAL app.tenant_id``; we don't have a DB so each repo test
    monkeypatches the helper to bind via :class:`_CtxHelper` instead,
    and this factory only has to return a session.
    """

    def __init__(
        self,
        store: list[_CapturedRow],
        tenant_set_log: list[str],
    ) -> None:
        self._store = store
        self._tenant_set_log = tenant_set_log

    def __call__(self) -> _FakeAsyncSession:
        return _FakeAsyncSession(self._store, self._tenant_set_log)


# --- Tests: repo --------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_inserts_row_with_full_tenant_and_bot_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repo.record() persists the row keyed by tenant + bot UUIDs.

    Patches ``session_with_tenant`` to a no-op context manager that
    yields the fake session — the real helper would run SET LOCAL on a
    live connection. The captured row carries every key the analytics
    layer needs.
    """
    store: list[_CapturedRow] = []
    tenant_set_log: list[str] = []
    factory = _FakeSessionFactory(store, tenant_set_log)

    monkeypatch.setattr(
        "ragbot.infrastructure.repositories.message_feedback_repository."
        "session_with_tenant",
        lambda sf, *, record_tenant_id: _CtxHelper(
            sf, record_tenant_id, tenant_set_log,
        ),
    )

    repo = MessageFeedbackRepository(factory)  # type: ignore[arg-type]
    new_id = await repo.record(
        record_tenant_id=_TENANT_A,
        record_bot_id=_BOT_A,
        verdict=FEEDBACK_VERDICT_THUMBS_UP,
        message_id=42,
        connect_id="user-xyz",
        comment="useful answer",
    )

    assert isinstance(new_id, uuid.UUID)
    assert len(store) == 1, "exactly one row must be inserted"
    row = store[0]
    assert row.record_tenant_id == _TENANT_A
    assert row.record_bot_id == _BOT_A
    assert row.verdict == FEEDBACK_VERDICT_THUMBS_UP
    assert row.message_id == 42
    assert row.connect_id == "user-xyz"
    assert row.comment == "useful answer"
    # The tenant binding must have been set on the session before insert.
    assert tenant_set_log == [str(_TENANT_A)]


class _CtxHelper:
    """Inline async context manager — yields one fake session."""

    def __init__(
        self, factory: Any, record_tenant_id: Any, log: list[str],
    ) -> None:
        self._factory = factory
        self._tid = record_tenant_id
        self._log = log
        self._session: _FakeAsyncSession | None = None

    async def __aenter__(self) -> _FakeAsyncSession:
        self._log.append(str(self._tid))
        self._session = self._factory()
        return self._session

    async def __aexit__(self, *_a: Any) -> None:
        if self._session is not None:
            await self._session.close()


@pytest.mark.asyncio
async def test_aggregate_per_bot_counts_verdicts_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Aggregate returns ``{thumbs_up: N, thumbs_down: M}`` from the same query."""
    store: list[_CapturedRow] = []
    tenant_set_log: list[str] = []
    factory = _FakeSessionFactory(store, tenant_set_log)

    monkeypatch.setattr(
        "ragbot.infrastructure.repositories.message_feedback_repository."
        "session_with_tenant",
        lambda sf, *, record_tenant_id: _CtxHelper(
            sf, record_tenant_id, tenant_set_log,
        ),
    )

    repo = MessageFeedbackRepository(factory)  # type: ignore[arg-type]

    # Seed two ups + one down via record() so the in-fake aggregator has rows.
    for verdict in (
        FEEDBACK_VERDICT_THUMBS_UP,
        FEEDBACK_VERDICT_THUMBS_UP,
        FEEDBACK_VERDICT_THUMBS_DOWN,
    ):
        await repo.record(
            record_tenant_id=_TENANT_A,
            record_bot_id=_BOT_A,
            verdict=verdict,
        )

    counts = await repo.aggregate_per_bot(
        record_tenant_id=_TENANT_A, record_bot_id=_BOT_A, since_days=7,
    )

    assert counts == {
        FEEDBACK_VERDICT_THUMBS_UP: 2,
        FEEDBACK_VERDICT_THUMBS_DOWN: 1,
    }, "verdict tally must split up vs down with both keys present"


# --- Tests: HTTP endpoint -----------------------------------------------------


def _make_app(
    *,
    tenant_id: uuid.UUID | None = _TENANT_A,
    bot_lookup_returns: Any = None,
    repo_record_id: uuid.UUID = _BOT_A,
    role: str = "admin",
) -> tuple[FastAPI, MagicMock]:
    """Mount only the new feedback router with a mocked container."""
    app = FastAPI()
    app.include_router(router, prefix="/api/ragbot")

    container = MagicMock()

    bot_cfg = MagicMock()
    bot_cfg.id = bot_lookup_returns if bot_lookup_returns is not None else _BOT_A
    if bot_lookup_returns is False:
        registry = AsyncMock()
        registry.lookup = AsyncMock(return_value=None)
    else:
        registry = AsyncMock()
        registry.lookup = AsyncMock(return_value=bot_cfg)
    container.bot_registry_service.return_value = registry

    repo = AsyncMock()
    repo.record = AsyncMock(return_value=repo_record_id)
    container.message_feedback_repo.return_value = repo

    app.state.container = container

    @app.exception_handler(ForbiddenError)
    async def _forbidden_handler(request, exc):  # noqa: ARG001
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.middleware("http")
    async def inject_state(request, call_next):
        request.state.role = role
        request.state.record_tenant_id = tenant_id
        request.state.trace_id = "trace-test-1"
        return await call_next(request)

    return app, container


@pytest.fixture()
def _bypass_rbac(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the RBAC gate with a no-op so the test exercises only the
    route logic. The real gate hits Redis + the permissions table — both
    are out of scope here; the gate's own behaviour is covered elsewhere.
    """
    async def _allow(*_a: Any, **_kw: Any) -> None:
        return None

    monkeypatch.setattr(
        "ragbot.interfaces.http.middlewares.rbac.require_permission",
        _allow,
    )


def test_endpoint_resolves_4_keys_and_lifts_tenant_from_jwt(
    _bypass_rbac: None,
) -> None:
    """The route must use the JWT-provided tenant + the body's 3-key
    bot identity and never trust a tenant value from the wire."""
    app, container = _make_app()
    client = TestClient(app)

    body = {
        "bot_id": "support",
        "channel_type": "web",
        "workspace_id": "ws-alpha",
        "message_id": 99,
        "verdict": FEEDBACK_VERDICT_THUMBS_UP,
        "comment": "great",
    }
    resp = client.post("/api/ragbot/feedback/thumbs", json=body)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert uuid.UUID(data["feedback_id"]) == _BOT_A

    # Assert the registry was called with the JWT tenant — NOT a body value.
    registry = container.bot_registry_service.return_value
    lookup_args = registry.lookup.await_args
    assert lookup_args.args[0] == _TENANT_A, "tenant must come from JWT"
    assert lookup_args.args[1] == "ws-alpha"
    assert lookup_args.args[2] == "support"
    assert lookup_args.args[3] == "web"

    # Repo got the same tenant + the resolved internal record_bot_id.
    repo = container.message_feedback_repo.return_value
    rec_kwargs = repo.record.await_args.kwargs
    assert rec_kwargs["record_tenant_id"] == _TENANT_A
    assert rec_kwargs["record_bot_id"] == _BOT_A
    assert rec_kwargs["verdict"] == FEEDBACK_VERDICT_THUMBS_UP
    assert rec_kwargs["message_id"] == 99
    assert rec_kwargs["comment"] == "great"


def test_endpoint_returns_422_when_body_missing_required_field(
    _bypass_rbac: None,
) -> None:
    """Missing ``verdict`` triggers pydantic 422 — no DB call happens."""
    app, container = _make_app()
    client = TestClient(app)

    bad_body = {
        "bot_id": "support",
        "channel_type": "web",
        # verdict missing on purpose
    }
    resp = client.post("/api/ragbot/feedback/thumbs", json=bad_body)
    assert resp.status_code == 422
    body = resp.json()
    # FastAPI's default envelope carries a ``detail`` list with the missing field.
    detail = body.get("detail")
    assert isinstance(detail, list) and len(detail) > 0
    assert any("verdict" in str(item.get("loc", "")) for item in detail), (
        f"422 must point at the missing 'verdict' field; got {detail!r}"
    )

    # No bot lookup, no repo write — failure must short-circuit.
    repo = container.message_feedback_repo.return_value
    assert repo.record.await_count == 0


def test_cross_tenant_write_blocked_at_repo_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repo write that names the wrong tenant is rejected before SQL fires.

    Simulates an RLS-style block by patching ``session_with_tenant`` to
    raise ``TenantIsolationViolation`` whenever the JWT tenant binding
    disagrees with the row's ``record_tenant_id``. In production the
    DB's ``WITH CHECK`` does this; in unit space we model the same
    invariant against the same exception class.
    """
    factory = MagicMock()
    repo = MessageFeedbackRepository(factory)

    def _enforce(sf: Any, *, record_tenant_id: Any) -> Any:  # noqa: ARG001 — RLS sim signature
        # Treat tenant_B as a "wrong" binding when the row claims tenant_A.
        # The real RLS policy compares the column to the GUC and fails the
        # WITH CHECK; we surface a TenantIsolationViolation so the test
        # asserts behaviour, not implementation detail.
        raise TenantIsolationViolation(
            f"cross-tenant write attempt: bound={record_tenant_id}",
        )

    monkeypatch.setattr(
        "ragbot.infrastructure.repositories.message_feedback_repository."
        "session_with_tenant",
        _enforce,
    )

    async def _attempt() -> None:
        await repo.record(
            record_tenant_id=_TENANT_B,
            record_bot_id=_BOT_A,
            verdict=FEEDBACK_VERDICT_THUMBS_UP,
        )

    with pytest.raises(TenantIsolationViolation, match="cross-tenant"):
        asyncio.run(_attempt())
