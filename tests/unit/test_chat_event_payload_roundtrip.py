"""Roundtrip: ChatReceived.to_dict() validates against ChatReceivedPayload.

``ChatReceived`` carries ``record_tenant_id`` (UUID) directly from the
DomainEvent base; legacy tenant_id (INT) and tenant_uuid (str) fields
stay as OPTIONAL aliases on the worker payload so old outbox rows can
still drain.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

import orjson
import pytest
from pydantic import ValidationError

from ragbot.application.dto.chat_payload import ChatReceivedPayload
from ragbot.domain.events.chat_events import ChatReceived
from ragbot.shared.types import (
    BotId,
    ConversationId,
    IdempotencyKey,
    JobId,
    MessageId,
    TenantId,
    TraceId,
    UserId,
)


def _serialize(ev: ChatReceived) -> dict:
    """Mirror UoW.add_outbox + outbox publisher: orjson dumps → JSON str → dict."""
    return json.loads(orjson.dumps(ev.to_dict(), default=str))


def _build_event(**overrides: object) -> ChatReceived:
    """Construct a ChatReceived with the bot identity slugs + record_tenant_id UUID."""
    from ragbot.shared.types import WorkspaceId
    record_tid = TenantId(uuid4())
    kwargs: dict[str, object] = {
        "occurred_at": datetime.now(tz=timezone.utc),
        "record_tenant_id": record_tid,
        "trace_id": TraceId(str(uuid4())),
        "workspace_id": WorkspaceId("ws-default"),
        "bot_id": "my-bot",
        "channel_type": "web",
        "job_id": JobId(uuid4()),
        "record_bot_id": BotId(uuid4()),
        "user_id": UserId("user-1"),
        "conversation_id": ConversationId(uuid4()),
        "message_id": MessageId(123),
        "content": "hello",
        "channel": "api",
        "idempotency_key": IdempotencyKey("a" * 64),
    }
    kwargs.update(overrides)
    return ChatReceived(**kwargs)  # type: ignore[arg-type]


def test_chat_received_emits_2key_bot_plus_record_tenant_uuid() -> None:
    ev = _build_event()
    payload = _serialize(ev)

    # 2-key bot identity + record_tenant_id UUID preserved end-to-end.
    assert payload["bot_id"] == "my-bot"
    assert payload["channel_type"] == "web"
    assert "record_tenant_id" in payload
    # Legacy INT shim must NOT auto-inject (test_event_payload_no_backcompat_shim).
    assert "tenant_id" not in payload


def test_chat_received_payload_validates_against_worker_schema() -> None:
    """Event payload (record_tenant_id UUID + 2-key bot + content) must
    parse cleanly through the worker DTO. Legacy aliases default to None."""
    ev = _build_event()
    payload = _serialize(ev)
    # Worker still accepts legacy ``tenant_id`` INT alias as backwards-compat
    # — supply None to pin the post-migration shape.
    payload.setdefault("tenant_id", None)

    valid = ChatReceivedPayload.model_validate(payload)
    assert valid.bot_id == "my-bot"
    assert valid.channel_type == "web"
    assert valid.user_id == "user-1"
    assert valid.message_id == 123
    assert valid.content == "hello"
    # record_tenant_id surfaces on the DTO (string UUID — resolver casts).
    assert valid.record_tenant_id is not None


def test_chat_received_payload_accepts_null_backcompat_tenant_id() -> None:
    """``tenant_id`` INT is the LEGACY alias kept optional. Null/missing
    must NOT trip the schema; resolver folds the canonical
    record_tenant_id UUID into the worker.
    """
    ev = _build_event()
    payload = _serialize(ev)
    payload["tenant_id"] = None
    valid = ChatReceivedPayload.model_validate(payload)
    assert valid.tenant_id is None


def test_chat_received_payload_rejects_missing_bot_2key() -> None:
    """Worker schema still rejects payloads missing the bot 2-key
    (``bot_id`` + ``channel_type``) — those keys remain REQUIRED."""
    ev = _build_event()
    payload = _serialize(ev)

    for missing in ("bot_id", "channel_type"):
        broken = dict(payload)
        broken.pop(missing)
        with pytest.raises(ValidationError):
            ChatReceivedPayload.model_validate(broken)


def test_chat_received_payload_record_tenant_id_optional_at_dto_level() -> None:
    """Resolver enforces non-null record_tenant_id at the worker boundary,
    not at the DTO. The DTO accepts None so old outbox rows still parse;
    ``chat_worker._resolve_record_tenant_id`` is the gate."""
    ev = _build_event()
    payload = _serialize(ev)
    payload["record_tenant_id"] = None
    valid = ChatReceivedPayload.model_validate(payload)
    assert valid.record_tenant_id is None
