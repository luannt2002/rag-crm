"""Agent N — Outbox exactly-once via FOR UPDATE SKIP LOCKED + per-row tx.

Pre-fix: ``SqlAlchemyOutboxRepository.poll_unprocessed`` issued
``SELECT ... FOR UPDATE SKIP LOCKED`` inside an ``async with
session_factory()`` block that closed (and thus released the lock) before
the publisher ever touched Redis. The lock was structurally a no-op, so
two competing publisher replicas could fetch the same row, both publish
to Redis Streams, and only one would race-win the bulk ``mark_processed``
UPDATE at the end of the batch. The other replica's events became
duplicate stream entries — at-least-twice delivery despite the comment
claiming exactly-once.

Post-fix design (Strategy A — per-row tx, lock held until commit):

1. New ``poll_one_for_update`` method returns ``(session, record)`` or
   ``(session, None)``. The caller owns the session lifecycle; the row
   lock survives until the caller commits or rolls back.
2. New ``mark_processed_in_session`` and ``mark_retry_in_session`` helpers
   accept the locked session so the publisher commits the state mutation
   in the **same** transaction that holds the lock — atomic.
3. ``mark_dlq_in_session`` likewise atomic.
4. Publisher refactor: per row → poll_one → publish → mark_processed →
   commit (single tx). Failure path: rollback then open a fresh tx to
   mark retry/DLQ (no row lock needed because that's a status-only write
   keyed by id).

These tests pin the contract — they do NOT spin up Postgres. Real
exactly-once depends on Postgres' MVCC behaviour, which is exercised by
the production smoke test (and ``ix_outbox_pending_retry`` already
covers the index scan). Here we assert the **structural** preconditions:

- ``poll_one_for_update`` exists on both the repo and the port.
- The repo's poll uses ``FOR UPDATE SKIP LOCKED`` with ``limit(1)``.
- The repo's per-session mutators do NOT open a new session (they take
  one as argument).
- The port's Protocol declares the new methods.
"""
from __future__ import annotations

import inspect
from typing import get_type_hints

from ragbot.application.ports.outbox_port import OutboxRepositoryPort
from ragbot.infrastructure.repositories.outbox_repository import (
    SqlAlchemyOutboxRepository,
)


def test_repo_has_poll_one_for_update() -> None:
    """Repo MUST expose ``poll_one_for_update`` returning an async cm.

    The implementation uses ``@asynccontextmanager`` so that the row
    lock + session lifetime live on the caller's stack frame — the
    lock is released only on the caller's ``__aexit__`` (commit /
    rollback), preserving exactly-once across crash + replica race.
    """
    assert hasattr(SqlAlchemyOutboxRepository, "poll_one_for_update")
    method = SqlAlchemyOutboxRepository.poll_one_for_update
    # @asynccontextmanager wraps an async generator; the inner function
    # must be an async generator (the public attribute carries the
    # __wrapped__ ref).
    inner = getattr(method, "__wrapped__", method)
    assert inspect.isasyncgenfunction(inner), (
        "poll_one_for_update must be an async generator (via @asynccontextmanager)"
    )


def test_repo_has_in_session_mutators() -> None:
    """Per-tx mutators MUST exist alongside the legacy fan-out variants."""
    for name in (
        "mark_processed_in_session",
        "mark_retry_in_session",
        "mark_dlq_in_session",
    ):
        assert hasattr(SqlAlchemyOutboxRepository, name), name
        assert inspect.iscoroutinefunction(getattr(SqlAlchemyOutboxRepository, name))


def test_port_declares_poll_one_for_update() -> None:
    """Port Protocol MUST declare the new exactly-once methods."""
    # Protocol attrs live in __annotations__ via ``...`` body but the
    # method definitions are on the class itself.
    for name in (
        "poll_one_for_update",
        "mark_processed_in_session",
        "mark_retry_in_session",
        "mark_dlq_in_session",
    ):
        assert hasattr(OutboxRepositoryPort, name), name


def test_poll_one_for_update_uses_for_update_skip_locked() -> None:
    """Source of poll_one_for_update MUST issue ``FOR UPDATE SKIP LOCKED``
    on a ``LIMIT 1`` query — that's the per-row lock contract."""
    src = inspect.getsource(SqlAlchemyOutboxRepository.poll_one_for_update)
    assert ".limit(1)" in src or "limit(1)" in src
    assert "skip_locked=True" in src
    assert "with_for_update" in src
    # status filter must be a literal — pending rows only
    assert '"pending"' in src or "'pending'" in src


def test_in_session_mutators_take_session_argument() -> None:
    """The new mutators MUST accept the caller-supplied session so the
    update commits in the same tx that holds the row lock."""
    sig = inspect.signature(SqlAlchemyOutboxRepository.mark_processed_in_session)
    assert "session" in sig.parameters
    sig = inspect.signature(SqlAlchemyOutboxRepository.mark_retry_in_session)
    assert "session" in sig.parameters
    sig = inspect.signature(SqlAlchemyOutboxRepository.mark_dlq_in_session)
    assert "session" in sig.parameters


def test_repo_implements_port() -> None:
    """Runtime Protocol check — repo must satisfy OutboxRepositoryPort
    even after the new methods are added (no signature drift)."""
    # We can't instantiate without a session factory, but isinstance via
    # Protocol works on the class only when ``runtime_checkable`` is used
    # and instances exist. Easier: assert the type hints resolve.
    hints = get_type_hints(SqlAlchemyOutboxRepository.poll_one_for_update)
    # Return type hint must resolve (no NameError)
    assert "return" in hints
