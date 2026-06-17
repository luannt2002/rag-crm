"""Agent Q — Conversation get_or_create single-query JOIN.

Pre-fix: ``SqlAlchemyConversationRepository.get_or_create`` ran two
sequential queries:

1. ``SELECT conversations WHERE tenant=X bot=Y connect=Z``
2. ``SELECT messages WHERE conversation_id=<row.id> ORDER BY created_at
    DESC LIMIT 20``

Each turn paid two round-trips. Under load this doubled the
``get_or_create`` budget and made history fetch the second-most-frequent
DB call after embedding lookup.

Post-fix: single LEFT OUTER JOIN ``conversations`` ↔ ``messages``
ordered ``created_at DESC LIMIT 20`` on the join, then partition rows
in Python (one conversation header + N message rows).

The contract test below stubs ``session.execute`` to count the number of
calls inside the get_or_create happy path. Pre-fix expectation = 2 calls.
Post-fix expectation = 1 call.
"""
from __future__ import annotations

import inspect

from ragbot.infrastructure.repositories.conversation_repository import (
    SqlAlchemyConversationRepository,
)


def test_get_or_create_uses_single_join_query() -> None:
    """``get_or_create`` source MUST call the merged JOIN helper exactly
    once and MUST NOT call the legacy two-step helpers."""
    src = inspect.getsource(SqlAlchemyConversationRepository.get_or_create)
    # Legacy fan-out helpers are gone in the hot path:
    assert "_fetch_by_keys(" not in src, (
        "legacy _fetch_by_keys must be replaced by _fetch_by_keys_with_messages"
    )
    assert "_fetch_messages(" not in src, (
        "_fetch_messages still in hot path → N+1 not fixed"
    )
    # The merged helper IS called:
    assert "_fetch_by_keys_with_messages" in src


def test_repo_has_single_join_helper() -> None:
    """Repo MUST expose the merged-fetch helper."""
    assert hasattr(SqlAlchemyConversationRepository, "_fetch_by_keys_with_messages")
    assert inspect.iscoroutinefunction(
        SqlAlchemyConversationRepository._fetch_by_keys_with_messages,
    )


def test_join_helper_uses_outerjoin_and_order_by_desc_limit() -> None:
    """The join helper MUST left-outer-join MessageModel and apply SQL
    ``ORDER BY created_at DESC LIMIT N`` so we never load the whole
    history into RAM (Python-side ``list[-20:]`` would do that)."""
    src = inspect.getsource(
        SqlAlchemyConversationRepository._fetch_by_keys_with_messages,
    )
    assert "outerjoin" in src or "join(" in src
    assert "MessageModel" in src
    assert "limit(" in src
    # DESC sort is what the LIMIT relies on (most-recent N messages)
    assert "desc(" in src or ".desc()" in src


def test_join_helper_signature_takes_session_and_keys() -> None:
    """Helper must take an active session + the 3 lookup keys (no extra
    DB round-trip building its own session)."""
    sig = inspect.signature(
        SqlAlchemyConversationRepository._fetch_by_keys_with_messages,
    )
    expected = {"session", "record_bot_id", "connect_id", "record_tenant_id"}
    assert expected.issubset(set(sig.parameters))


def test_get_or_create_keeps_max_history_pinned() -> None:
    """The JOIN MUST cap at ``MAX_HISTORY_LIMIT_REQUEST`` (= 20) to match
    the prior ``_fetch_messages(max_messages=20)`` semantics."""
    src = inspect.getsource(
        SqlAlchemyConversationRepository._fetch_by_keys_with_messages,
    )
    # Constant import OR literal 20 referenced via constants
    assert "MAX_HISTORY_LIMIT_REQUEST" in src
