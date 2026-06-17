"""PII Redactor Strategy registry — unit tests."""

from __future__ import annotations

from ragbot.application.ports.pii_redactor_port import PiiRedactorPort
from ragbot.infrastructure.pii.null_pii_redactor import NullPiiRedactor
from ragbot.infrastructure.pii.registry import (
    build_pii_redactor,
    list_providers,
)
from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor


def test_null_passthrough() -> None:
    r = NullPiiRedactor()
    out, ents = r.redact("Email me at me@example.com or 0901234567.")
    assert out == "Email me at me@example.com or 0901234567."
    assert ents == []
    assert r.get_provider_name() == "null"
    assert isinstance(r, PiiRedactorPort)


def test_vn_regex_redacts_cccd() -> None:
    r = VnRegexPiiRedactor()
    out, ents = r.redact("CCCD: 012345678901, ten: A")
    assert "[CCCD]" in out
    assert "012345678901" not in out
    assert any(e["type"] == "CCCD" for e in ents)
    cccd_ent = next(e for e in ents if e["type"] == "CCCD")
    assert cccd_ent["start"] == 6  # offset of "012..." in original input
    assert cccd_ent["end"] == 18


def test_vn_regex_redacts_phone() -> None:
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Goi 0901234567 nhe")
    assert "[PHONE]" in out
    assert "0901234567" not in out
    assert any(e["type"] == "PHONE" for e in ents)


def test_vn_regex_redacts_email() -> None:
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Lien he abc.def+tag@sub.example.vn")
    assert "[EMAIL]" in out
    assert "abc.def+tag@sub.example.vn" not in out
    assert any(e["type"] == "EMAIL" for e in ents)


def test_vn_regex_combined() -> None:
    r = VnRegexPiiRedactor()
    raw = (
        "Khach hang: Nguyen Van A, "
        "CCCD 012345678901, "
        "SDT 0901234567, "
        "email a@example.com"
    )
    out, ents = r.redact(raw)
    # All three masks present.
    assert "[CCCD]" in out
    assert "[PHONE]" in out
    assert "[EMAIL]" in out
    # No raw PII leaks through.
    assert "012345678901" not in out
    assert "0901234567" not in out
    assert "a@example.com" not in out
    # Entities are sorted by start offset.
    starts = [e["start"] for e in ents]
    assert starts == sorted(starts)
    types = {e["type"] for e in ents}
    assert {"CCCD", "PHONE", "EMAIL"}.issubset(types)


def test_registry_default_is_null() -> None:
    for prov in (None, "", "does_not_exist_xyz"):
        assert isinstance(build_pii_redactor(prov), NullPiiRedactor)
    providers = list_providers()
    assert "null" in providers
    assert "vn_regex" in providers
    assert "presidio" in providers
    assert providers == sorted(providers)


def test_presidio_stub_falls_back_to_null() -> None:
    instance = build_pii_redactor("presidio")
    assert isinstance(instance, NullPiiRedactor)


def test_registry_vn_regex_returns_real_impl() -> None:
    instance = build_pii_redactor("vn_regex")
    assert isinstance(instance, VnRegexPiiRedactor)
    out, ents = instance.redact("ten: a@b.com")
    assert "[EMAIL]" in out
    assert ents


# === P2-1 — collision + space-separated coverage ============================
def test_cccd_starting_with_zero_not_swallowed_by_phone() -> None:
    """A 12-digit CCCD beginning with ``0`` must mask as CCCD, not PHONE.

    Without the ``(start, -length)`` sort, the PHONE regex would match the
    first 10 digits and emit ``[PHONE]23`` — leaking the trailing ``23`` of
    the CCCD. We assert the **canonical** type code wins so this regression
    cannot creep back in.
    """
    r = VnRegexPiiRedactor()
    out, ents = r.redact("012345678901")
    assert out == "[CCCD]", f"expected single CCCD mask, got {out!r}"
    types = [e["type"] for e in ents]
    assert types == ["CCCD"], f"expected only CCCD entity, got {types}"
    assert ents[0]["start"] == 0
    assert ents[0]["end"] == 12
    # No raw digits leak (especially the trailing ``23`` that the PHONE
    # prefix-match would otherwise expose).
    assert "23" not in out


def test_space_separated_cccd_redacted() -> None:
    r = VnRegexPiiRedactor()
    out, ents = r.redact("CCCD: 0123 4567 8901 ten: A")
    assert "[CCCD]" in out
    assert "0123 4567 8901" not in out
    assert any(e["type"] == "CCCD" for e in ents)


def test_space_separated_phone_redacted() -> None:
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Goi 0901 234 567 nhe")
    assert "[PHONE]" in out
    assert "0901 234 567" not in out
    assert any(e["type"] == "PHONE" for e in ents)


def test_dot_separated_phone_redacted() -> None:
    """Sanity: dotted VN-style phone numbers get masked too."""
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Lien he 090.123.4567 nhe")
    assert "[PHONE]" in out
    assert "090.123.4567" not in out
    assert any(e["type"] == "PHONE" for e in ents)
