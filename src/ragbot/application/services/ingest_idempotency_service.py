"""BE-to-BE upload idempotency — case study P0-3 (2026-05-18).

Partner BE services upload documents over HTTP; gateway / network
retries can cause the same logical upload attempt to land twice at
the worker. Without an idempotency record the second attempt creates
orphan chunks + duplicate FAQ entries that retrieval surfaces twice.

Workflow:

1. Partner generates an opaque ``X-Idempotency-Key`` per logical
   attempt (UUID is fine).
2. The HTTP endpoint passes ``(record_tenant_id, workspace_id,
   idempotency_key, canonical_request_body)`` to
   :meth:`IngestIdempotencyService.check_and_record`.
3. First call returns ``(is_duplicate=False, existing_doc_id=None)``
   and the endpoint proceeds with normal ingest. The service stamps
   the row at state ``"processing"`` with ``expires_at`` = now + TTL.
4. Subsequent call within the TTL window with the SAME key returns
   ``(is_duplicate=True, existing_doc_id=<UUID>)`` so the endpoint can
   reply 200 with the original document_id instead of starting a
   second ingest.
5. Worker calls :meth:`mark_done` / :meth:`mark_failed` once the
   document persists so a follow-up retry sees a stable state.

Edge cases:

- **Same key, different payload**: ``request_hash`` mismatch returns
  is_duplicate=True but logs ``ingest_idempotency_hash_mismatch`` so
  ops can attribute partner-side bugs. We choose "honour first" over
  "reject second" — replaying a stale payload is safer than rejecting
  a legitimate retry whose body the gateway accidentally hashed
  differently.
- **Expired row**: a key whose ``expires_at`` is in the past behaves
  identically to a missing key — the service inserts a fresh row.
  The unique-constraint guarantees the cleanup script (sweep) trailing
  the request side never races into a dup insert.
- **Cross-tenant**: unique constraint includes ``record_tenant_id``,
  so the same key value cannot leak across tenants.

Tests: ``tests/unit/test_ingest_idempotency_service.py``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ragbot.infrastructure.db.models import IngestIdempotencyKeyModel
from ragbot.shared.constants import (
    DEFAULT_INGEST_IDEMPOTENCY_TTL_HOURS,
    INGEST_IDEMPOTENCY_STATE_DONE,
    INGEST_IDEMPOTENCY_STATE_FAILED,
    INGEST_IDEMPOTENCY_STATE_PROCESSING,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IdempotencyResult:
    """Outcome of a :meth:`check_and_record` call.

    Attributes:
        is_duplicate: ``True`` when an unexpired row already exists for
            ``(record_tenant_id, workspace_id, idempotency_key)``.
        existing_doc_id: When ``is_duplicate=True``, the document UUID
            from the original successful attempt (may be ``None`` if
            the original is still ``"processing"``).
        existing_status: Status of the original row when duplicate.
    """

    is_duplicate: bool
    existing_doc_id: UUID | None
    existing_status: str | None


def canonical_request_hash(payload: bytes | str) -> str:
    """Compute the request-body fingerprint stored on the idempotency
    row.

    SHA-256 keeps the column fixed at 64 hex chars. Caller is
    responsible for canonicalising the payload (JSON re-serialisation
    so key-order changes do not yield a different hash). Pure helper
    so callers + tests can verify ``request_hash`` parity.
    """
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class IngestIdempotencyService:
    """Insert + lookup idempotency records for BE-to-BE upload retries.

    The service expects a session_factory that produces async
    SQLAlchemy sessions (see :mod:`bootstrap`). RLS is applied at the
    PostgreSQL layer (see alembic 010j) — the caller is responsible
    for setting ``app.tenant_id`` on the connection before SELECT /
    INSERT. The service explicitly filters on ``record_tenant_id`` so
    a misconfigured RLS context still cannot leak across tenants.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[object],
        ttl_hours: int = DEFAULT_INGEST_IDEMPOTENCY_TTL_HOURS,
    ) -> None:
        self._sf = session_factory
        self._ttl_hours = int(ttl_hours)

    async def check_and_record(
        self,
        *,
        record_tenant_id: UUID,
        workspace_id: str,
        idempotency_key: str,
        request_hash: str,
    ) -> IdempotencyResult:
        """Insert a new ``"processing"`` row OR return the existing one.

        Atomic via PostgreSQL ``UniqueConstraint``: first INSERT wins;
        the colliding INSERT raises ``IntegrityError`` which we catch
        + fall back to a SELECT.

        Args:
            record_tenant_id: tenant scope (REQUIRED — never NULL on
                this table).
            workspace_id: workspace slug (REQUIRED — pass
                ``WORKSPACE_SYSTEM_SLUG`` for tenant-level uploads).
            idempotency_key: opaque partner-supplied identifier.
            request_hash: SHA-256 of the canonical request body
                (see :func:`canonical_request_hash`).

        Returns:
            ``IdempotencyResult`` with ``is_duplicate=False`` on first
            attempt (caller proceeds with ingest) or
            ``is_duplicate=True`` with the existing document_id
            (caller responds 200 with the original document).
        """
        expires_at = datetime.now(tz=timezone.utc) + timedelta(
            hours=self._ttl_hours,
        )
        new_row = IngestIdempotencyKeyModel(
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            record_document_id=None,
            status=INGEST_IDEMPOTENCY_STATE_PROCESSING,
            expires_at=expires_at,
        )
        async with self._sf() as session:  # type: ignore[misc]
            session.add(new_row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                # Collision — the unique constraint matched an existing
                # row. Pull it inside its own SELECT so we know whether
                # the original is still processing or already done.
                existing = await session.scalar(
                    select(IngestIdempotencyKeyModel).where(
                        IngestIdempotencyKeyModel.record_tenant_id
                        == record_tenant_id,
                        IngestIdempotencyKeyModel.workspace_id
                        == workspace_id,
                        IngestIdempotencyKeyModel.idempotency_key
                        == idempotency_key,
                    )
                )
                if existing is None:
                    # Race-window edge: row got deleted between INSERT
                    # collision + SELECT. Retry once — the cleanup
                    # sweeper deletes expired rows, so the second
                    # INSERT will succeed.
                    logger.info(
                        "ingest_idempotency_race_retry",
                        record_tenant_id=str(record_tenant_id),
                        workspace_id=workspace_id,
                    )
                    return await self.check_and_record(
                        record_tenant_id=record_tenant_id,
                        workspace_id=workspace_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                # Expired row?  Behave like missing — delete + retry.
                if existing.expires_at < datetime.now(tz=timezone.utc):
                    await session.delete(existing)
                    await session.commit()
                    return await self.check_and_record(
                        record_tenant_id=record_tenant_id,
                        workspace_id=workspace_id,
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                    )
                if existing.request_hash != request_hash:
                    logger.warning(
                        "ingest_idempotency_hash_mismatch",
                        record_tenant_id=str(record_tenant_id),
                        workspace_id=workspace_id,
                        expected_hash=existing.request_hash[:12],
                        got_hash=request_hash[:12],
                    )
                return IdempotencyResult(
                    is_duplicate=True,
                    existing_doc_id=existing.record_document_id,
                    existing_status=existing.status,
                )
        # First-write path.
        return IdempotencyResult(
            is_duplicate=False,
            existing_doc_id=None,
            existing_status=None,
        )

    async def mark_done(
        self,
        *,
        record_tenant_id: UUID,
        workspace_id: str,
        idempotency_key: str,
        record_document_id: UUID,
    ) -> None:
        """Persist the document_id + state ``"done"`` so subsequent
        retries can short-circuit with the original document.

        Called by the ingest worker once the document row commits.
        """
        await self._update_state(
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            status=INGEST_IDEMPOTENCY_STATE_DONE,
            record_document_id=record_document_id,
        )

    async def mark_failed(
        self,
        *,
        record_tenant_id: UUID,
        workspace_id: str,
        idempotency_key: str,
    ) -> None:
        """Mark a terminal failure so subsequent retries can decide
        whether to retry or surface the failure to the partner."""
        await self._update_state(
            record_tenant_id=record_tenant_id,
            workspace_id=workspace_id,
            idempotency_key=idempotency_key,
            status=INGEST_IDEMPOTENCY_STATE_FAILED,
            record_document_id=None,
        )

    async def _update_state(
        self,
        *,
        record_tenant_id: UUID,
        workspace_id: str,
        idempotency_key: str,
        status: str,
        record_document_id: UUID | None,
    ) -> None:
        async with self._sf() as session:  # type: ignore[misc]
            row = await session.scalar(
                select(IngestIdempotencyKeyModel).where(
                    IngestIdempotencyKeyModel.record_tenant_id
                    == record_tenant_id,
                    IngestIdempotencyKeyModel.workspace_id == workspace_id,
                    IngestIdempotencyKeyModel.idempotency_key
                    == idempotency_key,
                )
            )
            if row is None:
                logger.warning(
                    "ingest_idempotency_update_missing_row",
                    record_tenant_id=str(record_tenant_id),
                    workspace_id=workspace_id,
                    status=status,
                )
                return
            row.status = status
            if record_document_id is not None:
                row.record_document_id = record_document_id
            await session.commit()
