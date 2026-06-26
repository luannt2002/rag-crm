"""MT-1 — multi-turn history reconcile across two transport stores.

Bug reproduced: Ragbot writes conversation history to TWO independent
stores — ``chat_histories`` (HTTP/SSE transports) and
``messages``/``conversations`` (queued worker transport). Each transport
historically read only its own store, so a user whose turn 1 landed on
one transport and turn 2 on the other lost the earlier turn — the LLM
saw empty (or partial) multi-turn context.

These tests pin the read-path merge (:func:`merge_history_sources`) that
reconciles both stores by ``created_at`` so turn N always sees turns
``1..N-1`` regardless of which transport wrote them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ragbot.infrastructure.repositories.history_reconcile import (
    HistoryTurn,
    merge_history_sources,
)

_T0 = datetime(2026, 6, 26, 10, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def test_turn2_other_transport_sees_turn1() -> None:
    """Turn 1 written to store A, turn 2 to store B → turn 2's history
    load surfaces turn 1 (the core MT-1 regression).
    """
    # Store A (e.g. chat_histories / HTTP-SSE): turn 1 user + assistant.
    store_a = [
        HistoryTurn(role="user", content="cau hoi 1", created_at=_at(0)),
        HistoryTurn(role="assistant", content="tra loi 1", created_at=_at(1)),
    ]
    # Store B (e.g. messages / worker): turn 2 user only (current turn).
    store_b = [
        HistoryTurn(role="user", content="cau hoi 2", created_at=_at(10)),
    ]

    merged = merge_history_sources([store_a, store_b], limit=20)

    # Turn 1 (from the OTHER transport) must be present and ordered first.
    assert merged == [
        {"role": "user", "content": "cau hoi 1"},
        {"role": "assistant", "content": "tra loi 1"},
        {"role": "user", "content": "cau hoi 2"},
    ]


def test_global_time_order_interleaved_transports() -> None:
    """Turns alternate transports every exchange; merged history is in
    strict wall-clock order, not store-grouped order.
    """
    http = [
        HistoryTurn(role="user", content="q1", created_at=_at(0)),
        HistoryTurn(role="assistant", content="a1", created_at=_at(1)),
        HistoryTurn(role="user", content="q3", created_at=_at(20)),
        HistoryTurn(role="assistant", content="a3", created_at=_at(21)),
    ]
    worker = [
        HistoryTurn(role="user", content="q2", created_at=_at(10)),
        HistoryTurn(role="assistant", content="a2", created_at=_at(11)),
    ]

    merged = merge_history_sources([http, worker], limit=20)
    contents = [m["content"] for m in merged]
    assert contents == ["q1", "a1", "q2", "a2", "q3", "a3"]


def test_duplicate_turn_mirrored_into_both_stores_deduped() -> None:
    """A turn written to BOTH stores in the same exchange must appear once."""
    ts_user = _at(0)
    ts_assistant = _at(1)
    store_a = [
        HistoryTurn(role="user", content="hello", created_at=ts_user),
        HistoryTurn(role="assistant", content="hi there", created_at=ts_assistant),
    ]
    store_b = [
        HistoryTurn(role="user", content="hello", created_at=ts_user),
        HistoryTurn(role="assistant", content="hi there", created_at=ts_assistant),
    ]

    merged = merge_history_sources([store_a, store_b], limit=20)
    assert merged == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_limit_keeps_most_recent_turns_oldest_first() -> None:
    """When merged size exceeds ``limit``, keep the most-recent N
    but return them oldest-first (pipeline contract).
    """
    turns = [
        HistoryTurn(role="user", content=f"q{i}", created_at=_at(i))
        for i in range(6)
    ]
    merged = merge_history_sources([turns], limit=3)
    assert [m["content"] for m in merged] == ["q3", "q4", "q5"]


def test_limit_zero_returns_empty() -> None:
    """``limit <= 0`` means history disabled (zero == OFF convention)."""
    turns = [HistoryTurn(role="user", content="q", created_at=_at(0))]
    assert merge_history_sources([turns], limit=0) == []


def test_null_timestamp_sorts_before_timestamped() -> None:
    """A row with no ``created_at`` sorts to the epoch floor so a
    legitimately-timestamped row from the other store wins recency.
    """
    no_ts = [HistoryTurn(role="user", content="legacy", created_at=None)]
    ts = [HistoryTurn(role="assistant", content="fresh", created_at=_at(5))]
    merged = merge_history_sources([no_ts, ts], limit=20)
    assert [m["content"] for m in merged] == ["legacy", "fresh"]


def test_empty_content_rows_dropped() -> None:
    """Soft-deleted / empty rows contribute nothing to history."""
    turns = [
        HistoryTurn(role="user", content="", created_at=_at(0)),
        HistoryTurn(role="assistant", content="real", created_at=_at(1)),
    ]
    merged = merge_history_sources([turns], limit=20)
    assert merged == [{"role": "assistant", "content": "real"}]
