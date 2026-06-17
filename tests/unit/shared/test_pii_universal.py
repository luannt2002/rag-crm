"""Unit tests — universal PII redaction at the observability boundary.

Plan F1 (D2). Verifies:

  * Regex coverage: email, +84/0xxx phone, 12-digit CCCD, 9-digit CMND,
    10-16 digit bank account.
  * Per-bot opt-in: default OFF passes text through; both flags ON
    redacts.
  * Mapping recursion: nested dicts/lists masked, audit event emitted
    once per top-level call.
  * Surface filter: the ``surface`` tag flows into the audit event.
"""

from __future__ import annotations

from typing import Any

import structlog

from ragbot.shared.pii_universal import (
    DefaultRedactor,
    PII_SURFACE_AUDIT_LOG,
    PII_SURFACE_REQUEST_STEPS,
    PII_SURFACE_TELEMETRY,
    PII_UNIVERSAL_FLAG_KEY,
    PiiRedactorPort,
    redact_mapping,
    redact_text,
    universal_redaction_enabled,
)


class _BotCfg:
    """Minimal BotConfig stand-in carrying ``plan_limits`` only."""

    def __init__(self, plan_limits: dict[str, Any] | None = None) -> None:
        self.plan_limits = plan_limits or {}


def _bot_on() -> _BotCfg:
    return _BotCfg({
        "pii_redaction_enabled": True,
        PII_UNIVERSAL_FLAG_KEY: True,
    })


def _bot_off() -> _BotCfg:
    return _BotCfg({})


def test_default_redactor_implements_port() -> None:
    """``DefaultRedactor`` is structurally a ``PiiRedactorPort``."""
    r = DefaultRedactor()
    assert isinstance(r, PiiRedactorPort)
    assert r.get_provider_name() == "default_regex"


def test_email_redact() -> None:
    r = DefaultRedactor()
    masked, ents = r.redact("liên hệ user.name@example.com nhé")
    assert "[REDACTED_EMAIL]" in masked
    assert "user.name@example.com" not in masked
    assert any(e["type"] == "EMAIL" for e in ents)


def test_phone_vn_redact() -> None:
    """Both 0xxx and +84 forms redact under PHONE label."""
    r = DefaultRedactor()
    # 0xxx contiguous
    masked1, ents1 = r.redact("call 0901234567 now")
    assert "[REDACTED_PHONE]" in masked1
    assert "0901234567" not in masked1
    assert any(e["type"] == "PHONE" for e in ents1)
    # +84 form
    masked2, ents2 = r.redact("intl +84901234567")
    assert "[REDACTED_PHONE]" in masked2
    assert "+84901234567" not in masked2
    assert any(e["type"] == "PHONE" for e in ents2)


def test_cccd_12_digits() -> None:
    r = DefaultRedactor()
    masked, ents = r.redact("CCCD: 012345678901 here")
    assert "[REDACTED_CCCD]" in masked
    assert "012345678901" not in masked
    assert any(e["type"] == "CCCD" for e in ents)


def test_cmnd_9_digits() -> None:
    """Legacy CMND = 9 contiguous digits."""
    r = DefaultRedactor()
    masked, ents = r.redact("CMND old 023456789 end")
    assert "[REDACTED_CMND]" in masked
    assert "023456789" not in masked
    assert any(e["type"] == "CMND" for e in ents)


def test_bank_acc() -> None:
    """10-16 digit run masks as BANK_ACC."""
    r = DefaultRedactor()
    masked, ents = r.redact("STK 1234567890123 cuoi")
    # 13 digits is bank-account length
    assert "[REDACTED_BANK_ACC]" in masked
    assert "1234567890123" not in masked
    assert any(e["type"] == "BANK_ACC" for e in ents)


def test_mixed_pii_in_text() -> None:
    """Email + phone in one input → both masked, both reported."""
    r = DefaultRedactor()
    text = "Email a@b.com phone 0901234567"
    masked, ents = r.redact(text)
    assert "a@b.com" not in masked
    assert "0901234567" not in masked
    types = {e["type"] for e in ents}
    assert "EMAIL" in types
    assert "PHONE" in types


def test_opt_in_per_bot_default_off() -> None:
    """``universal_redaction_enabled`` False by default; both toggles
    needed for True."""
    assert universal_redaction_enabled(_bot_off()) is False
    # Only base flag ON → still off
    only_base = _BotCfg({"pii_redaction_enabled": True})
    assert universal_redaction_enabled(only_base) is False
    # Only universal ON without base → off (composes monotonically)
    only_universal = _BotCfg({PII_UNIVERSAL_FLAG_KEY: True})
    assert universal_redaction_enabled(only_universal) is False
    # Both ON → True
    assert universal_redaction_enabled(_bot_on()) is True


def test_redact_text_passthrough_when_off() -> None:
    """Flag OFF → text returned unchanged even if PII present."""
    r = DefaultRedactor()
    out = redact_text(
        "Email a@b.com",
        redactor=r, bot_cfg=_bot_off(), surface=PII_SURFACE_REQUEST_STEPS,
    )
    assert out == "Email a@b.com"


def test_redact_text_masks_when_on() -> None:
    r = DefaultRedactor()
    out = redact_text(
        "Email a@b.com",
        redactor=r, bot_cfg=_bot_on(), surface=PII_SURFACE_REQUEST_STEPS,
    )
    assert "a@b.com" not in (out or "")
    assert "[REDACTED_EMAIL]" in (out or "")


def test_redact_text_none_input_returns_none() -> None:
    """None input never raises; passes through unchanged."""
    r = DefaultRedactor()
    assert redact_text(
        None, redactor=r, bot_cfg=_bot_on(), surface=PII_SURFACE_TELEMETRY,
    ) is None
    assert redact_text(
        "", redactor=r, bot_cfg=_bot_on(), surface=PII_SURFACE_TELEMETRY,
    ) == ""


def test_redact_text_missing_redactor_or_cfg_passthrough() -> None:
    """Missing redactor OR bot_cfg → input returned verbatim."""
    out1 = redact_text(
        "phone 0901234567", redactor=None, bot_cfg=_bot_on(),
        surface=PII_SURFACE_REQUEST_STEPS,
    )
    assert out1 == "phone 0901234567"
    out2 = redact_text(
        "phone 0901234567", redactor=DefaultRedactor(), bot_cfg=None,
        surface=PII_SURFACE_REQUEST_STEPS,
    )
    assert out2 == "phone 0901234567"


def test_redact_mapping_dict() -> None:
    """Recursive dict masking covers nested values."""
    r = DefaultRedactor()
    payload: dict[str, Any] = {
        "query": "Liên hệ a@b.com",
        "nested": {"phone": "Gọi 0901234567"},
        "list": ["CCCD 012345678901"],
        "int_field": 42,        # non-string passes through
        "none_field": None,
    }
    out = redact_mapping(
        payload, redactor=r, bot_cfg=_bot_on(),
        surface=PII_SURFACE_REQUEST_STEPS,
    )
    assert out is not None
    assert "a@b.com" not in out["query"]
    assert "0901234567" not in out["nested"]["phone"]
    assert "012345678901" not in out["list"][0]
    assert out["int_field"] == 42
    assert out["none_field"] is None
    # Original mapping NOT mutated in-place (input dict still has raw).
    assert payload["query"] == "Liên hệ a@b.com"


def test_redact_mapping_off_returns_unchanged() -> None:
    r = DefaultRedactor()
    payload = {"q": "a@b.com"}
    out = redact_mapping(
        payload, redactor=r, bot_cfg=_bot_off(),
        surface=PII_SURFACE_REQUEST_STEPS,
    )
    assert out == {"q": "a@b.com"}


def test_redact_mapping_none_input() -> None:
    """None payload returns None without error."""
    r = DefaultRedactor()
    out = redact_mapping(
        None, redactor=r, bot_cfg=_bot_on(),
        surface=PII_SURFACE_TELEMETRY,
    )
    assert out is None


def test_surface_filter() -> None:
    """The ``surface`` argument flows into the audit event payload."""
    cap = structlog.testing.LogCapture()
    structlog.configure(
        processors=[cap],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )
    try:
        r = DefaultRedactor()
        redact_text(
            "Mail a@b.com",
            redactor=r, bot_cfg=_bot_on(),
            surface=PII_SURFACE_AUDIT_LOG,
            record_tenant_id="tenant-1",
            record_bot_id="bot-1",
        )
        redact_text(
            "Phone 0901234567",
            redactor=r, bot_cfg=_bot_on(),
            surface=PII_SURFACE_REQUEST_STEPS,
            record_tenant_id="tenant-1",
            record_bot_id="bot-1",
        )
    finally:
        structlog.reset_defaults()

    events = [e for e in cap.entries if e.get("event") == "pii_redacted"]
    assert len(events) == 2
    surfaces = {e["surface"] for e in events}
    assert surfaces == {PII_SURFACE_AUDIT_LOG, PII_SURFACE_REQUEST_STEPS}
    # Audit event never leaks the raw value.
    for ev in events:
        assert "a@b.com" not in str(ev)
        assert "0901234567" not in str(ev)
        assert ev["mask_count"] >= 1
        assert "mask_types" in ev


def test_cccd_beats_bank_acc_on_overlap() -> None:
    """12-digit CCCD wins over the 10-16 BANK_ACC class on the same span."""
    r = DefaultRedactor()
    masked, ents = r.redact("ID 012345678901 done")
    # Single emit per span (no double-mask).
    assert masked.count("[REDACTED_") == 1
    types = {e["type"] for e in ents}
    # CCCD pattern is more specific (exact 12 digits) than BANK_ACC range.
    # The (start, -length) sort with equal length keeps insertion order
    # (CCCD listed before BANK_ACC) so CCCD wins.
    assert "CCCD" in types
