"""S5 Phase-1 — PII redaction wired into ingest boundary.

Master Finding #4 fix: ``_maybe_redact_ingest_content`` is the boundary
hook called inside ``DocumentService.ingest()`` AFTER parse and BEFORE
chunk + persist, so the documents / document_chunks rows never carry
raw email / phone / CCCD / DSN / JWT / card.

This suite locks the wire-level contract (post-facade refactor —
``_maybe_redact_ingest_content`` delegates the decision tree to
``RecapPiiDetector`` so the observability hook is owned by a single
``recap_pii_detect`` event):

  1. Missing pii_redactor OR bot_repo → passthrough (no event) —
     legacy ingest paths still work.
  2. Bot toggle OFF (default ``plan_limits.pii_redaction_enabled=False``)
     → passthrough, no ``pii_redacted`` event.
  3. Both gates open (system kill-switch + per-bot opt-in) + PII in
     content → masked content returned, ``recap_pii_detect`` emitted
     with ``decision="masked"`` and ``entity_counts`` per type.
  4. Both gates open + clean content → passthrough, no masked event.
  5. Bot-repo failure → ``pii_redaction_failed`` stage ``bot_lookup``;
     redactor failure → facade ``recap_pii_detect_failed`` event.
  6. Observability event NEVER carries raw PII — only counts + types.
"""

from __future__ import annotations

import uuid

import pytest
import structlog
import structlog.testing

from ragbot.application.services.document_service import (
    _maybe_redact_ingest_content,
)
from ragbot.infrastructure.pii.null_pii_redactor import NullPiiRedactor
from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor


class _BotCfg:
    """Minimal BotConfig stub — only ``plan_limits`` is read."""

    def __init__(self, plan_limits: dict | None = None) -> None:
        self.plan_limits = plan_limits or {}


class _BotRepo:
    """Async stub returning a fixed bot config for ``get_by_id``."""

    def __init__(self, cfg: _BotCfg | None) -> None:
        self._cfg = cfg

    async def get_by_id(self, _bot_id, *, record_tenant_id):  # noqa: ARG002
        return self._cfg


class _BrokenBotRepo:
    async def get_by_id(self, _bot_id, *, record_tenant_id):  # noqa: ARG002
        raise RuntimeError("simulated DB failure")


class _BrokenRedactor:
    @staticmethod
    def get_provider_name() -> str:
        return "broken_test"

    def redact(self, _text: str) -> tuple[str, list[dict]]:
        raise RuntimeError("simulated redactor failure")


def _capture_logs():
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    return cap


_BOT_ID = uuid.uuid4()
_TENANT_ID = uuid.uuid4()


@pytest.mark.asyncio
async def test_missing_redactor_is_passthrough() -> None:
    cap = _capture_logs()
    text_in = "alice@example.com 0901234567"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=None,
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    assert out == text_in
    events = [e["event"] for e in cap.entries]
    assert "pii_redacted" not in events
    assert "pii_redaction_failed" not in events


@pytest.mark.asyncio
async def test_missing_bot_repo_is_passthrough() -> None:
    text_in = "alice@example.com"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=None,
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    assert out == text_in


@pytest.mark.asyncio
async def test_toggle_off_is_passthrough() -> None:
    cap = _capture_logs()
    text_in = "alice@example.com 0901234567"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": False})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    assert out == text_in
    assert all(e["event"] != "pii_redacted" for e in cap.entries)


class _StubConfigService:
    """Stand-in for SystemConfigService — only ``get_bool`` is exercised."""

    def __init__(self, values: dict[str, bool] | None = None) -> None:
        self._values = values or {}

    async def get_bool(self, key: str, default: bool = False) -> bool:
        return self._values.get(key, default)


@pytest.mark.asyncio
async def test_toggle_on_redacts_and_emits_audit_event() -> None:
    """Both gates open (system kill-switch + per-bot opt-in) → content
    masked + ``recap_pii_detect`` event emitted with ``decision="masked"``.

    Production wire (``_maybe_redact_ingest_content``) routes through the
    ``RecapPiiDetector`` facade, so the observability contract is the
    detector's ``recap_pii_detect`` event (carrying ``entity_counts`` +
    ``total_masks``), not a separate ``pii_redacted`` audit row.
    """
    from ragbot.infrastructure.safety.pii_detector import RECAP_PII_STEP_NAME

    cap = _capture_logs()
    text_in = (
        "Customer profile for user alice@example.com:\n"
        "Phone: 0901234567\n"
        "CCCD: 123456789012\n"
    )
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
        config_service=_StubConfigService({"recap_pii_enabled": True}),
    )
    # The content that will be chunked + persisted has NO raw PII.
    assert "alice@example.com" not in out
    assert "0901234567" not in out
    assert "123456789012" not in out
    assert "[EMAIL]" in out
    assert "[PHONE]" in out
    assert "[CCCD]" in out

    # Structured observability event present (facade-emitted).
    masked = [
        e for e in cap.entries
        if e["event"] == RECAP_PII_STEP_NAME and e.get("decision") == "masked"
    ]
    assert len(masked) == 1
    ev = masked[0]
    assert ev["surface"] == "ingest_content"
    assert ev["provider"] == "vn_regex"
    assert ev["total_masks"] >= 3
    assert ev["entity_counts"]["EMAIL"] >= 1
    assert ev["entity_counts"]["PHONE"] >= 1
    assert ev["entity_counts"]["CCCD"] >= 1

    # CRITICAL: zero raw PII in any logged value.
    for k, v in ev.items():
        if isinstance(v, str):
            assert "alice@example.com" not in v
            assert "0901234567" not in v
            assert "123456789012" not in v


@pytest.mark.asyncio
async def test_toggle_on_clean_content_no_audit_event() -> None:
    cap = _capture_logs()
    text_in = "Tai lieu mo ta san pham A: dac tinh, gia thanh, dieu khoan."
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    assert out == text_in
    assert all(e["event"] != "pii_redacted" for e in cap.entries)


@pytest.mark.asyncio
async def test_bot_repo_failure_degrades_silent() -> None:
    cap = _capture_logs()
    text_in = "test content"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BrokenBotRepo(),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    assert out == text_in
    failed = [e for e in cap.entries if e["event"] == "pii_redaction_failed"]
    assert len(failed) == 1
    assert failed[0]["stage"] == "bot_lookup"
    assert failed[0]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_redactor_failure_degrades_silent() -> None:
    """Redactor raises mid-detect → degrade safe (return original) AND
    facade emits ``recap_pii_detect_failed`` carrying the error type.

    Production wire delegates failure handling to ``RecapPiiDetector``
    which catches strategy errors with its own
    ``recap_pii_detect_failed`` event and ``decision="strategy_error"``
    on the returned result.
    """
    cap = _capture_logs()
    text_in = "alice@example.com"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=_BrokenRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
        config_service=_StubConfigService({"recap_pii_enabled": True}),
    )
    assert out == text_in
    failed = [e for e in cap.entries if e["event"] == "recap_pii_detect_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_null_provider_with_toggle_on_passthrough() -> None:
    cap = _capture_logs()
    text_in = "alice@example.com 0901234567"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=NullPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    # Null provider returns no entities → no audit event, raw text intact.
    assert out == text_in
    assert all(e["event"] != "pii_redacted" for e in cap.entries)


@pytest.mark.asyncio
async def test_bot_not_found_is_passthrough() -> None:
    text_in = "alice@example.com"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(None),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
    )
    assert out == text_in
