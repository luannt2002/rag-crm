"""S5 Phase-1 — PII redaction wired into chat boundary.

Master Finding #4 fix: ``_maybe_redact_chat_query`` is the boundary
hook that sits between ``valid.content`` and the message-persist /
LLM call so no raw PII reaches the DB or the model.

This suite locks the wire-level contract:

  1. Toggle OFF (default ``plan_limits.pii_redaction_enabled=False``)
     → text passes through verbatim, no audit event emitted.
  2. Toggle ON + PII present → masked string returned, ``pii_redacted``
     audit event emitted with ``mask_count`` and per-type histogram.
  3. Toggle ON + clean text → returns input unchanged, NO audit event
     emitted (mask_count=0 is degenerate, suppressed by design).
  4. Toggle ON + redactor raises → degrade silent (return input,
     ``pii_redaction_failed`` audit event, no exception bubble).
  5. Audit event NEVER carries raw PII — only mask_count / mask_types.
"""

from __future__ import annotations

import structlog
import structlog.testing

from ragbot.infrastructure.pii.null_pii_redactor import NullPiiRedactor
from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor
from ragbot.interfaces.workers.chat_worker import _maybe_redact_chat_query


class _BotCfg:
    """Minimal BotConfig stub — only the ``plan_limits`` attr is read."""

    def __init__(self, plan_limits: dict | None = None) -> None:
        self.plan_limits = plan_limits or {}


class _BrokenRedactor:
    """Raises on redact() to exercise the graceful-degradation branch."""

    @staticmethod
    def get_provider_name() -> str:
        return "broken_test"

    def redact(self, _text: str) -> tuple[str, list[dict]]:
        raise RuntimeError("simulated redactor failure")


def _capture_logs():
    structlog.configure(
        processors=[structlog.testing.LogCapture()],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    return cap


def test_toggle_off_passes_through_no_audit_event() -> None:
    cap = _capture_logs()
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": False})
    text_in = "email: leaky@example.com phone: 0901234567"
    out = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=VnRegexPiiRedactor(),
        record_tenant_id=None,
        record_bot_id="bot-stub",
    )
    assert out == text_in, "OFF toggle MUST pass text through verbatim"
    events = [e["event"] for e in cap.entries]
    assert "pii_redacted" not in events
    assert "pii_redaction_failed" not in events


def test_toggle_on_redacts_and_emits_audit_event_with_mask_count() -> None:
    cap = _capture_logs()
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": True})
    text_in = "Contact alice@example.com or call 0901234567 today"
    out = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=VnRegexPiiRedactor(),
        record_tenant_id="tenant-uuid-stub",
        record_bot_id="bot-uuid-stub",
    )
    # Raw PII gone from the wire output (string the LLM will see).
    assert "alice@example.com" not in out
    assert "0901234567" not in out
    assert "[EMAIL]" in out
    assert "[PHONE]" in out

    # Audit event present with structured count.
    redacted_events = [e for e in cap.entries if e["event"] == "pii_redacted"]
    assert len(redacted_events) == 1, (
        f"expected 1 pii_redacted event, got {len(redacted_events)}"
    )
    ev = redacted_events[0]
    assert ev["mask_count"] >= 2  # email + phone
    assert ev["mask_types"]["EMAIL"] >= 1
    assert ev["mask_types"]["PHONE"] >= 1
    assert ev["surface"] == "chat_query"
    assert ev["provider"] == "vn_regex"

    # CRITICAL: NO raw PII in audit log.
    for k, v in ev.items():
        if isinstance(v, str):
            assert "alice@example.com" not in v, f"raw EMAIL leaked into audit log field {k}"
            assert "0901234567" not in v, f"raw PHONE leaked into audit log field {k}"


def test_toggle_on_with_clean_text_no_audit_event() -> None:
    cap = _capture_logs()
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": True})
    text_in = "Xin chao, gia san pham A la bao nhieu?"
    out = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=VnRegexPiiRedactor(),
        record_tenant_id="t",
        record_bot_id="b",
    )
    assert out == text_in
    events = [e["event"] for e in cap.entries]
    assert "pii_redacted" not in events, (
        "no PII present → no audit event (mask_count=0 is degenerate, suppressed)"
    )


def test_toggle_on_redactor_failure_degrades_silent() -> None:
    cap = _capture_logs()
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": True})
    text_in = "test content"
    # MUST NOT raise.
    out = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=_BrokenRedactor(),
        record_tenant_id="t",
        record_bot_id="b",
    )
    assert out == text_in, "redactor failure MUST degrade to passthrough"
    events = [e["event"] for e in cap.entries]
    assert "pii_redaction_failed" in events
    failed = next(e for e in cap.entries if e["event"] == "pii_redaction_failed")
    assert failed["error_type"] == "RuntimeError"


def test_null_redactor_with_toggle_on_no_audit_event() -> None:
    """Provider="null" returns no entities → no audit event (passthrough)."""
    cap = _capture_logs()
    bot_cfg = _BotCfg(plan_limits={"pii_redaction_enabled": True})
    text_in = "alice@example.com 0901234567"
    out = _maybe_redact_chat_query(
        text_in,
        bot_cfg=bot_cfg,
        pii_redactor=NullPiiRedactor(),
        record_tenant_id="t",
        record_bot_id="b",
    )
    # Null provider = passthrough; raw text preserved.
    assert out == text_in
    events = [e["event"] for e in cap.entries]
    assert "pii_redacted" not in events
