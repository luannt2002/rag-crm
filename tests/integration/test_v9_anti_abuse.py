"""Integration tests for AntiAbuseMiddleware + honeypot routes.

Covers:
* User-Agent denylist → 403 (with curl/python-requests/empty UA)
* X-API-Key bypass for denylisted UA when key hash is in allowlist
* Honeypot routes (/wp-admin, /.env, /admin/login.php) → 404 + sadd suspicious
* Auth-fail ban after threshold → next request 429 with Retry-After
* HMAC verify helper accepted, tampered → False, missing secret → ValueError
"""

from __future__ import annotations

import hashlib
import importlib
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ragbot.shared.constants import DEFAULT_HONEYPOT_PATHS
from ragbot.shared.hmac_signing import (
    compute_signature,
    verify_request_signature,
)


def _make_redis_mock(
    *,
    banned: bool = False,
    suspicious: bool = False,
    auth_fail_count_start: int = 0,
) -> MagicMock:
    """AsyncMock-based Redis stand-in covering the anti-abuse contract."""
    redis = MagicMock()
    state = {
        "auth_fail": auth_fail_count_start,
        "ip_rl": 0,
        "4xx_total": 0,
        "4xx_fail": 0,
        "ban": banned,
        "suspicious_ips": set(),
        "path_set_card": 1,
        "sadd_calls": [],
    }

    async def _incr(key: str) -> int:
        if key.startswith("ragbot:antiabuse:authfail:"):
            state["auth_fail"] += 1
            return state["auth_fail"]
        if key.startswith("ragbot:rl:ip:"):
            state["ip_rl"] += 1
            return state["ip_rl"]
        if key.startswith("ragbot:antiabuse:4xx_total:"):
            state["4xx_total"] += 1
            return state["4xx_total"]
        if key.startswith("ragbot:antiabuse:4xx_fail:"):
            state["4xx_fail"] += 1
            return state["4xx_fail"]
        return 1

    async def _expire(_key: str, _ttl: int) -> bool:
        return True

    async def _get(key: str) -> str | None:
        if key.startswith("ragbot:antiabuse:ban:"):
            return "1" if state["ban"] else None
        if key.startswith("ragbot:antiabuse:4xx_fail:"):
            return str(state["4xx_fail"]) if state["4xx_fail"] else None
        return None

    async def _set(key: str, _val: str, ex: int | None = None) -> bool:
        if key.startswith("ragbot:antiabuse:ban:"):
            state["ban"] = True
        return True

    async def _sismember(_key: str, ip: str) -> bool:
        if suspicious:
            return True
        return ip in state["suspicious_ips"]

    async def _sadd(key: str, *ips: str) -> int:
        state["sadd_calls"].append((key, ips))
        for ip in ips:
            state["suspicious_ips"].add(ip)
        return len(ips)

    async def _scard(_key: str) -> int:
        return state["path_set_card"]

    redis.incr = AsyncMock(side_effect=_incr)
    redis.expire = AsyncMock(side_effect=_expire)
    redis.get = AsyncMock(side_effect=_get)
    redis.set = AsyncMock(side_effect=_set)
    redis.sismember = AsyncMock(side_effect=_sismember)
    redis.sadd = AsyncMock(side_effect=_sadd)
    redis.scard = AsyncMock(side_effect=_scard)
    redis._state = state  # expose for assertions
    return redis


@asynccontextmanager
async def _noop_lifespan(application: FastAPI) -> AsyncIterator[None]:
    """No-op lifespan; tests inject state on the FastAPI app directly."""
    yield


def _build_client(
    *,
    redis_mock: MagicMock | None,
    programmatic_api_keys: str = "",
    ua_denylist: str = "",
    ip_rate_limit_enabled: bool = False,
    anti_abuse_enabled: bool = True,
) -> TestClient:
    import os
    os.environ["APP_PROGRAMMATIC_API_KEYS"] = programmatic_api_keys
    os.environ["APP_UA_DENYLIST"] = ua_denylist
    os.environ["APP_IP_RATE_LIMIT_ENABLED"] = (
        "true" if ip_rate_limit_enabled else "false"
    )
    os.environ["APP_ANTI_ABUSE_ENABLED"] = (
        "true" if anti_abuse_enabled else "false"
    )
    os.environ["APP_TRUSTED_PROXIES"] = ""
    os.environ["APP_IP_ALLOWLIST"] = ""
    os.environ["APP_CORS_ALLOWED_ORIGINS"] = "[]"

    from ragbot.config import settings as settings_mod
    settings_mod.get_settings.cache_clear()

    app_mod = importlib.import_module("ragbot.interfaces.http.app")
    original = app_mod.lifespan
    app_mod.lifespan = _noop_lifespan
    try:
        application = app_mod.create_app()
    finally:
        app_mod.lifespan = original

    mock_container = MagicMock()
    if redis_mock is not None:
        mock_container.redis_client = MagicMock(return_value=redis_mock)
    else:
        mock_container.redis_client = None
    application.state.container = mock_container
    return TestClient(application, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# UA denylist
# ---------------------------------------------------------------------------


class TestUserAgentDenylist:
    def test_curl_ua_returns_403(self) -> None:
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={"User-Agent": "curl/7.81.0"},
        )
        assert r.status_code == 403
        body = r.json()
        assert body["error"]["code"] == "FORBIDDEN_USER_AGENT"

    def test_python_requests_ua_returns_403(self) -> None:
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={"User-Agent": "python-requests/2.31.0"},
        )
        assert r.status_code == 403

    def test_empty_ua_returns_403(self) -> None:
        """Empty UA is itself suspicious — most legitimate browsers send one."""
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={"User-Agent": ""},
        )
        assert r.status_code == 403

    def test_browser_ua_passes(self) -> None:
        """Real browser UA → middleware lets through."""
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                ),
            },
        )
        # Downstream may 404/422/500 — the gate is "not 403 from anti-abuse".
        assert r.status_code != 403

    def test_x_api_key_bypasses_ua_denylist(self) -> None:
        """Valid X-API-Key whose SHA256 is in allowlist → curl UA accepted."""
        raw_key = "secret-internal-monitor-key"
        key_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        redis = _make_redis_mock()
        client = _build_client(
            redis_mock=redis,
            programmatic_api_keys=key_hash,
        )
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={
                "User-Agent": "curl/7.81.0",
                "X-API-Key": raw_key,
            },
        )
        # Bypassed UA denylist → not 403 from us.
        assert r.status_code != 403
        # Wrong key still rejected.
        r2 = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={
                "User-Agent": "curl/7.81.0",
                "X-API-Key": "wrong-key",
            },
        )
        assert r2.status_code == 403

    def test_curl_ua_on_ops_endpoint_passes(self) -> None:
        """UA denylist is scoped to /chat + /sync hot endpoints — ops scripts
        (curl deploy probes, monitoring) hitting /tokens/self / /admin / /health
        must NOT be 403'd by anti-abuse."""
        redis = _make_redis_mock()
        client = _build_client(redis_mock=redis)
        # Token endpoint is ops/auth path → UA denylist skipped here.
        r = client.get(
            "/api/ragbot/test/tokens/self",
            headers={"User-Agent": "curl/7.81.0"},
        )
        # Downstream may 404/200/500 — the gate is "not 403 from anti-abuse".
        # Anti-abuse 403 = FORBIDDEN_USER_AGENT body code. Other 403s are RBAC
        # which we accept.
        if r.status_code == 403:
            body = r.json()
            assert body.get("error", {}).get("code") != "FORBIDDEN_USER_AGENT", (
                "ops endpoint /tokens/self must not trigger UA denylist"
            )


# ---------------------------------------------------------------------------
# Honeypot routes
# ---------------------------------------------------------------------------


class TestHoneypot:
    @pytest.mark.parametrize("path", list(DEFAULT_HONEYPOT_PATHS))
    def test_honeypot_returns_404_and_marks_suspicious(self, path: str) -> None:
        redis = _make_redis_mock()
        client = _build_client(
            redis_mock=redis,
            anti_abuse_enabled=False,  # disable so honeypot is the only gate
        )
        r = client.get(
            path,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux)"},
        )
        assert r.status_code == 404
        # IP must have been added to the suspicious set.
        sadd_keys = [call[0] for call in redis._state["sadd_calls"]]
        assert any(
            "antiabuse:suspicious_ips" in k for k in sadd_keys
        ), f"sadd calls so far: {redis._state['sadd_calls']}"


# ---------------------------------------------------------------------------
# Disabled switch
# ---------------------------------------------------------------------------


def test_anti_abuse_disabled_switch_skips_middleware() -> None:
    redis = _make_redis_mock()
    client = _build_client(
        redis_mock=redis,
        anti_abuse_enabled=False,
    )
    r = client.post(
        "/api/ragbot/test/chat",
        json={},
        headers={"User-Agent": "curl/7.81.0"},
    )
    # With middleware off, curl UA is no longer rejected by us.
    assert r.status_code != 403


# ---------------------------------------------------------------------------
# Banned-IP short circuit
# ---------------------------------------------------------------------------


class TestBannedIp:
    def test_banned_ip_returns_429_with_retry_after(self) -> None:
        redis = _make_redis_mock(banned=True)
        client = _build_client(redis_mock=redis)
        r = client.post(
            "/api/ragbot/test/chat",
            json={},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        assert r.status_code == 429
        assert r.headers.get("Retry-After") is not None
        body = r.json()
        assert body["error"]["code"] == "TEMPORARILY_BANNED"


# ---------------------------------------------------------------------------
# HMAC helper passthrough (verify path used by /sync)
# ---------------------------------------------------------------------------


class TestHmacHelperWiring:
    """Defence-in-depth: HMAC helper is import-clean + correct.

    The middleware-level enforcement is OPTIONAL gentle rollout (per
    plan); this test pins the helper contract so a future /sync route
    integration won't silently break.
    """

    def test_compute_then_verify_roundtrip(self) -> None:
        body = b'{"upstream_tenant_id": 1}'
        secret = "tenant-pre-shared-secret"  # noqa: S105 — test fixture
        sig = compute_signature(body, secret)
        assert verify_request_signature(body, sig, secret) is True

    def test_tampered_body_rejected(self) -> None:
        body = b'{"upstream_tenant_id": 1}'
        secret = "tenant-pre-shared-secret"  # noqa: S105 — test fixture
        sig = compute_signature(body, secret)
        assert (
            verify_request_signature(body + b"!", sig, secret) is False
        )

    def test_missing_secret_raises_so_caller_can_distinguish(self) -> None:
        body = b'{"upstream_tenant_id": 1}'
        sig = "0" * 64
        with pytest.raises(ValueError, match="non-empty"):
            verify_request_signature(body, sig, "")
