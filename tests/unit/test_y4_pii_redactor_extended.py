"""Y4-SECURITY-MAX — extended PII redactor coverage (2026-05-01).

The Y4 audit found the regex layer covered EMAIL/PHONE/CCCD/IP/BANK_ACCT
only — leaving high-impact secrets (API keys, JWTs, DB DSN passwords,
credit cards, VN biển số xe) un-masked when they appear in user chat or
log/event payloads. This suite locks the new patterns added to
``shared/constants.py`` + wired into ``VnRegexPiiRedactor``.

Domain-neutral check: every regex uses prefix SHAPE (``sk-``, ``AIza``,
``eyJ``, ``postgres://``) — no vendor-name literal — so the rule applies
to every tenant equally.
"""

from __future__ import annotations

from ragbot.infrastructure.pii.vn_regex_pii_redactor import VnRegexPiiRedactor


# ---------------------------------------------------------------------------
# §1 — API key shapes
# ---------------------------------------------------------------------------


def test_api_key_provider_prefix_sk_redacted() -> None:
    """OpenAI-style ``sk-`` 48-char tail must mask as API_KEY."""
    r = VnRegexPiiRedactor()
    raw = "key=sk-abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
    out, ents = r.redact(raw)
    assert "[API_KEY]" in out
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in out
    assert any(e["type"] == "API_KEY" for e in ents)


def test_api_key_provider_prefix_aiza_redacted() -> None:
    """Google-style ``AIza`` 35-char tail must mask as API_KEY."""
    r = VnRegexPiiRedactor()
    raw = "token=AIzaSyA-abcdefghijklmnop_qrstuvwxyz_123"
    out, ents = r.redact(raw)
    assert "[API_KEY]" in out
    assert "AIzaSyA" not in out
    assert any(e["type"] == "API_KEY" for e in ents)


def test_api_key_generic_bearer_redacted() -> None:
    """Generic ``bearer_<hex>`` shape masks as API_KEY."""
    r = VnRegexPiiRedactor()
    raw = "Authorization: bearer_abcdef0123456789ABCDEF"
    out, ents = r.redact(raw)
    assert "[API_KEY]" in out
    assert any(e["type"] == "API_KEY" for e in ents)


# ---------------------------------------------------------------------------
# §2 — JWT
# ---------------------------------------------------------------------------


def test_jwt_three_segments_redacted() -> None:
    """Three base64url segments separated by dots → JWT mask."""
    r = VnRegexPiiRedactor()
    jwt_token = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    out, ents = r.redact(f"token={jwt_token} end")
    assert "[JWT]" in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert any(e["type"] == "JWT" for e in ents)


def test_jwt_does_not_match_version_string() -> None:
    """Version strings like ``1.2.3`` must NOT trip the JWT pattern."""
    r = VnRegexPiiRedactor()
    out, ents = r.redact("ragbot version 1.2.3 — release 4.5.6")
    assert "[JWT]" not in out
    assert all(e["type"] != "JWT" for e in ents)


# ---------------------------------------------------------------------------
# §3 — DB DSN with inline password
# ---------------------------------------------------------------------------


def test_postgres_dsn_with_password_redacted() -> None:
    """Postgres DSN with inline password masks fully — credential leak vector."""
    r = VnRegexPiiRedactor()
    raw = "DATABASE_URL=postgresql://app_user:s3cret-pw@10.0.1.10:5432/db"
    out, ents = r.redact(raw)
    assert "[DSN]" in out
    assert "s3cret-pw" not in out
    assert "app_user" not in out
    assert any(e["type"] == "DSN" for e in ents)


def test_redis_dsn_with_password_redacted() -> None:
    """Redis DSN with auth user:pass masks as DSN."""
    r = VnRegexPiiRedactor()
    raw = "REDIS_URL=redis://default:topsecret@redis-host:6379/0"
    out, ents = r.redact(raw)
    assert "[DSN]" in out
    assert "topsecret" not in out
    assert any(e["type"] == "DSN" for e in ents)


def test_mongodb_srv_dsn_redacted() -> None:
    """``mongodb+srv://`` flavour also masks."""
    r = VnRegexPiiRedactor()
    raw = "mongodb+srv://u:p@cluster0.example.net"
    out, ents = r.redact(raw)
    assert "[DSN]" in out
    assert any(e["type"] == "DSN" for e in ents)


# ---------------------------------------------------------------------------
# §4 — Credit card
# ---------------------------------------------------------------------------


def test_credit_card_16_digit_redacted() -> None:
    """16-digit Visa-shape masks as CARD."""
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Card 4111 1111 1111 1111 paid")
    assert "[CARD]" in out
    assert "4111 1111 1111 1111" not in out
    assert any(e["type"] == "CARD" for e in ents)


def test_credit_card_13_digit_redacted() -> None:
    """Older 13-digit cards still masked."""
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Card 4111111111111 ok")
    assert "[CARD]" in out
    assert any(e["type"] == "CARD" for e in ents)


# ---------------------------------------------------------------------------
# §5 — VN biển số xe
# ---------------------------------------------------------------------------


def test_vn_plate_dash_redacted() -> None:
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Xe bien so 30A-12345 vao bai")
    assert "[VN_PLATE]" in out
    assert "30A-12345" not in out
    assert any(e["type"] == "VN_PLATE" for e in ents)


def test_vn_plate_dotted_redacted() -> None:
    """Format with .NN suffix common on plates ("51F 678.90")."""
    r = VnRegexPiiRedactor()
    out, ents = r.redact("Plate 51F 67890 entered")
    assert "[VN_PLATE]" in out
    assert any(e["type"] == "VN_PLATE" for e in ents)


# ---------------------------------------------------------------------------
# §6 — combined leak — every secret class in one chat message
# ---------------------------------------------------------------------------


def test_combined_secrets_all_redacted() -> None:
    """Single sentence with API key + JWT + DSN + card + email + phone +
    plate — every credential class masks; no raw secret leaks through.
    """
    r = VnRegexPiiRedactor()
    jwt_token = (
        "eyJhbGciOiJIUzI1NiJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    raw = (
        "API key sk-abcdefghijklmnopqrstuvwxyz012345 "
        f"JWT {jwt_token} "
        "DSN postgresql://u:p@10.0.1.5:5432/db "
        "card 4111 1111 1111 1111 "
        "phone 0901234567 "
        "plate 30A-12345 "
        "email a@b.com"
    )
    out, _ = r.redact(raw)
    # No raw secret leaks through.
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in out
    assert "eyJhbGciOiJIUzI1NiJ9" not in out
    assert "postgresql://u:p@" not in out
    assert "4111 1111 1111 1111" not in out
    assert "0901234567" not in out
    assert "30A-12345" not in out
    assert "a@b.com" not in out
    # Every class mask is present.
    for tag in ("[API_KEY]", "[JWT]", "[DSN]", "[CARD]", "[PHONE]",
                "[VN_PLATE]", "[EMAIL]"):
        assert tag in out, f"{tag} missing from {out!r}"
