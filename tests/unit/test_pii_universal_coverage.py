"""Phase D2 — PII redaction universal coverage tests.

Locks the universal-coverage contract introduced in Phase D2:

  1. Two toggles compose monotonically (universal True without base True
     = no-op so the column meanings stay aligned).
  2. Five canonical PII shapes (EMAIL, PHONE, CCCD, CARD, DSN) are masked
     across three persistence surfaces (audit_log, request_steps,
     telemetry) so coverage is universal — not chat-only.
  3. The ``pii_redacted`` audit event carries surface tag, mask_count
     and per-type histogram. Raw PII NEVER appears in the event payload.
  4. ``redact_mapping`` walks nested dicts + lists in JSONB-shaped
     payloads (audit before/after JSON) without losing structure.
  5. ``mask_count`` is consistent across surfaces — same redactor input
     produces same entity count regardless of the surface tag, so
     compliance dashboards can sum surfaces without double-counting.
  6. Failure mode degrades silent (redactor raises → original value
     returned, ``pii_redaction_failed`` event emitted, no exception bubble).
"""

from __future__ import annotations

from typing import Any

import structlog
import structlog.testing

from ragbot.infrastructure.pii.null_pii_redactor import NullPiiRedactor
from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor
from ragbot.shared.constants import (
    PII_SURFACE_AUDIT_LOG,
    PII_SURFACE_REQUEST_STEPS,
    PII_SURFACE_TELEMETRY,
)
from ragbot.shared.pii_universal import (
    redact_mapping,
    redact_text,
    universal_redaction_enabled,
)


class _BotCfg:
    """Minimal BotConfig stub — only ``plan_limits`` is read."""

    def __init__(self, plan_limits: dict[str, Any] | None = None) -> None:
        self.plan_limits = plan_limits or {}


class _BrokenRedactor:
    """Raises on every call to exercise graceful-degradation path."""

    @staticmethod
    def get_provider_name() -> str:
        return "broken_test"

    def redact(self, _text: str) -> tuple[str, list[dict]]:
        raise RuntimeError("simulated redactor failure")


def _cap() -> structlog.testing.LogCapture:
    """Reset structlog with a fresh LogCapture so events from this test
    do not leak into / from other tests in the suite."""
    cap = structlog.testing.LogCapture()
    structlog.configure(processors=[cap])
    return cap


# Toggle constants used throughout — keeps test intent explicit.
_BOTH_ON = {"pii_redaction_enabled": True, "pii_redaction_universal": True}
_BASE_ONLY = {"pii_redaction_enabled": True, "pii_redaction_universal": False}
_UNI_ONLY = {"pii_redaction_enabled": False, "pii_redaction_universal": True}
_BOTH_OFF: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Toggle composition — monotonic gate
# ---------------------------------------------------------------------------


def test_toggle_off_default_no_redaction() -> None:
    """Default config (both toggles absent) → universal coverage OFF."""
    assert universal_redaction_enabled(_BotCfg(_BOTH_OFF)) is False


def test_toggle_universal_only_does_not_enable() -> None:
    """``pii_redaction_universal=True`` without base toggle is a no-op.

    The two columns compose monotonically — universal extends base; it
    does not replace base. Without this gate, an admin flipping just
    the universal column would silently break chat redaction
    expectations.
    """
    assert universal_redaction_enabled(_BotCfg(_UNI_ONLY)) is False


def test_toggle_base_only_does_not_enable_universal() -> None:
    """Base toggle alone keeps universal coverage OFF (back-compat)."""
    assert universal_redaction_enabled(_BotCfg(_BASE_ONLY)) is False


def test_toggle_both_on_enables_universal() -> None:
    """Both toggles True → universal coverage active."""
    assert universal_redaction_enabled(_BotCfg(_BOTH_ON)) is True


# ---------------------------------------------------------------------------
# 5 canonical PII types × 3 surfaces — coverage matrix
# ---------------------------------------------------------------------------


# (label, raw_value, expected_mask_token) tuples drive the matrix below.
# These shapes are the 5 high-impact PII types the VN regex provider
# recognises out of the box.
_PII_FIXTURES: list[tuple[str, str, str]] = [
    ("email", "alice.bob+spam@example.com", "[EMAIL]"),
    ("phone_vn", "0901234567", "[PHONE]"),
    ("cccd_vn", "001234567890", "[CCCD]"),
    ("credit_card", "4532015112830366", "[CARD]"),
    ("db_dsn", "postgresql://admin:s3cret@db.internal:5432/app", "[DSN]"),
]

_SURFACES = (PII_SURFACE_AUDIT_LOG, PII_SURFACE_REQUEST_STEPS, PII_SURFACE_TELEMETRY)


def _assert_masked(masked: str, raw: str, token: str) -> None:
    assert raw not in masked, f"raw PII leaked into masked output: {raw!r}"
    assert token in masked, f"expected mask token {token!r} in {masked!r}"


def test_email_redacted_across_all_surfaces() -> None:
    redactor = VnRegexPiiRedactor()
    bot = _BotCfg(_BOTH_ON)
    raw = "Contact alice@example.com for help"
    for surface in _SURFACES:
        cap = _cap()
        out = redact_text(
            raw,
            redactor=redactor,
            bot_cfg=bot,
            surface=surface,
            record_tenant_id="t",
            record_bot_id="b",
        )
        assert out is not None
        _assert_masked(out, "alice@example.com", "[EMAIL]")
        events = [e for e in cap.entries if e["event"] == "pii_redacted"]
        assert len(events) == 1
        assert events[0]["surface"] == surface
        assert events[0]["mask_types"]["EMAIL"] >= 1


def test_phone_redacted_across_all_surfaces() -> None:
    redactor = VnRegexPiiRedactor()
    bot = _BotCfg(_BOTH_ON)
    raw = "Call me at 0901234567 please"
    for surface in _SURFACES:
        out = redact_text(
            raw, redactor=redactor, bot_cfg=bot, surface=surface,
            record_tenant_id="t", record_bot_id="b",
        )
        assert out is not None
        _assert_masked(out, "0901234567", "[PHONE]")


def test_cccd_redacted_across_all_surfaces() -> None:
    redactor = VnRegexPiiRedactor()
    bot = _BotCfg(_BOTH_ON)
    # 12-digit string that does NOT start with `0` so it can't be a
    # 11-digit VN phone (which the regex prefers when overlap occurs).
    raw = "CCCD number: 123456789012"
    for surface in _SURFACES:
        out = redact_text(
            raw, redactor=redactor, bot_cfg=bot, surface=surface,
            record_tenant_id="t", record_bot_id="b",
        )
        assert out is not None
        _assert_masked(out, "123456789012", "[CCCD]")


def test_credit_card_redacted_across_all_surfaces() -> None:
    redactor = VnRegexPiiRedactor()
    bot = _BotCfg(_BOTH_ON)
    raw = "Card: 4532015112830366 expires 12/28"
    for surface in _SURFACES:
        out = redact_text(
            raw, redactor=redactor, bot_cfg=bot, surface=surface,
            record_tenant_id="t", record_bot_id="b",
        )
        assert out is not None
        _assert_masked(out, "4532015112830366", "[CARD]")


def test_dsn_redacted_across_all_surfaces() -> None:
    redactor = VnRegexPiiRedactor()
    bot = _BotCfg(_BOTH_ON)
    raw = "Error: postgresql://admin:s3cret@db.internal:5432/app failed"
    for surface in _SURFACES:
        out = redact_text(
            raw, redactor=redactor, bot_cfg=bot, surface=surface,
            record_tenant_id="t", record_bot_id="b",
        )
        assert out is not None
        _assert_masked(out, "postgresql://admin:s3cret@db.internal:5432/app", "[DSN]")


# ---------------------------------------------------------------------------
# Audit event compliance — no raw PII allowed in event payload
# ---------------------------------------------------------------------------


def test_audit_event_never_carries_raw_pii() -> None:
    """The whole point of universal coverage: the AUDIT EVENT itself
    must NEVER carry the raw PII that triggered the event. Otherwise
    we are leaking compliance signal into the very log we use to prove
    compliance."""
    cap = _cap()
    raw = (
        "User secrets: alice@example.com, 0901234567, "
        "001234567890, 4532015112830366, postgresql://u:p@h/db"
    )
    redact_text(
        raw,
        redactor=VnRegexPiiRedactor(),
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
        record_tenant_id="tenant-uuid",
        record_bot_id="bot-uuid",
    )
    leaks = [
        "alice@example.com",
        "0901234567",
        "001234567890",
        "4532015112830366",
        "u:p@h",  # DSN credential body
    ]
    for entry in cap.entries:
        for k, v in entry.items():
            if isinstance(v, str):
                for leak in leaks:
                    assert leak not in v, (
                        f"raw PII {leak!r} leaked into audit event field {k}"
                    )


def test_audit_event_carries_surface_mask_count_and_types() -> None:
    """The structured event MUST carry the three compliance facts:
    surface tag, mask_count, mask_types (per-type histogram).
    """
    cap = _cap()
    redact_text(
        "alice@example.com and 0901234567",
        redactor=VnRegexPiiRedactor(),
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_REQUEST_STEPS,
        record_tenant_id="t",
        record_bot_id="b",
    )
    events = [e for e in cap.entries if e["event"] == "pii_redacted"]
    assert len(events) == 1
    ev = events[0]
    assert ev["surface"] == PII_SURFACE_REQUEST_STEPS
    assert ev["mask_count"] == 2
    assert ev["mask_types"] == {"EMAIL": 1, "PHONE": 1}
    assert ev["provider"] == "vn_regex"


# ---------------------------------------------------------------------------
# redact_mapping — walks JSONB-shaped payloads (audit before/after)
# ---------------------------------------------------------------------------


def test_redact_mapping_walks_nested_dict_and_list() -> None:
    """Audit ``before``/``after`` JSONB payloads can carry user-supplied
    text in nested fields. ``redact_mapping`` must walk every string,
    leave non-strings untouched, and preserve the dict/list shape."""
    redactor = VnRegexPiiRedactor()
    payload = {
        "name": "Tenant config update",
        "contact": {"email": "alice@example.com", "phone": "0901234567"},
        "notes": [
            "First note (clean)",
            "Second note: card 4532015112830366",
        ],
        "version": 42,  # non-string preserved
        "active": True,
    }
    out = redact_mapping(
        payload,
        redactor=redactor,
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
        record_tenant_id="t",
        record_bot_id="b",
    )
    assert out is not None
    assert out["version"] == 42
    assert out["active"] is True
    assert "alice@example.com" not in out["contact"]["email"]
    assert "[EMAIL]" in out["contact"]["email"]
    assert "0901234567" not in out["contact"]["phone"]
    assert "[PHONE]" in out["contact"]["phone"]
    # List string entries walked.
    assert "4532015112830366" not in out["notes"][1]
    assert "[CARD]" in out["notes"][1]
    # Clean string left alone.
    assert out["notes"][0] == "First note (clean)"


def test_redact_mapping_emits_single_aggregated_audit_event() -> None:
    """Walking a multi-field payload must emit ONE audit event with
    the aggregated mask_count across all leaves, NOT one event per
    leaf. Dashboards count rows-with-leaks, not strings-with-leaks."""
    cap = _cap()
    payload = {
        "a": "alice@example.com",
        "b": "0901234567",
        "c": "001234567890",
        "nested": {"d": "4532015112830366"},
    }
    redact_mapping(
        payload,
        redactor=VnRegexPiiRedactor(),
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
        record_tenant_id="t",
        record_bot_id="b",
    )
    events = [e for e in cap.entries if e["event"] == "pii_redacted"]
    assert len(events) == 1, "expected ONE aggregated event, not one per leaf"
    ev = events[0]
    # 4 distinct PII shapes across 4 leaves → mask_count=4.
    assert ev["mask_count"] == 4
    assert ev["mask_types"]["EMAIL"] == 1
    assert ev["mask_types"]["PHONE"] == 1
    assert ev["mask_types"]["CCCD"] == 1
    assert ev["mask_types"]["CARD"] == 1


def test_redact_mapping_passthrough_when_toggle_off() -> None:
    """``pii_redaction_universal=False`` → payload unchanged regardless
    of PII content, no audit event emitted."""
    cap = _cap()
    payload = {"email": "alice@example.com", "phone": "0901234567"}
    out = redact_mapping(
        payload,
        redactor=VnRegexPiiRedactor(),
        bot_cfg=_BotCfg(_BASE_ONLY),  # base only, no universal
        surface=PII_SURFACE_AUDIT_LOG,
        record_tenant_id="t",
        record_bot_id="b",
    )
    assert out == payload
    events = [e["event"] for e in cap.entries]
    assert "pii_redacted" not in events


# ---------------------------------------------------------------------------
# Cross-surface consistency — same input → same mask_count
# ---------------------------------------------------------------------------


def test_mask_count_consistent_across_surfaces() -> None:
    """The same redactor input MUST yield the same mask_count regardless
    of which surface tag the caller passes. Otherwise compliance
    dashboards summing surfaces would double-count or undercount."""
    redactor = VnRegexPiiRedactor()
    raw = "Email alice@example.com, phone 0901234567, card 4532015112830366"
    counts: dict[str, int] = {}
    for surface in _SURFACES:
        cap = _cap()
        redact_text(
            raw, redactor=redactor, bot_cfg=_BotCfg(_BOTH_ON),
            surface=surface, record_tenant_id="t", record_bot_id="b",
        )
        ev = next(e for e in cap.entries if e["event"] == "pii_redacted")
        counts[surface] = ev["mask_count"]
    # All three surfaces produced the same mask_count from the same input.
    assert len(set(counts.values())) == 1, f"surface counts diverged: {counts}"


# ---------------------------------------------------------------------------
# Failure mode — graceful degradation
# ---------------------------------------------------------------------------


def test_redactor_failure_degrades_silent() -> None:
    """Redactor raising → return original text, emit ``pii_redaction_failed``
    structured event, NO exception bubbles up to the persistence path."""
    cap = _cap()
    raw = "alice@example.com 0901234567"
    out = redact_text(
        raw,
        redactor=_BrokenRedactor(),
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_TELEMETRY,
        record_tenant_id="t",
        record_bot_id="b",
    )
    assert out == raw, "broken redactor must degrade to passthrough"
    events = [e["event"] for e in cap.entries]
    assert "pii_redaction_failed" in events
    failed = next(e for e in cap.entries if e["event"] == "pii_redaction_failed")
    assert failed["surface"] == PII_SURFACE_TELEMETRY
    assert failed["error_type"] == "RuntimeError"


def test_redactor_failure_in_mapping_walk_degrades_silent() -> None:
    """When the walker hits a broken redactor on ONE string field, the
    other fields still walk and the row still persists. Universal
    coverage is best-effort, NOT a hard validator that can fail the
    write."""
    cap = _cap()
    payload = {"safe": "no pii here", "leak": "alice@example.com"}
    out = redact_mapping(
        payload,
        redactor=_BrokenRedactor(),
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
        record_tenant_id="t",
        record_bot_id="b",
    )
    # Whole payload returned (passthrough on failure).
    assert out is not None
    assert out["safe"] == "no pii here"
    assert out["leak"] == "alice@example.com"
    # Failure event emitted for each broken redactor invocation.
    failed_events = [e for e in cap.entries if e["event"] == "pii_redaction_failed"]
    assert len(failed_events) >= 1


def test_null_redactor_passthrough_emits_no_event() -> None:
    """``NullPiiRedactor`` returns ``(text, [])`` → empty entity list →
    no audit event, no behaviour change. This is the DI default so
    new tenants flipping universal=True without setting a real
    provider see ZERO behaviour change."""
    cap = _cap()
    out = redact_text(
        "alice@example.com 0901234567",
        redactor=NullPiiRedactor(),
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
        record_tenant_id="t",
        record_bot_id="b",
    )
    assert out == "alice@example.com 0901234567"
    events = [e["event"] for e in cap.entries]
    assert "pii_redacted" not in events


# ---------------------------------------------------------------------------
# Boundary cases — None / empty / oversized inputs
# ---------------------------------------------------------------------------


def test_none_input_passthrough() -> None:
    """``None`` text input → ``None`` output, no event."""
    cap = _cap()
    out = redact_text(
        None, redactor=VnRegexPiiRedactor(), bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
    )
    assert out is None
    assert not any(e["event"] == "pii_redacted" for e in cap.entries)


def test_empty_string_passthrough() -> None:
    """Empty string → empty string, no event."""
    cap = _cap()
    out = redact_text(
        "", redactor=VnRegexPiiRedactor(), bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
    )
    assert out == ""
    assert not any(e["event"] == "pii_redacted" for e in cap.entries)


def test_missing_bot_cfg_passthrough() -> None:
    """``bot_cfg=None`` → toggle resolution short-circuits to False."""
    out = redact_text(
        "alice@example.com",
        redactor=VnRegexPiiRedactor(),
        bot_cfg=None,
        surface=PII_SURFACE_AUDIT_LOG,
    )
    assert out == "alice@example.com"


def test_missing_redactor_passthrough() -> None:
    """``redactor=None`` → can't redact → passthrough, no event."""
    cap = _cap()
    out = redact_text(
        "alice@example.com",
        redactor=None,
        bot_cfg=_BotCfg(_BOTH_ON),
        surface=PII_SURFACE_AUDIT_LOG,
    )
    assert out == "alice@example.com"
    assert not any(e["event"] == "pii_redacted" for e in cap.entries)
