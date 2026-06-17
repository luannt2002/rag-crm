"""JWT Token Service — generate/verify service tokens with versioning.

Each service (NestJS, ragbot, …) holds one JWT. Tokens carry a ``version``
claim that increments on regeneration so the previous token is rejected
even before its ``exp`` claim. Tokens also carry a ``tenant_id`` int claim
so the request-tenant guard middleware can scope admin routes; tokens
without it are treated as unscoped and admin endpoints deny them.

Redis cache: ``ragbot:token_ver:{service_name}`` → version int. Verify
checks Redis first, falls back to DB. ``bootstrap_cache()`` warms it on
startup. Invalidated on create / regenerate / revoke.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any

import jwt as pyjwt
import orjson
import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.shared.constants import (
    DEFAULT_JWT_TTL_S,
    DEFAULT_SERVICE_CACHE_TTL_S,
    FALLBACK_RATE_LIMIT_VALUE,
    FALLBACK_RATE_LIMIT_WINDOW,
    JWT_ISSUER,
    JWT_REQUIRED_CLAIMS,
    SUBJECT_TOKEN_REVOKED,
    WORKSPACE_SYSTEM_SLUG,
)

logger = structlog.get_logger(__name__)

_ALGORITHM = "HS256"

_CACHE_PREFIX = "ragbot:token_ver:"
_CACHE_TTL = DEFAULT_SERVICE_CACHE_TTL_S


class JwtTokenService:
    """Generate and verify JWT tokens for service-to-service auth."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        jwt_secret: str,
        default_rate_limit_value: int = FALLBACK_RATE_LIMIT_VALUE,
        default_rate_limit_window: int = FALLBACK_RATE_LIMIT_WINDOW,
    ) -> None:
        """Khởi tạo JwtTokenService với DB session factory và JWT secret.
        @param session_factory: SQLAlchemy async session factory
        @param jwt_secret: Secret key from env (JWT_SECRET or TENANT_HMAC_SECRET)
        @param default_rate_limit_value: default from system_config (caller reads it)
        @param default_rate_limit_window: default from system_config (caller reads it)
        """
        self._sf = session_factory
        self._secret = jwt_secret
        self._default_rl_value = default_rate_limit_value
        self._default_rl_window = default_rate_limit_window

    def _sign(self, payload: dict[str, Any]) -> str:
        """Ký JWT token từ payload dict bằng HMAC-SHA256.
        @param payload: dict chứa claims (service_name, version, ...)
        @return: JWT token string
        """
        return pyjwt.encode(payload, self._secret, algorithm=_ALGORITHM)

    def _decode(self, token: str) -> dict[str, Any]:
        """Decode JWT token — verify signature, expiry AND issuer (P0 fix).
        @param token: JWT token string
        @return: decoded payload dict
        @raises: pyjwt.ExpiredSignatureError if exp claim is past
        @raises: pyjwt.InvalidIssuerError if iss != JWT_ISSUER
        @raises: pyjwt.MissingRequiredClaimError if exp or iss absent
        @raises: pyjwt.InvalidTokenError nếu signature sai
        """
        # require=JWT_REQUIRED_CLAIMS rejects legacy tokens minted without
        # exp or iss; pyjwt's default verify_exp=True treats missing exp
        # as valid (RFC 7519 §4.1.4), and without issuer= an attacker can
        # mint a token with any iss claim (or none). Passing both closes
        # master report Finding #5.
        return pyjwt.decode(
            token,
            self._secret,
            algorithms=[_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"verify_exp": True, "require": JWT_REQUIRED_CLAIMS},
        )

    async def create_token(
        self,
        service_name: str,
        description: str = "",
        redis_client: Any | None = None,
        role: str = "service",
        rate_limit_value: int | None = None,
        rate_limit_window: int | None = None,
        record_tenant_id: uuid.UUID | None = None,
        ttl_s: int | None = None,
    ) -> dict[str, Any]:
        """Tạo token mới cho 1 service, init version=1, invalidate Redis cache.
        @param service_name: Tên service (unique)
        @param description: Mô tả service
        @param redis_client: Redis client để invalidate cache (optional)
        @param role: 'owner' (BE chính, không giới hạn) hoặc 'service' (external)
        @param rate_limit_value: Số request cho phép (0 = không giới hạn)
        @param rate_limit_window: Khoảng thời gian tính bằng giây
        @param tenant_id: Int tenant id baked into the JWT — REQUIRED for
            cross-tenant guard (P0-C). ``None`` mints an unscoped token
            (only useful for the platform owner / system services).
        @param ttl_s: Token TTL in seconds (default ``DEFAULT_JWT_TTL_S``).
        @return: {id, service_name, token, version, role, rate_limit_value, rate_limit_window}
        """
        if rate_limit_value is None:
            rate_limit_value = self._default_rl_value
        if rate_limit_window is None:
            rate_limit_window = self._default_rl_window
        token_id = uuid.uuid4()
        version = 1
        now = int(time.time())
        ttl = int(ttl_s) if ttl_s is not None else DEFAULT_JWT_TTL_S

        payload = {
            "jti": str(token_id),
            "sub": service_name,
            "ver": version,
            "role": role,
            "rl_val": rate_limit_value,
            "rl_win": rate_limit_window,
            "iat": now,
            "exp": now + ttl,
            "iss": "ragbot",
        }
        if record_tenant_id is not None:
            payload["record_tenant_id"] = str(record_tenant_id)
        token = self._sign(payload)

        async with self._sf() as session:
            await session.execute(
                text("""
                    INSERT INTO api_tokens (id, service_name, description, token_hash, version, role, rate_limit_value, rate_limit_window, created_at)
                    VALUES (:id, :name, :desc, :hash, :ver, :role, :rl_val, :rl_win, now())
                    ON CONFLICT (service_name) DO UPDATE
                    SET token_hash = :hash, version = 1, description = :desc,
                        role = :role, rate_limit_value = :rl_val, rate_limit_window = :rl_win,
                        revoked_at = NULL, updated_at = now()
                """),
                {
                    "id": token_id,
                    "name": service_name,
                    "desc": description,
                    "hash": self._hash_token(token),
                    "ver": version,
                    "role": role,
                    "rl_val": rate_limit_value,
                    "rl_win": rate_limit_window,
                },
            )
            await session.commit()

        # Update Redis cache
        if redis_client is not None:
            try:
                await redis_client.set(
                    f"{_CACHE_PREFIX}{service_name}", str(version), ex=_CACHE_TTL,
                )
            except (RedisError, OSError, asyncio.TimeoutError):
                logger.debug("token_cache_set_failed", service=service_name)

        logger.info("api_token_created", service=service_name, version=version, role=role)
        return {
            "id": str(token_id),
            "service_name": service_name,
            "token": token,
            "version": version,
            "role": role,
            "rate_limit_value": rate_limit_value,
            "rate_limit_window": rate_limit_window,
        }

    async def regenerate_token(
        self,
        service_name: str,
        redis_client: Any | None = None,
        record_tenant_id: uuid.UUID | None = None,
        ttl_s: int | None = None,
    ) -> dict[str, Any]:
        """Regenerate token cho service — tăng version, token cũ bị reject, cập nhật Redis cache.
        @param service_name: Tên service
        @param redis_client: Redis client để invalidate cache (optional)
        @param tenant_id: Optional int tenant id to bake into the new JWT.
        @param ttl_s: Override TTL seconds (default ``DEFAULT_JWT_TTL_S``).
        @return: {service_name, token, old_version, new_version}
        @raises: ValueError nếu service không tồn tại
        """
        async with self._sf() as session:
            row = (await session.execute(
                text("SELECT id, version, role, rate_limit_value, rate_limit_window FROM api_tokens WHERE service_name = :name AND revoked_at IS NULL"),
                {"name": service_name},
            )).fetchone()

            if row is None:
                raise ValueError(f"Service '{service_name}' not found or revoked")

            token_id = row[0]
            old_version = row[1]
            role = row[2] or "service"
            rl_val = row[3] if row[3] is not None else self._default_rl_value
            rl_win = row[4] if row[4] is not None else self._default_rl_window
            new_version = old_version + 1
            now = int(time.time())
            ttl = int(ttl_s) if ttl_s is not None else DEFAULT_JWT_TTL_S

            payload = {
                "jti": str(token_id),
                "sub": service_name,
                "ver": new_version,
                "role": role,
                "rl_val": rl_val,
                "rl_win": rl_win,
                "iat": now,
                "exp": now + ttl,
                "iss": "ragbot",
            }
            if record_tenant_id is not None:
                payload["record_tenant_id"] = str(record_tenant_id)
            token = self._sign(payload)

            await session.execute(
                text("""
                    UPDATE api_tokens
                    SET version = :ver, token_hash = :hash, updated_at = now()
                    WHERE id = :id
                """),
                {"id": token_id, "ver": new_version, "hash": self._hash_token(token)},
            )
            await session.commit()

        # Update Redis cache with new version
        if redis_client is not None:
            try:
                await redis_client.set(
                    f"{_CACHE_PREFIX}{service_name}", str(new_version), ex=_CACHE_TTL,
                )
            except (RedisError, OSError, asyncio.TimeoutError):
                logger.debug("token_cache_set_failed", service=service_name)

        logger.info("api_token_regenerated", service=service_name, old_ver=old_version, new_ver=new_version)
        return {
            "service_name": service_name,
            "token": token,
            "old_version": old_version,
            "new_version": new_version,
            "role": role,
            "rate_limit_value": rl_val,
            "rate_limit_window": rl_win,
        }

    async def verify_token(
        self, token: str, redis_client: Any | None = None,
    ) -> dict[str, Any] | None:
        """Verify JWT token: decode → check version (Redis cache → DB fallback).
        @param token: JWT Bearer token
        @param redis_client: Redis client để check cached version (optional)
        @return: decoded payload nếu valid, None nếu invalid/version cũ
        """
        try:
            payload = self._decode(token)
        except (pyjwt.PyJWTError, ValueError, TypeError):
            return None

        service_name = payload.get("sub")
        token_version = payload.get("ver")
        if not service_name or token_version is None:
            return None

        # 1. Try Redis cache first
        db_version: int | None = None
        if redis_client is not None:
            try:
                cached = await redis_client.get(f"{_CACHE_PREFIX}{service_name}")
                if cached is not None:
                    db_version = int(cached)
            except (RedisError, OSError, asyncio.TimeoutError, ValueError, TypeError):
                pass  # fallback to DB

        # 2. Fallback to DB if cache miss
        if db_version is None:
            async with self._sf() as session:
                row = (await session.execute(
                    text("""
                        SELECT version FROM api_tokens
                        WHERE service_name = :name AND revoked_at IS NULL
                    """),
                    {"name": service_name},
                )).fetchone()

            if row is None:
                return None

            db_version = row[0]

            # Populate cache for next time
            if redis_client is not None:
                try:
                    await redis_client.set(
                        f"{_CACHE_PREFIX}{service_name}", str(db_version), ex=_CACHE_TTL,
                    )
                except (RedisError, OSError, asyncio.TimeoutError):
                    pass

        if token_version < db_version:
            logger.warning("api_token_version_mismatch",
                           service=service_name, token_ver=token_version, db_ver=db_version)
            return None

        return payload

    async def revoke_token(
        self, service_name: str, redis_client: Any | None = None,
    ) -> bool:
        """Revoke token cho service — xóa Redis cache, token không dùng được nữa.
        @param service_name: Tên service
        @param redis_client: Redis client để invalidate cache (optional)
        @return: True nếu revoke thành công

        Bug 5 (P1) — emit ``token.revoked.v1`` outbox row in the SAME
        transaction as the ``UPDATE api_tokens`` so peer replicas drop
        their stale ``ragbot:token_ver:{service}`` cache. Without this
        a leaked token validates on a peer for up to ``_CACHE_TTL``
        seconds after revocation.
        """
        async with self._sf() as session:
            result = await session.execute(
                text("UPDATE api_tokens SET revoked_at = now() WHERE service_name = :name AND revoked_at IS NULL"),
                {"name": service_name},
            )
            revoked = (result.rowcount or 0) > 0
            if revoked:
                # Insert outbox row in the same UoW — atomic with the
                # revocation so the publisher cannot dispatch a
                # phantom invalidation, nor can the revocation succeed
                # while the cross-replica notice is lost.
                await session.execute(
                    text(
                        """
                        INSERT INTO outbox (
                            id, subject, payload, headers, trace_id,
                            workspace_id, retry_count, status,
                            metadata_json, created_at
                        )
                        VALUES (
                            :id, :subject, :payload,
                            CAST(:headers AS jsonb), :trace_id,
                            :workspace_id, 0, 'pending',
                            CAST(:meta AS jsonb), now()
                        )
                        """,
                    ),
                    {
                        "id": uuid.uuid4(),
                        "subject": SUBJECT_TOKEN_REVOKED,
                        "payload": orjson.dumps({"service_name": service_name}),
                        "headers": json.dumps(
                            {"event-type": SUBJECT_TOKEN_REVOKED},
                        ),
                        "trace_id": "",
                        "workspace_id": WORKSPACE_SYSTEM_SLUG,
                        "meta": json.dumps(
                            {"event_type": SUBJECT_TOKEN_REVOKED},
                        ),
                    },
                )
            await session.commit()

        # Delete LOCAL Redis cache so verify_token on this replica
        # falls through to DB (→ None) immediately. Peer replicas
        # drop their cache via the outbox event handler.
        if revoked and redis_client is not None:
            try:
                await redis_client.delete(f"{_CACHE_PREFIX}{service_name}")
            except (RedisError, OSError, asyncio.TimeoutError):
                logger.debug("token_cache_delete_failed", service=service_name)

        return revoked

    async def list_tokens(self) -> list[dict]:
        """Liệt kê tất cả tokens — chỉ trả metadata, không trả token value.
        @return: list of {id, service_name, version, description, created_at, revoked_at}
        """
        async with self._sf() as session:
            rows = (await session.execute(
                text("""
                    SELECT id, service_name, version, description, created_at, updated_at,
                           revoked_at, role, rate_limit_value, rate_limit_window
                    FROM api_tokens ORDER BY created_at DESC
                """),
            )).fetchall()

        return [
            {
                "id": str(r[0]),
                "service_name": r[1],
                "version": r[2],
                "description": r[3],
                "created_at": r[4].isoformat() if r[4] else None,
                "updated_at": r[5].isoformat() if r[5] else None,
                "revoked_at": r[6].isoformat() if r[6] else None,
                "active": r[6] is None,
                "role": r[7] or "service",
                "rate_limit_value": r[8] if r[8] is not None else self._default_rl_value,
                "rate_limit_window": r[9] if r[9] is not None else self._default_rl_window,
            }
            for r in rows
        ]

    async def bootstrap_cache(self, redis_client: Any) -> int:
        """Load tất cả active token versions từ DB vào Redis cache khi startup.
        @param redis_client: Redis client instance
        @return: số lượng tokens đã cache
        """
        async with self._sf() as session:
            rows = (await session.execute(
                text("SELECT service_name, version FROM api_tokens WHERE revoked_at IS NULL"),
            )).fetchall()

        pipe = redis_client.pipeline()
        for service_name, version in rows:
            pipe.set(f"{_CACHE_PREFIX}{service_name}", str(version), ex=_CACHE_TTL)
        await pipe.execute()

        logger.info("api_token_cache_bootstrapped", count=len(rows))
        return len(rows)

    async def ensure_owner_token(
        self,
        service_name: str = "ragbot-owner",
        description: str = "Auto-init owner token (BE chính)",
        redis_client: Any | None = None,
    ) -> dict[str, Any] | None:
        """Đảm bảo tồn tại owner token cho BE — tạo nếu chưa có, skip nếu đã có.
        @param service_name: tên owner service
        @param description: mô tả
        @param redis_client: Redis client
        @return: token info nếu mới tạo, None nếu đã tồn tại
        """
        async with self._sf() as session:
            row = (await session.execute(
                text("SELECT id FROM api_tokens WHERE service_name = :name AND revoked_at IS NULL"),
                {"name": service_name},
            )).fetchone()

        if row is not None:
            logger.debug("owner_token_exists", service=service_name)
            return None

        result = await self.create_token(
            service_name, description, redis_client=redis_client,
            role="owner", rate_limit_value=0, rate_limit_window=self._default_rl_window,
        )
        logger.info("owner_token_auto_created", service=service_name)
        return result

    @staticmethod
    def _hash_token(token: str) -> str:
        """Hash token bằng SHA-256 để lưu DB — không lưu raw token.
        @param token: JWT token string
        @return: SHA-256 hex digest
        """
        return hashlib.sha256(token.encode()).hexdigest()


__all__ = ["JwtTokenService"]
