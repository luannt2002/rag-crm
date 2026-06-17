"""Health check for ALL AI providers (embedder, reranker, LLM).

GET /health/models
  → returns connect+smoke status for every active model in
    bot_model_bindings + ai_models + ai_providers.

Designed to NEVER crash:
  - Each provider check wrapped in narrow exception handler with timeout.
  - Returns 200 with status=unhealthy if a provider fails.
  - Returns 200 even if DB query fails (top-level fail-soft).
  - HALLU=0 invariant — health check uses dedicated probe inputs from
    constants (no tenant data, no PII), never writes to conversation tables.

Use cases:
  - Pre-flight check before deploy: ``curl /health/models | jq``
  - CI/CD gate: block deploy when any provider is unhealthy.
  - Post-incident verify: after rotating keys, hit this to confirm.

Strategy + DI compliance:
  - Embedder probe via ``EmbeddingPort`` (Port).
  - Reranker probe via registry ``build_reranker`` (Strategy).
  - LLM probe via ``DynamicLiteLLMRouter.complete`` (DI'd container).
  - Adding a new provider = drop a Strategy, register key — endpoint picks
    it up automatically because the loop iterates ``bot_model_bindings``.

Auth: public path (matches existing ``/health`` policy). Response carries
no secrets — only model_name, provider code, latency, status. API keys
are never logged or returned.
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, Query, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from ragbot.application.dto.ai_specs import EmbeddingSpec, LLMSpec
from ragbot.application.ports.llm_port import LLMMessage
from ragbot.infrastructure.embedding.registry import build_embedder
from ragbot.infrastructure.reranker.null_reranker import NullReranker
from ragbot.infrastructure.reranker.registry import build_reranker
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_HEALTH_MODELS_DEGRADED_LATENCY_MS,
    DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S,
    DEFAULT_HEALTH_PROBE_DOC_A,
    DEFAULT_HEALTH_PROBE_DOC_B,
    DEFAULT_HEALTH_PROBE_LLM_MAX_TOKENS,
    DEFAULT_HEALTH_PROBE_LLM_PROMPT,
    DEFAULT_HEALTH_PROBE_QUERY,
    DEFAULT_TENANT_ADMIN_LEVEL,
    MS_PER_SECOND,
)
from ragbot.shared.errors import ExternalServiceError
from ragbot.shared.types import TenantId, TraceId

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["health"])


# Status enum (string literals — kept alongside the routine that emits them).
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"
STATUS_NOT_CONFIGURED = "not_configured"
STATUS_CONFIG_DRIFT = "config_drift"

# ``purpose`` strings recognised in ``bot_model_bindings``. The query splits
# results into these three buckets. Anything else lands in ``other`` and
# emits a config_drift warning so ops sees stray rows.
_PURPOSES_KNOWN: tuple[str, ...] = ("embedding", "rerank", "llm_primary")
# Legacy column values that should map to canonical purposes. Adding a new
# alias = one entry here, no other edits needed.
_PURPOSE_ALIASES: dict[str, str] = {
    "reranker": "rerank",  # legacy — flagged in config_drift
}


# SQL: enumerate every distinct (purpose, provider, model) tuple that has at
# least one active binding plus the bot count using it. Pure read — no
# tenant filter (operator-facing platform-level health view).
_LIST_BINDINGS_SQL = text("""
    SELECT
        b.purpose                AS purpose,
        m.name                   AS model_name,
        m.embedding_dimension    AS model_dim,
        p.code                   AS provider_code,
        p.name                   AS provider_name,
        p.api_key_ref            AS api_key_ref,
        p.api_key_encrypted      AS api_key_encrypted,
        p.base_url               AS base_url,
        COUNT(DISTINCT b.record_bot_id) AS bot_count
    FROM bot_model_bindings b
    JOIN ai_models    m ON b.record_model_id    = m.id
    JOIN ai_providers p ON m.record_provider_id = p.id
    WHERE b.active     = true
      AND b.deleted_at IS NULL
      AND m.enabled    = true
      AND m.deleted_at IS NULL
      AND p.enabled    = true
      AND p.deleted_at IS NULL
    GROUP BY b.purpose, m.name, m.embedding_dimension,
             p.code, p.name, p.api_key_ref, p.api_key_encrypted, p.base_url
    ORDER BY b.purpose, p.code, m.name
""")


# ---------------------------------------------------------------------------
# Per-provider probes — each is fail-soft, returns dict (never raises).
# ---------------------------------------------------------------------------


def _classify_status(latency_ms: int, *, is_ok: bool) -> str:
    """Map (latency, success) tuple → status enum string."""
    if not is_ok:
        return STATUS_UNHEALTHY
    if latency_ms > DEFAULT_HEALTH_MODELS_DEGRADED_LATENCY_MS:
        return STATUS_DEGRADED
    return STATUS_HEALTHY


def _api_key_for(api_key_ref: str | None) -> str:
    """Resolve env-backed API key. Returns empty string on miss (caller decides)."""
    if not api_key_ref:
        return ""
    return os.getenv(api_key_ref, "")


async def _probe_embedding(
    *,
    model_name: str,
    provider_code: str,
    expected_dim: int | None,
) -> dict[str, Any]:
    """Smoke-call the embedder with the deterministic probe input.

    Wire-format parity: ``task`` mirrors the runtime query path
    (``query_graph._embed_query``) so the probe exercises the same
    asymmetric-retrieval head a live user request hits. Probing without
    ``task`` would hide a query-head outage behind a green probe.

    Uses ``build_embedder`` (Registry) so each provider gets its native
    adapter: LiteLLM for jina/openai, the ZeroEntropy direct-HTTP adapter
    for ZE models (no LiteLLM provider-prefix requirement), and the
    BKAI Vietnamese adapter for self-hosted PhoBERT.
    """
    embedder = build_embedder(provider=provider_code, model=model_name)
    start = time.monotonic()
    try:
        spec = EmbeddingSpec(
            binding_id=UUID(int=0),
            model_name=model_name,
            provider=provider_code or "unknown",
            dimension=expected_dim or 0,
            model_version="health-probe",
            task=DEFAULT_EMBEDDING_TASK_QUERY,
        )
        async with asyncio.timeout(DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S):
            vectors = await embedder.embed_batch(
                [DEFAULT_HEALTH_PROBE_QUERY],
                spec=spec,
                record_tenant_id=TenantId(UUID(int=0)),
            )
        latency_ms = int((time.monotonic() - start) * MS_PER_SECOND)
        if not vectors or not vectors[0]:
            return {
                "status": STATUS_UNHEALTHY,
                "latency_ms": latency_ms,
                "error": "empty_response",
                "dimension": None,
                "dim_match_db": None,
            }
        dim = len(vectors[0])
        dim_match = (expected_dim is None) or (dim == expected_dim)
        return {
            "status": _classify_status(latency_ms, is_ok=dim_match),
            "latency_ms": latency_ms,
            "dimension": dim,
            "dim_match_db": dim_match,
            "error": None if dim_match else f"dim_mismatch:got={dim},db={expected_dim}",
        }
    finally:
        try:
            await embedder.close()
        except (OSError, RuntimeError):
            pass


async def _probe_reranker(
    *,
    model_name: str,
    provider_code: str,
    api_key: str,
) -> dict[str, Any]:
    """Smoke-call the reranker via the same registry the runtime uses."""
    if not api_key:
        return {
            "status": STATUS_UNHEALTHY,
            "latency_ms": 0,
            "error": "missing_api_key",
            "test_query_score": None,
        }
    reranker = build_reranker(
        provider=provider_code,
        api_key=api_key,
        model=model_name,
    )
    if isinstance(reranker, NullReranker):
        return {
            "status": STATUS_CONFIG_DRIFT,
            "latency_ms": 0,
            "error": f"registry_fell_back_to_null:provider={provider_code}",
            "test_query_score": None,
        }
    start = time.monotonic()
    try:
        async with asyncio.timeout(DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S):
            results = await reranker.rerank(
                DEFAULT_HEALTH_PROBE_QUERY,
                [
                    {"content": DEFAULT_HEALTH_PROBE_DOC_A, "score": 0.0},
                    {"content": DEFAULT_HEALTH_PROBE_DOC_B, "score": 0.0},
                ],
                top_n=2,
            )
        latency_ms = int((time.monotonic() - start) * MS_PER_SECOND)
        if not results:
            return {
                "status": STATUS_UNHEALTHY,
                "latency_ms": latency_ms,
                "error": "empty_response",
                "test_query_score": None,
            }
        score = float(results[0].get("rerank_score") or results[0].get("score") or 0.0)
        return {
            "status": _classify_status(latency_ms, is_ok=True),
            "latency_ms": latency_ms,
            "test_query_score": score,
            "error": None,
        }
    finally:
        try:
            await reranker.close()
        except (OSError, RuntimeError):
            pass


async def _probe_llm(
    *,
    model_name: str,
    provider_code: str,
    llm_router: Any,
) -> dict[str, Any]:
    """Smoke-call the runtime LLM router via the public ``LLMPort.complete``.

    Wire-format parity: builds a real ``LLMSpec`` + ``[LLMMessage]`` and calls
    ``complete(messages, *, spec, record_tenant_id, trace_id)`` — the same
    Port contract every runtime caller uses (see ``application.ports.llm_port``).
    Bypassing the spec/Tenant/trace kwargs (the prior probe shape) would
    TypeError before reaching the upstream — drift that hid behind the outer
    fail-soft wrapper.
    """
    if llm_router is None:
        return {
            "status": STATUS_UNHEALTHY,
            "latency_ms": 0,
            "error": "llm_router_unavailable",
            "tokens_used": None,
        }
    full_name = (
        model_name
        if "/" in model_name
        else f"{provider_code or 'unknown'}/{model_name}"
    )
    spec = LLMSpec(
        binding_id=UUID(int=0),
        model_name=full_name,
        provider=provider_code or "unknown",
        temperature=0.0,
        max_tokens=DEFAULT_HEALTH_PROBE_LLM_MAX_TOKENS,
    )
    messages = [LLMMessage(role="user", content=DEFAULT_HEALTH_PROBE_LLM_PROMPT)]
    start = time.monotonic()
    async with asyncio.timeout(DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S):
        resp = await llm_router.complete(
            messages,
            spec=spec,
            record_tenant_id=TenantId(UUID(int=0)),
            trace_id=TraceId("health-probe"),
        )
    latency_ms = int((time.monotonic() - start) * MS_PER_SECOND)
    # ``LLMResponse`` is a frozen dataclass; tolerate mock-style dicts too so
    # the probe survives a router stubbed in CI without breaking parity.
    text_content = ""
    tokens_used: int | None = None
    content_attr = getattr(resp, "content", None)
    if content_attr is not None:
        text_content = str(content_attr)
        tokens_in = int(getattr(resp, "tokens_in", 0) or 0)
        tokens_out = int(getattr(resp, "tokens_out", 0) or 0)
        tokens_used = tokens_in + tokens_out or None
    elif isinstance(resp, dict):
        text_content = str(
            resp.get("content")
            or resp.get("text")
            or resp.get("answer")
            or "",
        )
        usage = resp.get("usage") or {}
        if isinstance(usage, dict):
            tokens_used = (
                int(usage.get("total_tokens") or 0)
                or int(usage.get("completion_tokens") or 0)
            )
    is_ok = bool(text_content)
    return {
        "status": _classify_status(latency_ms, is_ok=is_ok),
        "latency_ms": latency_ms,
        "tokens_used": tokens_used,
        "error": None if is_ok else "empty_response",
    }


# ---------------------------------------------------------------------------
# Fail-soft outer wrapper — translates any exception into a status dict.
# ---------------------------------------------------------------------------


async def _safe_probe(probe_coro_factory, *, provider_name: str) -> dict[str, Any]:
    """Run ``probe_coro_factory()`` with an outer fail-soft wrapper.

    Each probe already has its own ``asyncio.timeout``; this wrapper catches
    anything the probe raises, maps to a status dict, and never propagates.
    Per CLAUDE.md the broad-except is justified inline because health checks
    must be 100% fail-soft for production reliability.
    """
    try:
        return await probe_coro_factory()
    except asyncio.TimeoutError:
        return {
            "status": STATUS_UNHEALTHY,
            "latency_ms": int(DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S * MS_PER_SECOND),
            "error": f"timeout_after_{DEFAULT_HEALTH_MODELS_PROBE_TIMEOUT_S}s",
        }
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 401:
            return {"status": STATUS_UNHEALTHY, "error": "invalid_api_key", "latency_ms": 0}
        if code == 429:
            return {"status": STATUS_DEGRADED, "error": "rate_limited", "latency_ms": 0}
        return {"status": STATUS_UNHEALTHY, "error": f"http_{code}", "latency_ms": 0}
    except httpx.ConnectError:
        return {"status": STATUS_UNHEALTHY, "error": "connect_failed", "latency_ms": 0}
    except (KeyError, ValueError, TypeError) as exc:
        return {"status": STATUS_CONFIG_DRIFT, "error": str(exc)[:200], "latency_ms": 0}
    except ExternalServiceError as exc:
        return {"status": STATUS_UNHEALTHY, "error": str(exc)[:200], "latency_ms": 0}
    except Exception as exc:  # noqa: BLE001 — health check must be fail-soft (production reliability per CLAUDE.md)
        logger.warning(
            "health_models_unexpected_probe_error",
            provider=provider_name,
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return {
            "status": STATUS_UNHEALTHY,
            "error": f"{type(exc).__name__}:{str(exc)[:180]}",
            "latency_ms": 0,
        }


# ---------------------------------------------------------------------------
# Config drift detector — pure function on the row list.
# ---------------------------------------------------------------------------


def _detect_config_drift(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Inspect raw bindings rows for known operator-actionable misconfigs."""
    warnings: list[dict[str, str]] = []
    legacy_purpose_count = 0
    missing_dim_count = 0
    missing_key_count = 0
    for r in rows:
        purpose = r.get("purpose") or ""
        if purpose in _PURPOSE_ALIASES:
            legacy_purpose_count += 1
        if purpose == "embedding" and r.get("model_dim") in (None, 0):
            missing_dim_count += 1
        api_key_ref = r.get("api_key_ref")
        if api_key_ref and not os.getenv(api_key_ref):
            missing_key_count += 1
    if legacy_purpose_count:
        warnings.append({
            "severity": "high",
            "issue": (
                f"Found {legacy_purpose_count} binding(s) with legacy "
                f"purpose value (aliases={list(_PURPOSE_ALIASES.keys())})"
            ),
            "fix": (
                "UPDATE bot_model_bindings SET purpose='rerank' "
                "WHERE purpose='reranker'"
            ),
        })
    if missing_dim_count:
        warnings.append({
            "severity": "medium",
            "issue": (
                f"{missing_dim_count} embedding model row(s) have NULL "
                "embedding_dimension — dim-match check skipped"
            ),
            "fix": (
                "Backfill ai_models.embedding_dimension from provider "
                "documentation."
            ),
        })
    if missing_key_count:
        warnings.append({
            "severity": "high",
            "issue": (
                f"{missing_key_count} provider(s) reference an env var that "
                "is unset — probe will fail with missing_api_key"
            ),
            "fix": "Set env var named in ai_providers.api_key_ref and restart",
        })
    return warnings


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("/health/models")
async def health_models(
    request: Request,
    skip_smoke: bool = Query(
        default=False,
        description=(
            "When true, skip the live API call and return DB-bindings only. "
            "Useful for cheap CI sanity check that doesn't burn tokens."
        ),
    ),
) -> dict[str, Any]:
    """Verify all AI provider connections.

    Always returns HTTP 200 with a JSON body. The ``ok`` field plus per-model
    ``status`` tell ops the truth — never trust the HTTP code for routing.
    """
    # Topology details (model name, provider, binding id) leak deployment
    # internals; require admin-level caller.
    from ragbot.shared.rbac import require_min_level
    require_min_level(request, DEFAULT_TENANT_ADMIN_LEVEL)
    container = request.app.state.container
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    # 1. List active bindings ------------------------------------------------
    rows: list[dict[str, Any]] = []
    db_error: str | None = None
    try:
        sf = container.session_factory()
        async with sf() as session:
            result = await session.execute(_LIST_BINDINGS_SQL)
            rows = [dict(r) for r in result.mappings().all()]
    except (SQLAlchemyError, OSError) as exc:
        db_error = f"{type(exc).__name__}:{str(exc)[:200]}"
        logger.warning("health_models_db_query_failed", error=db_error, exc_info=True)
    except Exception as exc:  # noqa: BLE001 — top-level fail-soft entry per CLAUDE.md
        db_error = f"{type(exc).__name__}:{str(exc)[:200]}"
        logger.warning(
            "health_models_db_unexpected_error",
            error_type=type(exc).__name__,
            exc_info=True,
        )

    # 2. Group by purpose ---------------------------------------------------
    grouped: dict[str, list[dict[str, Any]]] = {p: [] for p in _PURPOSES_KNOWN}
    grouped["other"] = []
    for r in rows:
        raw_purpose = r.get("purpose") or ""
        purpose = _PURPOSE_ALIASES.get(raw_purpose, raw_purpose)
        bucket = purpose if purpose in _PURPOSES_KNOWN else "other"
        grouped[bucket].append(r)

    # 3. Resolve LLM router once (DI-cached) --------------------------------
    llm_router = None
    if not skip_smoke:
        try:
            llm_router = container.llm()
        except Exception as exc:  # noqa: BLE001 — fail-soft per CLAUDE.md health policy
            logger.warning(
                "health_models_llm_router_resolve_failed",
                error_type=type(exc).__name__,
                exc_info=True,
            )

    # 4. Probe each distinct (purpose, provider, model) ---------------------
    output: dict[str, list[dict[str, Any]]] = {p: [] for p in _PURPOSES_KNOWN}
    for purpose, group in grouped.items():
        if purpose not in _PURPOSES_KNOWN:
            continue
        for r in group:
            entry: dict[str, Any] = {
                "model_name": r.get("model_name"),
                "provider": r.get("provider_code") or r.get("provider_name") or "",
                "bot_count_using": int(r.get("bot_count") or 0),
                "test_query": DEFAULT_HEALTH_PROBE_QUERY,
            }
            if skip_smoke:
                entry["status"] = STATUS_NOT_CONFIGURED
                entry["error"] = "skip_smoke=true"
                entry["latency_ms"] = 0
            else:
                provider_code = entry["provider"]
                model_name = entry["model_name"] or ""
                if purpose == "embedding":
                    probe_result = await _safe_probe(
                        lambda mn=model_name, pc=provider_code, dim=r.get("model_dim"): _probe_embedding(
                            model_name=mn,
                            provider_code=pc,
                            expected_dim=dim,
                        ),
                        provider_name=f"embedding:{provider_code}",
                    )
                elif purpose == "rerank":
                    api_key = _api_key_for(r.get("api_key_ref"))
                    probe_result = await _safe_probe(
                        lambda mn=model_name, pc=provider_code, ak=api_key: _probe_reranker(
                            model_name=mn,
                            provider_code=pc,
                            api_key=ak,
                        ),
                        provider_name=f"rerank:{provider_code}",
                    )
                elif purpose == "llm_primary":
                    probe_result = await _safe_probe(
                        lambda mn=model_name, pc=provider_code: _probe_llm(
                            model_name=mn,
                            provider_code=pc,
                            llm_router=llm_router,
                        ),
                        provider_name=f"llm:{provider_code}",
                    )
                else:
                    probe_result = {
                        "status": STATUS_NOT_CONFIGURED,
                        "error": "unknown_purpose",
                        "latency_ms": 0,
                    }
                entry.update(probe_result)
            output[purpose].append(entry)

    # 5. Aggregate summary --------------------------------------------------
    flat = [m for arr in output.values() for m in arr]
    summary = {
        "total_models": len(flat),
        "healthy": sum(1 for m in flat if m.get("status") == STATUS_HEALTHY),
        "degraded": sum(1 for m in flat if m.get("status") == STATUS_DEGRADED),
        "unhealthy": sum(1 for m in flat if m.get("status") == STATUS_UNHEALTHY),
        "config_drift": sum(1 for m in flat if m.get("status") == STATUS_CONFIG_DRIFT),
        "not_configured": sum(1 for m in flat if m.get("status") == STATUS_NOT_CONFIGURED),
        "total_bot_bindings": sum(int(r.get("bot_count") or 0) for r in rows),
        "config_drift_warnings": _detect_config_drift(rows),
    }
    if db_error:
        summary["config_drift_warnings"].append({
            "severity": "high",
            "issue": f"DB query failed: {db_error}",
            "fix": "Check Postgres connectivity and the bot_model_bindings schema",
        })

    overall_ok = (
        db_error is None
        and summary["unhealthy"] == 0
        and summary["config_drift"] == 0
    )

    return {
        "ok": overall_ok,
        "models": output,
        "summary": summary,
        "timestamp": timestamp,
    }


__all__ = ["router"]
