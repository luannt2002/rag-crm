"""Active-passive API key pool with Redis cooldown — provider-agnostic.

Two keys per pool (primary + secondary). On HTTP 403 / 429 from upstream the
active key is marked cooldown (Redis TTL) and the pool switches to the
standby. When the cooldown expires the pool naturally retries primary on
the next ``get_active`` call.

Pools are tagged with a ``provider_code`` (e.g. ``"jina"``, ``"openai"``,
``"cohere"``) and a ``purpose`` (e.g. ``"embed"``, ``"rerank"``) so different
upstream surfaces fail independently and the Prometheus / Redis label
cardinality stays bounded.

The Redis ledger stores a sha256 prefix of the key, never the credential
itself, so the cooldown record is not a leak vector.

The ``ApiKeyPoolFactory`` reads a ``provider_keys`` dict (see
``settings.provider_api_keys``) and lazily constructs one pool per
``(provider_code, purpose)`` request — adapters resolve their pool by
their own internal ``_PROVIDER_CODE`` so business logic never branches on
brand strings.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass

import structlog

from ragbot.shared.constants import (
    API_KEY_COOLDOWN_REDIS_PREFIX,
    DEFAULT_API_KEY_COOLDOWN_S,
)

logger = structlog.get_logger(__name__)


# Operator-friendly labels emitted in metrics + logs. Keep narrow so the
# Prometheus label cardinality stays bounded (always at most two values).
_LABEL_PRIMARY: str = "primary"
_LABEL_SECONDARY: str = "secondary"
# Length of the sha256 hex prefix used as the cooldown-ledger identifier.
# Sixteen hex chars = 64 random bits — collision-safe for 2 keys per pool
# while keeping Redis keys short.
_KEY_HASH_PREFIX_LEN: int = 16


@dataclass(frozen=True, slots=True)
class ApiKeyEntry:
    """A single key plus operator-friendly label for metrics and logs."""

    key: str
    label: str


class ApiKeyPool:
    """Active-passive key pool — Redis-backed cooldown ledger.

    Provider-agnostic: caller passes ``provider_code`` (e.g. ``"jina"``,
    ``"openai"``) so the same code path serves any upstream that enforces
    HTTP 403/429 quota signalling. Concrete adapters know their own code
    via an internal ``_PROVIDER_CODE`` constant.
    """

    def __init__(
        self,
        primary: str,
        secondary: str | None,
        redis_client,
        *,
        provider_code: str,
        purpose: str,
        cooldown_s: int = DEFAULT_API_KEY_COOLDOWN_S,
        extras: list[str] | None = None,
    ) -> None:
        if not primary:
            raise ValueError("ApiKeyPool: primary key required")
        if not provider_code:
            raise ValueError("ApiKeyPool: provider_code required")
        if not purpose:
            raise ValueError("ApiKeyPool: purpose required")
        self._primary = ApiKeyEntry(key=primary, label=_LABEL_PRIMARY)
        self._secondary = (
            ApiKeyEntry(key=secondary, label=_LABEL_SECONDARY)
            if secondary
            else None
        )
        # All keys as an ordered round-robin ring (primary, secondary, extras).
        # ``get_active`` rotates through them per call so sustained load is
        # SPREAD evenly across keys — each upstream key sees ~1/N the rate,
        # which is what keeps a per-minute (BPM) quota from tripping under a
        # parallel load test. Distinct upstream accounts ⇒ N× headroom.
        # Cooled keys are skipped; failover is the degenerate N=2 case.
        self._entries: list[ApiKeyEntry] = [self._primary]
        if self._secondary is not None:
            self._entries.append(self._secondary)
        for i, k in enumerate(extras or []):
            if k:
                self._entries.append(ApiKeyEntry(key=k, label=f"extra{i+1}"))
        self._rr_index = 0
        self._redis = redis_client
        self._provider_code = provider_code
        self._purpose = purpose
        self._cooldown_s = int(cooldown_s)
        # Serialize cooldown writes per process so a burst of 403 retries
        # can't double-write the TTL and reset the cooldown clock.
        self._lock = asyncio.Lock()

    @property
    def provider_code(self) -> str:
        return self._provider_code

    @property
    def purpose(self) -> str:
        return self._purpose

    @property
    def has_secondary(self) -> bool:
        return self._secondary is not None

    @property
    def key_count(self) -> int:
        return len(self._entries)

    async def get_active(self) -> ApiKeyEntry:
        """Return the next usable key, ROUND-ROBIN across all entries.

        Each call advances the rotation so load is spread evenly — key N
        sees ~1/len(entries) of the requests, dividing the per-minute (BPM)
        rate per key. Cooled keys are skipped (failover). When every key is
        cooled the rotation pick is returned anyway — the caller hits the
        API and learns the truth (Redis may hold a stale cooldown after an
        upstream refill). With a single key this degenerates to "always
        return it"; with two it matches the previous primary→secondary
        behaviour minus the strict primary-preference.
        """
        n = len(self._entries)
        start = self._rr_index % n
        self._rr_index = (self._rr_index + 1) % n
        for offset in range(n):
            entry = self._entries[(start + offset) % n]
            if not await self._is_cooled(entry):
                return entry
        return self._entries[start]

    async def mark_cooldown(
        self, entry: ApiKeyEntry, *, reason: str, cooldown_s: int | None = None
    ) -> None:
        """Mark ``entry`` in cooldown.

        ``cooldown_s`` overrides the pool default for this single mark — a
        transient per-minute (BPM) ``429`` refills in ~60s, so cooling the
        key for the full ``DEFAULT_API_KEY_COOLDOWN_S`` (5 min) would drop
        it out of the round-robin far longer than the quota actually needs,
        cascading the remaining keys into the same wall. A hard ``403``
        (revoked / unauthorised) keeps the long default. ``None`` → pool
        default.

        Idempotent within the cooldown window; Redis ``SET .. EX`` simply
        refreshes the TTL on a re-mark. Failures to reach Redis are
        absorbed (logged) so a degraded ledger never blocks the request.
        """
        ttl = int(cooldown_s) if cooldown_s else self._cooldown_s
        async with self._lock:
            redis_key = self._key_redis_id(entry)
            try:
                await self._redis.set(redis_key, reason, ex=ttl)
            except Exception:  # noqa: BLE001 — Redis down → degrade open, request proceeds
                logger.warning(
                    "api_key_cooldown_set_failed",
                    provider_code=self._provider_code,
                    purpose=self._purpose,
                    label=entry.label,
                )
                return
            logger.warning(
                "api_key_cooldown_set",
                provider_code=self._provider_code,
                purpose=self._purpose,
                label=entry.label,
                reason=reason,
                cooldown_s=ttl,
            )

    async def _is_cooled(self, entry: ApiKeyEntry) -> bool:
        try:
            return bool(await self._redis.get(self._key_redis_id(entry)))
        except Exception:  # noqa: BLE001 — Redis down → assume not cooled, fail-soft
            return False

    def _key_redis_id(self, entry: ApiKeyEntry) -> str:
        digest = hashlib.sha256(entry.key.encode()).hexdigest()[:_KEY_HASH_PREFIX_LEN]
        # ``ragbot:api_key_cooldown:<provider_code>:<purpose>:<digest>``
        # — provider_code makes pools across providers fail independently
        # and the digest prevents the plaintext key landing in Redis.
        return (
            f"{API_KEY_COOLDOWN_REDIS_PREFIX}"
            f"{self._provider_code}:{self._purpose}:{digest}"
        )


class ApiKeyPoolFactory:
    """Lazy per-(provider_code, purpose) pool builder.

    ``provider_keys`` is a dict like ``{"jina": ["k1", "k2"], "openai":
    ["k3"]}``; the first list element is treated as primary, the second
    (if any) as secondary. Calling ``get(code, purpose)`` returns ``None``
    when no keys are configured for the requested provider so adapters
    can fall back to their legacy env-var path without a special case.
    """

    def __init__(
        self,
        provider_keys: dict[str, list[str]],
        redis_client,
    ) -> None:
        # Keep a dict copy so caller mutations after construct don't change
        # pool keys mid-flight; the factory is meant to be a Singleton.
        self._keys: dict[str, list[str]] = {
            code: list(keys) for code, keys in (provider_keys or {}).items()
        }
        self._redis = redis_client
        self._pools: dict[tuple[str, str], ApiKeyPool] = {}

    def get(self, provider_code: str, purpose: str) -> ApiKeyPool | None:
        """Return cached pool for ``(provider_code, purpose)`` or ``None``.

        ``None`` means no keys are configured for ``provider_code`` —
        adapters interpret it as "use legacy env var path" so deployments
        that have not opted into multi-key failover keep booting.
        """
        cache_key = (provider_code, purpose)
        cached = self._pools.get(cache_key)
        if cached is not None:
            return cached
        keys = self._keys.get(provider_code, [])
        if not keys:
            return None
        pool = ApiKeyPool(
            primary=keys[0],
            secondary=keys[1] if len(keys) > 1 else None,
            extras=keys[2:] if len(keys) > 2 else None,
            redis_client=self._redis,
            provider_code=provider_code,
            purpose=purpose,
        )
        self._pools[cache_key] = pool
        return pool


_DB_POOL_CACHE_TTL_S: int = 30
_DB_POOL_LOCK_TIMEOUT_S: int = 5


class DBBackedApiKeyPoolFactory(ApiKeyPoolFactory):
    """Pool factory reading active keys from ``ai_keys`` DB table.

    Refreshes per-(provider_code, purpose) pool every TTL seconds so a
    rotation via admin endpoint is picked up without server restart.
    Falls back to env-based ``provider_keys`` dict when DB has no row
    (back-compat with deployments that have not migrated to ai_keys).

    AES-decrypted plaintext keys are kept in-process only; they are
    never logged or written back to disk.
    """

    def __init__(
        self,
        provider_keys: dict[str, list[str]],
        redis_client,
        session_factory,
    ) -> None:
        super().__init__(provider_keys=provider_keys, redis_client=redis_client)
        self._sf = session_factory
        self._refresh_at: dict[tuple[str, str], float] = {}
        self._lock = asyncio.Lock()

    async def get_with_refresh(
        self, provider_code: str, purpose: str
    ) -> ApiKeyPool | None:
        """Return pool, refreshing from DB when TTL expired.

        Sync ``get()`` (inherited) keeps working for callers not on the
        async path; this method is the async opt-in for hot reload.
        """
        import time
        cache_key = (provider_code, purpose)
        now = time.monotonic()
        if self._refresh_at.get(cache_key, 0) > now:
            cached = self._pools.get(cache_key)
            if cached is not None:
                return cached
        async with asyncio.timeout(_DB_POOL_LOCK_TIMEOUT_S):
            async with self._lock:
                # Re-check inside lock to avoid duplicate DB reads when
                # multiple workers race on the same expired entry.
                if self._refresh_at.get(cache_key, 0) > time.monotonic():
                    return self._pools.get(cache_key)
                keys = await self._load_db_keys(provider_code)
                if not keys:
                    keys = self._keys.get(provider_code, [])
                if not keys:
                    self._refresh_at[cache_key] = now + _DB_POOL_CACHE_TTL_S
                    return None
                pool = ApiKeyPool(
                    primary=keys[0],
                    secondary=keys[1] if len(keys) > 1 else None,
                    extras=keys[2:] if len(keys) > 2 else None,
                    redis_client=self._redis,
                    provider_code=provider_code,
                    purpose=purpose,
                )
                self._pools[cache_key] = pool
                self._refresh_at[cache_key] = now + _DB_POOL_CACHE_TTL_S
                return pool

    async def _load_db_keys(self, provider_code: str) -> list[str]:
        """Read active keys from ai_keys table, decrypted in-memory.

        Returns empty list when DB query fails or no rows match — caller
        falls back to env-based dict.
        """
        from sqlalchemy import text

        try:
            from ragbot.infrastructure.security.env_secrets import EnvSecretsAdapter
        except ImportError:
            logger.warning("db_pool_secrets_adapter_unavailable", provider=provider_code)
            return []
        try:
            async with self._sf() as session:
                result = await session.execute(
                    text("""
                        SELECT k.api_key_encrypted, k.is_default
                        FROM ai_keys k
                        JOIN ai_providers p ON p.id = k.record_provider_id
                        WHERE p.code = :code
                          AND p.deleted_at IS NULL
                          AND p.enabled = true
                          AND k.status = 'active'
                        ORDER BY k.is_default DESC, k.created_at DESC
                        LIMIT 5
                    """),
                    {"code": provider_code},
                )
                rows = result.fetchall()
        except Exception as exc:  # noqa: BLE001 — DB outage must not crash request path; fall back to env keys
            logger.warning(
                "db_pool_load_failed", provider=provider_code, error=str(exc)[:200]
            )
            return []
        secrets = EnvSecretsAdapter()
        out: list[str] = []
        for row in rows:
            try:
                # ``resolve`` is an async instance method — the previous
                # unbound, un-awaited call raised TypeError on every row
                # (swallowed below), silently killing the DB-key path.
                plaintext = await secrets.resolve(None, row[0])
            except Exception as exc:  # noqa: BLE001 — decrypt fail per-row, log and skip rather than crash whole pool refresh
                logger.warning(
                    "db_pool_decrypt_failed",
                    provider=provider_code,
                    error=str(exc)[:120],
                )
                continue
            if plaintext:
                out.append(plaintext)
        return out


__all__ = [
    "ApiKeyEntry",
    "ApiKeyPool",
    "ApiKeyPoolFactory",
    "DBBackedApiKeyPoolFactory",
]
