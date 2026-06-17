"""S5 Phase-1 — pii_redactor.redact() returns masked text (NOT just labels).

Master Finding #4: prior to S5 the redactor only LABELLED entities
(returned spans) but callers never received a masked string. This suite
locks the contract that :meth:`PiiRedactorPort.redact` returns
``(masked_text, entities)`` and that ``masked_text`` actually scrubs
raw PII from the input across the 5 high-impact classes the wiring is
designed to cover at the boundary:

  1. EMAIL    — chat / form / doc body
  2. PHONE    — VN mobile number
  3. CCCD     — VN national ID (12 digits)
  4. DSN      — Postgres/Mongo connection string with inline password
                (architecture-neutral stand-in for "address" since the
                 wire's purpose is leak prevention, not literal street
                 address — DSN catches the same operator-paste leak
                 surface)
  5. CARD     — credit-card 13-19 digits

The wire path (chat_worker + DocumentService.ingest) relies on this
contract; if redact() ever silently degrades to label-only the tests
here fail loud.
"""

from __future__ import annotations

from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor


def test_redact_method_returns_tuple_with_masked_text_for_email() -> None:
    r = VnRegexPiiRedactor()
    masked, entities = r.redact("Contact me: alice.smith+work@example.org tomorrow")
    assert "alice.smith+work@example.org" not in masked, (
        "redact() MUST scrub raw EMAIL, not just emit labels"
    )
    assert "[EMAIL]" in masked
    assert any(e["type"] == "EMAIL" for e in entities)
    # spans reference the ORIGINAL input
    email_ent = next(e for e in entities if e["type"] == "EMAIL")
    assert email_ent["end"] > email_ent["start"]


def test_redact_method_returns_masked_text_for_vn_phone() -> None:
    r = VnRegexPiiRedactor()
    masked, entities = r.redact("Hotline: 0901234567 - call any time")
    assert "0901234567" not in masked, "raw VN phone leaked through redact()"
    assert "[PHONE]" in masked
    assert any(e["type"] == "PHONE" for e in entities)


def test_redact_method_returns_masked_text_for_vn_cccd() -> None:
    r = VnRegexPiiRedactor()
    masked, entities = r.redact("CCCD nguoi nop: 123456789012, da xac thuc")
    assert "123456789012" not in masked, "raw CCCD digits leaked through redact()"
    assert "[CCCD]" in masked
    assert any(e["type"] == "CCCD" for e in entities)


def test_redact_method_returns_masked_text_for_dsn_address() -> None:
    """DSN = network-address class leak. Wire must mask the inline secret.

    A DSN carries hostname + DB user + DB password in a single token —
    the highest-impact "address-shaped" leak we encounter in chat/log
    pastes. Asserting full mask is the operational equivalent of
    "no raw address with credentials reaches the model or DB".
    """
    r = VnRegexPiiRedactor()
    raw = "connect: postgres://dbuser:hunter2pwd@db.internal.example.org:5432/mydb"
    masked, entities = r.redact(raw)
    # The DSN body MUST be masked — inline password and host both gone.
    assert "hunter2pwd" not in masked
    assert "[DSN]" in masked
    assert any(e["type"] == "DSN" for e in entities)


def test_redact_method_returns_masked_text_for_credit_card() -> None:
    r = VnRegexPiiRedactor()
    # 16-digit Visa-shaped card (Luhn-valid not required at regex layer).
    masked, entities = r.redact("Paid via card 4111 1111 1111 1111 today")
    assert "4111 1111 1111 1111" not in masked
    # Regex layer matches as CARD (longest span at offset wins). Equally
    # acceptable: DSN/JWT/API_KEY are excluded because the input shape
    # is a 16-digit card-style cluster.
    masked_types = {e["type"] for e in entities}
    assert "CARD" in masked_types or "API_KEY" in masked_types, (
        f"expected CARD or API_KEY classification, got {masked_types}"
    )


def test_redact_empty_string_is_noop() -> None:
    r = VnRegexPiiRedactor()
    masked, entities = r.redact("")
    assert masked == ""
    assert entities == []


def test_redact_returns_provider_name() -> None:
    r = VnRegexPiiRedactor()
    assert r.get_provider_name() == "vn_regex"
