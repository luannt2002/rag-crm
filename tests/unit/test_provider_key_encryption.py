"""ADR-W1-KEY — provider API key encryption at-rest.

Test matrix (ADR §6 bước 1, 8 tests) + adjacent api_key_pool fix (§6 bước 3):
1. EnvSecretsAdapter encrypt/decrypt roundtrip + envelope shape.
2. Resolver prefers ``value_encrypted`` over ``value_plain`` (dual-read).
3. Resolver plain-fallback emits ``api_key_plaintext_read`` (no value leaked).
4. Redis cache stores CIPHERTEXT, never plaintext; cache-hit decrypts.
5. Stale/undecryptable cache entry treated as miss (falls to DB).
6. Write-path upsert writes ``value_encrypted`` only, NULLs ``value_plain``,
   stores fingerprint in ``metadata_json``.
7. Write-path fail-loud without KEK — no SQL executed, no plaintext row.
8. Migration A inline AESGCM envelope is compatible with EnvSecretsAdapter.
9. api_key_pool ``_load_db_keys`` decrypts rows (was dead: unbound resolve).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib.util
import os
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

from ragbot.application.services.provider_key_resolver import (
    ProviderKeyResolver,
    upsert_api_key,
)
from ragbot.infrastructure.security.env_secrets import EnvSecretsAdapter
from ragbot.shared.api_key_pool import DBBackedApiKeyPoolFactory
from ragbot.shared.constants import API_KEY_FINGERPRINT_HEX_LEN

_KEK_ENV = "RAGBOT_CONFIG_KEK"
_AESGCM_NONCE_LEN = 12
_AESGCM_TAG_LEN = 16


@pytest.fixture()
def kek(monkeypatch: pytest.MonkeyPatch) -> bytes:
    """Random 32-byte KEK exported to env for the duration of one test."""
    raw = os.urandom(32)
    monkeypatch.setenv(_KEK_ENV, base64.b64encode(raw).decode())
    return raw


def _make_session_factory(row: tuple | None):
    """Session factory whose SELECT returns a single (value_encrypted, value_plain) row."""
    result = MagicMock()
    result.first.return_value = row
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _sf():
        yield session

    return _sf


class _FakeRedis:
    """Async Redis stub recording setex args; get() returns a preset value."""

    def __init__(self, get_value: str | None = None) -> None:
        self.get_value = get_value
        self.setex_calls: list[tuple[str, int, str]] = []

    async def get(self, key: str):
        return self.get_value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.setex_calls.append((key, ttl, value))

    async def delete(self, key: str) -> None:
        pass


class _CaptureSession:
    """Capture (sql, params) pairs; first execute returns given rowcount."""

    def __init__(self, update_rowcount: int) -> None:
        self.calls: list[tuple[str, dict]] = []
        self._update_rowcount = update_rowcount

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), dict(params or {})))
        result = MagicMock()
        result.rowcount = self._update_rowcount
        return result


# --- 1. roundtrip ------------------------------------------------------------

def test_env_secrets_roundtrip(kek: bytes) -> None:
    plain = "sk-test-roundtrip-0123456789"
    enc = EnvSecretsAdapter.encrypt(plain)
    assert enc != plain
    raw = base64.b64decode(enc)
    assert len(raw) == _AESGCM_NONCE_LEN + len(plain.encode()) + _AESGCM_TAG_LEN
    out = asyncio.run(EnvSecretsAdapter().resolve(None, enc))
    assert out == plain


# --- 2. resolver prefers encrypted -------------------------------------------

def test_resolver_prefers_encrypted(kek: bytes) -> None:
    plain = "sk-real-key-encrypted-path"
    enc = EnvSecretsAdapter.encrypt(plain)
    resolver = ProviderKeyResolver(
        session_factory=_make_session_factory((enc, "SHOULD-NOT-BE-USED")),
        redis_client=_FakeRedis(),
        secrets=EnvSecretsAdapter(),
    )
    out = asyncio.run(resolver.get("provider-a"))
    assert out == plain
    assert out != "SHOULD-NOT-BE-USED"


# --- 3. plain fallback warns -------------------------------------------------

def test_resolver_dual_read_plain_fallback_warns(kek: bytes) -> None:
    plain = "sk-legacy-plaintext-row-value"
    resolver = ProviderKeyResolver(
        session_factory=_make_session_factory((None, plain)),
        redis_client=_FakeRedis(),
        secrets=EnvSecretsAdapter(),
    )
    with structlog.testing.capture_logs() as logs:
        out = asyncio.run(resolver.get("provider-a"))
    assert out == plain
    events = [entry for entry in logs if entry["event"] == "api_key_plaintext_read"]
    assert len(events) == 1
    assert events[0]["provider_code"] == "provider-a"
    # The key value must NEVER appear in the log record.
    assert plain not in repr(logs)


# --- 4. cache stores ciphertext ----------------------------------------------

def test_resolver_caches_ciphertext_not_plaintext(kek: bytes) -> None:
    plain = "sk-cache-me-but-encrypted"
    enc = EnvSecretsAdapter.encrypt(plain)
    redis = _FakeRedis()
    resolver = ProviderKeyResolver(
        session_factory=_make_session_factory((enc, None)),
        redis_client=redis,
        secrets=EnvSecretsAdapter(),
    )
    out = asyncio.run(resolver.get("provider-a"))
    assert out == plain
    assert len(redis.setex_calls) == 1
    cached_value = redis.setex_calls[0][2]
    assert cached_value != plain
    assert plain not in cached_value
    # Cached value is decryptable ciphertext.
    assert asyncio.run(EnvSecretsAdapter().resolve(None, cached_value)) == plain

    # Cache-hit path: ciphertext in Redis still yields plaintext to caller.
    resolver_hit = ProviderKeyResolver(
        session_factory=_make_session_factory(None),
        redis_client=_FakeRedis(get_value=cached_value),
        secrets=EnvSecretsAdapter(),
    )
    assert asyncio.run(resolver_hit.get("provider-a")) == plain


# --- 5. stale cache entry = miss ----------------------------------------------

def test_resolver_cache_stale_plaintext_treated_as_miss(kek: bytes) -> None:
    plain = "sk-db-is-source-of-truth"
    enc = EnvSecretsAdapter.encrypt(plain)
    resolver = ProviderKeyResolver(
        session_factory=_make_session_factory((enc, None)),
        redis_client=_FakeRedis(get_value="legacy-plaintext-cache-entry"),
        secrets=EnvSecretsAdapter(),
    )
    out = asyncio.run(resolver.get("provider-a"))
    assert out == plain  # fell through to DB, did not raise


# --- 6. write path encrypted only ----------------------------------------------

def test_write_path_writes_encrypted_only(kek: bytes) -> None:
    plain = "sk-brand-new-admin-key"
    session = _CaptureSession(update_rowcount=0)  # force INSERT fall-through
    fingerprint = asyncio.run(
        upsert_api_key(session, EnvSecretsAdapter(), "provider-a", "primary", plain),
    )
    expected_fp = hashlib.sha256(plain.encode()).hexdigest()[:API_KEY_FINGERPRINT_HEX_LEN]
    assert fingerprint == expected_fp
    assert len(session.calls) == 2  # UPDATE miss → INSERT
    for sql, params in session.calls:
        # Plaintext key never appears as a bound parameter or inline SQL.
        assert plain not in sql
        assert all(value != plain for value in params.values())
        assert "value_encrypted" in sql
        # Bound ciphertext decrypts back to the original key.
        assert asyncio.run(EnvSecretsAdapter().resolve(None, params["v"])) == plain
        assert params["fp"] == expected_fp
    update_sql = session.calls[0][0]
    assert "value_plain = NULL" in update_sql
    insert_sql = session.calls[1][0]
    assert "value_plain" not in insert_sql
    assert "metadata_json" in insert_sql


# --- 7. write path fail-loud without KEK ---------------------------------------

def test_write_path_fail_loud_without_kek(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_KEK_ENV, raising=False)
    session = _CaptureSession(update_rowcount=1)
    with pytest.raises(RuntimeError):
        asyncio.run(
            upsert_api_key(
                session, EnvSecretsAdapter(), "provider-a", "primary", "sk-x",
            ),
        )
    assert session.calls == []  # no SQL ran — no plaintext row written


# --- 8. migration A envelope compatible ----------------------------------------

def _load_migration_module():
    repo_root = Path(__file__).resolve().parents[2]
    matches = sorted(repo_root.glob("alembic/versions/*encrypt_api_keys_backfill.py"))
    assert matches, "Migration A (encrypt_api_keys_backfill) not found"
    spec = importlib.util.spec_from_file_location("encrypt_api_keys_backfill", matches[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backfill_encrypt_function(kek: bytes) -> None:
    migration = _load_migration_module()
    plain = "sk-backfill-row-value"
    enc = migration._encrypt_value(plain, kek)
    # Envelope must match env_secrets.py: base64(nonce[12] || ct+tag).
    out = asyncio.run(EnvSecretsAdapter().resolve(None, enc))
    assert out == plain


# --- 9. api_key_pool decrypt fix -----------------------------------------------

def _make_pool_session_factory(rows: list[tuple[str, bool]]):
    result = MagicMock()
    result.fetchall.return_value = rows
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _sf():
        yield session

    return _sf


def test_api_key_pool_decrypts_db_rows(kek: bytes) -> None:
    plain = "sk-pool-db-key"
    enc = EnvSecretsAdapter.encrypt(plain)
    factory = DBBackedApiKeyPoolFactory(
        provider_keys={},
        redis_client=MagicMock(),
        session_factory=_make_pool_session_factory([(enc, True)]),
    )
    keys = asyncio.run(factory._load_db_keys("provider-a"))
    assert keys == [plain]
