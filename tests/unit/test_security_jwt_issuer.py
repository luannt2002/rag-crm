"""S4.2 — JWT decode requires `iss` claim and matching issuer (Finding #5).

Audit `RAG_Master_of_Masters_DeepDive_Report.md` Finding #5:
``JwtTokenService._decode`` previously called ``pyjwt.decode`` without
the ``issuer=`` kwarg, so a token whose ``iss`` claim was missing or
attacker-supplied would still decode successfully (signature OK, exp
OK). Combined with the HMAC secret being reused across services, an
attacker who compromised any service token leaked the same trust
boundary to every peer service. Fix: require ``iss`` and pin it to
``JWT_ISSUER = "ragbot"`` — pyjwt then raises ``InvalidIssuerError``
on mismatch and ``MissingRequiredClaimError`` when ``iss`` is absent.

These unit tests round-trip pyjwt directly (no DB / Redis) so they
verify the decode contract in isolation. ``verify_token`` swallows
the exception and returns ``None``; the middleware turns ``None`` into
a 401 — those paths are covered by the integration test suite.
"""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.application.services.jwt_token_service import JwtTokenService
from ragbot.shared.constants import JWT_ISSUER, JWT_REQUIRED_CLAIMS

_SECRET = "unit-test-secret-do-not-deploy-padding-32B"
_ALG = "HS256"


def _svc() -> JwtTokenService:
    """Build a JwtTokenService with a no-op session factory.

    ``_decode`` does not touch the DB so we can hand it a stub factory.
    A bare class is enough — type hint ``async_sessionmaker[AsyncSession]``
    is structural at the call site.
    """
    # The decode path never opens a session; cast through Any.
    sf: async_sessionmaker[AsyncSession] = None  # type: ignore[assignment]
    return JwtTokenService(session_factory=sf, jwt_secret=_SECRET)


def _mint(claims: dict, *, secret: str = _SECRET) -> str:
    return pyjwt.encode(claims, secret, algorithm=_ALG)


def test_valid_issuer_decodes() -> None:
    """Round-trip with the correct issuer succeeds."""
    now = int(time.time())
    token = _mint({
        "sub": "ragbot-nestjs",
        "ver": 1,
        "iat": now,
        "exp": now + 3600,
        "iss": JWT_ISSUER,
    })
    payload = _svc()._decode(token)
    assert payload["sub"] == "ragbot-nestjs"
    assert payload["iss"] == JWT_ISSUER


def test_wrong_issuer_rejected() -> None:
    """Token with `iss="attacker"` must raise InvalidIssuerError."""
    now = int(time.time())
    token = _mint({
        "sub": "ragbot-nestjs",
        "ver": 1,
        "iat": now,
        "exp": now + 3600,
        "iss": "attacker",
    })
    with pytest.raises(pyjwt.InvalidIssuerError):
        _svc()._decode(token)


def test_missing_issuer_rejected() -> None:
    """Token without `iss` claim must raise MissingRequiredClaimError."""
    now = int(time.time())
    token = _mint({
        "sub": "ragbot-nestjs",
        "ver": 1,
        "iat": now,
        "exp": now + 3600,
        # NOTE: no `iss` field — pre-S4 tokens that pyjwt would accept.
    })
    with pytest.raises(pyjwt.MissingRequiredClaimError) as exc:
        _svc()._decode(token)
    assert "iss" in str(exc.value).lower()


def test_missing_exp_still_rejected() -> None:
    """Regression: `exp` requirement (prior P0 fix) MUST remain enforced."""
    token = _mint({
        "sub": "ragbot-nestjs",
        "ver": 1,
        "iss": JWT_ISSUER,
        # NOTE: no `exp` — should still fail.
    })
    with pytest.raises(pyjwt.MissingRequiredClaimError) as exc:
        _svc()._decode(token)
    assert "exp" in str(exc.value).lower()


def test_expired_token_rejected() -> None:
    """Regression: exp check still trips on past tokens."""
    past = int(time.time()) - 3600
    token = _mint({
        "sub": "ragbot-nestjs",
        "ver": 1,
        "iat": past - 10,
        "exp": past,
        "iss": JWT_ISSUER,
    })
    with pytest.raises(pyjwt.ExpiredSignatureError):
        _svc()._decode(token)


def test_wrong_secret_rejected() -> None:
    """Regression: signature still verified."""
    now = int(time.time())
    token = _mint({
        "sub": "ragbot-nestjs",
        "ver": 1,
        "iat": now,
        "exp": now + 3600,
        "iss": JWT_ISSUER,
    }, secret="different-secret-padding-padding-32B")
    with pytest.raises(pyjwt.InvalidSignatureError):
        _svc()._decode(token)


def test_required_claims_constant_locked() -> None:
    """Lock the public contract so accidental edits don't drop a claim."""
    assert "exp" in JWT_REQUIRED_CLAIMS
    assert "iss" in JWT_REQUIRED_CLAIMS
    assert JWT_ISSUER == "ragbot"
