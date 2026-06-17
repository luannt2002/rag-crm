"""Compose all routers."""

from __future__ import annotations

from fastapi import APIRouter

from ragbot.config.settings import get_settings
from ragbot.interfaces.http.routes import (
    admin_ai,
    admin_analytics,
    admin_audit,
    admin_bots,
    admin_documents_debug,
    admin_gdpr,
    admin_metrics,
    admin_notify,
    admin_policy,
    admin_rate_limits,
    admin_refuse_suggestions,
    admin_tenant_policy,
    admin_tenants,
    admin_webhooks,
    chat,
    chat_async,
    chat_stream,
    crm,
    documents,
    documents_stream_upload,
    feedback,
    health,
    health_models,
    honeypot,
    jobs,
    sync,
    test_chat,
)

BASE = get_settings().app.api_base_path

router = APIRouter()
router.include_router(health.router)
# Health/models — public path (matches /health policy). Verifies AI provider
# connections (embedder/reranker/LLM). Used by CI/CD gates + post-deploy.
router.include_router(health_models.router)
# Passive honey-pot routes. Logged + add IP to suspicious set.
router.include_router(honeypot.router)
router.include_router(chat.router, prefix=BASE)
# B.6 — production SSE streaming variant of /chat. Same body schema, same
# 3-key resolve + RBAC, returns text/event-stream with per-token deltas.
router.include_router(chat_stream.router, prefix=BASE)
# Thumbs feedback analytics — separate path (/feedback/thumbs) from the
# legacy /feedback rating endpoint exposed by chat.router. The two coexist
# (Wire Option A): chat.feedback writes request_logs (per-request inline);
# feedback.thumbs writes message_feedback (training-loop analytics).
router.include_router(feedback.router, prefix=BASE)
router.include_router(documents.router, prefix=BASE)
# WB-2 P1-5 — streaming upload for >50MB binaries.  Bypasses the JSON
# /documents/create body path; multipart form data is read chunk-by-chunk
# and persisted to a temp file before worker hand-off (caps resident
# memory at DEFAULT_UPLOAD_STREAM_CHUNK_SIZE regardless of body size).
router.include_router(documents_stream_upload.router, prefix=BASE)
router.include_router(jobs.router, prefix=BASE)
router.include_router(sync.router, prefix=BASE)
# CRM analytics read-layer — operator console over request_logs/request_steps.
# Mounted at {BASE}/crm; RBAC + tenant-scope enforced inside each endpoint.
router.include_router(crm.router, prefix=BASE)
router.include_router(admin_ai.router, prefix=f"{BASE}/admin")
router.include_router(admin_metrics.router, prefix=f"{BASE}/admin")
router.include_router(admin_policy.router, prefix=f"{BASE}/admin")
router.include_router(admin_gdpr.router, prefix=f"{BASE}/admin")
router.include_router(admin_audit.router, prefix=f"{BASE}/admin")
router.include_router(admin_bots.router, prefix=f"{BASE}/admin")
router.include_router(admin_tenant_policy.router, prefix=f"{BASE}/admin")
# Super-admin tenant CRUD (POST/GET/LIST/PATCH/DELETE).
router.include_router(admin_tenants.router, prefix=f"{BASE}/admin")
router.include_router(admin_analytics.router, prefix=f"{BASE}/admin")
# Notify channel — webhook target for error alerts (admin-only mutate).
router.include_router(admin_notify.router, prefix=f"{BASE}/admin")
# Webhook HMAC secret rotation (WA-6 security) — RBAC level 80.
router.include_router(admin_webhooks.router, prefix=f"{BASE}/admin")
# Partner-facing rate-limit inspection (2026-05-16 multi-tenant fairness).
# Lists 4-key bot identities owned by caller tenant + current consumption
# so partners can client-side throttle before hitting 429.
router.include_router(admin_rate_limits.router, prefix=BASE)
# Operator debug — download/inline view the parsed representation of a
# document. Helps debug chunker decisions (table-with-footer, heading
# hierarchy) without grepping raw_content in DB. Generic route accepts
# ``?format=md`` today; future ``?format=html|json`` reuses same path.
router.include_router(admin_documents_debug.router, prefix=BASE)
# D12 feedback-loop read path — refuse-suggestion analytics +
# FAQ-from-refuse candidate generation. The module already carries its
# own ``/admin/...`` path segments (like admin_rate_limits), so it is
# mounted on BASE rather than {BASE}/admin to avoid a doubled prefix.
# Closing-the-loop reads only: surfaces refused-intent counts +
# clustered FAQ candidates for operator review — no answer injection.
router.include_router(admin_refuse_suggestions.router, prefix=BASE)
# TEST platform — split: API routes at /api/ragbot/test/*, pages at root
router.include_router(test_chat.router, prefix=f"{BASE}/test")
# G26 — async LLM-queue chat endpoints (POST job_id + GET polling).
# Lives under /test/* alongside the existing sync /chat for parity; the
# production async path is /api/ragbot/chat in chat.router (separate flow).
router.include_router(chat_async.router, prefix=f"{BASE}/test")
router.include_router(test_chat.pages_router)

__all__ = ["router"]
