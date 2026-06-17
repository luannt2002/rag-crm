"""Admin analytics routes — multi-tenant control plane (T1, S5).

4 read-only GET endpoints surface the per-bot operational signals an
operator needs to manage many tenants + verticals from one console:

  * ``GET /analytics/bots/pass-rate``  — quality (PASS / REFUSE / HALLU)
  * ``GET /analytics/bots/cost``       — cost (total + avg/turn)
  * ``GET /analytics/bots/latency``    — performance (p50/p95/p99/max)
  * ``GET /analytics/bots/{record_bot_id}/drift`` — drift severity
    rolling current N days vs prior N days.

Identity contract (CLAUDE.md 3-key strict): every endpoint resolves
``record_tenant_id`` from JWT (``request.state.record_tenant_id``) — NEVER from
the query / body. Missing tenant context → 401. Insufficient role → 403
via :func:`require_min_level`.

Domain-neutral: the service computes counts + ratios + percentiles only;
no industry / brand literals.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from ragbot.application.services.tenant_analytics_service import (
    DEFAULT_ANALYTICS_DEFAULT_WINDOW_DAYS,
    TenantAnalyticsService,
    TenantSummary,
    WorkspaceSummary,
)
from ragbot.shared.constants import (
    DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
    DEFAULT_ANALYTICS_ALL_TENANTS_WINDOW_DAYS,
    DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS,
    DEFAULT_FEEDBACK_AGGREGATE_DAYS,
    DEFAULT_SUPER_ADMIN_LEVEL,
    MAX_ANALYTICS_ALL_TENANTS_LIMIT,
    MAX_ANALYTICS_WORKSPACE_RESULTS,
    MAX_FEEDBACK_AGGREGATE_DAYS,
)
from ragbot.shared.rbac import check_min_level, require_min_level

router = APIRouter(tags=["admin/analytics"])

# Operator level required to read tenant analytics. Level 60 = admin
# (single-tenant scope). Cross-tenant reads are blocked at the data
# layer — every query is filtered by JWT tenant.
_MIN_OPERATOR_LEVEL = 60
# Super-admin level required for the cross-tenant rollup endpoint
# (``GET /admin/analytics/all-tenants``). Level 100 = platform owner —
# the ONLY role permitted to read aggregated numbers across tenants.
_MIN_SUPER_ADMIN_LEVEL = 100


def _require_super_admin(request: Request) -> None:
    """Enforce platform super-admin (level 100) — cross-tenant gate.

    Used ONLY by ``GET /admin/analytics/all-tenants``: that endpoint
    aggregates over every tenant in ``request_logs``, so no per-request
    JWT tenant scope applies. Anything below level 100 → 403.
    """
    require_min_level(request, _MIN_SUPER_ADMIN_LEVEL)


def _require_admin_and_tenant(request: Request) -> UUID:
    """Enforce min-level + return tenant UUID from JWT.

    Mirrors :func:`admin_audit._require_admin_and_tenant`. Centralised
    so one bug here cannot leak cross-tenant numbers.
    """
    require_min_level(request, _MIN_OPERATOR_LEVEL)
    record_tenant = getattr(request.state, "record_tenant_id", None)
    if record_tenant is None:
        raise HTTPException(status_code=401, detail="Missing tenant context")
    return record_tenant


def _resolve_tenant_scope(
    request: Request,
    *,
    record_tenant_id_param: UUID | None,
) -> UUID:
    """Pick the effective tenant for tenant-scoped admin analytics.

    Two-tier RBAC:
      * ``super_admin`` (level >= 100) MAY target any tenant via the
        ``record_tenant_id`` query parameter; if omitted, JWT tenant
        is used so the same endpoint works for a super-admin browsing
        their own tenant.
      * Below super-admin (admin level 60–99) MUST stay within JWT
        tenant. Supplying a different ``record_tenant_id`` than the
        JWT claim → 403 (defence vs caller-spoofed cross-tenant probe).

    Lower than ``_MIN_OPERATOR_LEVEL`` is rejected by the caller via
    :func:`_require_admin_and_tenant`, so this helper assumes >=60.
    """
    jwt_tenant = _require_admin_and_tenant(request)
    is_super = check_min_level(request, DEFAULT_SUPER_ADMIN_LEVEL)
    if record_tenant_id_param is None:
        return jwt_tenant
    if is_super:
        return record_tenant_id_param
    if record_tenant_id_param != jwt_tenant:
        raise HTTPException(
            status_code=403,
            detail="Cross-tenant query requires super_admin",
        )
    return jwt_tenant


def _resolve_window(
    *,
    since: datetime | None,
    until: datetime | None,
    default_window_days: int = DEFAULT_ANALYTICS_DEFAULT_WINDOW_DAYS,
) -> tuple[datetime, datetime]:
    """Default-fill the [since, until] window if caller omitted either.

    Defaults: last ``default_window_days`` days (per-endpoint override
    keeps the cross-tenant endpoint independent of the per-bot window),
    ending at server clock now (UTC).
    """
    from datetime import timezone

    if until is None:
        until = datetime.now(tz=timezone.utc)
    if since is None:
        since = until - timedelta(days=default_window_days)
    if since > until:
        raise HTTPException(
            status_code=400,
            detail="`since` must be earlier than `until`",
        )
    return since, until


def _build_service(request: Request) -> TenantAnalyticsService:
    """Construct the service from the DI container without touching
    bootstrap.py (S1 holds bootstrap edits this branch)."""
    container = request.app.state.container
    session_factory = container.session_factory()
    return TenantAnalyticsService(
        session_factory=session_factory,
        request_log_repo=container.request_log_repo(),
        message_repo=container.message_repo(),
    )


def _serialize_per_bot(stats: dict[UUID | None, Any]) -> dict[str, Any]:
    """Normalise dict[UUID|None, dataclass] → dict[str, dict] for JSON.

    The ``None`` bucket (logs missing record_bot_id) is keyed as the
    literal string ``"unbound"`` so JSON consumers never see ``null`` as
    a key.
    """
    out: dict[str, Any] = {}
    for bot_id, stat in stats.items():
        key = str(bot_id) if bot_id is not None else "unbound"
        # dataclass with slots → use vars() / asdict-equivalent
        out[key] = {
            slot: _coerce(getattr(stat, slot))
            for slot in stat.__slots__  # type: ignore[attr-defined]
        }
    return out


def _coerce(value: Any) -> Any:
    """JSON-friendly coercion: UUID → str; everything else passthrough."""
    if isinstance(value, UUID):
        return str(value)
    return value


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------
@router.get("/analytics/bots/pass-rate")
async def analytics_pass_rate(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> dict[str, object]:
    """Per-bot PASS rate / REFUSE / HALLU counts over [since, until]."""
    tenant_id = _require_admin_and_tenant(request)
    s, u = _resolve_window(since=since, until=until)
    svc = _build_service(request)
    stats = await svc.pass_rate_per_bot(
        record_tenant_id=tenant_id, since=s, until=u,
    )
    return {
        "ok": True,
        "data": _serialize_per_bot(stats),
        "window": {"since": s.isoformat(), "until": u.isoformat()},
    }


@router.get("/analytics/bots/cost")
async def analytics_cost(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> dict[str, object]:
    """Per-bot cost summary over [since, until]."""
    tenant_id = _require_admin_and_tenant(request)
    s, u = _resolve_window(since=since, until=until)
    svc = _build_service(request)
    stats = await svc.cost_per_bot(
        record_tenant_id=tenant_id, since=s, until=u,
    )
    return {
        "ok": True,
        "data": _serialize_per_bot(stats),
        "window": {"since": s.isoformat(), "until": u.isoformat()},
    }


@router.get("/analytics/bots/latency")
async def analytics_latency(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> dict[str, object]:
    """Per-bot latency percentiles over [since, until]."""
    tenant_id = _require_admin_and_tenant(request)
    s, u = _resolve_window(since=since, until=until)
    svc = _build_service(request)
    stats = await svc.latency_per_bot(
        record_tenant_id=tenant_id, since=s, until=u,
    )
    return {
        "ok": True,
        "data": _serialize_per_bot(stats),
        "window": {"since": s.isoformat(), "until": u.isoformat()},
    }


@router.get("/analytics/bots/{record_bot_id}/drift")
async def analytics_drift(
    request: Request,
    record_bot_id: UUID,
    window_days: int = Query(
        default=DEFAULT_ANALYTICS_DEFAULT_WINDOW_DAYS, ge=1, le=90,
    ),
) -> dict[str, object]:
    """Per-bot drift signal over [now-2N, now-N) vs [now-N, now]."""
    tenant_id = _require_admin_and_tenant(request)
    svc = _build_service(request)
    signal = await svc.drift_signal(
        record_tenant_id=tenant_id,
        record_bot_id=record_bot_id,
        window_days=window_days,
    )
    return {
        "ok": True,
        "data": {
            "record_bot_id": str(signal.record_bot_id),
            "pass_rate_delta_pp": signal.pass_rate_delta_pp,
            "cost_delta_pct": signal.cost_delta_pct,
            "p95_delta_ms": signal.p95_delta_ms,
            "drift_severity": signal.drift_severity,
        },
        "window_days": window_days,
    }


@router.get("/analytics/bots/{record_bot_id}/feedback")
async def analytics_feedback_aggregate(
    request: Request,
    record_bot_id: UUID,
    since_days: int = Query(
        default=DEFAULT_FEEDBACK_AGGREGATE_DAYS, ge=1, le=MAX_FEEDBACK_AGGREGATE_DAYS,
    ),
) -> dict[str, object]:
    """Per-bot thumbs up/down aggregate over the last ``since_days`` days.

    D12 read path: closes the feedback loop so thumbs verdicts written to
    ``message_feedback`` (via ``POST /feedback/thumbs``) become visible to
    the bot owner instead of dying unread. RLS-scoped — the repo opens a
    ``session_with_tenant`` so cross-tenant reads return zero rows.

    Returns the shape-stable ``{"thumbs_up": int, "thumbs_down": int}``
    so dashboard renderers never branch on a missing key.
    """
    tenant_id = _require_admin_and_tenant(request)
    repo = request.app.state.container.message_feedback_repo()
    counts = await repo.aggregate_per_bot(
        record_tenant_id=tenant_id,
        record_bot_id=record_bot_id,
        since_days=since_days,
    )
    return {
        "ok": True,
        "data": counts,
        "record_bot_id": str(record_bot_id),
        "since_days": since_days,
    }


@router.get("/analytics/usage")
async def analytics_usage_multi_bot(
    request: Request,
    bot_ids: str | None = Query(
        default=None,
        description=(
            "Comma-separated bot_id slugs. NULL → all bots in tenant's "
            "current workspace. Multi-bot filter for client dashboard."
        ),
    ),
    channels: str | None = Query(
        default=None,
        description="Comma-separated channel_types. NULL → all channels.",
    ),
    workspace_id: str | None = Query(
        default=None,
        description=(
            "Optional workspace slug filter. NULL → all workspaces in "
            "tenant. Slug `^[a-zA-Z0-9-]+$`, 1-64 char (validated)."
        ),
    ),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> dict[str, object]:
    """Usage + cost metadata cho 1-N bot trong cùng tenant (+ optional workspace).

    Tenant tự gọi endpoint này để monitoring cost từ client. JWT tenant
    scope enforced — KHÔNG được query bot của tenant khác.

    Returns per-bot summary: total_requests, total_cost_usd, avg/p95
    duration_ms, total_tokens, first/last_seen_at. Plus aggregate totals.

    Filter chain:
      1. record_tenant_id = JWT (mandatory, single-tenant scope)
      2. workspace_id LIKE filter (optional; default all WS)
      3. bot_id IN (...) filter (optional; default all bots)
      4. channel_type IN (...) filter (optional; default all channels)
      5. [since, until] window (default last 7 days)
    """
    tenant_id = _require_admin_and_tenant(request)
    s, u = _resolve_window(since=since, until=until)

    # Parse comma-separated filters → list[str] | None
    bot_id_list: list[str] | None = None
    if bot_ids:
        bot_id_list = [b.strip() for b in bot_ids.split(",") if b.strip()]
        if not bot_id_list:
            bot_id_list = None

    channel_list: list[str] | None = None
    if channels:
        channel_list = [c.strip() for c in channels.split(",") if c.strip()]
        if not channel_list:
            channel_list = None

    # Validate workspace slug if provided (mirror IDENTITY_RULE_DETAIL.md)
    if workspace_id is not None:
        import re as _re  # noqa: PLC0415
        if not _re.match(r"^[a-zA-Z0-9-]+$", workspace_id) or not (1 <= len(workspace_id) <= 64):
            raise HTTPException(
                status_code=422,
                detail="workspace_id invalid format (slug ^[a-zA-Z0-9-]+$, 1-64 char)",
            )

    svc = _build_service(request)
    usage = await svc.usage_multi_bot(
        record_tenant_id=tenant_id,
        workspace_id=workspace_id,
        bot_ids=bot_id_list,
        channel_types=channel_list,
        since=s,
        until=u,
    )
    return {
        "ok": True,
        "data": usage["per_bot"],
        "totals": usage["totals"],
        "filter": {
            "record_tenant_id": str(tenant_id),
            "workspace_id": workspace_id,
            "bot_ids": bot_id_list,
            "channel_types": channel_list,
        },
        "window": {"since": s.isoformat(), "until": u.isoformat()},
    }


def _serialize_tenant_summary(s: TenantSummary) -> dict[str, Any]:
    """JSON-friendly view of one :class:`TenantSummary` row."""
    return {
        "record_tenant_id": str(s.record_tenant_id),
        "workspace_count": s.workspace_count,
        "bot_count": s.bot_count,
        "total_requests": s.total_requests,
        "total_cost_usd": s.total_cost_usd,
        "avg_duration_ms": s.avg_duration_ms,
        "p95_duration_ms": s.p95_duration_ms,
        "total_tokens": s.total_tokens,
        "first_seen_at": (
            s.first_seen_at.isoformat() if s.first_seen_at is not None else None
        ),
        "last_seen_at": (
            s.last_seen_at.isoformat() if s.last_seen_at is not None else None
        ),
    }


@router.get("/admin/analytics/all-tenants")
async def analytics_all_tenants(
    request: Request,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(
        default=DEFAULT_ANALYTICS_ALL_TENANTS_LIMIT,
        ge=1,
        le=MAX_ANALYTICS_ALL_TENANTS_LIMIT,
    ),
    sort_by: str = Query(
        default="total_cost",
        pattern="^(total_cost|total_requests|avg_latency)$",
    ),
) -> dict[str, object]:
    """Cross-tenant rollup of ``request_logs`` — super-admin only.

    Aggregates every tenant's traffic in [since, until] into one row
    each, ordered by ``sort_by`` descending, capped at ``limit``. RBAC
    is enforced at level 100 — there is no per-request JWT tenant scope
    here by design (this is THE endpoint that crosses tenants).
    """
    _require_super_admin(request)
    s, u = _resolve_window(
        since=since,
        until=until,
        default_window_days=DEFAULT_ANALYTICS_ALL_TENANTS_WINDOW_DAYS,
    )
    svc = _build_service(request)
    try:
        rows = await svc.all_tenants_summary(
            since=s, until=u, limit=limit, sort_by=sort_by,
        )
    except ValueError as exc:
        # Service-level validation (unknown sort_by or non-positive
        # limit). Pydantic Query() already filters at the boundary, but
        # keep the narrow except for defence-in-depth.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    grand_total_cost = sum(r.total_cost_usd for r in rows)
    grand_total_requests = sum(r.total_requests for r in rows)
    return {
        "ok": True,
        "data": [_serialize_tenant_summary(r) for r in rows],
        "window": {"since": s.isoformat(), "until": u.isoformat()},
        "totals": {
            "total_tenants": len(rows),
            "grand_total_cost_usd": grand_total_cost,
            "grand_total_requests": grand_total_requests,
        },
    }


def _serialize_workspace_summary(s: WorkspaceSummary) -> dict[str, Any]:
    """JSON-friendly view of one :class:`WorkspaceSummary` row."""
    return {
        "record_tenant_id": str(s.record_tenant_id),
        "workspace_id": s.workspace_id,
        "bot_count": s.bot_count,
        "total_requests": s.total_requests,
        "total_cost_usd": s.total_cost_usd,
        "avg_duration_ms": s.avg_duration_ms,
        "p95_duration_ms": s.p95_duration_ms,
        "total_tokens": s.total_tokens,
        "first_seen_at": (
            s.first_seen_at.isoformat() if s.first_seen_at is not None else None
        ),
        "last_seen_at": (
            s.last_seen_at.isoformat() if s.last_seen_at is not None else None
        ),
    }


@router.get("/analytics/workspace-aggregate")
async def analytics_workspace_aggregate(
    request: Request,
    record_tenant_id: UUID | None = Query(
        default=None,
        description=(
            "Target tenant UUID. Required for super_admin querying a "
            "tenant other than their own; ignored / forced to match JWT "
            "for tenant-admin (mismatch → 403)."
        ),
    ),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    sort_by: str = Query(
        default="total_cost",
        pattern="^(total_cost|total_requests|avg_latency)$",
    ),
) -> dict[str, object]:
    """Per-workspace rollup of ``request_logs`` within one tenant.

    Groups one tenant's ``request_logs`` by ``workspace_id`` in
    [since, until] and orders by ``sort_by`` descending, capped at
    :data:`MAX_ANALYTICS_WORKSPACE_RESULTS`.

    RBAC:
      * super_admin (level 100): MAY pass ``record_tenant_id`` to
        target any tenant; omitted → uses JWT tenant.
      * admin / tenant_admin (level 60-99): MUST match JWT tenant.
        Mismatch → 403; below level 60 → 403 via
        :func:`_require_admin_and_tenant`.

    Each row carries ``bot_count`` (DISTINCT ``record_bot_id``),
    request totals, cost in USD, avg/p95 ``duration_ms`` and the
    first/last timestamps observed in window.
    """
    effective_tenant = _resolve_tenant_scope(
        request, record_tenant_id_param=record_tenant_id,
    )
    s, u = _resolve_window(
        since=since,
        until=until,
        default_window_days=DEFAULT_ANALYTICS_WORKSPACE_WINDOW_DAYS,
    )
    svc = _build_service(request)
    try:
        rows = await svc.workspace_aggregate(
            record_tenant_id=effective_tenant,
            since=s,
            until=u,
            sort_by=sort_by,
            limit=MAX_ANALYTICS_WORKSPACE_RESULTS,
        )
    except ValueError as exc:
        # Service-level validation (unknown sort_by or non-positive
        # limit). Pydantic Query() already filters sort_by at the
        # boundary, but keep the narrow except for defence-in-depth.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "ok": True,
        "data": [_serialize_workspace_summary(r) for r in rows],
        "window": {"since": s.isoformat(), "until": u.isoformat()},
    }


__all__ = ["router"]
