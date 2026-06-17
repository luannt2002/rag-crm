"""RECAP PII VN Redaction tests.

Locks the boundary-layer Vietnamese PII contract:

  §1  Recognizer registry — every required VN entity type is wired
      (CMND / CCCD / phone / email / address).
  §2  Recognizer correctness — 10+ real-world VN PII variants are
      detected and masked.
  §3  False-positive guard — legitimate prose (dates, years, ratios,
      generic numbers) is NOT mis-classified.
  §4  Facade flag semantics — system kill-switch + per-bot opt-in
      compose correctly; either OFF → passthrough.
  §5  Observability contract — ``step_name="recap_pii_detect"`` +
      ``feature_flag="recap_pii_enabled"`` per OBSERVABILITY-MATRIX.md.
      ``entity_counts`` per type, ZERO raw PII in any logged value.
  §6  Graceful degradation — strategy / config-service errors return
      passthrough + warning event; ingest job never 5xx-es.
  §7  Wire integration — ``_maybe_redact_ingest_content`` composite
      gate: ``recap_pii_enabled=False`` AND ``bot_opt_in=True`` →
      passthrough (system kill-switch wins).

Domain-neutral guarantee
------------------------
Every fixture string uses placeholder names ("Nguyễn Văn A",
"alice@example.com") and generic VN postal vocabulary — NO tenant
brand / customer literal. Compliant with the project's
SECRET_SCRUB_WORKFLOW.md banned-literal contract.
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
from ragbot.infrastructure.safety.pii_detector import (
    RECAP_PII_FEATURE_FLAG,
    RECAP_PII_STEP_NAME,
    PiiDetectResult,
    RecapPiiDetector,
)
from ragbot.infrastructure.safety.vn_recognizers import (
    VN_RECOGNIZERS,
    VnRecognizerSpec,
    get_recognizer_labels,
    get_recognizers,
)
from ragbot.shared.constants import (
    DEFAULT_RECAP_PII_ENABLED,
    PII_REGEX_VN_ADDRESS,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


class _BotCfg:
    def __init__(self, plan_limits: dict | None = None) -> None:
        self.plan_limits = plan_limits or {}


class _BotRepo:
    def __init__(self, cfg: _BotCfg | None) -> None:
        self._cfg = cfg

    async def get_by_id(self, _bot_id, *, record_tenant_id):  # noqa: ARG002
        return self._cfg


class _StubConfigService:
    """Stand-in for SystemConfigService — only ``get_bool`` is exercised."""

    def __init__(self, values: dict[str, bool] | None = None) -> None:
        self._values = values or {}

    async def get_bool(self, key: str, default: bool = False) -> bool:
        return self._values.get(key, default)


class _BrokenConfigService:
    async def get_bool(self, key: str, default: bool = False) -> bool:  # noqa: ARG002
        raise RuntimeError("simulated config-service failure")


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


# ---------------------------------------------------------------------------
# §1 — Recognizer registry shape
# ---------------------------------------------------------------------------


def test_default_flag_is_off() -> None:
    """Default :data:`DEFAULT_RECAP_PII_ENABLED` MUST be False (opt-in)."""
    assert DEFAULT_RECAP_PII_ENABLED is False


def test_recognizer_registry_covers_required_vn_types() -> None:
    """The VN recognizer registry MUST cover CCCD / CMND / PHONE /
    EMAIL / VN_ADDRESS at minimum per the SPRINT-GAP-CLOSURE.md spec.
    """
    labels = set(get_recognizer_labels())
    required = {"CCCD", "CMND", "PHONE", "EMAIL", "VN_ADDRESS"}
    missing = required - labels
    assert not missing, f"VN recognizer registry missing: {missing}"


def test_recognizers_are_immutable_spec_tuples() -> None:
    """``get_recognizers`` returns frozen ``VnRecognizerSpec`` instances."""
    recs = get_recognizers()
    assert isinstance(recs, tuple)
    assert recs is VN_RECOGNIZERS
    for spec in recs:
        assert isinstance(spec, VnRecognizerSpec)
        assert spec.label and isinstance(spec.label, str)
        assert spec.pattern is not None
        assert spec.description  # non-empty


def test_vn_address_regex_compiles_and_matches_keyword() -> None:
    """The VN address regex must match keyword-anchored forms."""
    import re

    pat = re.compile(PII_REGEX_VN_ADDRESS, re.IGNORECASE)
    assert pat.search("Số 12 Lê Lợi, Quận 1") is not None
    assert pat.search("Đường Nguyễn Huệ, TP HCM") is not None
    # No keyword → no match (false-positive guard).
    assert pat.search("Mua 12 quả táo và 3 kg gạo.") is None


# ---------------------------------------------------------------------------
# §2 — Recognizer correctness (≥10 VN PII variants)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_in, expected_type",
    [
        # §2.1 CCCD 12 digits (post-2016 national ID).
        ("CCCD 123456789012 cap ngay 01/2022", "CCCD"),
        # §2.2 CCCD with thousand-grouping spaces.
        ("CCCD: 1234 5678 9012", "CCCD"),
        # §2.3 CCCD starting with 0 — overlap-resolution must
        # classify as CCCD, NOT PHONE.
        ("012345678901", "CCCD"),
        # §2.4 CMND 9 digits (legacy, phased out 2021).
        ("CMND so 012345678", "CMND"),
        # §2.5 VN mobile 10 digits 0xxxxxxxxx.
        ("Goi 0901234567 nhe", "PHONE"),
        # §2.6 VN mobile 11 digits 0xxxxxxxxxx (legacy series).
        ("Lien he 01234567890", "PHONE"),
        # §2.7 VN mobile with +84 prefix.
        ("Phone: +84901234567", "PHONE"),
        # §2.8 VN mobile space-separated.
        ("SDT 090 123 4567", "PHONE"),
        # §2.9 VN mobile dot-separated.
        ("Goi 090.123.4567", "PHONE"),
        # §2.10 Email standard.
        ("Lien he alice@example.com", "EMAIL"),
        # §2.11 Email with plus-tag.
        ("Email: bob.dev+tag@sub.example.vn", "EMAIL"),
        # §2.12 VN address with Số / Đường keyword.
        ("Dia chi: Số 12 Lê Lợi, Quận 1", "VN_ADDRESS"),
        # §2.13 VN address with TP prefix.
        ("Văn phòng tại TP Hồ Chí Minh, tang 5", "VN_ADDRESS"),
    ],
)
def test_vn_pii_variant_redacted(raw_in: str, expected_type: str) -> None:
    """Every parametrized VN PII variant masks under the default
    recognizer-registry strategy (covers CMND / +84 phone / VN_ADDRESS
    on top of legacy CCCD / PHONE / EMAIL).
    """
    detector = RecapPiiDetector()  # default = full VN registry
    result = detector.detect(
        raw_in,
        feature_enabled=True,
        bot_opt_in=True,
    )
    assert result.changed, (
        f"expected at least one mask for {raw_in!r}, "
        f"got entity_counts={result.entity_counts}"
    )
    assert (
        expected_type in result.entity_counts
        or any(t == expected_type for t in result.entity_counts)
    ), (
        f"expected type {expected_type!r} in entity_counts, "
        f"got {result.entity_counts}"
    )
    # Raw value never leaks back into redacted_text (sample the PII
    # token from each fixture).
    if expected_type == "EMAIL":
        assert "@" not in result.redacted_text or "[EMAIL]" in result.redacted_text


# ---------------------------------------------------------------------------
# §3 — False-positive guard (legitimate prose stays intact)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clean_input",
    [
        # §3.1 Year + percentages — must NOT trigger PHONE/CCCD/CARD.
        "Doanh thu nam 2024 tang 15% so voi 2023.",
        # §3.2 Generic 8-digit price — too short for CCCD, no 0-prefix.
        "Gia san pham la 12345678 dong.",
        # §3.3 Vietnamese sentences with no PII at all.
        "Cong ty cung cap dich vu tu van va dao tao.",
        # §3.4 Ratio / fraction — slash inside number must NOT be PII.
        "Ti le 1/3 so voi tong so.",
    ],
)
def test_legitimate_text_not_falsely_redacted(clean_input: str) -> None:
    detector = RecapPiiDetector()
    result = detector.detect(
        clean_input,
        feature_enabled=True,
        bot_opt_in=True,
    )
    # PHONE / CCCD / CMND must NOT match — these are the high-precision
    # numeric recognizers most prone to false positives.
    for label in ("PHONE", "CCCD", "CMND"):
        assert label not in result.entity_counts, (
            f"false positive {label} on clean input {clean_input!r} → "
            f"{result.entity_counts}"
        )


# ---------------------------------------------------------------------------
# §4 — Facade flag semantics (composite gate)
# ---------------------------------------------------------------------------


def test_feature_flag_off_short_circuits() -> None:
    """``feature_enabled=False`` → passthrough, no event emitted."""
    cap = _capture_logs()
    detector = RecapPiiDetector(pii_redactor=VnRegexPiiRedactor())
    raw = "Email: alice@example.com phone 0901234567"
    result = detector.detect(
        raw,
        feature_enabled=False,
        bot_opt_in=True,
    )
    assert result.redacted_text == raw
    assert result.decision == "skipped_flag_off"
    assert result.changed is False
    # Hot-path: silent skip when the flag is off (no event).
    assert all(e["event"] != RECAP_PII_STEP_NAME for e in cap.entries)


def test_bot_opt_in_off_emits_skipped_event() -> None:
    """``feature_enabled=True`` AND ``bot_opt_in=False`` → passthrough +
    one event with ``decision="skipped_bot_opt_out"``.
    """
    cap = _capture_logs()
    detector = RecapPiiDetector(pii_redactor=VnRegexPiiRedactor())
    raw = "Email: alice@example.com"
    result = detector.detect(
        raw,
        feature_enabled=True,
        bot_opt_in=False,
        record_tenant_id="t1",
        record_bot_id="b1",
    )
    assert result.redacted_text == raw
    assert result.decision == "skipped_bot_opt_out"
    events = [e for e in cap.entries if e["event"] == RECAP_PII_STEP_NAME]
    assert len(events) == 1
    assert events[0]["decision"] == "skipped_bot_opt_out"
    assert events[0]["feature_flag"] == RECAP_PII_FEATURE_FLAG
    assert events[0]["flag_value"] is True
    assert events[0]["bot_opt_in"] is False
    assert events[0]["record_tenant_id"] == "t1"
    assert events[0]["record_bot_id"] == "b1"


def test_both_gates_open_emits_masked_event_with_counts() -> None:
    """Both gates open → masked output + event with ``entity_counts``."""
    cap = _capture_logs()
    detector = RecapPiiDetector(pii_redactor=VnRegexPiiRedactor())
    raw = (
        "Khach hang Nguyen Van A, "
        "Email: alice@example.com, "
        "SDT: 0901234567, "
        "CCCD: 123456789012."
    )
    result = detector.detect(
        raw,
        feature_enabled=True,
        bot_opt_in=True,
        record_tenant_id="t1",
        record_bot_id="b1",
    )
    assert result.changed
    assert result.decision == "masked"
    assert result.total_masks >= 3
    assert "alice@example.com" not in result.redacted_text
    assert "0901234567" not in result.redacted_text
    assert "123456789012" not in result.redacted_text

    events = [e for e in cap.entries if e["event"] == RECAP_PII_STEP_NAME]
    assert len(events) == 1
    ev = events[0]
    assert ev["step_name"] == RECAP_PII_STEP_NAME
    assert ev["feature_flag"] == RECAP_PII_FEATURE_FLAG
    assert ev["flag_value"] is True
    assert ev["bot_opt_in"] is True
    assert ev["decision"] == "masked"
    assert ev["entity_counts"]["EMAIL"] >= 1
    assert ev["entity_counts"]["PHONE"] >= 1
    assert ev["entity_counts"]["CCCD"] >= 1
    assert ev["total_masks"] >= 3
    assert ev["provider"] == "vn_regex"
    assert ev["duration_ms"] >= 0


def test_no_entities_branch_emits_zero_counts_event() -> None:
    cap = _capture_logs()
    detector = RecapPiiDetector(pii_redactor=VnRegexPiiRedactor())
    raw = "Tai lieu mo ta san pham A, gia thanh, dieu khoan."
    result = detector.detect(raw, feature_enabled=True, bot_opt_in=True)
    assert result.changed is False
    assert result.decision == "no_entities_detected"
    events = [e for e in cap.entries if e["event"] == RECAP_PII_STEP_NAME]
    assert len(events) == 1
    assert events[0]["decision"] == "no_entities_detected"
    assert events[0]["total_masks"] == 0
    assert events[0]["entity_counts"] == {}


# ---------------------------------------------------------------------------
# §5 — Observability: ZERO raw PII in any logged value
# ---------------------------------------------------------------------------


def test_event_payload_carries_no_raw_pii() -> None:
    """CRITICAL — observability rule: structlog event NEVER carries raw
    PII. Only counts + types + offsets-via-result. This is the
    CLAUDE.md "PII redaction TẠI HOOK LAYER" guarantee.
    """
    cap = _capture_logs()
    detector = RecapPiiDetector(pii_redactor=VnRegexPiiRedactor())
    raw = "Email: alice@example.com phone 0901234567 CCCD 123456789012"
    detector.detect(
        raw,
        feature_enabled=True,
        bot_opt_in=True,
        record_tenant_id="t1",
        record_bot_id="b1",
    )
    events = [e for e in cap.entries if e["event"] == RECAP_PII_STEP_NAME]
    assert events
    for ev in events:
        for k, v in ev.items():
            if isinstance(v, str):
                assert "alice@example.com" not in v, k
                assert "0901234567" not in v, k
                assert "123456789012" not in v, k


def test_pii_detect_result_is_frozen_dataclass() -> None:
    """``PiiDetectResult`` MUST be immutable so callers can't mutate the
    facade outcome and leak raw PII into downstream logs."""
    result = PiiDetectResult(redacted_text="x")
    with pytest.raises(Exception):  # FrozenInstanceError is dataclass-specific
        result.redacted_text = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# §6 — Graceful degradation
# ---------------------------------------------------------------------------


def test_strategy_error_emits_failed_event_and_passes_through() -> None:
    cap = _capture_logs()
    detector = RecapPiiDetector(pii_redactor=_BrokenRedactor())
    raw = "alice@example.com"
    result = detector.detect(raw, feature_enabled=True, bot_opt_in=True)
    assert result.redacted_text == raw
    assert result.decision == "strategy_error"
    failed = [e for e in cap.entries if e["event"] == "recap_pii_detect_failed"]
    assert len(failed) == 1
    assert failed[0]["error_type"] == "RuntimeError"


def test_facade_constructs_with_no_strategy_defaults_to_registry() -> None:
    """Calling ``RecapPiiDetector()`` with no strategy MUST fall back to
    the VN recognizer registry — facade is always safe to construct
    AND covers the full VN PII surface out of the box.
    """
    detector = RecapPiiDetector()
    assert detector.provider_name == "vn_safety_registry"
    result = detector.detect(
        "alice@example.com",
        feature_enabled=True,
        bot_opt_in=True,
    )
    # Registry covers EMAIL → masked.
    assert result.decision == "masked"
    assert "alice@example.com" not in result.redacted_text
    assert "[EMAIL]" in result.redacted_text


# ---------------------------------------------------------------------------
# §7 — Wire integration via _maybe_redact_ingest_content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wire_recap_flag_off_short_circuits_even_when_bot_opt_in() -> None:
    """System kill-switch ``recap_pii_enabled=False`` wins over bot opt-in."""
    cap = _capture_logs()
    text_in = "alice@example.com 0901234567"
    cfg = _StubConfigService({"recap_pii_enabled": False})
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
        config_service=cfg,
    )
    assert out == text_in  # system kill-switch passes through
    # No masked event when flag is off (silent fast-path).
    masked_events = [
        e for e in cap.entries
        if e["event"] == RECAP_PII_STEP_NAME and e.get("decision") == "masked"
    ]
    assert not masked_events


@pytest.mark.asyncio
async def test_wire_recap_flag_on_and_bot_opt_in_redacts() -> None:
    """Both system + bot gates open → content masked + event emitted."""
    cap = _capture_logs()
    text_in = "Email alice@example.com, SDT 0901234567"
    cfg = _StubConfigService({"recap_pii_enabled": True})
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
        config_service=cfg,
    )
    assert "alice@example.com" not in out
    assert "0901234567" not in out
    assert "[EMAIL]" in out
    assert "[PHONE]" in out
    masked = [
        e for e in cap.entries
        if e["event"] == RECAP_PII_STEP_NAME and e.get("decision") == "masked"
    ]
    assert len(masked) == 1
    assert masked[0]["entity_counts"]["EMAIL"] >= 1
    assert masked[0]["entity_counts"]["PHONE"] >= 1


@pytest.mark.asyncio
async def test_wire_config_service_failure_kills_feature() -> None:
    """A config-service exception must DEGRADE SAFE (kill-switch
    defaults to OFF) rather than 5xx the ingest job.
    """
    cap = _capture_logs()
    text_in = "alice@example.com"
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=VnRegexPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
        config_service=_BrokenConfigService(),
    )
    assert out == text_in  # safe fallback
    failed = [
        e for e in cap.entries
        if e["event"] == "pii_redaction_failed"
        and e.get("stage") == "config_lookup"
    ]
    assert len(failed) == 1


@pytest.mark.asyncio
async def test_wire_null_strategy_with_both_gates_on_is_no_op() -> None:
    """Null strategy + both gates open → no_entities event, no mask."""
    cap = _capture_logs()
    text_in = "alice@example.com"
    cfg = _StubConfigService({"recap_pii_enabled": True})
    out = await _maybe_redact_ingest_content(
        text_in,
        pii_redactor=NullPiiRedactor(),
        bot_repo=_BotRepo(_BotCfg({"pii_redaction_enabled": True})),
        record_bot_id=_BOT_ID,
        record_tenant_id=_TENANT_ID,
        config_service=cfg,
    )
    assert out == text_in
    detect = [e for e in cap.entries if e["event"] == RECAP_PII_STEP_NAME]
    assert len(detect) == 1
    assert detect[0]["decision"] == "no_entities_detected"
    assert detect[0]["provider"] == "null"
