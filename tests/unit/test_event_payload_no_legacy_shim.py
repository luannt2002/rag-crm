"""DomainEvent.to_dict must not auto-inject `tenant_id` from record_tenant_id."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar
from uuid import uuid4

from ragbot.domain.events.base import DomainEvent
from ragbot.domain.events.chat_events import ChatReceived


@dataclass(frozen=True, kw_only=True, slots=True)
class _BareEvent(DomainEvent):
    event_type: ClassVar[str] = "test.bare.v1"


def test_base_event_does_not_inject_external_tenant_id():
    """Bare DomainEvent with no `tenant_id` field must NOT carry one in to_dict."""
    ev = _BareEvent(
        occurred_at=datetime.now(tz=timezone.utc),
        record_tenant_id=uuid4(),
        trace_id="t",
    )
    d = ev.to_dict()
    assert "record_tenant_id" in d
    assert "tenant_id" not in d, (
        "Legacy shim must be gone — only events declaring an explicit "
        "`tenant_id` field may carry one in their payload."
    )


def test_chat_received_carries_record_tenant_id_uuid_only():
    """``ChatReceived`` carries ``record_tenant_id`` UUID only; legacy
    ``tenant_id: int`` + ``tenant_uuid: str`` shims removed. Payload
    survives ``to_dict`` round-trip with the UUID intact.
    """
    record_tid = uuid4()
    ev = ChatReceived(
        occurred_at=datetime.now(tz=timezone.utc),
        record_tenant_id=record_tid,
        trace_id="t",
        workspace_id="ws-default",
        bot_id="my-bot",
        channel_type="web",
        job_id=uuid4(),
        record_bot_id=uuid4(),
        user_id="u1",
        conversation_id=uuid4(),
        message_id=1,
        content="hi",
        channel="web",
        idempotency_key="k",
    )
    d = ev.to_dict()
    # UUID identity intact, no legacy INT shim.
    assert d["record_tenant_id"] == record_tid
    assert "tenant_id" not in d, (
        "ChatReceived must NOT carry legacy ``tenant_id`` int — record_tenant_id UUID is canonical."
    )
    assert "tenant_uuid" not in d, (
        "ChatReceived must NOT duplicate the UUID into a ``tenant_uuid`` string field."
    )
