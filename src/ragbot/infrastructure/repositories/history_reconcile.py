"""History reconcile — unify two multi-turn history stores on the read path.

Ragbot grew two independent conversation-history stores that never knew
about each other:

- ``chat_histories`` — written by the HTTP/SSE transports
  (``chat_stream.py`` + ``test_chat/chat_routes.py``), keyed by the
  external identity pair ``(record_bot_id, channel_type, connect_id)``.
- ``messages`` / ``conversations`` — written by the queued worker
  transport (``chat_worker``), keyed by the internal
  ``record_conversation_id`` whose parent ``conversations`` row carries
  the same ``(record_bot_id, connect_id)`` external pair.

Because each transport read only its own store, a user who sent turn 1
over SSE and turn 2 over the worker queue (or vice-versa) lost the
earlier turn — multi-turn context silently broke across transports
(MT-1).

This module reconciles the two stores **at read time** only — no schema
change, no per-bot logic, no second write path. Given the shared
external pair ``(record_bot_id, connect_id)`` it reads both stores,
merges by ``created_at`` ascending, drops adjacent exact duplicates
(same ``role`` + ``content`` written to both stores within the same
turn), and returns the most-recent ``limit`` turns oldest-first — the
exact shape every transport already feeds the pipeline
(``[{"role": ..., "content": ...}]``).

Sacred-rule alignment
~~~~~~~~~~~~~~~~~~~~~~~
- Domain-neutral: only generic identity keys + role/content; no
  tenant/industry/brand literal.
- Zero-hardcode: limit comes from the caller (config-driven upstream);
  the SSoT default ``MAX_HISTORY_LIMIT_REQUEST`` is imported, not inlined.
- Read-path only: application does NOT inject or override answer text;
  it only re-orders rows the LLM already would have seen.
- Multi-tenant: reads are scoped by ``record_bot_id`` (unique internal
  key) + ``connect_id`` exactly as the existing per-store readers were.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.shared.constants import MAX_HISTORY_LIMIT_REQUEST

# Sentinel timestamp for rows whose store left ``created_at`` NULL — sort
# them first so a legitimately-timestamped row from the other store always
# wins the recency cut. ``datetime.min`` (tz-aware) is the natural floor.
_EPOCH_FLOOR: Final[datetime] = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class HistoryTurn:
    """One history row from either store, normalised for the merge.

    ``created_at`` may be ``None`` when a store omitted it; the merge
    treats that as the epoch floor so timestamped rows take precedence.
    """

    role: str
    content: str
    created_at: datetime | None


def _sort_key(turn: HistoryTurn) -> datetime:
    """Sort by wall-clock time; NULL timestamps fall to the epoch floor."""
    return turn.created_at or _EPOCH_FLOOR


def merge_history_sources(
    sources: list[list[HistoryTurn]],
    *,
    limit: int = MAX_HISTORY_LIMIT_REQUEST,
) -> list[dict[str, str]]:
    """Merge N history sources by time → most-recent ``limit`` oldest-first.

    @param sources: one list of :class:`HistoryTurn` per store. Each list
        may be in any order; this function sorts globally by ``created_at``.
    @param limit: keep at most this many of the most-recent turns. Values
        ``<= 0`` are treated as "no rows" (history disabled) to match the
        zero-hardcode convention where ``0`` means OFF.
    @return: ``[{"role": ..., "content": ...}]`` oldest-first, ready to
        feed the pipeline's ``conversation_history``.

    Dedup: a turn written to BOTH stores in the same exchange appears
    twice with identical ``(role, content)``. After the global time sort,
    an entry equal to the immediately-preceding kept entry is dropped, so
    the merged history never double-counts a single logical turn.
    """
    if limit <= 0:
        return []

    flat: list[HistoryTurn] = [turn for src in sources for turn in src if turn.content]
    flat.sort(key=_sort_key)

    deduped: list[HistoryTurn] = []
    for turn in flat:
        if deduped and deduped[-1].role == turn.role and deduped[-1].content == turn.content:
            # Same logical turn mirrored into the other store — keep one.
            continue
        deduped.append(turn)

    recent = deduped[-limit:]
    return [{"role": t.role, "content": t.content} for t in recent]


class HistoryReconciler:
    """Read both history stores for an external identity pair + merge.

    One instance per request is cheap (holds only a session factory). The
    reconcile is read-only; nothing here writes either store.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def load(
        self,
        *,
        record_bot_id: UUID,
        connect_id: str,
        channel_type: str | None = None,
        limit: int = MAX_HISTORY_LIMIT_REQUEST,
    ) -> list[dict[str, str]]:
        """Load + merge history from ``chat_histories`` and ``messages``.

        @param record_bot_id: internal bot UUID (unique key — scopes both
            stores).
        @param connect_id: external user id (shared key across stores).
        @param channel_type: when given, scopes the ``chat_histories`` read
            to one channel (the HTTP/SSE readers always pass it). The
            ``messages`` store has no ``channel_type`` column; it is scoped
            by the parent conversation's ``(record_bot_id, connect_id)``.
        @param limit: most-recent turns to keep (config-driven upstream).
        @return: merged ``conversation_history`` oldest-first. Empty list
            on any DB error — multi-turn history is best-effort and must
            never 500 the chat call.
        """
        if limit <= 0:
            return []

        try:
            async with self._sf() as session:
                http_rows = await self._read_chat_histories(
                    session,
                    record_bot_id=record_bot_id,
                    connect_id=connect_id,
                    channel_type=channel_type,
                    limit=limit,
                )
                worker_rows = await self._read_messages(
                    session,
                    record_bot_id=record_bot_id,
                    connect_id=connect_id,
                    limit=limit,
                )
        except SQLAlchemyError:
            # Best-effort: degrade to empty history rather than failing the
            # turn. Caller already logs the wider failure context.
            return []

        return merge_history_sources([http_rows, worker_rows], limit=limit)

    async def load_chat_histories_turns(
        self,
        *,
        record_bot_id: UUID,
        connect_id: str,
        channel_type: str | None = None,
        limit: int = MAX_HISTORY_LIMIT_REQUEST,
    ) -> list[HistoryTurn]:
        """Return only the ``chat_histories`` (HTTP/SSE) store as timestamped turns.

        Used by the worker transport, which already holds the ``messages``
        side via its conversation aggregate (with ``created_at``) and only
        needs the *other* store's rows to time-merge them. Returns an empty
        list on DB error (best-effort, never raises).
        """
        if limit <= 0:
            return []
        try:
            async with self._sf() as session:
                return await self._read_chat_histories(
                    session,
                    record_bot_id=record_bot_id,
                    connect_id=connect_id,
                    channel_type=channel_type,
                    limit=limit,
                )
        except SQLAlchemyError:
            return []

    @staticmethod
    async def _read_chat_histories(
        session: AsyncSession,
        *,
        record_bot_id: UUID,
        connect_id: str,
        channel_type: str | None,
        limit: int,
    ) -> list[HistoryTurn]:
        """Most-recent ``limit`` rows from ``chat_histories`` (HTTP/SSE)."""
        channel_clause = "AND channel_type = :ch" if channel_type is not None else ""
        rows = (
            await session.execute(
                text(
                    f"""
                    SELECT role, content, created_at
                    FROM chat_histories
                    WHERE record_bot_id = :bid AND connect_id = :cid
                    {channel_clause}
                    ORDER BY id DESC
                    LIMIT :lim
                    """,  # noqa: S608 — channel_clause is a fixed literal, not user input.
                ),
                {
                    "bid": record_bot_id,
                    "cid": connect_id,
                    "ch": channel_type,
                    "lim": limit,
                },
            )
        ).fetchall()
        return [HistoryTurn(role=r[0], content=r[1], created_at=r[2]) for r in rows]

    @staticmethod
    async def _read_messages(
        session: AsyncSession,
        *,
        record_bot_id: UUID,
        connect_id: str,
        limit: int,
    ) -> list[HistoryTurn]:
        """Most-recent ``limit`` rows from ``messages`` (worker transport).

        Joined to ``conversations`` on the shared external pair so the read
        is scoped exactly like the per-store worker reader. Soft-deleted
        rows (GDPR null-out → ``deleted_at`` set) are excluded.
        """
        rows = (
            await session.execute(
                text(
                    """
                    SELECT m.role, m.content, m.created_at
                    FROM messages m
                    JOIN conversations c
                      ON c.id = m.record_conversation_id
                    WHERE c.record_bot_id = :bid
                      AND c.connect_id = :cid
                      AND m.deleted_at IS NULL
                    ORDER BY m.created_at DESC
                    LIMIT :lim
                    """,
                ),
                {"bid": record_bot_id, "cid": connect_id, "lim": limit},
            )
        ).fetchall()
        return [HistoryTurn(role=r[0], content=r[1], created_at=r[2]) for r in rows]


__all__ = ["HistoryReconciler", "HistoryTurn", "merge_history_sources"]
