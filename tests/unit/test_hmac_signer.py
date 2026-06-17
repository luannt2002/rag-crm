"""Unit tests for ``infrastructure.security.hmac_signer``.

Covers:
- Sign produces stable hex digest for same (body, secret).
- Different bodies produce different signatures.
- Different secrets produce different signatures.
- ``verify_signature`` uses constant-time compare and accepts the matching pair.
- ``verify_signature`` rejects tampered body / tampered signature.
"""

from __future__ import annotations

import pytest

from ragbot.infrastructure.security.hmac_signer import sign_payload, verify_signature

_SECRET = "webhook-secret-fixture"
_OTHER_SECRET = "rotated-secret"


def test_sign_payload_is_deterministic_hex() -> None:
    body = b'{"event":"answered","id":1}'
    sig1 = sign_payload(body, secret=_SECRET)
    sig2 = sign_payload(body, secret=_SECRET)

    assert sig1 == sig2
    assert len(sig1) == 64  # SHA-256 hex digest length
    int(sig1, 16)  # raises if non-hex


def test_sign_payload_changes_with_body() -> None:
    sig_a = sign_payload(b"alpha", secret=_SECRET)
    sig_b = sign_payload(b"beta", secret=_SECRET)
    assert sig_a != sig_b


def test_sign_payload_changes_with_secret() -> None:
    body = b"same body"
    sig_default = sign_payload(body, secret=_SECRET)
    sig_other = sign_payload(body, secret=_OTHER_SECRET)
    assert sig_default != sig_other


def test_verify_signature_accepts_matching_pair() -> None:
    body = b'{"k":"v"}'
    sig = sign_payload(body, secret=_SECRET)
    assert verify_signature(body, sig, secret=_SECRET) is True


@pytest.mark.parametrize(
    "tamper",
    [
        b'{"k":"V"}',     # body changed (case)
        b'{"k":"v"} ',    # trailing space
        b"",              # empty body
        b'{"k":"v","x":1}',  # extra field
    ],
)
def test_verify_signature_rejects_tampered_body(tamper: bytes) -> None:
    original = b'{"k":"v"}'
    sig = sign_payload(original, secret=_SECRET)
    assert verify_signature(tamper, sig, secret=_SECRET) is False


def test_verify_signature_rejects_tampered_signature() -> None:
    body = b'{"k":"v"}'
    sig = sign_payload(body, secret=_SECRET)
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]

    assert verify_signature(body, flipped, secret=_SECRET) is False
    # Wrong secret = wrong sig — cross-check
    assert verify_signature(body, sig, secret=_OTHER_SECRET) is False
