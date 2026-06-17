"""Pin test — ``TestChatAsyncRequest.callback_url`` field contract.

Verifies:
- Field is optional (None when absent)
- Field is accepted when a valid URL string is provided
- Field is passed through to the model as-is (no schema-level SSRF check;
  SSRF validation happens at the handler layer via ``_is_url_safe``)
- Other required fields still validated correctly
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from ragbot.interfaces.http.routes.chat_async import TestChatAsyncRequest


def _base_kwargs(**overrides):
    """Minimal valid request body."""
    return {
        "bot_id": "support",
        "channel_type": "web",
        "question": "What is the refund policy?",
        **overrides,
    }


def test_callback_url_field_is_optional():
    """``callback_url`` absent → None (field is optional)."""
    req = TestChatAsyncRequest(**_base_kwargs())
    assert req.callback_url is None


def test_callback_url_field_accepted_https():
    """``callback_url`` with HTTPS URL is accepted by the schema."""
    req = TestChatAsyncRequest(
        **_base_kwargs(callback_url="https://example.com/webhook")
    )
    assert req.callback_url == "https://example.com/webhook"


def test_callback_url_field_accepted_http():
    """``callback_url`` with HTTP URL is accepted by the schema.

    Schema does NOT block HTTP — SSRF check happens at the handler
    layer (``_is_url_safe``). Tests for the SSRF check are in
    ``test_chat_worker_callback_validate_url.py``.
    """
    req = TestChatAsyncRequest(
        **_base_kwargs(callback_url="http://webhook.example.org/recv")
    )
    assert req.callback_url == "http://webhook.example.org/recv"


def test_callback_url_field_accepts_explicit_none():
    """Explicitly passing ``None`` is the same as omitting."""
    req = TestChatAsyncRequest(**_base_kwargs(callback_url=None))
    assert req.callback_url is None


def test_callback_url_field_is_string_type():
    """Schema does NOT coerce non-string to URL — a bare integer must fail."""
    with pytest.raises(ValidationError):
        TestChatAsyncRequest(**_base_kwargs(callback_url=12345))


def test_required_fields_still_enforced():
    """``bot_id`` remains required — omitting it raises ``ValidationError``."""
    with pytest.raises(ValidationError):
        TestChatAsyncRequest(
            channel_type="web",
            question="hello",
            callback_url="https://example.com/wh",
        )


def test_callback_url_survives_round_trip_model_dump():
    """``model_dump()`` includes ``callback_url`` so the route can pipe it
    to the Redis Stream payload.
    """
    url = "https://partner.example.io/ragbot/callback"
    req = TestChatAsyncRequest(**_base_kwargs(callback_url=url))
    dumped = req.model_dump()
    assert dumped["callback_url"] == url
