"""Per-call API key lookup for hot-swap (no restart).

Embedder / Reranker / LLM adapters call ``ProviderKeyResolver.get(code)``
on every request. Hits a 30-second Redis cache; on miss reads the
``api_keys`` table directly. Admin ``PUT /admin/api-keys/{code}`` writes
the new value + busts the cache → next request observes the new key.

Compared to the legacy env-var path:
- Operator can rotate keys in seconds via API, no systemctl restart.
- Soft cooldown via ``rotation_state='cooldown'`` lets in-flight calls
  drain on the old key before it disappears (caller decides duration).
- Env-var fallback preserved so a fresh dev DB or a worker that boots
  before alembic ran still functions.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

import structlog
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.ports.secrets_port import SecretsPort
from ragbot.shared.constants import API_KEY_FINGERPRINT_HEX_LEN

logger = logging.getLogger(__name__)
# structlog for the plaintext-read audit event — kwargs render reliably
# under the ProcessorFormatter (stdlib ``extra={}`` gets swallowed).
_slog = structlog.get_logger(__name__)

_REDIS_KEY_TEMPLATE = "ragbot:apikey:{provider_code}:{label}"
_CACHE_TTL_S = 30  # short — admin flip lands within 30s without invalidate

# Conservative fallback env-var map (one per provider). Adapter modules
# also accept their own per-purpose env vars (e.g. ``ZEROENTROPY_EMBEDDING_API_KEY``)
# — those wins over this generic map in the resolver chain.
_ENV_FALLBACK: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "zeroentropy": "ZEROENTROPY_API_KEY",
    "jina_ai": "JINA_API_KEY",
    "jina": "JINA_API_KEY",
}


class ProviderKeyResolver:
    """Hot-swappable API key resolver.

    Reads from Redis cache (30s TTL) → ``api_keys`` table → env fallback.
    Admin mutation invalidates Redis so the next request reads fresh DB.
    """

    def __init__(
        self, session_factory: Any, redis_client: Any, secrets: SecretsPort,
    ) -> None:
        self._sf = session_factory
        self._redis = redis_client
        self._secrets = secrets

    async def get(self, provider_code: str, label: str = "primary") -> str | None:
        """Return the active key for ``provider_code`` + ``label``.

        Resolution chain (first non-empty wins):
        1. Redis cache (30s TTL) — stores CIPHERTEXT, decrypted after hit.
        2. ``api_keys`` row (active=true, rotation_state='live', not deleted) —
           ``value_encrypted`` preferred, ``value_plain`` fallback (dual-read).
        3. Process env var via ``_ENV_FALLBACK`` map.

        Returns ``None`` if all three sources are empty.
        """
        cache_key = _REDIS_KEY_TEMPLATE.format(
            provider_code=provider_code, label=label,
        )
        try:
            cached = await self._redis.get(cache_key)
            if cached is not None:
                value = cached.decode() if isinstance(cached, bytes) else cached
                # Negative cache marker — sentinel string for "we already
                # checked and DB is empty"; lets us skip the SQL roundtrip.
                if value == "":
                    return self._env_fallback(provider_code)
                try:
                    return await self._secrets.resolve(None, value)
                except Exception:  # noqa: BLE001 — stale entry (pre-encryption plaintext within TTL, or KEK rotated) is undecryptable; treat as cache miss and fall through to DB
                    logger.debug(
                        "provider_key_resolver_cache_undecryptable",
                        extra={"provider_code": provider_code},
                    )
        except (RedisError, OSError, AttributeError) as exc:
            logger.debug(
                "provider_key_resolver_cache_read_failed",
                extra={"err": str(exc)[:100], "provider_code": provider_code},
            )

        # DB lookup — dual-read: value_encrypted preferred, value_plain
        # fallback. Remove value_plain from this SELECT after Migration B
        # (null_out_api_keys_value_plain) is verified + 48h soak with zero
        # api_key_plaintext_read events (ADR-W1-KEY kill-date).
        db_plain: str | None = None
        db_cipher: str | None = None
        row = None
        try:
            async with self._sf() as session:
                result = await session.execute(
                    text(
                        """
                        SELECT value_encrypted, value_plain
                        FROM api_keys
                        WHERE provider_code = :p
                          AND label = :l
                          AND active = true
                          AND rotation_state = 'live'
                          AND deleted_at IS NULL
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                    ),
                    {"p": provider_code, "l": label},
                )
                row = result.first()
        except (SQLAlchemyError, OSError) as exc:
            logger.warning(
                "provider_key_resolver_db_failed",
                extra={
                    "err": str(exc)[:200],
                    "provider_code": provider_code,
                    "label": label,
                },
            )
        if row is not None and row[0]:
            db_cipher = row[0]
            # Misconfigured/missing KEK raises here — fail loud: a key row
            # exists but cannot be used; degrading silently to env fallback
            # would mask the misconfiguration.
            db_plain = await self._secrets.resolve(None, db_cipher)
        elif row is not None and row[1]:
            db_plain = row[1]
            _slog.warning(
                "api_key_plaintext_read",
                provider_code=provider_code,
                label=label,
            )
            # Re-encrypt for the cache so Redis never holds plaintext; a
            # missing KEK skips the cache write (key stays usable).
            try:
                db_cipher = self._secrets.encrypt(db_plain)
            except RuntimeError:
                db_cipher = None

        # Cache write — ciphertext, or the negative sentinel (empty string)
        # so a missing row doesn't hit DB on every request. Plain rows that
        # could not be re-encrypted are not cached (no plaintext in Redis).
        if db_cipher is not None or db_plain is None:
            try:
                await self._redis.setex(cache_key, _CACHE_TTL_S, db_cipher or "")
            except (RedisError, OSError, AttributeError) as exc:
                logger.debug(
                    "provider_key_resolver_cache_write_failed",
                    extra={"err": str(exc)[:100], "provider_code": provider_code},
                )

        if db_plain:
            return db_plain
        return self._env_fallback(provider_code)

    async def invalidate(self, provider_code: str, label: str = "primary") -> None:
        """Drop the Redis cache entry for ``(provider_code, label)``.

        Call from the admin endpoint after writing ``api_keys`` so the
        next request reads fresh DB state.
        """
        cache_key = _REDIS_KEY_TEMPLATE.format(
            provider_code=provider_code, label=label,
        )
        try:
            await self._redis.delete(cache_key)
        except (RedisError, OSError, AttributeError) as exc:
            logger.warning(
                "provider_key_resolver_invalidate_failed",
                extra={"err": str(exc)[:100], "provider_code": provider_code},
            )

    @staticmethod
    def _env_fallback(provider_code: str) -> str | None:
        env_var = _ENV_FALLBACK.get(provider_code)
        if not env_var:
            return None
        return os.environ.get(env_var) or None


async def upsert_api_key(
    session: Any,
    secrets: SecretsPort,
    provider_code: str,
    label: str,
    value: str,
) -> str:
    """Encrypt-and-upsert one ``api_keys`` row; returns the key fingerprint.

    Write-path companion of the resolver (ADR-W1-KEY): the plaintext key is
    encrypted BEFORE any SQL touches the session, so a missing KEK raises
    ``RuntimeError`` and no plaintext row is ever written (secret-write
    misconfiguration = client-bug class → fail loud, never degrade).
    ``value_plain`` is NULLed on update; the fingerprint (sha256 hex prefix)
    is persisted in ``metadata_json`` so the admin list endpoint never needs
    the plaintext again. Caller owns the transaction (``session.commit()``).
    """
    encrypted = secrets.encrypt(value)
    fingerprint = hashlib.sha256(value.encode()).hexdigest()[
        :API_KEY_FINGERPRINT_HEX_LEN
    ]
    params = {"p": provider_code, "l": label, "v": encrypted, "fp": fingerprint}
    # Upsert via two-step (DB has unique partial index on (provider_code,
    # label) WHERE deleted_at IS NULL — a plain ON CONFLICT needs the same
    # predicate; explicit UPDATE then INSERT fall-through keeps it simple).
    upd = await session.execute(
        text(
            """
            UPDATE api_keys
            SET value_encrypted = :v,
                value_plain = NULL,
                metadata_json = COALESCE(metadata_json, '{}'::jsonb)
                    || jsonb_build_object('fingerprint', :fp),
                active = true,
                rotation_state = 'live',
                updated_at = now()
            WHERE provider_code = :p
              AND label = :l
              AND deleted_at IS NULL
            RETURNING id
            """,
        ),
        params,
    )
    if upd.rowcount == 0:
        await session.execute(
            text(
                """
                INSERT INTO api_keys
                    (provider_code, label, value_encrypted, metadata_json,
                     active, rotation_state)
                VALUES (:p, :l, :v,
                        jsonb_build_object('fingerprint', :fp), true, 'live')
                """,
            ),
            params,
        )
    return fingerprint


__all__ = ["ProviderKeyResolver", "upsert_api_key"]
