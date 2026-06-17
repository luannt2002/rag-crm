"""Unit tests: ChatReceivedPayload validator."""
from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from ragbot.application.dto.chat_payload import ChatReceivedPayload


def _base_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "bot_id": "bot-1",
        "channel_type": "api",
        "tenant_id": 7,
        "job_id": str(uuid4()),
        "message_id": 42,
        "conversation_id": str(uuid4()),
        "user_id": "user-1",
        "trace_id": "trace-1",
        "tenant_uuid": str(uuid4()),
        "content": "hello",
        "callback_url": None,
    }
    base.update(overrides)
    return base


def test_valid_payload_parsed() -> None:
    p = ChatReceivedPayload.model_validate(_base_payload())
    assert p.bot_id == "bot-1"
    assert p.channel_type == "api"
    assert p.message_id == 42
    assert p.tenant_id == 7


def test_missing_bot_id_raises() -> None:
    payload = _base_payload()
    payload.pop("bot_id")
    with pytest.raises(ValidationError):
        ChatReceivedPayload.model_validate(payload)


def test_empty_channel_type_raises() -> None:
    with pytest.raises(ValidationError):
        ChatReceivedPayload.model_validate(_base_payload(channel_type=""))
    with pytest.raises(ValidationError):
        ChatReceivedPayload.model_validate(_base_payload(channel_type="   "))


def test_invalid_job_id_uuid_raises() -> None:
    with pytest.raises(ValidationError):
        ChatReceivedPayload.model_validate(_base_payload(job_id="not-a-uuid"))


def test_trim_bot_id() -> None:
    p = ChatReceivedPayload.model_validate(_base_payload(bot_id="  bot-2  "))
    assert p.bot_id == "bot-2"


def test_tenant_id_backcompat_optional_int() -> None:
    """``ChatReceivedPayload.tenant_id`` is the LEGACY upstream INT alias
    kept for back-compat. Canonical identity is ``record_tenant_id``
    UUID; the worker's ``_resolve_record_tenant_id`` accepts either, but
    both being absent is rejected downstream (not at DTO level) so old
    outbox rows still drain.
    """
    # Explicit None → accepted (legacy alias, optional)
    p_none = ChatReceivedPayload.model_validate(_base_payload(tenant_id=None))
    assert p_none.tenant_id is None
    # Missing key → accepted (default None)
    payload = _base_payload()
    payload.pop("tenant_id")
    p_missing = ChatReceivedPayload.model_validate(payload)
    assert p_missing.tenant_id is None
    # Happy path — concrete INT preserved (resolver folds it into UUID).
    p2 = ChatReceivedPayload.model_validate(_base_payload(tenant_id=99))
    assert p2.tenant_id == 99


def test_tenant_uuid_optional() -> None:
    p1 = ChatReceivedPayload.model_validate(_base_payload(tenant_uuid=None))
    assert p1.tenant_uuid is None
    p2 = ChatReceivedPayload.model_validate(_base_payload(tenant_uuid=""))
    assert p2.tenant_uuid is None
    uid = str(uuid4())
    p3 = ChatReceivedPayload.model_validate(_base_payload(tenant_uuid=uid))
    assert p3.tenant_uuid == uid
    with pytest.raises(ValidationError):
        ChatReceivedPayload.model_validate(_base_payload(tenant_uuid="bad"))
