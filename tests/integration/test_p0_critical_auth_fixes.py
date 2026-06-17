"""Critical security fix — auth surface tests.

Three chained vulnerabilities patched in this sprint:

* **P0-A** ``/api/ragbot/test/tokens/self`` was unauthenticated and minted
  level-100 ``role="owner"`` tokens for any caller. Now hard-gated behind
  ``RAGBOT_DEV_TOKEN_ENABLED`` (default OFF → 404) and
  ``RAGBOT_DEV_TOKEN_ALLOW_NETWORK`` (default OFF → 403 for non-loopback).
* **P0-B** Service JWT tokens carried no ``exp`` claim and were decoded
  with ``verify_exp=False`` → immortal. We now mint with
  ``exp=now+DEFAULT_JWT_TTL_S`` and decode with ``verify_exp=True,
  require=["exp"]`` so legacy tokens fail validation.
* **P0-C** Service JWTs lacked ``tenant_id`` so ``TenantContextMiddleware``
  read ``None`` and skipped the cross-tenant guard. We add a
  ``tenant_id`` parameter to ``create_token`` / ``regenerate_token`` and
  bake the int claim into the payload.

Tests in this module exercise *unit-level* surface (decode logic and
endpoint gating) without booting the full FastAPI app — the existing
``test_3key_cross_tenant_isolation`` integration suite already covers
the end-to-end mismatch path, and we add focused tests here so the
P0 fixes have direct red/green coverage.
"""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from ragbot.application.services.jwt_token_service import JwtTokenService
from ragbot.shared.constants import DEFAULT_JWT_TTL_S


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


_JWT_SECRET = "p0-test-secret"  # noqa: S105 — test-only fixture


class _FakeSession:
    """In-memory async session — captures one row per service_name."""

    _store: dict[str, dict[str, Any]] = {}  # noqa: RUF012

    def __init__(self) -> None:
        pass

    async def __aenter__(self) -> "_FakeSession":
        return self

    async def __aexit__(self, *_a: Any) -> None:
        return None

    async def execute(self, stmt: Any, params: dict | None = None) -> Any:
        sql = str(stmt).strip()
        params = params or {}
        if sql.startswith("INSERT INTO api_tokens"):
            self._store[params["name"]] = {
                "id": params["id"], "version": params["ver"],
                "role": params.get("role", "service"),
                "rate_limit_value": params.get("rl_val", 0),
                "rate_limit_window": params.get("rl_win", 60),
                "revoked_at": None,
            }
        elif sql.startswith("UPDATE api_tokens"):
            for v in self._store.values():
                if v["id"] == params["id"]:
                    v["version"] = params["ver"]
                    break
        elif "SELECT id, version, role" in sql:
            row = self._store.get(params["name"])
            if row is None or row["revoked_at"] is not None:
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace(
                fetchone=lambda: (
                    row["id"], row["version"], row["role"],
                    row["rate_limit_value"], row["rate_limit_window"],
                ),
            )
        elif "SELECT version FROM api_tokens" in sql:
            row = self._store.get(params["name"])
            if row is None or row["revoked_at"] is not None:
                return SimpleNamespace(fetchone=lambda: None)
            return SimpleNamespace(fetchone=lambda: (row["version"],))
        return SimpleNamespace(fetchone=lambda: None, rowcount=0)

    async def commit(self) -> None:
        return None


def _make_session_factory() -> Any:
    """Return a fresh session factory bound to a *new* in-memory store."""
    _FakeSession._store = {}
    return _FakeSession


@pytest.fixture()
def jwt_svc() -> JwtTokenService:
    return JwtTokenService(
        session_factory=_make_session_factory(),
        jwt_secret=_JWT_SECRET,
    )


# ---------------------------------------------------------------------------
# P0-A — dev token endpoint gating
# ---------------------------------------------------------------------------


def _build_endpoint_request(client_host: str = "127.0.0.1") -> Any:
    """Mock the bits of ``Request`` the dev-token endpoint reads."""
    return SimpleNamespace(
        client=SimpleNamespace(host=client_host),
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(
                    redis_client=lambda: AsyncMock(),
                    session_factory=lambda: _make_session_factory(),
                ),
                settings=SimpleNamespace(
                    app=SimpleNamespace(api_token=_JWT_SECRET),
                ),
                dev_jwt_secret=_JWT_SECRET,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_dev_token_endpoint_returns_404_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default (env unset) → 404, hiding the endpoint entirely.

    Production safety: even an attacker who knows the path receives
    ``Not Found`` rather than a hint that the route exists.
    """
    monkeypatch.delenv("RAGBOT_DEV_TOKEN_ENABLED", raising=False)
    monkeypatch.delenv("RAGBOT_DEV_TOKEN_ALLOW_NETWORK", raising=False)

    from ragbot.interfaces.http.routes.test_chat import get_self_token

    request = _build_endpoint_request("127.0.0.1")
    with pytest.raises(HTTPException) as ei:
        await get_self_token(request)
    assert ei.value.status_code == 404
    assert ei.value.detail == "not found"


@pytest.mark.asyncio
async def test_dev_token_endpoint_blocked_from_non_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENABLED=true + non-loopback caller + ALLOW_NETWORK=false → 403."""
    monkeypatch.setenv("RAGBOT_DEV_TOKEN_ENABLED", "true")
    monkeypatch.setenv("RAGBOT_DEV_TOKEN_ALLOW_NETWORK", "false")

    from ragbot.interfaces.http.routes.test_chat import get_self_token

    request = _build_endpoint_request("10.0.0.1")
    with pytest.raises(HTTPException) as ei:
        await get_self_token(request)
    assert ei.value.status_code == 403
    assert "localhost" in ei.value.detail.lower()


@pytest.mark.asyncio
async def test_dev_token_endpoint_works_from_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ENABLED=true + loopback caller → 200 + token returned.

    Patches ``_token_service`` so we don't need a real DB; verifies the
    gate logic flows through to the mint step on localhost.
    """
    monkeypatch.setenv("RAGBOT_DEV_TOKEN_ENABLED", "true")
    monkeypatch.delenv("RAGBOT_DEV_TOKEN_ALLOW_NETWORK", raising=False)

    fake_token = "mock.jwt.token"  # noqa: S105
    fake_svc = MagicMock()
    fake_svc.create_token = AsyncMock(return_value={"token": fake_token})
    fake_svc.regenerate_token = AsyncMock(return_value={"token": fake_token})

    redis_mock = AsyncMock()
    redis_mock.get = AsyncMock(return_value=None)  # cache miss
    redis_mock.set = AsyncMock()

    sf_mock = MagicMock()
    session_mock = AsyncMock()
    session_mock.__aenter__ = AsyncMock(return_value=session_mock)
    session_mock.__aexit__ = AsyncMock()
    session_mock.execute = AsyncMock(
        return_value=SimpleNamespace(fetchone=lambda: None),
    )
    sf_mock.return_value = session_mock

    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                container=SimpleNamespace(
                    redis_client=lambda: redis_mock,
                    session_factory=lambda: sf_mock,
                ),
                settings=SimpleNamespace(
                    app=SimpleNamespace(api_token=_JWT_SECRET),
                ),
                dev_jwt_secret=_JWT_SECRET,
            ),
        ),
    )

    with patch(
        "ragbot.interfaces.http.routes.test_chat._token_service",
        new=AsyncMock(return_value=fake_svc),
    ):
        from ragbot.interfaces.http.routes.test_chat import get_self_token
        result = await get_self_token(request)

    assert result == {"ok": True, "token": fake_token}
    fake_svc.create_token.assert_awaited_once()


# ---------------------------------------------------------------------------
# P0-B — JWT exp claim + verify_exp
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwt_token_has_exp_claim(jwt_svc: JwtTokenService) -> None:
    """Newly-minted token MUST carry ``exp`` ≈ now + DEFAULT_JWT_TTL_S."""
    before = int(time.time())
    result = await jwt_svc.create_token(
        service_name=f"svc-{uuid.uuid4().hex[:8]}",
        rate_limit_value=0, rate_limit_window=60,
    )
    decoded = pyjwt.decode(
        result["token"], _JWT_SECRET, algorithms=["HS256"],
        options={"verify_exp": False},  # only for inspection
    )
    assert "exp" in decoded
    # exp is roughly now + DEFAULT_JWT_TTL_S (allow 5s skew)
    assert before + DEFAULT_JWT_TTL_S - 5 <= decoded["exp"] <= before + DEFAULT_JWT_TTL_S + 5


@pytest.mark.asyncio
async def test_jwt_expired_token_rejected(jwt_svc: JwtTokenService) -> None:
    """A token whose ``exp`` is in the past MUST raise on decode.

    We mint a token bypassing ``create_token`` so ``exp`` lies in the
    past, then call the private ``_decode`` directly to verify
    ``verify_exp=True`` is wired.
    """
    payload = {
        "jti": str(uuid.uuid4()),
        "sub": "expired-svc",
        "ver": 1,
        "role": "service",
        "rl_val": 0,
        "rl_win": 60,
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,  # expired 1h ago
        "iss": "ragbot",
    }
    expired_token = pyjwt.encode(payload, _JWT_SECRET, algorithm="HS256")

    with pytest.raises(pyjwt.ExpiredSignatureError):
        jwt_svc._decode(expired_token)


@pytest.mark.asyncio
async def test_jwt_backcompat_token_without_exp_rejected(
    jwt_svc: JwtTokenService,
) -> None:
    """Legacy immortal tokens (no ``exp`` claim) MUST be rejected.

    Without ``require=["exp"]`` pyjwt would silently accept a missing
    ``exp`` even with ``verify_exp=True``. This test guards the
    ``require`` clause in ``_decode``.
    """
    legacy_payload = {
        "jti": str(uuid.uuid4()),
        "sub": "legacy-svc",
        "ver": 1,
        "role": "owner",
        "rl_val": 0,
        "rl_win": 60,
        "iat": int(time.time()),
        "iss": "ragbot",
        # NOTE: no "exp" — this is what every pre-fix token looks like.
    }
    legacy_token = pyjwt.encode(legacy_payload, _JWT_SECRET, algorithm="HS256")

    with pytest.raises(pyjwt.MissingRequiredClaimError):
        jwt_svc._decode(legacy_token)


@pytest.mark.asyncio
async def test_jwt_verify_token_returns_none_for_expired(
    jwt_svc: JwtTokenService,
) -> None:
    """``verify_token`` swallows expired/invalid → None (not crash)."""
    payload = {
        "jti": str(uuid.uuid4()),
        "sub": "expired-svc",
        "ver": 1,
        "iat": int(time.time()) - 7200,
        "exp": int(time.time()) - 3600,
        "iss": "ragbot",
    }
    expired_token = pyjwt.encode(payload, _JWT_SECRET, algorithm="HS256")
    result = await jwt_svc.verify_token(expired_token)
    assert result is None


# ---------------------------------------------------------------------------
# P0-C — tenant_id claim baked into JWT
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_token_includes_tenant_id_claim(
    jwt_svc: JwtTokenService,
) -> None:
    """create_token(tenant_id=42) → decoded payload has tenant_id=42 (int)."""
    result = await jwt_svc.create_token(
        service_name=f"svc-{uuid.uuid4().hex[:8]}",
        rate_limit_value=0, rate_limit_window=60,
        tenant_id=42,
    )
    decoded = pyjwt.decode(
        result["token"], _JWT_SECRET, algorithms=["HS256"],
        options={"verify_exp": False},
    )
    assert decoded.get("tenant_id") == 42
    assert isinstance(decoded["tenant_id"], int)


@pytest.mark.asyncio
async def test_service_token_without_tenant_id_omits_claim(
    jwt_svc: JwtTokenService,
) -> None:
    """tenant_id=None (default) → no ``tenant_id`` field in payload.

    Middleware (tenant_context.py) treats absent claim as unscoped:
    ``_tok_tenant_id is None`` → cross-tenant guard skipped + warning
    log emitted (covered in middleware tests).
    """
    result = await jwt_svc.create_token(
        service_name=f"svc-{uuid.uuid4().hex[:8]}",
        rate_limit_value=0, rate_limit_window=60,
    )
    decoded = pyjwt.decode(
        result["token"], _JWT_SECRET, algorithms=["HS256"],
        options={"verify_exp": False},
    )
    assert "tenant_id" not in decoded


@pytest.mark.asyncio
async def test_regenerate_token_carries_tenant_id(
    jwt_svc: JwtTokenService,
) -> None:
    """Regenerated token also includes tenant_id when supplied."""
    name = f"svc-{uuid.uuid4().hex[:8]}"
    await jwt_svc.create_token(
        service_name=name, rate_limit_value=0, rate_limit_window=60,
    )
    regen = await jwt_svc.regenerate_token(name, tenant_id=99)
    decoded = pyjwt.decode(
        regen["token"], _JWT_SECRET, algorithms=["HS256"],
        options={"verify_exp": False},
    )
    assert decoded.get("tenant_id") == 99
    assert decoded["ver"] == 2


__all__: list[str] = []
