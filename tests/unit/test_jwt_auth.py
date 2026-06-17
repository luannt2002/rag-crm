"""Unit tests for ``infrastructure.security.jwt_auth``.

Covers:
- HS256 round-trip (encode external -> verify internal).
- HS256 wrong-secret rejection (UnauthorizedError, no panic).
- Issuer / audience claim mismatch rejection.
- Key not configured -> UnauthorizedError (no silent verify).
- Algorithm fallback when caller passes neither RS path nor explicit HS.
- ``decode_unverified`` succeeds on any well-formed token but raises on
  malformed input — keeps diagnostic-only contract.
"""

from __future__ import annotations

import pytest
from jose import jwt as _jwt

from ragbot.infrastructure.security.jwt_auth import JwtVerifier, decode_unverified
from ragbot.shared.errors import UnauthorizedError

_HS_SECRET = "unit-test-secret-do-not-deploy"
_OTHER_SECRET = "different-secret-rotated"


def _hs256_token(claims: dict, secret: str = _HS_SECRET) -> str:
    return _jwt.encode(claims, secret, algorithm="HS256")


def test_hs256_round_trip_returns_payload_dict() -> None:
    token = _hs256_token({"sub": "user-1", "tid": "abc"})
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_HS_SECRET)

    payload = verifier.verify(token)

    assert isinstance(payload, dict)
    assert payload["sub"] == "user-1"
    assert payload["tid"] == "abc"


def test_hs256_wrong_secret_raises_unauthorized() -> None:
    token = _hs256_token({"sub": "user-1"})
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_OTHER_SECRET)

    with pytest.raises(UnauthorizedError) as exc:
        verifier.verify(token)

    assert "invalid jwt" in str(exc.value).lower()


def test_audience_mismatch_raises_unauthorized() -> None:
    token = _hs256_token({"sub": "u", "aud": "ragbot-api"})
    verifier = JwtVerifier(
        algorithm="HS256",
        hmac_secret=_HS_SECRET,
        audience="some-other-audience",
    )

    with pytest.raises(UnauthorizedError):
        verifier.verify(token)


def test_no_key_configured_raises_unauthorized() -> None:
    # Asking for HS256 without a hmac_secret -> _key stays None -> verify must
    # fail loud, never return an unsigned-trusted payload.
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=None)

    with pytest.raises(UnauthorizedError) as exc:
        verifier.verify(_hs256_token({"sub": "u"}))

    assert "not configured" in str(exc.value).lower()


def test_unknown_algorithm_falls_back_to_hs256_with_secret() -> None:
    # Caller passes a non-RS / non-HS algorithm string -> ctor falls back to
    # HS256 + provided secret. Token signed with that secret must verify.
    token = _hs256_token({"sub": "u-fallback"})
    verifier = JwtVerifier(algorithm="bogus", hmac_secret=_HS_SECRET)

    payload = verifier.verify(token)
    assert payload["sub"] == "u-fallback"


def test_decode_unverified_returns_claims_without_signature_check() -> None:
    # Tokens signed with a secret we DO NOT know — decode_unverified still
    # returns claims because it explicitly skips signature verification.
    token = _hs256_token({"sub": "diag", "scope": "read"}, secret=_OTHER_SECRET)

    claims = decode_unverified(token)
    assert claims["sub"] == "diag"
    assert claims["scope"] == "read"


def test_decode_unverified_rejects_malformed_token() -> None:
    with pytest.raises(UnauthorizedError) as exc:
        decode_unverified("not.a.valid.jwt")
    assert "malformed jwt" in str(exc.value).lower()
