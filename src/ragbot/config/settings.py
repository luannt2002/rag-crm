"""Application settings (pydantic-settings).

Ref: docs/application/PLAN_01_WORKSPACE_BOOTSTRAP.md §settings.py
     RAGBOT_MASTER §24 Tech Stack / §12.6 Secrets / §19 Cache.

Secrets (JWT keys, provider API keys) are loaded from env vars.
Local development uses `.env`.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PostgresDsn, RedisDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ragbot.shared.constants import (
    DEFAULT_APP_PORT,
    DEFAULT_CHUNK_OVERLAP,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNKING_AVG_LEN_LONG,
    DEFAULT_CHUNKING_AVG_LEN_SHORT,
    DEFAULT_CHUNKING_HEADING_MAX_FOR_SEMANTIC,
    DEFAULT_CHUNKING_HEADING_THRESHOLD,
    DEFAULT_CHUNKING_MIXED_CONTENT_THRESHOLD,
    DEFAULT_CHUNKING_TABLE_THRESHOLD,
    DEFAULT_CIRCUIT_BREAKER_FAIL_MAX,
    DEFAULT_CIRCUIT_BREAKER_RESET_TIMEOUT_S,
    DEFAULT_DB_MAX_OVERFLOW,
    DEFAULT_DB_POOL_RECYCLE_S,
    DEFAULT_DB_POOL_SIZE,
    DEFAULT_DB_POOL_TIMEOUT_S,
    DEFAULT_DEBOUNCE_WINDOW_MS,
    DEFAULT_EMBEDDING_FALLBACK_DIMENSION,
    DEFAULT_EMBEDDING_FALLBACK_MODEL,
    DEFAULT_EMBEDDING_FALLBACK_VERSION,
    DEFAULT_ENRICHMENT_MAX_TOKENS,
    DEFAULT_ENRICHMENT_MODEL,
    DEFAULT_ENRICHMENT_TEMPERATURE,
    DEFAULT_ENRICHMENT_TIMEOUT_S,
    DEFAULT_HISTORY_LIMIT,
    DEFAULT_JWT_ALGORITHM,
    DEFAULT_JWT_AUDIENCE,
    DEFAULT_JWT_ISSUER,
    DEFAULT_MAX_ITERATION_CAP,
    DEFAULT_RAG_RERANK_TOP_N,
    DEFAULT_RAG_TOP_K,
    DEFAULT_REDIS_POOL_SIZE,
    DEFAULT_RRF_K,
    DEFAULT_SEMANTIC_CACHE_THRESHOLD,
    RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV,
    RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE,
)
from ragbot.shared.constants import (
    DEFAULT_ENRICHMENT_CHUNK_PREVIEW_CHARS as _DEFAULT_ENRICHMENT_CHUNK_PREVIEW_CHARS,
)
from ragbot.shared.constants import (
    DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS as _DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS,
)
from ragbot.shared.constants import (
    DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS as _DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS,
)

# Stdlib logger — settings load runs at import time, before structlog is
# configured. Captures CORS wildcard warning emitted from the validator.
_settings_logger = logging.getLogger(__name__)

# Strict envs (CORS wildcard reject, weak JWT secret reject, etc.).
# Lifted into shared/constants.py SSoT per CLAUDE.md §3 zero-hardcode —
# env names live in exactly one place (the SSoT) and the
# ``AppSettings.env: Literal[...]`` typed-validate guards parse time.
from ragbot.shared.constants import APP_ENVS_STRICT  # noqa: E402 — settings ordering

_CORS_STRICT_ENVS: frozenset[str] = APP_ENVS_STRICT


class AppSettings(BaseSettings):
    """Core application settings."""

    name: str = "ragbot"
    env: Literal["development", "uat", "staging", "production"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"  # noqa: S104 - bind all interfaces inside container
    port: int = DEFAULT_APP_PORT
    version: str = "0.1.0"
    api_token: str = ""  # long-lived API token for service-to-service auth
    api_base_path: str = "/api/ragbot"
    # CORS: JSON array string of allowed origins. Empty "[]" = disabled (no
    # Access-Control-Allow-Origin headers emitted). Overridden at runtime by
    # system_config key `cors_allowed_origins` (same JSON-array string form)
    # when loaded during lifespan; this env var is the boot-time fallback.
    cors_allowed_origins: str = "[]"
    # IP-based pre-auth rate limit + anti-abuse middleware config.
    # Trusted reverse-proxy IPs whose X-Forwarded-For we honour (comma-
    # separated). Empty = use request.client.host directly (no XFF trust).
    trusted_proxies: str = ""
    # IP allowlist — comma-separated list of IPs exempt from IP rate limit
    # + anti-abuse checks (e.g. internal monitoring / synthetic probes).
    ip_allowlist: str = ""
    # User-Agent denylist override (comma-separated lowercase substrings).
    # Empty → falls back to DEFAULT_UA_DENYLIST_PATTERNS.
    ua_denylist: str = ""
    # Programmatic API keys (SHA256-hex of allowed X-API-Key values, comma-
    # separated). Bearers in this list bypass the User-Agent denylist —
    # legitimate scripted clients (curl-based monitoring, CI) opt in by
    # presenting the pre-shared key. NEVER store the plain key here.
    programmatic_api_keys: str = ""
    # Anti-abuse master switch — operator can disable on dev / canary boxes
    # without removing the middleware from the stack. Default ON in prod.
    anti_abuse_enabled: bool = True
    ip_rate_limit_enabled: bool = True
    # Single-process deployment toggle (case study 2026-05-16).
    # Default ON: lifespan spawns document_consumer + outbox_publisher as
    # background asyncio tasks inside the API process. Dev runs one
    # service (`systemctl restart ragbot-api`) and gets the full pipeline.
    # Set to false to revert to the legacy 3-service split (DevOps may
    # opt-in when they want to scale workers independently via systemd
    # template / K8s deployments).
    embed_workers_enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="APP_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @model_validator(mode="after")
    def _validate_cors_allowed_origins(self) -> "AppSettings":
        """Reject CORS wildcards / empty lists in non-dev environments (SEC-13).

        Boot-time guard. ``Access-Control-Allow-Origin: *`` paired with
        ``Access-Control-Allow-Credentials: true`` (which the per-tenant
        middleware emits on a match) is a CSRF amplifier. An operator
        typo of ``APP_CORS_ALLOWED_ORIGINS='["*"]'`` for a production
        tenant would land that combination on the wire — fail boot
        instead. Development keeps the permissive default so a same-
        origin local box doesn't need an explicit allow-list, but a
        ``WARNING`` log entry surfaces the wildcard at startup.

        Raises:
            ValueError: malformed JSON, non-list payload, wildcard in
                a strict environment, or empty list in a strict environment.
        """
        raw = self.cors_allowed_origins or "[]"
        try:
            origins = json.loads(raw)
        except json.JSONDecodeError as exc:
            msg = (
                "APP_CORS_ALLOWED_ORIGINS must be a JSON array of origin "
                f"strings; got invalid JSON: {exc.msg}"
            )
            raise ValueError(msg) from exc
        if not isinstance(origins, list):
            msg = (
                "APP_CORS_ALLOWED_ORIGINS must be a JSON list of origin "
                f"strings; got {type(origins).__name__}"
            )
            raise ValueError(msg)

        has_wildcard = "*" in origins
        env = self.env
        if env in _CORS_STRICT_ENVS:
            if has_wildcard:
                msg = (
                    "CORS allow_origins wildcard '*' is forbidden in "
                    f"APP_ENV={env!r}. Set APP_CORS_ALLOWED_ORIGINS to an "
                    "explicit list of origins (e.g. "
                    '\'["https://app.example.com"]\').'
                )
                raise ValueError(msg)
            if not origins:
                msg = (
                    f"CORS allow_origins cannot be empty in APP_ENV={env!r}; "
                    "set APP_CORS_ALLOWED_ORIGINS to at least one origin."
                )
                raise ValueError(msg)
        elif has_wildcard:
            _settings_logger.warning(
                "CORS allow_origins wildcard '*' active in APP_ENV=%r — "
                "permitted in development only; never deploy this to a "
                "non-dev environment (CSRF risk with credentialed CORS).",
                env,
            )
        return self


class DatabaseSettings(BaseSettings):
    """PostgreSQL async connection pool.

    ``url`` (DATABASE_URL) is the admin DSN used by migrations and ops
    scripts. ``url_app`` (DATABASE_URL_APP) is the runtime DSN bound to a
    non-superuser role so row-level-security policies actually filter —
    a superuser connection silently bypasses every RLS policy.
    """

    url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://ragbot:ragbot@localhost:5432/ragbot"),
    )
    url_app: PostgresDsn | None = Field(default=None)
    pool_size: int = DEFAULT_DB_POOL_SIZE
    max_overflow: int = DEFAULT_DB_MAX_OVERFLOW
    pool_recycle: int = DEFAULT_DB_POOL_RECYCLE_S
    pool_timeout: int = DEFAULT_DB_POOL_TIMEOUT_S  # seconds to wait for a free conn before raising
    pool_pre_ping: bool = True
    echo: bool = False

    model_config = SettingsConfigDict(env_prefix="DATABASE_", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("url_app")
    @classmethod
    def _check_url_app(cls, v: PostgresDsn | None) -> PostgresDsn | None:
        if v is not None:
            return v
        escape = os.getenv(RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV, "").strip()
        if escape != RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE:
            msg = (
                "DATABASE_URL_APP is required for runtime database access. "
                f"Set {RAGBOT_ALLOW_SUPERUSER_RUNTIME_ENV}="
                f"{RAGBOT_ALLOW_SUPERUSER_RUNTIME_VALUE} to opt into the "
                "admin DSN at runtime."
            )
            raise RuntimeError(msg)
        return None


class RedisSettings(BaseSettings):
    """Redis Stack (RediSearch + Streams + JSON) settings."""

    url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))
    pool_size: int = DEFAULT_REDIS_POOL_SIZE

    model_config = SettingsConfigDict(env_prefix="REDIS_", env_file=".env", env_file_encoding="utf-8", extra="ignore")


class StreamSettings(BaseSettings):
    """Redis Streams event bus settings."""

    stream_prefix: str = "ragbot"

    model_config = SettingsConfigDict(env_prefix="STREAM_", env_file=".env", env_file_encoding="utf-8", extra="ignore")


class EmbeddingSettings(BaseSettings):
    """FALLBACK ONLY — system_config overrides at runtime.

    Embedding settings (cloud API via LiteLLM).
    Primary source: system_config keys embedding_model, embedding_dimension, embedding_model_version.
    """

    model_name: str = DEFAULT_EMBEDDING_FALLBACK_MODEL
    dimension: int = DEFAULT_EMBEDDING_FALLBACK_DIMENSION
    model_version: str = DEFAULT_EMBEDDING_FALLBACK_VERSION

    model_config = SettingsConfigDict(env_prefix="EMBEDDING_", env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)


class OCRSettings(BaseSettings):
    """OCR provider settings."""

    provider: Literal["docling", "mistral"] = "docling"

    model_config = SettingsConfigDict(env_prefix="OCR_", env_file=".env", env_file_encoding="utf-8", extra="ignore")


class RerankerSettings(BaseSettings):
    """FALLBACK ONLY — system_config overrides at runtime (key: reranker_model).

    Cross-encoder reranker settings. Default tracks DEFAULT_RERANK_MODEL.

    Per-provider keys live in ``Settings.provider_api_keys`` (dict keyed
    by provider_code), not here — adapters resolve their pool by their
    own ``_PROVIDER_CODE`` so no brand string surfaces in this module.
    The legacy single-key ``RERANKER_JINA_API_KEY`` env is still read at
    the top-level ``Settings`` so deployments that have not migrated
    their .env keep booting unchanged."""

    from ragbot.shared.constants import DEFAULT_RERANK_MODEL as _DEFAULT_RERANK_MODEL

    model_name: str = _DEFAULT_RERANK_MODEL
    enabled: bool = True

    model_config = SettingsConfigDict(env_prefix="RERANKER_", env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)


class ChunkingSettings(BaseSettings):
    """FALLBACK ONLY — system_config overrides at runtime (keys: chunking_*).

    Adaptive chunking strategy thresholds — tune per deployment."""

    heading_threshold: int = DEFAULT_CHUNKING_HEADING_THRESHOLD  # ≥ N headings → HDT strategy
    avg_len_short: int = DEFAULT_CHUNKING_AVG_LEN_SHORT           # avg text length < N → recursive
    table_threshold: int = DEFAULT_CHUNKING_TABLE_THRESHOLD       # > N tables + short text → recursive
    avg_len_long: int = DEFAULT_CHUNKING_AVG_LEN_LONG             # avg text > N + few headings → semantic
    heading_max_for_semantic: int = DEFAULT_CHUNKING_HEADING_MAX_FOR_SEMANTIC  # < N headings for semantic
    mixed_content_threshold: float = DEFAULT_CHUNKING_MIXED_CONTENT_THRESHOLD  # > threshold → hybrid

    model_config = SettingsConfigDict(env_prefix="CHUNKING_", env_file=".env", env_file_encoding="utf-8", extra="ignore")


class EnrichmentSettings(BaseSettings):
    """FALLBACK ONLY — system_config overrides at runtime (keys: enrichment_*).

    Contextual enrichment settings (Anthropic-style, at ingest time)."""

    # Default OFF (2026-06-17): per-chunk nano enrichment is redundant with Jina
    # late_chunking and was part of the O(n^2) ingest storm. Safe-by-default so a
    # cold-start (before system_config loads) never re-triggers it. Opt-in via
    # system_config.enrichment_enabled.
    enabled: bool = False
    model_name: str = DEFAULT_ENRICHMENT_MODEL  # LLM cho enrichment (cheap model)
    temperature: float = DEFAULT_ENRICHMENT_TEMPERATURE
    max_tokens: int = DEFAULT_ENRICHMENT_MAX_TOKENS
    timeout_s: int = DEFAULT_ENRICHMENT_TIMEOUT_S
    doc_preview_chars: int = _DEFAULT_ENRICHMENT_DOC_PREVIEW_CHARS    # chars gửi cho LLM từ full doc
    chunk_preview_chars: int = _DEFAULT_ENRICHMENT_CHUNK_PREVIEW_CHARS  # chars gửi cho LLM từ chunk
    max_prefix_chars: int = _DEFAULT_ENRICHMENT_MAX_PREFIX_CHARS      # max chars cho prefix output

    model_config = SettingsConfigDict(env_prefix="ENRICHMENT_", env_file=".env", env_file_encoding="utf-8", extra="ignore")


class JwtSettings(BaseSettings):
    """JWT authentication settings."""

    public_key_path: str | None = None
    private_key_path: str | None = None
    algorithm: str = DEFAULT_JWT_ALGORITHM
    issuer: str = DEFAULT_JWT_ISSUER
    audience: str = DEFAULT_JWT_AUDIENCE

    model_config = SettingsConfigDict(env_prefix="JWT_", env_file=".env", env_file_encoding="utf-8", extra="ignore")


class ObservabilitySettings(BaseSettings):
    """Observability stack settings."""

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    prometheus_path: str = Field(default="/metrics", alias="PROMETHEUS_METRICS_PATH")

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore", populate_by_name=True)


class RagSettings(BaseSettings):
    """RAG pipeline fallback defaults; runtime overrides flow through ``system_config`` and per-bot bindings."""

    default_top_k: int = DEFAULT_RAG_TOP_K
    default_rerank_top_n: int = DEFAULT_RAG_RERANK_TOP_N
    default_chunk_size: int = DEFAULT_CHUNK_SIZE
    default_chunk_overlap: int = DEFAULT_CHUNK_OVERLAP
    semantic_cache_threshold: float = DEFAULT_SEMANTIC_CACHE_THRESHOLD
    max_iteration_cap: int = DEFAULT_MAX_ITERATION_CAP
    debounce_window_ms: int = DEFAULT_DEBOUNCE_WINDOW_MS
    rrf_k: int = DEFAULT_RRF_K
    circuit_breaker_fail_max: int = DEFAULT_CIRCUIT_BREAKER_FAIL_MAX
    circuit_breaker_reset_timeout: int = DEFAULT_CIRCUIT_BREAKER_RESET_TIMEOUT_S
    default_history_limit: int = DEFAULT_HISTORY_LIMIT

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("semantic_cache_threshold")
    @classmethod
    def _check_threshold(cls, v: float) -> float:
        if not 0.0 < v <= 1.0:
            msg = "semantic_cache_threshold must be in (0.0, 1.0]"
            raise ValueError(msg)
        return v


class SecretSettings(BaseSettings):
    """Misc secrets not fit elsewhere."""

    tenant_hmac_secret: str = "change-me-in-prod"  # noqa: S105
    prompt_canary_token: str | None = None

    model_config = SettingsConfigDict(env_prefix="", env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("tenant_hmac_secret")
    @classmethod
    def _check_hmac_secret(cls, v: str) -> str:
        # Any env other than "development" must use a real secret. Previously
        # only staging/production triggered the check, which left UAT silently
        # accepting "change-me-in-prod" — the very value an operator copying
        # .env.example would forget to override.
        env = os.getenv("APP_ENV", "development")
        # APP_ENVS_STRICT lifted from shared/constants.py SSoT — same set as
        # CORS gate above; env identifier strings live in exactly one place.
        weak = {"change-me-in-prod", "secret", "test", "default", "changeme", "password", "admin", ""}
        if v.lower() in weak and env in APP_ENVS_STRICT:
            msg = (
                f"TENANT_HMAC_SECRET is a known-weak placeholder "
                f"({v!r}); set a real 32+ byte secret for APP_ENV={env!r}"
            )
            raise ValueError(msg)
        if len(v) < 32 and env in APP_ENVS_STRICT:
            msg = (
                f"TENANT_HMAC_SECRET must be at least 32 characters "
                f"for APP_ENV={env!r} (got {len(v)})"
            )
            raise ValueError(msg)
        return v


class Settings(BaseSettings):
    """Root settings composition.

    Loads from `.env` + environment variables. Use `get_settings()` to get
    cached singleton.
    """

    app: AppSettings = Field(default_factory=AppSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    stream: StreamSettings = Field(default_factory=StreamSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    ocr: OCRSettings = Field(default_factory=OCRSettings)
    jwt: JwtSettings = Field(default_factory=JwtSettings)
    observability: ObservabilitySettings = Field(default_factory=ObservabilitySettings)
    rag: RagSettings = Field(default_factory=RagSettings)
    reranker: RerankerSettings = Field(default_factory=RerankerSettings)
    chunking: ChunkingSettings = Field(default_factory=ChunkingSettings)
    enrichment: EnrichmentSettings = Field(default_factory=EnrichmentSettings)
    secrets: SecretSettings = Field(default_factory=SecretSettings)
    # Provider-agnostic API key map: ``{"<provider_code>": [primary, secondary, ...]}``.
    # Source of truth for ``ApiKeyPoolFactory``. Populated by
    # ``model_post_init`` from the ``PROVIDER_API_KEYS_JSON`` env var (raw
    # JSON read directly to avoid pydantic-settings' built-in JSON decoder
    # firing during source loading) plus a back-compat seed from legacy
    # per-provider single-key envs. Adapters resolve their own pool via
    # their internal ``_PROVIDER_CODE`` — no brand string surfaces here.
    # ``init=False`` keeps pydantic-settings' env source from trying to
    # decode the dict on its own; ``model_post_init`` is the only writer.
    provider_api_keys: dict[str, list[str]] = Field(
        default_factory=dict,
        init=False,
    )

    # Boot-time fallback for the error notify channel. Parsed by
    # ``model_post_init`` from ``NOTIFY_CHANNEL_CONFIG_JSON`` (raw JSON
    # object). The runtime source of truth is the ``system_config`` row
    # keyed by ``NOTIFY_CHANNEL_CONFIG_KEY`` — env is consulted only
    # when the row is absent. The dict shape mirrors
    # ``NotifyChannelConfig`` so the resolver builds the DTO from
    # whichever source returns first.
    notify_channel_config: dict[str, Any] | None = Field(
        default=None,
        init=False,
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        """Hydrate ``provider_api_keys`` from env (JSON dict + legacy seeds).

        ``PROVIDER_API_KEYS_JSON`` is the canonical input. Legacy single-key
        envs (e.g. ``EMBEDDING_JINA_API_KEY``) seed any provider not already
        present so deployments that have not migrated keep booting. We walk
        the env once per ``Settings`` construct (rare; ``get_settings`` is
        lru-cached).
        """
        # Direct JSON read — pydantic-settings' source-level JSON decoder is
        # not used so the env value's quoting rules stay simple (one line of
        # raw JSON, no escaping).
        raw_json = os.environ.get("PROVIDER_API_KEYS_JSON", "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError as exc:
                msg = f"PROVIDER_API_KEYS_JSON: invalid JSON ({exc})"
                raise ValueError(msg) from exc
            if not isinstance(parsed, dict):
                msg = "PROVIDER_API_KEYS_JSON: must decode to an object/dict"
                raise ValueError(msg)
            for code, keys in parsed.items():
                if not isinstance(keys, list):
                    msg = f"PROVIDER_API_KEYS_JSON[{code!r}]: must be a list"
                    raise ValueError(msg)
                self.provider_api_keys[code] = [str(k) for k in keys if k]
        # Back-compat env aliases for the ``<purpose>_<provider>_API_KEY``
        # layout (primary first, secondary second). Concrete provider codes
        # live in this back-compat shim only — the production runtime
        # resolves codes from adapter constants.
        _env_alias_layout: dict[str, list[tuple[str, str]]] = {
            "jina": [
                ("EMBEDDING_JINA_API_KEY_PRIMARY", "EMBEDDING_JINA_API_KEY_SECONDARY"),
                ("RERANKER_JINA_API_KEY_PRIMARY", "RERANKER_JINA_API_KEY_SECONDARY"),
                ("EMBEDDING_JINA_API_KEY", ""),
                ("RERANKER_JINA_API_KEY", ""),
                ("JINA_API_KEY", ""),
            ],
        }
        for code, env_pairs in _env_alias_layout.items():
            if self.provider_api_keys.get(code):
                continue  # explicit dict wins
            collected: list[str] = []
            for primary_env, secondary_env in env_pairs:
                primary_val = os.environ.get(primary_env, "").strip()
                if primary_val and primary_val not in collected:
                    collected.append(primary_val)
                secondary_val = (
                    os.environ.get(secondary_env, "").strip() if secondary_env else ""
                )
                if secondary_val and secondary_val not in collected:
                    collected.append(secondary_val)
                if len(collected) >= 2:
                    break
            if collected:
                self.provider_api_keys[code] = collected

        # Hydrate notify_channel_config from raw env JSON. Same approach
        # as ``provider_api_keys``: read the env directly so pydantic's
        # source-level JSON decoder doesn't intercept the value.
        notify_raw = os.environ.get("NOTIFY_CHANNEL_CONFIG_JSON", "").strip()
        if notify_raw:
            try:
                notify_parsed = json.loads(notify_raw)
            except json.JSONDecodeError as exc:
                msg = f"NOTIFY_CHANNEL_CONFIG_JSON: invalid JSON ({exc})"
                raise ValueError(msg) from exc
            if not isinstance(notify_parsed, dict):
                msg = "NOTIFY_CHANNEL_CONFIG_JSON: must decode to an object/dict"
                raise ValueError(msg)
            self.notify_channel_config = notify_parsed

    @property
    def is_production(self) -> bool:
        return self.app.env == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings instance."""
    return Settings()


# Re-export Path for convenience in tests
__all__ = ["Path", "Settings", "get_settings"]
