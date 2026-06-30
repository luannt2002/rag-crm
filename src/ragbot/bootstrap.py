"""DI container — wires adapters + services + use cases.

Ref: PLAN_10 §dependencies / RAGBOT_MASTER §25.3.
"""

from __future__ import annotations

import os

from dependency_injector import containers, providers
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ragbot.application.events.chat_completed import ChatHookRegistry
from ragbot.application.services.ai_config_service import AIConfigService
from ragbot.application.services.audit_verifier import AuditVerifier
from ragbot.application.services.bot_lifecycle_service import BotLifecycleService
from ragbot.application.services.bot_management_service import BotManagementService
from ragbot.application.services.bot_registry_service import BotRegistryService
from ragbot.application.services.citation_policy import CitationPolicyService
from ragbot.application.services.corpus_version_service import CorpusVersionService
from ragbot.application.services.crag_grader.registry import build_crag_grader
from ragbot.application.services.error_notify_hook import ErrorNotifyHook
from ragbot.application.services.guardrail_rule_loader import GuardrailRuleLoader
from ragbot.application.services.hallu_verifier import HALLUVerifier
from ragbot.application.services.hyde_generator import HyDEGenerator
from ragbot.application.services.idempotency import IdempotencyService
from ragbot.application.services.ingest_idempotency_service import (
    IngestIdempotencyService,
)
from ragbot.application.services.ingest_quota_service import IngestQuotaService
from ragbot.application.services.language_pack_service import LanguagePackService
from ragbot.application.services.model_resolver import ModelResolverService
from ragbot.application.services.notify_channel_resolver import (
    NotifyChannelResolver,
)
from ragbot.application.services.oos_template_resolver import OosTemplateResolver
from ragbot.application.services.provider_key_resolver import ProviderKeyResolver
from ragbot.application.services.reranker_resolver import RerankerResolver
from ragbot.application.services.slot_extractor import SlotExtractor
from ragbot.application.services.sysprompt_assembler import SysPromptAssembler
from ragbot.application.services.system_config_service import SystemConfigService
from ragbot.application.services.tenant_config_cache import TenantConfigCache
from ragbot.application.services.tenant_guard import TenantGuardService
from ragbot.application.services.tenant_rate_limiter import TenantRateLimiter
from ragbot.application.services.tenant_token_meter import TenantTokenMeter
from ragbot.application.services.token_budget import TokenBudgetPolicy
from ragbot.application.use_cases.answer_question import AnswerQuestionUseCase
from ragbot.application.use_cases.delete_document import DeleteDocumentUseCase
from ragbot.application.use_cases.get_job_status import GetJobStatusUseCase
from ragbot.application.use_cases.give_feedback import GiveFeedbackUseCase
from ragbot.application.use_cases.ingest_document import IngestDocumentUseCase
from ragbot.application.use_cases.rechunk_document import RechunkDocumentUseCase
from ragbot.config.settings import Settings, get_settings
from ragbot.infrastructure.cache.embed_cache import EmbedCache
from ragbot.infrastructure.cache.redis_cache import (
    RedisCache,
    create_redis_client,
    create_redis_streams_client,
)
from ragbot.infrastructure.cache.semantic_cache import PgSemanticCache
from ragbot.infrastructure.cache.understand_query_cache import UnderstandQueryCache
from ragbot.infrastructure.chat_hooks.quota_threshold_notify_hook import (
    QuotaThresholdNotifyHook,
)
from ragbot.infrastructure.chat_hooks.token_usage_db_hook import TokenUsageDbHook
from ragbot.infrastructure.chat_hooks.token_usage_redis_hook import (
    TokenUsageRedisHook,
)
from ragbot.infrastructure.conversation_state.registry import (
    build_conversation_state,
)
from ragbot.infrastructure.db.engine import (
    create_engine_app,
    create_engine_system,
    create_session_factory,
    session_with_tenant,
)
from ragbot.infrastructure.db.session import create_rls_session_factory
from ragbot.infrastructure.db.uow import UnitOfWorkFactory
from ragbot.infrastructure.embedding.litellm_embedder import (
    LiteLLMEmbedder,  # noqa: F401  # kept for re-export
)
from ragbot.infrastructure.embedding.registry import build_embedder
from ragbot.infrastructure.entity_extractor.registry import build_entity_extractor
from ragbot.infrastructure.events.redis_streams_bus import RedisStreamsEventBus
from ragbot.infrastructure.guardrails.registry import build_guardrail
from ragbot.infrastructure.llm.dynamic_litellm_router import DynamicLiteLLMRouter
from ragbot.infrastructure.metadata_filter.registry import build_metadata_filter
from ragbot.infrastructure.notify.webhook_dispatcher import WebhookNotifyDispatcher
from ragbot.infrastructure.notify.webhook_notifier import WebhookNotifier
from ragbot.infrastructure.observability.invocation_logger import InvocationLogger
from ragbot.infrastructure.observability.pipeline_audit_logger import (
    PipelineAuditLogger,
)
from ragbot.infrastructure.observability.prometheus_metrics_adapter import (
    PrometheusMetricsAdapter,
)
from ragbot.infrastructure.ocr.ocr_factory import build_ocr_parser
from ragbot.infrastructure.pii.registry import build_pii_redactor
from ragbot.infrastructure.rate_limiter.registry import build_rate_limiter
from ragbot.infrastructure.repositories.ai_config_repository import (
    SqlAlchemyAIConfigRepository,
)
from ragbot.infrastructure.repositories.audit_chain_writer import insert_audit_row
from ragbot.infrastructure.repositories.audit_repository import AuditRepository
from ragbot.infrastructure.repositories.bot_repository import SqlAlchemyBotRepository
from ragbot.infrastructure.repositories.conversation_repository import (
    SqlAlchemyConversationRepository,
)
from ragbot.infrastructure.repositories.document_repository import (
    SqlAlchemyDocumentRepository,
)
from ragbot.infrastructure.repositories.guardrail_repository import GuardrailRepository
from ragbot.infrastructure.repositories.job_repository import SqlAlchemyJobRepository
from ragbot.infrastructure.repositories.language_pack_repository import (
    LanguagePackRepository,
)
from ragbot.infrastructure.repositories.message_feedback_repository import (
    MessageFeedbackRepository,
)
from ragbot.infrastructure.repositories.message_repository import MessageRepository
from ragbot.infrastructure.repositories.outbox_repository import (
    SqlAlchemyOutboxRepository,
)
from ragbot.infrastructure.repositories.quota_repository import SqlAlchemyQuotaRepository
from ragbot.infrastructure.repositories.request_log_repository import RequestLogRepository
from ragbot.infrastructure.repositories.stats_index_repository import StatsIndexRepository
from ragbot.infrastructure.repositories.tenant_policy_repository import (
    TenantPolicyRepository,
)
from ragbot.infrastructure.repositories.tenant_repository import TenantRepository
from ragbot.infrastructure.repositories.token_ledger_analytics_repository import (
    TokenLedgerAnalyticsRepository,
)
from ragbot.infrastructure.repositories.workspace_repository import WorkspaceRepository
from ragbot.infrastructure.reranker.litellm_reranker import (
    LiteLLMReranker,  # noqa: F401  # kept for backward compat re-export
)
from ragbot.infrastructure.reranker.registry import build_reranker
from ragbot.infrastructure.retrieval.lexical_registry import build_lexical_retrieval
from ragbot.infrastructure.security.env_secrets import EnvSecretsAdapter
from ragbot.infrastructure.security.jwt_auth import JwtVerifier
from ragbot.infrastructure.token_ledger import build_token_ledger
from ragbot.infrastructure.vector.pgvector_store import (
    PgVectorStore,  # noqa: F401  # kept for backward-compat re-export
)
from ragbot.infrastructure.vector.registry import build_vector_store
from ragbot.shared.api_key_pool import (
    ApiKeyPoolFactory,
    DBBackedApiKeyPoolFactory,
)
from ragbot.shared.bootstrap_config import get_boot_config
from ragbot.shared.clock import SystemClock
from ragbot.shared.constants import (
    DEFAULT_ARTICLE_REF_PATTERNS,
    DEFAULT_CONVERSATION_STATE_TTL_HOURS,
    DEFAULT_CRAG_GRADER_PROVIDER,
    DEFAULT_ENTITY_EXTRACTOR_PROVIDER,
    DEFAULT_LANGUAGE,
    DEFAULT_LEXICAL_RETRIEVAL_PROVIDER,
    DEFAULT_MAX_ACTION_SLOTS,
    DEFAULT_METADATA_FILTER_PROVIDER,
    DEFAULT_PII_REDACTOR_PROVIDER,
    DEFAULT_RERANK_MODEL,
    DEFAULT_RERANKER_PROVIDER,
    DEFAULT_TOKEN_QUOTA_NOTIFY_THROTTLE_S,
    DEFAULT_VECTOR_STORE_PROVIDER,
    PROMPT_VERSION_UQ,
)


class Container(containers.DeclarativeContainer):
    """Application DI container."""

    settings: providers.Provider[Settings] = providers.Singleton(get_settings)
    clock = providers.Singleton(SystemClock)

    # --- Infrastructure singletons -----------------------------------------
    db_engine: providers.Provider[AsyncEngine] = providers.Singleton(
        create_engine_app, settings=settings,
    )
    # Routed through the RLS wrapper (ADR-W1-D3): factory + attached
    # per-transaction SET LOCAL binder. No-op under superuser DSN; enforces
    # the moment DATABASE_URL_APP points at the NOBYPASSRLS role.
    session_factory: providers.Provider[async_sessionmaker[object]] = providers.Singleton(
        create_rls_session_factory, engine=db_engine,
    )
    # System engine + factory for the trusted cross-tenant background workers
    # (outbox publisher, recovery scan, semantic-cache GC, cost-cap aggregate).
    # Bound to the BYPASSRLS ``ragbot_system`` role (DATABASE_URL_SYSTEM) so the
    # cross-tenant scans are NOT fail-closed to zero rows; NO RLS hook attached
    # (plain create_session_factory). Falls back to the admin DSN until ops
    # provisions the role, so this is inert today (both DSNs → superuser).
    db_engine_system: providers.Provider[AsyncEngine] = providers.Singleton(
        create_engine_system, settings=settings,
    )
    system_session_factory: providers.Provider[async_sessionmaker[object]] = providers.Singleton(
        create_session_factory, engine=db_engine_system,
    )
    uow_factory = providers.Singleton(UnitOfWorkFactory, session_factory=session_factory)

    redis_client = providers.Singleton(
        create_redis_client,
        url=providers.Callable(lambda s: str(s.redis.url), settings),
        max_connections=providers.Callable(lambda s: s.redis.pool_size, settings),
    )
    # Separate Redis client for the Streams bus — its blocking XREADGROUP
    # needs a longer ``socket_timeout`` than the cache/rate-limit hot path
    # (see ``create_redis_streams_client`` docstring). Sharing the cache
    # client caused production worker stuck loops with TimeoutError before
    # the 5s ``block`` window elapsed.
    redis_streams_client = providers.Singleton(
        create_redis_streams_client,
        url=providers.Callable(lambda s: str(s.redis.url), settings),
    )

    # Provider-agnostic API key pool factory. Adapters resolve their own
    # pool from this factory using their internal ``_PROVIDER_CODE`` —
    # bootstrap.py never names a brand. Source of keys: ``provider_api_keys``
    # dict on Settings (PROVIDER_API_KEYS_JSON env, with legacy single-key
    # envs hydrated by Settings.model_post_init for back-compat).
    api_key_pool_factory: providers.Provider[ApiKeyPoolFactory] = providers.Singleton(
        lambda s, redis, sf: DBBackedApiKeyPoolFactory(
            provider_keys=s.provider_api_keys,
            redis_client=redis,
            session_factory=sf,
        ),
        settings,
        redis_client,
        session_factory,
    )

    # --- Adapters ----------------------------------------------------------
    cache = providers.Factory(RedisCache, client=redis_client)
    semantic_cache = providers.Factory(PgSemanticCache, session_factory=session_factory)
    # Hot-path short-TTL caches (Stream S5 Pipeline-Opt). Both degrade
    # silent on Redis error — the chat pipeline never hard-fails on aux
    # cache. TTL resolved per-call from system_config so an operator flip
    # takes effect after the bootstrap_config TTL elapses (≤30s).
    understand_query_cache = providers.Singleton(
        UnderstandQueryCache,
        redis_client=redis_client,
        prompt_version=PROMPT_VERSION_UQ,
    )
    embed_cache = providers.Singleton(EmbedCache, redis_client=redis_client)
    bus = providers.Factory(
        RedisStreamsEventBus,
        client=redis_streams_client,
        stream_prefix=providers.Callable(lambda s: s.stream.stream_prefix, settings),
        # Transactional inbox (ADR-W1-D8b) — process-then-mark
        # exactly-once needs a DB session to write event_inbox rows.
        session_factory=session_factory,
    )
    # Vector store strategy — registry-driven. Provider resolved at boot
    # from ``system_config.vector_store_provider`` (Redis-cached). Default
    # = ``"pgvector"`` (the only DB-backed provider shipped in-tree);
    # ``"null"`` is the fail-soft disabled mode. Adding a new backend
    # (qdrant / weaviate / …) = drop a file in ``infrastructure/vector/``
    # and register it in ``infrastructure/vector/registry.py`` — no
    # bootstrap edit, no orchestrator edit.
    vector_store = providers.Singleton(
        build_vector_store,
        provider=providers.Callable(
            lambda: get_boot_config(
                "vector_store_provider", DEFAULT_VECTOR_STORE_PROVIDER,
            ),
        ),
        session_factory=session_factory,
        dimension=providers.Callable(lambda s: s.embedding.dimension, settings),
    )
    # Lexical retrieval strategy (Strategy + DI) — sparse / BM25 branch
    # running in parallel with the vector branch in the retrieve node.
    # Provider resolved PER-CALL from ``system_config.lexical_retrieval_provider``
    # (DB SSoT) so an operator flip from ``"null"`` to ``"pg_textsearch"``
    # takes effect on the next request after Redis cache TTL (no app
    # restart needed). Default ``"null"`` preserves the pre-S7 single-branch
    # retrieve behaviour for tenants who have not opted in.
    lexical_retrieval = providers.Factory(
        build_lexical_retrieval,
        provider=providers.Callable(
            lambda: get_boot_config(
                "lexical_retrieval_provider", DEFAULT_LEXICAL_RETRIEVAL_PROVIDER,
            ),
        ),
        session_factory=session_factory,
    )
    # Embedder strategy — provider resolved from ``system_config.embedding_provider``
    # at container boot. Env var ``EMBEDDING_PROVIDER`` overrides for local
    # dev / one-off testing. Constant default is the last-resort fallback
    # when both DB and env are absent (CLAUDE.md zero-hardcode discipline:
    # DB is the single source of truth, code only holds the fallback).
    # Singleton (was Factory) — every embedder strategy in
    # ``infrastructure.embedding/`` lazy-inits a shared ``httpx.AsyncClient``
    # behind an asyncio.Lock, so reuse across requests saves a TLS+DNS
    # handshake per call (Q16/Q17 verified). Adapter constructor must
    # raise on configuration error so the Singleton fails-loud at boot
    # instead of silently degrading on the first request.
    # Token-log-center sink (per-call durable ledger, decoupled / fire-and-
    # forget). Declared before embedder/reranker so those adapters can capture
    # their own usage. 'db' provider uses its OWN session factory so the audit
    # write never shares the LLM/retrieve-path session.
    token_ledger = providers.Singleton(
        build_token_ledger,
        provider="db",
        session_factory=session_factory,
    )

    embedder = providers.Singleton(
        build_embedder,
        provider=providers.Callable(
            lambda: os.environ.get("EMBEDDING_PROVIDER")
            or get_boot_config("embedding_provider", "litellm"),
        ),
        model=providers.Callable(lambda s: s.embedding.model_name, settings),
        key_pool_factory=api_key_pool_factory,
        # Log-center: capture embedding token usage to the durable ledger.
        ledger=token_ledger,
    )
    # Parser picked from system_config key `parser_engine`:
    #   'docling' → DoclingParser (layout-aware), falls back to Simple if dep missing
    #   'simple' / anything else → SimpleTextParser (default, zero deps)
    ocr = providers.Singleton(build_ocr_parser)
    system_config_service = providers.Singleton(
        SystemConfigService,
        session_factory=session_factory,
        redis_client=redis_client,
    )
    # Secrets adapter — AES-GCM with KEK from env (RAGBOT_CONFIG_KEK).
    # Declared before its consumers (resolver below, model_resolver later).
    secrets_port = providers.Singleton(EnvSecretsAdapter)
    # Provider API key resolver — runtime hot-swap (admin PUT /admin/api-keys/{code}
    # writes ``api_keys`` table + busts cache; next request reads fresh).
    # Keys are stored encrypted (value_encrypted); resolver decrypts via
    # the injected SecretsPort and caches only ciphertext in Redis.
    provider_key_resolver = providers.Singleton(
        ProviderKeyResolver,
        session_factory=session_factory,
        redis_client=redis_client,
        secrets=secrets_port,
    )
    # Guardrail rule loader — DB-backed moderation rules (alembic 010f).
    # Single in-process cache; tenants override platform defaults by
    # inserting a row with the same ``rule_id`` and a non-NULL
    # ``record_tenant_id``. ``bootstrap()`` is called from the FastAPI
    # lifespan so the platform-default set is warm before the first request.
    guardrail_rule_loader = providers.Singleton(
        GuardrailRuleLoader,
        session_factory=session_factory,
        redis_client=redis_client,
    )
    # Guardrail strategy — registry-driven (Strategy + DI). Default
    # provider is "local" (LocalGuardrail), but operators can flip to
    # "null" (NullGuardrail) per-tenant for free tiers, or to a future
    # "openai_moderation" / "azure_content_safety" without changing
    # orchestration code. The factory looks the class up in
    # :mod:`ragbot.infrastructure.guardrails.registry` and constructs it
    # with the kwargs below; NullGuardrail ignores the kwargs it does
    # not need.
    guardrail = providers.Factory(
        build_guardrail,
        provider="local",  # TODO Phase 4: lift to system_config.guardrail_provider
        guardrail_repository=providers.Factory(
            GuardrailRepository, session_factory=session_factory,
        ),
        config_service=system_config_service,
        rule_loader=guardrail_rule_loader,
    )
    # Reranker strategy — registry-driven. Provider resolved PER-CALL from
    # ``system_config.reranker_provider`` (DB SSoT) so an operator flip
    # takes effect on the next request after Redis cache TTL (no app
    # restart needed). Per-bot resolver still supersedes when
    # ``record_bot_id`` is set on state.
    # Singleton (was Factory) — reranker strategies (ZeroEntropy, Jina,
    # Voyage) reuse a single ``httpx.AsyncClient`` via lazy-init under
    # ``asyncio.Lock``; sharing one instance across requests amortises
    # the TLS handshake + keeps the connection pool warm. Provider /
    # model strings still resolve PER-CALL from system_config inside
    # the rerank node — Singleton scope here only caches the constructed
    # HTTP client, NOT the routing decision.
    reranker = providers.Singleton(
        build_reranker,
        provider=providers.Callable(
            lambda: get_boot_config("reranker_provider", DEFAULT_RERANKER_PROVIDER),
        ),
        model=providers.Callable(
            lambda: get_boot_config("reranker_model", DEFAULT_RERANK_MODEL),
        ),
        # Concrete reranker strategies pick up their own pool via the
        # factory — no brand string lives in this DI wiring.
        key_pool_factory=api_key_pool_factory,
        # Log-center: capture rerank token usage to the durable ledger
        # (build_reranker filters kwargs to ctor sig, so null/other strategies
        # that don't accept ledger ignore it).
        ledger=token_ledger,
    )
    # Entity extractor strategy — T3 entity-grounded query expansion.
    # Default ``"null"`` (no-op) so existing tenants see no behaviour
    # change. Per-bot opt-in via ``pipeline_config.entity_extractor_provider``
    # resolved by the multi_query_fanout node when it builds the variant list.
    entity_extractor = providers.Singleton(
        build_entity_extractor,
        provider=providers.Callable(
            lambda: get_boot_config(
                "entity_extractor_provider", DEFAULT_ENTITY_EXTRACTOR_PROVIDER,
            ),
        ),
        language=DEFAULT_LANGUAGE,
    )
    # Article-aware metadata filter strategy — C2 query-side pre-filter
    # that mirrors the ingest-side structured-ref extractor. Provider
    # resolved PER-CALL from ``system_config.metadata_filter_provider``
    # (DB SSoT). Default ``"null"`` (no-op) so existing tenants see no
    # behaviour change. Pattern list also lives in ``system_config``
    # (``article_ref_patterns``) so a bot owner with a non-VN corpus can
    # swap the keyword set without a code change.
    metadata_filter_strategy = providers.Factory(
        build_metadata_filter,
        provider=providers.Callable(
            lambda: get_boot_config(
                "metadata_filter_provider", DEFAULT_METADATA_FILTER_PROVIDER,
            ),
        ),
        patterns=providers.Callable(
            lambda: get_boot_config(
                "article_ref_patterns", list(DEFAULT_ARTICLE_REF_PATTERNS),
            ),
        ),
    )
    # CRAG grader strategy factory — registry-driven. Provider resolved
    # PER-CALL from ``system_config.crag_grader_provider``. The orchestrator
    # (Phase 2) calls ``container.crag_grader_factory(structured_llm_caller=...,
    # system_prompt=...)`` per request to materialise a strategy bound to
    # the request's LLM transport + bot language pack — runtime values
    # that cannot be frozen at container boot.
    crag_grader_factory = providers.Factory(
        build_crag_grader,
        provider=providers.Callable(
            lambda: get_boot_config("crag_grader_provider", DEFAULT_CRAG_GRADER_PROVIDER),
        ),
    )
    # PII Redactor (Strategy + DI). Provider resolved PER-CALL from
    # ``system_config.pii_redactor_provider``. Default = "null"
    # (passthrough) so the platform stays opt-in until a bot owner flips
    # ``plan_limits.pii_redaction_enabled`` AND ``system_config`` selects
    # a real provider (e.g. ``vn_regex``). Wiring lives in chat_worker and
    # DocumentService.ingest — see Master Finding #4.
    pii = providers.Singleton(
        build_pii_redactor,
        provider=DEFAULT_PII_REDACTOR_PROVIDER,
    )

    jwt_verifier = providers.Singleton(
        JwtVerifier,
        algorithm=providers.Callable(lambda s: s.jwt.algorithm, settings),
        public_key_path=providers.Callable(lambda s: s.jwt.public_key_path, settings),
        hmac_secret=providers.Callable(lambda s: s.secrets.tenant_hmac_secret, settings),
        issuer=providers.Callable(lambda s: s.jwt.issuer, settings),
        audience=providers.Callable(lambda s: s.jwt.audience, settings),
    )

    # --- Repositories ------------------------------------------------------
    conv_repo = providers.Factory(
        SqlAlchemyConversationRepository, session_factory=session_factory,
    )
    document_repo = providers.Factory(
        SqlAlchemyDocumentRepository, session_factory=session_factory,
    )
    bot_repo = providers.Factory(SqlAlchemyBotRepository, session_factory=session_factory)
    job_repo = providers.Factory(SqlAlchemyJobRepository, session_factory=session_factory)
    # Workspace entity (ADR-W2-D2) — slug → row lookup / lifecycle. Bare
    # session inherits the RLS app.tenant_id GUC via the D3 hook.
    workspace_repo = providers.Factory(
        WorkspaceRepository, session_factory=session_factory,
    )
    quota_repo = providers.Factory(SqlAlchemyQuotaRepository, session_factory=session_factory)
    # Stats Index — deterministic numeric-entity extraction for table chunks.
    # Singleton: holds no per-request state (just the session_factory reference).
    stats_index_repo = providers.Singleton(
        StatsIndexRepository, session_factory=session_factory,
    )
    # Publisher-only repo — drains the outbox cross-tenant (FOR UPDATE SKIP
    # LOCKED over every tenant's rows), so it MUST use the BYPASSRLS system
    # factory or it would be fail-closed to zero rows under the request role.
    outbox_repo = providers.Factory(
        SqlAlchemyOutboxRepository, session_factory=system_session_factory,
    )
    ai_config_repo = providers.Factory(
        SqlAlchemyAIConfigRepository, session_factory=session_factory,
    )
    token_ledger_analytics_repo = providers.Factory(
        TokenLedgerAnalyticsRepository, session_factory=session_factory,
    )
    request_log_repo = providers.Factory(
        RequestLogRepository, session_factory=session_factory,
    )
    audit_repo = providers.Factory(
        AuditRepository, session_factory=session_factory,
    )
    # alembic 010g — scans audit_log + recomputes hash chain to detect tamper.
    audit_verifier = providers.Factory(
        AuditVerifier, session_factory=session_factory,
    )
    guardrail_repo = providers.Factory(
        GuardrailRepository, session_factory=session_factory,
    )
    message_repo = providers.Factory(
        MessageRepository, session_factory=session_factory,
    )
    # Thumbs feedback analytics — backs /feedback/thumbs (route registered
    # in interfaces/http/router.py). RLS-scoped via session_with_tenant.
    message_feedback_repo = providers.Factory(
        MessageFeedbackRepository, session_factory=session_factory,
    )
    tenant_policy_repo = providers.Factory(
        TenantPolicyRepository, session_factory=session_factory,
    )
    language_pack_repo = providers.Factory(
        LanguagePackRepository, session_factory=session_factory,
    )
    invocation_logger = providers.Singleton(
        InvocationLogger, session_factory=session_factory,
    )
    # Default OFF; flip on via RAGBOT_PIPELINE_AUDIT_ENABLED env.
    pipeline_audit_logger = providers.Singleton(PipelineAuditLogger)

    # --- Services ----------------------------------------------------------
    idempotency = providers.Factory(IdempotencyService, cache=cache)
    tenant_guard = providers.Singleton(TenantGuardService)
    citation_policy = providers.Factory(CitationPolicyService)
    token_budget = providers.Factory(TokenBudgetPolicy, quota_repo=quota_repo)
    model_resolver = providers.Singleton(
        ModelResolverService,
        repo=ai_config_repo,
        cache=cache,
        clock=clock,
        secrets_port=secrets_port,
        # Read-only system_config SSoT (Redis-cached) so a bot WITHOUT a
        # per-bot binding follows the realtime platform-default model. An
        # operator ``UPDATE system_config`` (llm_default_model /
        # reranker_model / embedding_model) then swaps the model for every
        # binding-less bot with no app restart.
        system_config=system_config_service,
    )
    # --- Per-tenant rate limit + monthly token meter ----------------------
    metrics_port = providers.Singleton(PrometheusMetricsAdapter)
    tenant_rate_limiter = providers.Singleton(
        TenantRateLimiter, redis_client=redis_client, metrics=metrics_port,
    )
    tenant_token_meter = providers.Singleton(
        TenantTokenMeter, redis_client=redis_client,
    )
    tenant_config_cache = providers.Singleton(
        TenantConfigCache,
        session_factory=session_factory,
        redis_client=redis_client,
    )
    # Layer-2 sliding-window rate limiter (per-token + per-endpoint).
    # Defaults to ``redis_sliding`` against the same Redis client as Layer-1.
    # Tests + dev runs may swap to ``in_memory`` via system_config without
    # code edits (Open-Closed via the registry).
    rate_limiter = providers.Singleton(
        build_rate_limiter,
        provider="redis_sliding",
        redis_client=redis_client,
    )


    llm = providers.Singleton(
        DynamicLiteLLMRouter,
        ai_config_repo=ai_config_repo,
        redis_client=redis_client,
        token_meter=tenant_token_meter,
        ledger=token_ledger,
    )
    # HyDE generator — Wave F T1.4 application facade. Wraps the shared
    # LLM Port (cheap tier route — actual model resolution per-call by
    # DynamicLiteLLMRouter). Singleton scope amortises the timeout cap
    # constant; the LLM call itself is per-request. Wired here AFTER
    # ``llm`` so DI can pass it as a dependency. Default-OFF per-bot;
    # consumed by ``query_graph._embed_query`` only when
    # ``pipeline_config.hyde_enabled`` is True. Wave G BF4 pilot caught
    # this DI gap — without the wire, the flag was a no-op.
    hyde_generator = providers.Singleton(
        HyDEGenerator,
        llm=llm,
    )
    # Speculative Streaming Phase 3 — HALLU verifier (Wave K2 + L1).
    # The verifier is a stateless service; it borrows the shared
    # ``embedder`` for the optional Gate 3 (topic-divergence cosine).
    # Default thresholds resolve from ``shared/constants`` so an
    # operator can tune them via the constants module without touching
    # this wiring. Default OFF for the streaming gate itself is the
    # responsibility of the caller (per-bot ``plan_limits
    # .speculative_hallu_verify_enabled``); injecting a Singleton here
    # has zero hot-path cost and lets the SpeculativeRouter receive a
    # live verifier instance when ``query_graph`` wraps it.
    hallu_verifier = providers.Singleton(
        HALLUVerifier,
        embedder=embedder,
    )
    bot_registry_service = providers.Singleton(
        BotRegistryService,
        repo=bot_repo,
        redis_client=redis_client,
    )
    # Language packs — DB-driven prompt translations (migrations 0055/0056).
    # Adding a new language is a SQL INSERT + Redis bust; zero code change.
    language_pack_service = providers.Singleton(
        LanguagePackService,
        repo=language_pack_repo,
        redis_client=redis_client,
    )
    # OOS / refuse template resolver — 7-tier chain spanning
    # bot column -> plan_limits -> (workspace_config, tenants — Phase 4)
    # -> system_config -> language_packs -> constants. See module docstring
    # for the full ladder. Singleton because both inner ports are
    # Singletons and the resolver itself is stateless.
    oos_template_resolver = providers.Singleton(
        OosTemplateResolver,
        config_service=system_config_service,
        language_pack_service=language_pack_service,
    )
    # SysPrompt assembler — append platform-default rules to
    # bot.system_prompt at request time. Rules live in
    # ``language_packs[code].sysprompt_default_rules`` (seeded by alembic
    # 0146 for vi+en); per-bot opt-out via
    # ``bots.plan_limits.sysprompt_rules_disabled`` list. See module
    # docstring for the full multi-tenant rationale.
    sysprompt_assembler = providers.Singleton(
        SysPromptAssembler,
        language_pack_service=language_pack_service,
    )
    # Conversation state — per-conversation structured state for multi-turn
    # HALLU prevention (Tier 2 X2 BUNDLED ship 2026-05-30). Provider
    # defaults to "null" (Null Object pattern, no-op) so bots without
    # ``action_config.enabled=true`` see no behavioural change. Production
    # provider "jsonb" backs state on ``conversations.action_state`` JSONB
    # column (alembic 0150). Per-bot opt-in toggles backend selection.
    conversation_state = providers.Singleton(
        build_conversation_state,
        provider="jsonb",  # registry built once; Null vs Jsonb decided per-request via bot.action_config
        session_factory=session_factory,
        # TTL (hours of inactivity → flow self-clears) + max slots (anti-bloat),
        # runtime-tunable via system_config; defaults from constants.
        ttl_hours=providers.Callable(
            lambda: int(get_boot_config(
                "conversation_state_ttl_hours",
                DEFAULT_CONVERSATION_STATE_TTL_HOURS,
            )),
        ),
        max_slots=providers.Callable(
            lambda: int(get_boot_config(
                "conversation_state_max_slots",
                DEFAULT_MAX_ACTION_SLOTS,
            )),
        ),
    )
    # SlotExtractor — parse user message → structured slots via LLM JSON
    # mode + Pydantic validate. Uses litellm directly via
    # ``structured_output_helper.call_with_schema`` (J1-verified path).
    # Model alias resolved from system_config.slot_extractor_model
    # (default "haiku" per feedback_haiku_partial_only). Stateless.
    slot_extractor = providers.Singleton(
        SlotExtractor,
        litellm_module=providers.Object(__import__("litellm")),
        config_service=system_config_service,
    )
    # Per-bot semantic-cache discriminator — replaces the legacy
    # ``corpus_version="latest"`` literal so cache keys actually rotate
    # when a bot's corpus changes (see corpus_version_service module
    # docstring for the full rationale).
    corpus_version_service = providers.Singleton(
        CorpusVersionService,
        session_factory=session_factory,
        redis_client=redis_client,
    )
    bot_management_service = providers.Factory(
        BotManagementService,
        repo=bot_repo,
        registry=bot_registry_service,
        uow_factory=uow_factory,
        session_factory=session_factory,
    )
    # Irreversible phase 2 of the two-phase bot delete: hard-delete saga
    # (FK CASCADE wipe) + registry/corpus/uq cache busts. Composes the
    # singletons above — no new infra. The infra callables are injected
    # here (hexagonal boundary: application/ must not import them).
    bot_lifecycle_service = providers.Factory(
        BotLifecycleService,
        session_factory=session_factory,
        registry=bot_registry_service,
        corpus_version_service=corpus_version_service,
        redis_client=redis_client,
        tenant_session=providers.Object(session_with_tenant),
        audit_writer=providers.Object(insert_audit_row),
        tenant_repository_factory=providers.Object(TenantRepository),
    )
    ai_config_service = providers.Factory(
        AIConfigService,
        ai_config_repo=ai_config_repo,
        model_resolver=model_resolver,
        uow_factory=uow_factory,
        session_factory=session_factory,
    )

    # Per-bot reranker resolver. Resolves bot_model_bindings.purpose='rerank'
    # → RerankerPort. Redis-cached. Fail-soft → NullReranker.
    reranker_resolver = providers.Singleton(
        RerankerResolver,
        session_factory=session_factory,
        redis_client=redis_client,
        key_pool_factory=api_key_pool_factory,
        # Log-center: per-bot rerankers capture token usage to the ledger.
        ledger=token_ledger,
    )

    # Notify channel — DB-first resolver + webhook dispatcher + error
    # hook. Wired into the chat + ingest top-level error catches so an
    # exhausted retry envelope surfaces realtime to the configured
    # webhook target without redeploy. Channel target lives in
    # ``system_config[NOTIFY_CHANNEL_CONFIG_KEY]`` with env fallback.
    notify_resolver = providers.Singleton(
        NotifyChannelResolver,
        system_config_service=system_config_service,
        redis_client=redis_client,
        env_settings=settings,
    )
    webhook_dispatcher = providers.Singleton(
        WebhookNotifyDispatcher,
        httpx_client=None,
        resolver=notify_resolver,
        redis_client=redis_client,
    )
    error_notify_hook = providers.Singleton(
        ErrorNotifyHook,
        dispatcher=webhook_dispatcher,
    )

    # Quota-exhausted notify channel (Strategy + DI). URL empty → adapter
    # silently disables; transport failure logged + swallowed. Throttle
    # window read from env so ops can flip without redeploy.
    webhook_notifier = providers.Singleton(
        lambda redis: WebhookNotifier(
            url=os.environ.get("RAGBOT_TENANT_QUOTA_NOTIFY_URL", ""),
            auth_token=os.environ.get(
                "RAGBOT_TENANT_QUOTA_NOTIFY_AUTH_TOKEN", "",
            ),
            redis_client=redis,
            throttle_s=int(
                os.environ.get(
                    "RAGBOT_TENANT_QUOTA_NOTIFY_THROTTLE_S",
                    DEFAULT_TOKEN_QUOTA_NOTIFY_THROTTLE_S,
                ),
            ),
        ),
        redis_client,
    )

    # Chat completion hooks (Open-Closed extension point).
    # Add new side-effects = register hook here, NEVER edit chat_worker.py.
    # Stage 'db' runs inside caller's transaction; 'post_commit' runs after.
    chat_hook_registry = providers.Singleton(
        ChatHookRegistry,
        hooks=providers.List(
            # Stage 'db' — atomic UPDATE bots.tokens_used += delta
            providers.Factory(TokenUsageDbHook),
            # Stage 'post_commit' — INCR Redis L1 counter + quota notify
            providers.Factory(TokenUsageRedisHook, redis_client=redis_client),
            providers.Factory(
                QuotaThresholdNotifyHook,
                redis_client=redis_client,
                notifier=webhook_notifier,
                config_service=system_config_service,
            ),
        ),
    )

    # --- Use cases ---------------------------------------------------------
    answer_question_uc = providers.Factory(
        AnswerQuestionUseCase,
        conv_repo=conv_repo,
        job_repo=job_repo,
        uow_factory=uow_factory,
        idempotency=idempotency,
        budget=token_budget,
        clock=clock,
    )
    ingest_document_uc = providers.Factory(
        IngestDocumentUseCase,
        doc_repo=document_repo,
        bot_repo=bot_repo,
        job_repo=job_repo,
        uow_factory=uow_factory,
        idempotency=idempotency,
        clock=clock,
    )
    # BE-to-BE upload idempotency (case study P0-3) — Singleton because
    # the service holds no per-request state and the constructor is
    # cheap (single TTL int + the shared session_factory). Surfaces
    # ``check_and_record`` to the HTTP layer + ``mark_done`` /
    # ``mark_failed`` to the worker.
    ingest_idempotency_service = providers.Singleton(
        IngestIdempotencyService,
        session_factory=session_factory,
    )
    # Per-tenant daily document quota gate. Stateless — the caller passes
    # the session per call; wired into both upload routes via the shared
    # _ingest_quota_guard helper (closes P2-H IQ-1 orphan, ADR-W2-D2 §c).
    ingest_quota_service = providers.Singleton(IngestQuotaService)
    delete_document_uc = providers.Factory(
        DeleteDocumentUseCase,
        doc_repo=document_repo,
        bot_repo=bot_repo,
        vector_store=vector_store,
        uow_factory=uow_factory,
        clock=clock,
        # ING-7: without this the stats-index purge in the use-case is dead —
        # deleted entities keep serving until the 300s corpus-version TTL.
        stats_index_repo=stats_index_repo,
    )
    rechunk_document_uc = providers.Factory(
        RechunkDocumentUseCase,
        doc_repo=document_repo,
        job_repo=job_repo,
        vector_store=vector_store,
        uow_factory=uow_factory,
        clock=clock,
    )
    get_job_status_uc = providers.Factory(GetJobStatusUseCase, job_repo=job_repo)
    give_feedback_uc = providers.Factory(
        GiveFeedbackUseCase,
        uow_factory=uow_factory,
        clock=clock,
        request_log_repo=request_log_repo,
    )


__all__ = ["Container"]
