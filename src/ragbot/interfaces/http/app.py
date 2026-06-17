"""Factory tạo ứng dụng FastAPI và quản lý vòng đời (lifespan)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncio

from redis.exceptions import RedisError
import os
import structlog

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, ORJSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ragbot.bootstrap import Container
from ragbot.config.logging import setup_logging
from ragbot.config.settings import get_settings
from ragbot.infrastructure.observability.metrics import setup_metrics_app
from ragbot.interfaces.http.errors import register_exception_handlers
from ragbot.interfaces.http.middlewares.body_size import BodySizeLimitMiddleware
from ragbot.interfaces.http.middlewares.cors_per_tenant import (
    CORSPerTenantMiddleware,
    parse_global_origins,
)
from ragbot.interfaces.http.middlewares.logging_mw import LoggingMiddleware
from ragbot.interfaces.http.middlewares.rate_limit import SlidingRateLimitMiddleware
from ragbot.interfaces.http.middlewares.schema_version import (
    SchemaVersionMiddleware,
)
from ragbot.interfaces.http.middlewares.security_headers import (
    SecurityHeadersMiddleware,
)
from ragbot.interfaces.http.middlewares.tenant_context import TenantContextMiddleware
from ragbot.interfaces.http.middlewares.trace_context import TraceContextMiddleware
from ragbot.interfaces.http.router import router as api_router
from ragbot.shared.constants import (
    APP_ENVS_STRICT,
    DEFAULT_CORS_PREFLIGHT_MAX_AGE_S,
    DEFAULT_RATE_LIMIT_PER_IP_PER_MIN,
    DEFAULT_RL_EMIT_HEADERS,
    DEFAULT_RL_FAIL_MODE,
    DEFAULT_RL_IP_WINDOW_S,
    DEFAULT_SECURITY_HEADERS_HSTS_ENABLED,
    GZIP_MINIMUM_SIZE,
    RAGBOT_METRICS_AUTH_TOKEN_ENV,
)

logger = structlog.get_logger(__name__)


def _check_reranker_preflight(
    *,
    enabled: bool,
    model_name: str,
    provider: str | None = None,
) -> None:
    """Enforce reranker provider readiness at startup via the registry.

    Strategy + DI: instead of branching on ``model.startswith(...)`` for
    each provider (which couples this preflight to every brand and
    forces a code change to add a new provider), we delegate to
    ``build_reranker(provider)``. Each ``RerankerPort`` strategy raises
    its own ``ValueError("X requires PROVIDER_API_KEYS_JSON or ...")``
    constructor when its env keys are missing — so adding a new
    provider is "drop a file in infrastructure/reranker/" plus register
    it in the registry; no edit to this helper.

    The legacy ``model_name`` arg is kept for backwards-compat with
    callers that only have the model string; we accept it but the
    authoritative input is ``provider`` (the registry key).

    Raises RuntimeError on failure so the operator sees a single,
    actionable boot error instead of a downstream ``NullReranker``
    silent-degrade. When ``enabled=False`` this is a no-op.
    """
    if not enabled:
        return

    # Lazy import to keep the preflight helper unit-testable without
    # pulling in the heavy reranker dependencies at module load.
    from ragbot.infrastructure.reranker.registry import (  # noqa: PLC0415
        build_reranker, list_providers,
    )

    provider_key = (provider or "").strip().lower()
    if not provider_key:
        # Fall back to deriving from model_name prefix only when the
        # caller has no provider config to pass. This branch is for
        # legacy call sites and emits a single warning so ops can move
        # them to the provider-keyed call shape.
        logger.warning(
            "reranker_preflight_no_provider_provided",
            model=model_name,
            note=(
                "model_name only — derive provider from registry "
                "available keys; ops should pass provider= explicitly"
            ),
            registered=list_providers(),
        )
        return

    try:
        # Strategy's __init__ surfaces missing env-key + bad-config as
        # ValueError (see ZeroEntropyReranker / JinaReranker / etc).
        build_reranker(provider=provider_key, model=model_name)
    except (ValueError, RuntimeError) as exc:
        # Re-wrap with operator-actionable boot guidance. Keep the
        # original exception in __cause__ for debug.
        raise RuntimeError(
            f"reranker_enabled=true but provider {provider_key!r} cannot "
            f"initialise (model={model_name!r}): {exc}. "
            f"Set the missing env vars, switch reranker provider in "
            f"system_config, or set reranker_enabled=false."
        ) from exc


def _check_required_provider_keys(settings: Any) -> None:
    """Fail loud at startup when UAT/staging/production has missing keys.

    Boot-time validation closes the silent-degraded-startup gap: a
    missing OPENAI_API_KEY would previously boot cleanly and only
    surface on the first chat request (broad-except in resource init
    blocks below swallows the error). Dev environments are exempt so
    local development without all keys still works.

    Raises ``RuntimeError`` listing every missing key in one message
    so the operator sees the full picture, not the first one.
    """
    env = settings.app.env
    if env not in APP_ENVS_STRICT:
        return

    missing: list[str] = []

    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not openai_key or openai_key == "sk-your-key-here":
        missing.append(
            "OPENAI_API_KEY (empty or placeholder; UAT requires real key)"
        )

    # Reranker key required only when reranker is enabled in env-tier config.
    # The reranker preflight further down validates DB-side enable flag.
    if getattr(settings.reranker, "enabled", True):
        jina_keys = (
            os.getenv("PROVIDER_API_KEYS_JSON", "").strip()
            or os.getenv("RERANKER_JINA_API_KEY_PRIMARY", "").strip()
            or os.getenv("EMBEDDING_JINA_API_KEY_PRIMARY", "").strip()
        )
        if not jina_keys:
            missing.append(
                "PROVIDER_API_KEYS_JSON or RERANKER_JINA_API_KEY_PRIMARY "
                "(reranker enabled but no Jina key configured)"
            )

    if missing:
        msg = (
            f"Startup preflight failed for APP_ENV={env!r}: missing "
            f"required provider keys:\n  - " + "\n  - ".join(missing)
        )
        raise RuntimeError(msg)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Quản lý vòng đời app: khởi tạo container, cache, shutdown tài nguyên.
    @param app: FastAPI instance
    @return: async generator cho lifespan
    """
    settings = get_settings()
    setup_logging(
        level=settings.observability.log_level,
        json=settings.observability.log_format == "json",
    )

    # Fail loud BEFORE any best-effort init blocks — if we let the broad-
    # except blocks below swallow a missing-key error, the app would boot
    # in degraded state and fail only on the first user request. Dev is
    # exempt; UAT/staging/production raise immediately with all gaps
    # listed in one message.
    _check_required_provider_keys(settings)

    container = Container()
    app.state.container = container
    app.state.settings = settings

    # Init observability (OTel + Sentry — graceful fallback if packages absent)
    from ragbot.infrastructure.observability.tracing import init_tracing, init_sentry
    init_tracing(service_name="ragbot")
    init_sentry(dsn=getattr(settings.observability, "sentry_dsn", None))

    # Init resources eagerly (catch config errors at startup)
    try:
        bus = container.bus()
        await bus.ensure_streams()
    except (RedisError, OSError, asyncio.TimeoutError, RuntimeError):
        # Narrow: bus.ensure_streams calls XGROUP CREATE / XADD probe —
        # only Redis transport + asyncio-runtime classes are reachable.
        # AttributeError / TypeError surface loud (programmer bug at
        # bootstrap-wiring time, must not be swallowed).
        logger.warning("bus_init_skipped", reason="Redis Streams unavailable")

    # Refresh LiteLLM model list once (best-effort)
    try:
        llm = container.llm()
        await llm.refresh_routing()
    except Exception:  # noqa: BLE001 — startup best-effort; LiteLLM/DB blip must not block boot
        logger.warning("llm_refresh_skipped", reason="ai_models table empty or DB unavailable")

    # Prime ModelRuntimeConfig L1 cache + BotRegistry cache in parallel.
    async def _bootstrap_resolver() -> None:
        try:
            resolver = container.model_resolver()
            loaded = await resolver.bootstrap_cache()
            logger.info("model_resolver_bootstrap", entries=loaded)
        except Exception:  # noqa: BLE001 — startup cache prime best-effort; first request will lazy-load on miss
            logger.warning("model_resolver_bootstrap_skipped")

    async def _bootstrap_bot_registry() -> None:
        try:
            registry = container.bot_registry_service()
            loaded = await registry.bootstrap_cache()
            logger.info("bot_registry_bootstrap_done", entries=loaded)
        except Exception:  # noqa: BLE001 — startup cache prime best-effort; first request will lazy-load on miss
            logger.warning("bot_registry_bootstrap_skipped")

    async def _bootstrap_guardrail_rules() -> None:
        # Pre-warm platform-default moderation rules (alembic 010f). Empty
        # table logs CRITICAL inside the loader (seed migration missing)
        # — the service stays up: LocalGuardrail's static fallback still
        # honours the SSoT defaults compiled in-process.
        #
        # The loader's bootstrap() absorbs DB/Redis errors internally
        # (returns empty RuleSet, logs warning). Narrow except types
        # here cover only the remaining startup-failure surface:
        #   - AttributeError: DI container missing provider
        #   - RuntimeError: provider Singleton init fault
        try:
            loader = container.guardrail_rule_loader()
            await loader.bootstrap()
        except (AttributeError, RuntimeError):
            logger.warning("guardrail_rule_loader_bootstrap_skipped", exc_info=True)

    async def _bootstrap_tokens() -> None:
        # CRITICAL: JWT secret comes from env ONLY. No in-process fallback —
        # CLAUDE.md zero-hardcode rule + operator must provision APP_API_TOKEN
        # in every environment (dev too uses a real secret from .env).
        jwt_secret = settings.app.api_token
        if not jwt_secret:
            raise RuntimeError(
                "APP_API_TOKEN env var is required (dev/uat/staging/prod). "
                "Generate 32-byte hex via `openssl rand -hex 32` and add to .env."
            )

        try:
            from ragbot.application.services.jwt_token_service import JwtTokenService
            svc = JwtTokenService(
                session_factory=container.session_factory(),
                jwt_secret=jwt_secret,
            )
            redis = container.redis_client()
            # Auto-init owner token cho BE nếu chưa có
            await svc.ensure_owner_token(redis_client=redis)
            loaded = await svc.bootstrap_cache(redis)
            logger.info("api_tokens_bootstrap", entries=loaded)
        except Exception:  # noqa: BLE001 — startup token cache best-effort; verify path falls through to DB
            logger.warning("api_tokens_bootstrap_skipped")

    await asyncio.gather(
        _bootstrap_resolver(),
        _bootstrap_bot_registry(),
        _bootstrap_tokens(),
        _bootstrap_guardrail_rules(),
    )

    # CORS runtime-config surfacing: report any system_config override so
    # operators see a mismatch vs what the middleware actually bound at
    # create_app() time (env var APP_CORS_ALLOWED_ORIGINS). Middleware stack
    # is sealed before lifespan runs, so a DB value here is informational —
    # to change CORS at runtime the service must be restarted.
    try:
        from ragbot.application.services.system_config_service import SystemConfigService
        _cors_svc = SystemConfigService(
            session_factory=container.session_factory(),
            redis_client=container.redis_client(),
        )
        _db_cors_raw = await _cors_svc.get("cors_allowed_origins", None)
        if _db_cors_raw is not None and _db_cors_raw != settings.app.cors_allowed_origins:
            logger.warning(
                "cors_config_mismatch",
                env_value=settings.app.cors_allowed_origins,
                db_value=_db_cors_raw,
                note="system_config override takes effect only after restart",
            )
    except Exception:  # noqa: BLE001 — non-fatal diagnostic
        logger.debug("cors_db_check_skipped")

    # Reranker preflight: fail-loud at startup when reranker is enabled but the
    # configured provider is unusable (missing API key, etc). Silent fallback to
    # RRF was masking config drift — operators saw `top_score` derived from RRF
    # ranks rather than a real cross-encoder score.
    #
    # Contract:
    #   reranker_enabled = false  → silent OK (intentional: pure RRF mode)
    #   reranker_enabled = true   → provider must be usable, else RuntimeError
    _rr_provider: str | None = None
    try:
        from ragbot.application.services.system_config_service import SystemConfigService
        _rr_svc = SystemConfigService(
            session_factory=container.session_factory(),
            redis_client=container.redis_client(),
        )
        _rr_enabled = await _rr_svc.get("reranker_enabled", True)
        _rr_provider = await _rr_svc.get("reranker_provider", None)
    except Exception:  # noqa: BLE001 — DB unavailable, defer to settings
        _rr_enabled = settings.reranker.enabled
    _check_reranker_preflight(
        enabled=bool(_rr_enabled),
        model_name=settings.reranker.model_name,
        provider=_rr_provider,
    )

    # Fire-and-forget warmup probe so the first real request does
    # not pay cold-start tax. Best-effort: never raises, never blocks
    # readiness. See ``infrastructure/observability/warmup.py`` for design
    # notes (probe-text vs prompt content, DI-only model resolution).
    from ragbot.infrastructure.observability.warmup import run_warmup
    asyncio.create_task(run_warmup(container))

    # Single-process consolidation (case study 2026-05-16). When the
    # operator flag ``APP_EMBED_WORKERS_ENABLED`` is true (default), spawn
    # the document_consumer + outbox_publisher as background asyncio
    # tasks here so ``systemctl restart ragbot-api`` covers the whole
    # ingest pipeline. DevOps that want to horizontally scale workers
    # (separate K8s deploys, ECS tasks) flip the flag false and run
    # ``python -m ragbot.interfaces.workers.{document_worker,outbox_publisher}``
    # as standalone processes — both entry points remain intact.
    embedded_worker_tasks: list[asyncio.Task[None]] = []
    if settings.app.embed_workers_enabled:
        from ragbot.interfaces.http.embedded_workers import (  # noqa: PLC0415
            start_embedded_workers,
        )
        embedded_worker_tasks = start_embedded_workers(container)
        logger.info(
            "embedded_workers_spawned",
            count=len(embedded_worker_tasks),
            names=[t.get_name() for t in embedded_worker_tasks],
        )

    logger.info("ragbot.startup_complete", env=settings.app.env, version=settings.app.version)

    try:
        yield
    finally:
        # Shutdown — drain best-effort
        if embedded_worker_tasks:
            from ragbot.interfaces.http.embedded_workers import (  # noqa: PLC0415
                stop_embedded_workers,
            )
            try:
                await stop_embedded_workers(embedded_worker_tasks)
            except (OSError, RuntimeError, asyncio.TimeoutError):
                # Narrow shutdown drain — the inner supervisor already
                # absorbs RedisError / CancelledError on its side; only
                # asyncio runtime / OS-level errors can reach here.
                logger.warning("embedded_workers_teardown_failed", exc_info=True)
        try:
            await container.bus().close()
        except Exception:  # noqa: BLE001 — shutdown drain; isolate any failure type so subsequent disposers still run
            pass
        try:
            await container.cache().close()
        except Exception:  # noqa: BLE001 — shutdown drain; isolate any failure type so subsequent disposers still run
            pass
        try:
            await container.db_engine().dispose()
        except Exception:  # noqa: BLE001 — shutdown drain; isolate any failure type so process exits cleanly
            pass
        logger.info("ragbot.shutdown_complete")


def create_app() -> FastAPI:
    """Tạo FastAPI app với middleware, router, static files.
    @return: FastAPI instance đã cấu hình đầy đủ
    """
    settings = get_settings()
    app = FastAPI(
        title="RAGbot",
        version=settings.app.version,
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    # Middleware stack — Starlette wraps in REVERSE insertion order so the
    # LAST add is outermost (runs first on request). Target request flow:
    #   BodySize → CORS → GZip → TraceContext → TenantContext
    #     → SchemaVersion → Logging → route
    # So insertion order is the inverse below: Logging first, BodySize last.
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(SchemaVersionMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(TraceContextMiddleware)
    app.add_middleware(GZipMiddleware, minimum_size=GZIP_MINIMUM_SIZE)
    # Security response headers — Y4 (2026-05-01). Wires AFTER GZip so the
    # headers attach to the *final* response (compressed or not). HSTS is
    # opt-in (env: APP_SECURITY_HSTS_ENABLED) — never advertise HSTS over
    # plain HTTP or browsers refuse plain dev traffic for the directive's
    # TTL.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_enabled=DEFAULT_SECURITY_HEADERS_HSTS_ENABLED,
    )

    # Sliding-window per-token rate limit (Layer-2). Sits between
    # CORS-per-tenant (outer) and SecurityHeaders (inner). Coarse Layer-1
    # tenant_rate_limiter remains in front of route handlers via
    # TenantContextMiddleware; this layer adds per-token + per-endpoint
    # back-pressure with W3C ``X-RateLimit-*`` response headers. The
    # limiter is resolved per-request from ``request.app.state.container.
    # rate_limiter()`` (Container provider, lifespan-bound) so tests with
    # a noop lifespan can supply a mock or skip enforcement entirely.
    app.add_middleware(
        SlidingRateLimitMiddleware,
        limiter=None,  # lazy — resolved from container on each request
        fail_mode=DEFAULT_RL_FAIL_MODE,
        emit_headers=DEFAULT_RL_EMIT_HEADERS,
    )

    # Per-4-key bot rate limit (multi-tenant fairness — 2026-05-16).
    # Composes WITH the per-token sliding-window layer above: both gates
    # must pass before a request reaches the route. Scope key is the
    # internal 4-key tuple ``(record_tenant_id, workspace_id, bot_id,
    # channel_type)`` so two tenants × workspaces that both name a bot
    # "support" on channel "web" each get an independent bucket.
    # Partners read their current consumption via
    # ``GET /api/ragbot/admin/rate-limits/inspect`` (route added below).
    from ragbot.interfaces.http.middlewares.bot_rate_limit import (  # noqa: PLC0415
        BotRateLimitMiddleware,
    )
    app.add_middleware(BotRateLimitMiddleware)

    # Per-source-tag ingest rate limit (W5 case study — 2026-05-18).
    # Composes IN FRONT of BotRateLimitMiddleware (later add = outer in
    # starlette reverse-insertion ordering). Scope key is
    # ``(record_tenant_id, source_tag)`` restricted to the documents
    # ingest path — KMS-A flooding cannot starve KMS-B inside the same
    # tenant. Off-scope paths bypass cheaply (single startswith check).
    # Degrades open on Redis transport error so source-RL outage cannot
    # become a DoS amplifier.
    from ragbot.interfaces.http.middlewares.source_rate_limit import (  # noqa: PLC0415
        SourceRateLimitMiddleware,
    )
    app.add_middleware(SourceRateLimitMiddleware)

    # Per-tenant CORS strict whitelist. Replaces the legacy global
    # ``starlette.middleware.cors.CORSMiddleware``. Reads
    # ``tenants.allowed_origins`` via TenantConfigCache for tenant-scoped
    # paths; falls back to the env-driven global list for pre-auth paths
    # (``/health``, ``/metrics``, Swagger). Preflight OPTIONS also matches
    # against the global list because the browser strips JWT
    # from preflight (cannot key per-tenant before the actual request).
    _global_origins = parse_global_origins(settings.app.cors_allowed_origins)
    app.add_middleware(
        CORSPerTenantMiddleware,
        global_origins=_global_origins,
        max_age_s=DEFAULT_CORS_PREFLIGHT_MAX_AGE_S,
    )
    logger.info(
        "cors_per_tenant_enabled",
        global_origins=list(_global_origins),
        note="per-tenant whitelist via tenants.allowed_origins",
    )

    # BodySizeLimit — reject oversized payloads before auth/body read.
    app.add_middleware(BodySizeLimitMiddleware)

    # AntiAbuse runs ahead of auth so failed-auth attempts feed
    # the per-IP fail-ban counter, but inside IP rate limit so flooders
    # are short-circuited at the cheapest layer.
    if settings.app.anti_abuse_enabled:
        from ragbot.shared.hmac_signing import (  # noqa: F401 — touch-import surfaces module-load errors at boot
            compute_signature,
        )
        from ragbot.interfaces.http.middlewares.anti_abuse import (
            AntiAbuseMiddleware,
        )
        _trusted_proxies = frozenset(
            p.strip() for p in (settings.app.trusted_proxies or "").split(",") if p.strip()
        )
        _ip_allowlist = frozenset(
            p.strip() for p in (settings.app.ip_allowlist or "").split(",") if p.strip()
        )
        _ua_overrides = tuple(
            p.strip().lower() for p in (settings.app.ua_denylist or "").split(",") if p.strip()
        )
        from ragbot.shared.constants import DEFAULT_UA_DENYLIST_PATTERNS
        _ua_patterns = _ua_overrides or DEFAULT_UA_DENYLIST_PATTERNS
        _api_key_hashes = frozenset(
            p.strip().lower()
            for p in (settings.app.programmatic_api_keys or "").split(",")
            if p.strip()
        )
        app.add_middleware(
            AntiAbuseMiddleware,
            ua_denylist=_ua_patterns,
            programmatic_key_hashes=_api_key_hashes,
            trusted_proxies=_trusted_proxies,
            ip_allowlist=_ip_allowlist,
            enabled=True,
        )

    # IP-based pre-auth rate limit. Added LAST → wraps OUTERMOST →
    # runs FIRST per request, before any auth or body parse. Fail-CLOSED
    # on Redis outage so anti-spray cannot become a DoS amplifier.
    #
    # Combined per-IP + per-token gate (SEC-INJ-8): this layer caps every
    # caller from one source IP regardless of how many JWT tokens they
    # rotate through. The inner SlidingRateLimitMiddleware caps per-token
    # at the per-endpoint policy. Both gates must pass before the request
    # reaches a route handler. Cap is sized so ~5 concurrent users at the
    # 60/min chat policy stay below the IP ceiling
    # (DEFAULT_RATE_LIMIT_PER_IP_PER_MIN = 5 × 60).
    if settings.app.ip_rate_limit_enabled:
        from ragbot.interfaces.http.middlewares.ip_rate_limit import (
            IpRateLimitMiddleware,
        )
        _trusted_proxies_rl = frozenset(
            p.strip() for p in (settings.app.trusted_proxies or "").split(",") if p.strip()
        )
        _ip_allowlist_rl = frozenset(
            p.strip() for p in (settings.app.ip_allowlist or "").split(",") if p.strip()
        )
        app.add_middleware(
            IpRateLimitMiddleware,
            per_min=DEFAULT_RATE_LIMIT_PER_IP_PER_MIN,
            window_s=DEFAULT_RL_IP_WINDOW_S,
            trusted_proxies=_trusted_proxies_rl,
            ip_allowlist=_ip_allowlist_rl,
            enabled=True,
        )

    register_exception_handlers(app)

    # Static files (JS/CSS for demo pages)
    _static_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / "static"
    if _static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    # /favicon.ico — public, no-auth. Browsers fetch this on every page
    # load; without an explicit route the JWT middleware bounces it as
    # 401 which the anti-abuse counter then treats as auth-fail and bans
    # the operator IP. Serve the static file if present, else 204.
    _favicon = _static_dir / "favicon.ico" if _static_dir.is_dir() else None

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        if _favicon and _favicon.is_file():
            return FileResponse(str(_favicon), media_type="image/x-icon")
        return Response(status_code=204)

    # Routers
    app.include_router(api_router)

    # Metrics endpoint — Bearer token guard. The Prometheus path is listed
    # in TenantContextMiddleware._PUBLIC_PATHS so the JWT middleware does
    # not intercept it; that means scraper traffic also bypasses auth. To
    # close the public-/metrics leak (master report Finding #3) we enforce
    # an operator-issued Bearer token here when the env var is set. When
    # unset (dev), the route stays open for backward compatibility — local
    # uvicorn + tests don't need to carry a header.
    @app.get(settings.observability.prometheus_path, include_in_schema=False)
    async def metrics(request: Request) -> Response:
        expected = os.environ.get(RAGBOT_METRICS_AUTH_TOKEN_ENV)
        if expected:
            auth = request.headers.get("Authorization", "")
            presented = (
                auth.removeprefix("Bearer ").strip()
                if auth.startswith("Bearer ")
                else ""
            )
            if not presented or presented != expected:
                raise HTTPException(
                    status_code=401, detail="metrics auth required",
                )
        body, ct = setup_metrics_app()
        return Response(content=body, media_type=ct)

    return app


app = create_app()


__all__ = ["app", "create_app", "lifespan"]
