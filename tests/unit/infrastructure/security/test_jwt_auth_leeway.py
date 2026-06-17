"""Regression test for SECURITY_AUDIT_20260516 AUTH-7 — JWT clock skew leeway.

Without leeway, a JWT issued 1 second in the future (NTP drift between
the auth service and the gateway) is rejected → legitimate sessions
fail during clock-correction events.

Post-fix: ``JwtVerifier`` accepts an optional ``leeway_s`` param and
defaults to ``DEFAULT_JWT_CLOCK_SKEW_S`` (30 s), passed into
``jose.jwt.decode(options={"leeway": ...})``.
"""
from __future__ import annotations

import time

import pytest
from jose import jwt

from ragbot.infrastructure.security.jwt_auth import JwtVerifier
from ragbot.shared.constants import DEFAULT_JWT_CLOCK_SKEW_S
from ragbot.shared.errors import UnauthorizedError


_SECRET = "a-test-secret-of-sufficient-length-32-bytes-long-enough!"


def _make_token(*, nbf_offset: int = 0, exp_offset: int = 300) -> str:
    now = int(time.time())
    payload = {
        "sub": "user-1",
        "iat": now,
        "nbf": now + nbf_offset,
        "exp": now + exp_offset,
    }
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def test_jwt_verifier_default_leeway_constant_is_30s() -> None:
    """SSoT: the leeway default lives in shared.constants for zero-hardcode."""
    assert DEFAULT_JWT_CLOCK_SKEW_S == 30


def test_jwt_verifier_accepts_token_within_leeway_window() -> None:
    """Token issued 5s in the future (NBF in the future) is accepted under leeway."""
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_SECRET)
    token = _make_token(nbf_offset=5, exp_offset=600)
    claims = verifier.verify(token)
    assert claims["sub"] == "user-1"


def test_jwt_verifier_rejects_token_beyond_leeway_window() -> None:
    """Token issued 60s in the future (> 30s leeway) is still rejected."""
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_SECRET)
    token = _make_token(nbf_offset=60, exp_offset=600)
    with pytest.raises(UnauthorizedError) as exc:
        verifier.verify(token)
    # Some jose versions report "not yet valid"; others raise NBF claim
    # error. Both surface as UnauthorizedError("invalid jwt: ...") here.
    msg = str(exc.value).lower()
    assert "invalid jwt" in msg


def test_jwt_verifier_accepts_just_expired_token_within_leeway() -> None:
    """Token that expired 5s ago is still accepted (leeway applies to exp too)."""
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_SECRET)
    token = _make_token(nbf_offset=0, exp_offset=-5)
    claims = verifier.verify(token)
    assert claims["sub"] == "user-1"


def test_jwt_verifier_rejects_long_expired_token() -> None:
    """Token expired 60s ago (> leeway) is rejected."""
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_SECRET)
    token = _make_token(nbf_offset=0, exp_offset=-60)
    with pytest.raises(UnauthorizedError):
        verifier.verify(token)


def test_jwt_verifier_explicit_leeway_override_applies() -> None:
    """Caller can override leeway when stricter behaviour is required."""
    verifier = JwtVerifier(algorithm="HS256", hmac_secret=_SECRET, leeway_s=0)
    token = _make_token(nbf_offset=5, exp_offset=600)
    with pytest.raises(UnauthorizedError):
        verifier.verify(token)
