"""Tenant analytics service — pure-application multi-tenant control plane.

Computes per-tenant, per-bot operational analytics from existing
``request_logs`` (and joins ``messages`` only when raw text needed).
NO new tables, NO LLM injection, NO answer override — read-only.

Identity contract (CLAUDE.md 3-key strict): every public method REQUIRES
``record_tenant_id`` (UUID). Cross-tenant aggregation is impossible by
construction — every query is filtered ``WHERE record_tenant_id = :tid``.

Domain-neutral: counts + percentiles + ratios; no industry / brand
literals; suitable_for / drift_severity are platform-level operator
signals, not bot behaviour.

Wired into HTTP routes via ``admin_analytics.py`` (level-60 minimum).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from ragbot.infrastructure.db.models_monitoring import RequestLogModel
from ragbot.shared.errors import TenantIsolationViolation
from ragbot.shared.types import TenantId

# ============================================================================
# Module-level constants — placed here (not shared/constants.py) because
# constants.py is held by another agent on this branch (S1 entity extractor).
# Once that lands, lift these to shared/constants.py with the same names.
# Operators can override by introducing SystemConfig-driven resolvers later.
# ============================================================================

# Pass-rate delta (percentage points) thresholds for drift severity. Two
# windows are compared: a "current" window of length N days vs the
# immediately-preceding "baseline" window of the same length. A drop of
# more than DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP points triggers MAJOR;
# above DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP triggers MINOR.
DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP: Final[float] = 10.0
DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP: Final[float] = 5.0
# Cost delta (percentage of baseline) thresholds. Increase-only signal —
# we flag a cost spike, not a cost drop.
DEFAULT_DRIFT_MAJOR_COST_DELTA_PCT: Final[float] = 25.0
DEFAULT_DRIFT_MINOR_COST_DELTA_PCT: Final[float] = 10.0
# Default analytics window (days) when caller does not specify.
DEFAULT_ANALYTICS_DEFAULT_WINDOW_DAYS: Final[int] = 7


# ============================================================================
# DTOs — slots=True for memory efficiency on multi-tenant lists
# ============================================================================
@dataclass(slots=True)
class PassRateStats:
    """Per-bot pass / refuse / hallucination breakdown."""

    record_bot_id: UUID | None
    total: int
    pass_count: int
    refuse_count: int
    hallu_count: int
    pass_rate_pct: float


@dataclass(slots=True)
class CostStats:
    """Per-bot cost aggregate."""

    record_bot_id: UUID | None
    total_cost_usd: float
    total_requests: int
    avg_cost_per_turn: float


@dataclass(slots=True)
class LatencyStats:
    """Per-bot latency percentiles (milliseconds)."""

    record_bot_id: UUID | None
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(slots=True)
class TenantSummary:
    """Per-tenant cross-workspace aggregate for super-admin analytics.

    One row = one ``record_tenant_id`` rolled up over [since, until].
    ``workspace_count`` / ``bot_count`` are DISTINCT counts within the
    window — a workspace with zero requests in the window does not
    appear. Used by ``GET /admin/analytics/all-tenants``.
    """

    record_tenant_id: UUID
    workspace_count: int
    bot_count: int
    total_requests: int
    total_cost_usd: float
    avg_duration_ms: float
    p95_duration_ms: float
    total_tokens: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None


@dataclass(slots=True)
class WorkspaceSummary:
    """Per-workspace cross-bot aggregate within a single tenant.

    One row = one ``(record_tenant_id, workspace_id)`` rolled up over
    [since, until]. ``bot_count`` is a DISTINCT count of ``record_bot_id``
    within the window. Used by
    ``GET /admin/analytics/workspace-aggregate``.
    """

    record_tenant_id: UUID
    workspace_id: str
    bot_count: int
    total_requests: int
    total_cost_usd: float
    avg_duration_ms: float
    p95_duration_ms: float
    total_tokens: int
    first_seen_at: datetime | None
    last_seen_at: datetime | None


@dataclass(slots=True)
class DriftSignal:
    """Per-bot drift signal across two adjacent equal-length windows.

    Deltas are *current minus baseline* for pass-rate (negative = quality
    drop), and *(current - baseline) / baseline* for cost. Severity rolls
    up the worst dimension (pass-rate drop OR cost spike) into a single
    operator-friendly string.
    """

    record_bot_id: UUID
    pass_rate_delta_pp: float
    cost_delta_pct: float
    p95_delta_ms: float
    drift_severity: str  # NONE | MINOR | MAJOR


# ============================================================================
# Service
# ============================================================================
class TenantAnalyticsService:
    """Pure-application analytics over ``request_logs``.

    Identity-strict: every method requires ``record_tenant_id`` (UUID).
    None / missing tenant raises :class:`TenantIsolationViolation` at
    the boundary so a coding bug never produces cross-tenant numbers.

    Constructor accepts repos by-name for symmetry with the rest of the
    bootstrap, but the service queries the session factory directly so
    new aggregations do not require touching the shared repos (those are
    on other agents' edit paths today).
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        request_log_repo: object | None = None,
        message_repo: object | None = None,
    ) -> None:
        """Initialise analytics service.

        @param session_factory: async session maker (REQUIRED — used for
            aggregate queries).
        @param request_log_repo: optional, retained for future
            delegation; not used in read-only aggregates today.
        @param message_repo: optional, retained for future delegation
            (raw-text drill-down would JOIN ``messages``).
        """
        self._sf = session_factory
        # Held for future drill-down endpoints (e.g. raw text per
        # offending hash). Not used by aggregate queries below.
        self._request_log_repo = request_log_repo
        self._message_repo = message_repo

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ensure_tenant(record_tenant_id: TenantId | UUID | None) -> UUID:
        """Reject None/missing tenant at the boundary — never run a
        cross-tenant query by accident."""
        if record_tenant_id is None:
            raise TenantIsolationViolation(
                "record_tenant_id required for tenant analytics",
            )
        return record_tenant_id  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # PASS / REFUSE / HALLU aggregation
    # ------------------------------------------------------------------
    async def pass_rate_per_bot(
        self,
        *,
        record_tenant_id: TenantId | UUID,
        since: datetime,
        until: datetime,
    ) -> dict[UUID | None, PassRateStats]:
        """Per-bot PASS rate, REFUSE rate, HALLU count over [since, until].

        Definitions (mirror MEGA-load-test convention used in reports):
          * PASS = ``status='success' AND refusal_reason IS NULL``
          * REFUSE = ``status='refused' OR refusal_reason IS NOT NULL``
          * HALLU = ``is_correct IS FALSE`` (operator-marked, judge-marked
            or golden-mismatch — single is_correct truthy column)

        Returns a dict keyed by ``record_bot_id`` (None bucket included
        if untyped logs exist).
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                select(
                    RequestLogModel.record_bot_id.label("bot"),
                    func.count().label("total"),
                    func.count().filter(
                        (RequestLogModel.status == "success")
                        & (RequestLogModel.refusal_reason.is_(None)),
                    ).label("pass_count"),
                    func.count().filter(
                        (RequestLogModel.status == "refused")
                        | (RequestLogModel.refusal_reason.isnot(None)),
                    ).label("refuse_count"),
                    func.count().filter(
                        RequestLogModel.is_correct.is_(False),
                    ).label("hallu_count"),
                )
                .where(
                    RequestLogModel.record_tenant_id == tid,
                    RequestLogModel.started_at >= since,
                    RequestLogModel.started_at <= until,
                )
                .group_by(RequestLogModel.record_bot_id)
            )
            rows = (await session.execute(stmt)).all()

        out: dict[UUID | None, PassRateStats] = {}
        for r in rows:
            total = int(r.total or 0)
            pass_count = int(r.pass_count or 0)
            refuse_count = int(r.refuse_count or 0)
            hallu_count = int(r.hallu_count or 0)
            pass_rate_pct = (
                (pass_count / total) * 100.0 if total > 0 else 0.0
            )
            out[r.bot] = PassRateStats(
                record_bot_id=r.bot,
                total=total,
                pass_count=pass_count,
                refuse_count=refuse_count,
                hallu_count=hallu_count,
                pass_rate_pct=pass_rate_pct,
            )
        return out

    # ------------------------------------------------------------------
    # Cost aggregation
    # ------------------------------------------------------------------
    async def cost_per_bot(
        self,
        *,
        record_tenant_id: TenantId | UUID,
        since: datetime,
        until: datetime,
    ) -> dict[UUID | None, CostStats]:
        """Per-bot total / avg cost in USD over [since, until].

        ``total_cost_usd`` is the sum of ``request_logs.cost_usd`` (USD,
        already aggregated per request by the chat worker). Average is
        per-turn (``total / count``), not per-token.
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                select(
                    RequestLogModel.record_bot_id.label("bot"),
                    func.count().label("total_requests"),
                    func.sum(RequestLogModel.cost_usd).label("total_cost"),
                )
                .where(
                    RequestLogModel.record_tenant_id == tid,
                    RequestLogModel.started_at >= since,
                    RequestLogModel.started_at <= until,
                )
                .group_by(RequestLogModel.record_bot_id)
            )
            rows = (await session.execute(stmt)).all()

        out: dict[UUID | None, CostStats] = {}
        for r in rows:
            total_requests = int(r.total_requests or 0)
            total_cost = float(r.total_cost or 0.0)
            avg = total_cost / total_requests if total_requests > 0 else 0.0
            out[r.bot] = CostStats(
                record_bot_id=r.bot,
                total_cost_usd=total_cost,
                total_requests=total_requests,
                avg_cost_per_turn=avg,
            )
        return out

    # ------------------------------------------------------------------
    # Multi-bot usage (cost + latency + tokens) for client dashboard
    # ------------------------------------------------------------------
    async def usage_multi_bot(
        self,
        *,
        record_tenant_id: TenantId | UUID,
        workspace_id: str | None,
        bot_ids: list[str] | None,
        channel_types: list[str] | None,
        since: datetime,
        until: datetime,
    ) -> dict[str, Any]:
        """Per-bot usage cho client dashboard.

        Tenant-scoped query — filter chain:
          - record_tenant_id (mandatory, JWT scope)
          - workspace_id (optional)
          - bot_ids slug list (optional)
          - channel_types (optional)
          - [since, until] window
        """
        from sqlalchemy import text as _sql_text  # noqa: PLC0415
        tid = self._ensure_tenant(record_tenant_id)

        where_clauses = [
            "rl.record_tenant_id = :tid",
            "rl.started_at >= :since",
            "rl.started_at <= :until",
        ]
        params: dict[str, Any] = {
            "tid": str(tid),
            "since": since,
            "until": until,
        }
        if workspace_id is not None:
            where_clauses.append("rl.workspace_id = :ws")
            params["ws"] = workspace_id
        if bot_ids:
            where_clauses.append(
                "rl.record_bot_id IN ("
                "SELECT id FROM bots WHERE record_tenant_id = :tid "
                "AND bot_id = ANY(:bot_ids))",
            )
            params["bot_ids"] = bot_ids
        if channel_types:
            where_clauses.append("rl.channel_type = ANY(:channels)")
            params["channels"] = channel_types

        sql = f"""
        SELECT
          rl.record_bot_id AS bot_uuid,
          b.bot_id AS bot_slug,
          b.bot_name AS bot_name,
          rl.workspace_id AS workspace_id,
          rl.channel_type AS channel_type,
          COUNT(*) AS total_requests,
          COALESCE(SUM(rl.cost_usd), 0)::float AS total_cost_usd,
          COALESCE(SUM(rl.total_tokens), 0) AS total_tokens,
          COALESCE(AVG(rl.duration_ms), 0)::int AS avg_duration_ms,
          COALESCE(
            percentile_cont(0.95) WITHIN GROUP (ORDER BY rl.duration_ms),
            0
          )::int AS p95_duration_ms,
          MIN(rl.started_at) AS first_seen_at,
          MAX(rl.started_at) AS last_seen_at
        FROM request_logs rl
        LEFT JOIN bots b ON b.id = rl.record_bot_id
        WHERE {" AND ".join(where_clauses)}
        GROUP BY rl.record_bot_id, b.bot_id, b.bot_name, rl.workspace_id, rl.channel_type
        ORDER BY total_cost_usd DESC
        """

        async with self._sf() as session:
            rows = (await session.execute(_sql_text(sql), params)).all()

        per_bot = []
        grand_total_requests = 0
        grand_total_cost = 0.0
        grand_total_tokens = 0
        for r in rows:
            per_bot.append({
                "record_bot_id": str(r.bot_uuid) if r.bot_uuid else None,
                "bot_id": r.bot_slug,
                "bot_name": r.bot_name,
                "workspace_id": r.workspace_id,
                "channel_type": r.channel_type,
                "total_requests": int(r.total_requests),
                "total_cost_usd": float(r.total_cost_usd),
                "total_tokens": int(r.total_tokens),
                "avg_duration_ms": int(r.avg_duration_ms),
                "p95_duration_ms": int(r.p95_duration_ms),
                "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
                "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            })
            grand_total_requests += int(r.total_requests)
            grand_total_cost += float(r.total_cost_usd)
            grand_total_tokens += int(r.total_tokens)

        return {
            "per_bot": per_bot,
            "totals": {
                "bot_channel_count": len(per_bot),
                "total_requests": grand_total_requests,
                "total_cost_usd": grand_total_cost,
                "total_tokens": grand_total_tokens,
            },
        }

    # ------------------------------------------------------------------
    # Latency percentiles
    # ------------------------------------------------------------------
    async def latency_per_bot(
        self,
        *,
        record_tenant_id: TenantId | UUID,
        since: datetime,
        until: datetime,
    ) -> dict[UUID | None, LatencyStats]:
        """Per-bot p50 / p95 / p99 / max duration_ms.

        Uses Postgres ``percentile_cont(...) WITHIN GROUP (ORDER BY ...)``
        — same pattern as ``RequestLogRepository.get_step_breakdown``.
        """
        tid = self._ensure_tenant(record_tenant_id)
        async with self._sf() as session:
            stmt = (
                select(
                    RequestLogModel.record_bot_id.label("bot"),
                    func.percentile_cont(0.5)
                    .within_group(RequestLogModel.duration_ms.asc())
                    .label("p50"),
                    func.percentile_cont(0.95)
                    .within_group(RequestLogModel.duration_ms.asc())
                    .label("p95"),
                    func.percentile_cont(0.99)
                    .within_group(RequestLogModel.duration_ms.asc())
                    .label("p99"),
                    func.max(RequestLogModel.duration_ms).label("max_ms"),
                )
                .where(
                    RequestLogModel.record_tenant_id == tid,
                    RequestLogModel.started_at >= since,
                    RequestLogModel.started_at <= until,
                )
                .group_by(RequestLogModel.record_bot_id)
            )
            rows = (await session.execute(stmt)).all()

        out: dict[UUID | None, LatencyStats] = {}
        for r in rows:
            out[r.bot] = LatencyStats(
                record_bot_id=r.bot,
                p50_ms=float(r.p50 or 0.0),
                p95_ms=float(r.p95 or 0.0),
                p99_ms=float(r.p99 or 0.0),
                max_ms=float(r.max_ms or 0.0),
            )
        return out

    # ------------------------------------------------------------------
    # Cross-tenant rollup (super-admin only)
    # ------------------------------------------------------------------
    async def all_tenants_summary(
        self,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        sort_by: str,
    ) -> list[TenantSummary]:
        """Aggregate ``request_logs`` across ALL tenants in [since, until].

        Caller is responsible for RBAC (super-admin level). This method
        intentionally does NOT take ``record_tenant_id`` — it is the one
        analytics path that crosses tenant scope and only the HTTP layer
        gates access (level 100 in :mod:`admin_analytics`).

        @param sort_by: one of ``total_cost`` / ``total_requests`` /
            ``avg_latency``. Unknown value raises :class:`ValueError`.
        @param limit: positive integer; caller is expected to have
            clamped it to the schema-level max before invoking.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        order_col = _ALL_TENANTS_SORT_COLUMNS.get(sort_by)
        if order_col is None:
            raise ValueError(
                f"unknown sort_by={sort_by!r}; "
                f"expected one of {sorted(_ALL_TENANTS_SORT_COLUMNS)}",
            )

        async with self._sf() as session:
            stmt = (
                select(
                    RequestLogModel.record_tenant_id.label("tenant"),
                    func.count(func.distinct(
                        RequestLogModel.workspace_id,
                    )).label("workspace_count"),
                    func.count(func.distinct(
                        RequestLogModel.record_bot_id,
                    )).label("bot_count"),
                    func.count().label("total_requests"),
                    func.sum(RequestLogModel.cost_usd).label("total_cost"),
                    func.avg(RequestLogModel.duration_ms).label("avg_duration"),
                    func.percentile_cont(0.95)
                    .within_group(RequestLogModel.duration_ms.asc())
                    .label("p95_duration"),
                    func.sum(RequestLogModel.total_tokens).label("total_tokens"),
                    func.min(RequestLogModel.started_at).label("first_seen"),
                    func.max(RequestLogModel.started_at).label("last_seen"),
                )
                .where(
                    RequestLogModel.started_at >= since,
                    RequestLogModel.started_at <= until,
                )
                .group_by(RequestLogModel.record_tenant_id)
                .order_by(order_col.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()

        out: list[TenantSummary] = []
        for r in rows:
            out.append(TenantSummary(
                record_tenant_id=r.tenant,
                workspace_count=int(r.workspace_count or 0),
                bot_count=int(r.bot_count or 0),
                total_requests=int(r.total_requests or 0),
                total_cost_usd=float(r.total_cost or 0.0),
                avg_duration_ms=float(r.avg_duration or 0.0),
                p95_duration_ms=float(r.p95_duration or 0.0),
                total_tokens=int(r.total_tokens or 0),
                first_seen_at=r.first_seen,
                last_seen_at=r.last_seen,
            ))
        return out

    # ------------------------------------------------------------------
    # Per-tenant workspace rollup — every method below is tenant-scoped
    # ------------------------------------------------------------------
    async def workspace_aggregate(
        self,
        *,
        record_tenant_id: TenantId | UUID,
        since: datetime,
        until: datetime,
        sort_by: str,
        limit: int,
    ) -> list[WorkspaceSummary]:
        """Aggregate ``request_logs`` per workspace within one tenant.

        Mirrors :meth:`all_tenants_summary` but groups by
        ``(record_tenant_id, workspace_id)`` and is filtered to a single
        ``record_tenant_id`` so a tenant-admin cannot see another
        tenant's workspaces. RBAC (super-admin can target any tenant;
        tenant-admin only its own) is enforced by the HTTP layer.

        @param sort_by: one of ``total_cost`` / ``total_requests`` /
            ``avg_latency``. Unknown value raises :class:`ValueError`.
        @param limit: positive integer; caller is expected to have
            clamped it to the schema-level max before invoking.
        """
        tid = self._ensure_tenant(record_tenant_id)
        if limit <= 0:
            raise ValueError("limit must be positive")
        order_col = _WORKSPACE_SORT_COLUMNS.get(sort_by)
        if order_col is None:
            raise ValueError(
                f"unknown sort_by={sort_by!r}; "
                f"expected one of {sorted(_WORKSPACE_SORT_COLUMNS)}",
            )

        async with self._sf() as session:
            stmt = (
                select(
                    RequestLogModel.record_tenant_id.label("tenant"),
                    RequestLogModel.workspace_id.label("workspace"),
                    func.count(func.distinct(
                        RequestLogModel.record_bot_id,
                    )).label("bot_count"),
                    func.count().label("total_requests"),
                    func.sum(RequestLogModel.cost_usd).label("total_cost"),
                    func.avg(RequestLogModel.duration_ms).label("avg_duration"),
                    func.percentile_cont(0.95)
                    .within_group(RequestLogModel.duration_ms.asc())
                    .label("p95_duration"),
                    func.sum(RequestLogModel.total_tokens).label("total_tokens"),
                    func.min(RequestLogModel.started_at).label("first_seen"),
                    func.max(RequestLogModel.started_at).label("last_seen"),
                )
                .where(
                    RequestLogModel.record_tenant_id == tid,
                    RequestLogModel.started_at >= since,
                    RequestLogModel.started_at <= until,
                )
                .group_by(
                    RequestLogModel.record_tenant_id,
                    RequestLogModel.workspace_id,
                )
                .order_by(order_col.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()

        out: list[WorkspaceSummary] = []
        for r in rows:
            out.append(WorkspaceSummary(
                record_tenant_id=r.tenant,
                workspace_id=r.workspace,
                bot_count=int(r.bot_count or 0),
                total_requests=int(r.total_requests or 0),
                total_cost_usd=float(r.total_cost or 0.0),
                avg_duration_ms=float(r.avg_duration or 0.0),
                p95_duration_ms=float(r.p95_duration or 0.0),
                total_tokens=int(r.total_tokens or 0),
                first_seen_at=r.first_seen,
                last_seen_at=r.last_seen,
            ))
        return out

    # ------------------------------------------------------------------
    # Drift detection — current window vs prior window of equal length
    # ------------------------------------------------------------------
    async def drift_signal(
        self,
        *,
        record_tenant_id: TenantId | UUID,
        record_bot_id: UUID,
        window_days: int = DEFAULT_ANALYTICS_DEFAULT_WINDOW_DAYS,
        now: datetime | None = None,
    ) -> DriftSignal:
        """Compare last N days vs prior N days for one bot.

        Window split:
          * baseline window: ``[now - 2*N, now - N)``
          * current window:  ``[now - N, now]``

        ``pass_rate_delta_pp`` = current - baseline (negative = drop).
        ``cost_delta_pct`` = (current_avg - baseline_avg) / baseline_avg * 100.
        ``p95_delta_ms`` = current_p95 - baseline_p95.

        Severity (rolls up the WORST dimension — quality drop OR cost spike):
          * MAJOR if pass-rate drops by >= DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP
            OR cost rises by >= DEFAULT_DRIFT_MAJOR_COST_DELTA_PCT.
          * MINOR if either crosses the MINOR threshold.
          * NONE otherwise.

        @param now: optional injection for deterministic tests; defaults
            to ``datetime.now(tz=...)`` from the most-recent log so we
            do not import time-zone state into the service. Tests pass
            an explicit value.
        """
        tid = self._ensure_tenant(record_tenant_id)
        if record_bot_id is None:
            raise ValueError("record_bot_id required for drift_signal")
        if window_days <= 0:
            raise ValueError("window_days must be positive")

        if now is None:
            now = datetime.now(tz=_timezone_utc())

        cur_since = now - timedelta(days=window_days)
        base_since = now - timedelta(days=2 * window_days)
        base_until = cur_since

        async with self._sf() as session:
            cur = await self._window_summary(
                session,
                tid=tid,
                record_bot_id=record_bot_id,
                since=cur_since,
                until=now,
            )
            base = await self._window_summary(
                session,
                tid=tid,
                record_bot_id=record_bot_id,
                since=base_since,
                until=base_until,
            )

        pass_rate_delta_pp = cur.pass_rate_pct - base.pass_rate_pct
        if base.avg_cost > 0:
            cost_delta_pct = (
                (cur.avg_cost - base.avg_cost) / base.avg_cost
            ) * 100.0
        else:
            cost_delta_pct = 0.0
        p95_delta_ms = cur.p95 - base.p95

        severity = _classify_drift(
            pass_rate_delta_pp=pass_rate_delta_pp,
            cost_delta_pct=cost_delta_pct,
        )
        return DriftSignal(
            record_bot_id=record_bot_id,
            pass_rate_delta_pp=pass_rate_delta_pp,
            cost_delta_pct=cost_delta_pct,
            p95_delta_ms=p95_delta_ms,
            drift_severity=severity,
        )

    # ------------------------------------------------------------------
    async def _window_summary(
        self,
        session: AsyncSession,
        *,
        tid: UUID,
        record_bot_id: UUID,
        since: datetime,
        until: datetime,
    ) -> _WindowSummary:
        """Single-window aggregate (pass-rate, avg cost, p95 latency).

        Private; one round-trip query — the drift_signal call site does
        two of these (current + baseline) under the same session.
        """
        stmt = select(
            func.count().label("total"),
            func.count().filter(
                (RequestLogModel.status == "success")
                & (RequestLogModel.refusal_reason.is_(None)),
            ).label("pass_count"),
            func.sum(RequestLogModel.cost_usd).label("total_cost"),
            func.percentile_cont(0.95)
            .within_group(RequestLogModel.duration_ms.asc())
            .label("p95"),
        ).where(
            RequestLogModel.record_tenant_id == tid,
            RequestLogModel.record_bot_id == record_bot_id,
            RequestLogModel.started_at >= since,
            RequestLogModel.started_at <= until,
        )
        row = (await session.execute(stmt)).one()
        total = int(row.total or 0)
        pass_count = int(row.pass_count or 0)
        total_cost = float(row.total_cost or 0.0)
        pass_rate_pct = (pass_count / total) * 100.0 if total > 0 else 0.0
        avg_cost = total_cost / total if total > 0 else 0.0
        p95 = float(row.p95 or 0.0)
        return _WindowSummary(
            total=total,
            pass_rate_pct=pass_rate_pct,
            avg_cost=avg_cost,
            p95=p95,
        )


# ============================================================================
# Private helpers
# ============================================================================
@dataclass(slots=True)
class _WindowSummary:
    total: int
    pass_rate_pct: float
    avg_cost: float
    p95: float


# Sort-by alias → SQL aggregate expression for ``all_tenants_summary``.
# Lifted to module-level so the set of valid sort keys is one place;
# adding a new key here is the only change needed if the route ever
# grows a new ordering option.
_ALL_TENANTS_SORT_COLUMNS = {
    "total_cost": func.sum(RequestLogModel.cost_usd),
    "total_requests": func.count(),
    "avg_latency": func.avg(RequestLogModel.duration_ms),
}


# Sort-by alias → SQL aggregate expression for ``workspace_aggregate``.
# Kept separate from ``_ALL_TENANTS_SORT_COLUMNS`` because the per-row
# aggregation grain is different (per-workspace vs per-tenant); even
# though the SQL happens to be identical today, keeping two registries
# means a future grain-specific ordering does not need to fork.
_WORKSPACE_SORT_COLUMNS = {
    "total_cost": func.sum(RequestLogModel.cost_usd),
    "total_requests": func.count(),
    "avg_latency": func.avg(RequestLogModel.duration_ms),
}


def _classify_drift(
    *, pass_rate_delta_pp: float, cost_delta_pct: float,
) -> str:
    """Roll up the worst dimension into a single severity string.

    Pass-rate is a *drop* signal (negative delta = bad). Cost is a
    *rise* signal (positive delta = bad). Either dimension crossing
    the MAJOR threshold beats both at MINOR.
    """
    pass_drop = -pass_rate_delta_pp  # convert to "how much we dropped"
    if (
        pass_drop >= DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP
        or cost_delta_pct >= DEFAULT_DRIFT_MAJOR_COST_DELTA_PCT
    ):
        return "MAJOR"
    if (
        pass_drop >= DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP
        or cost_delta_pct >= DEFAULT_DRIFT_MINOR_COST_DELTA_PCT
    ):
        return "MINOR"
    return "NONE"


def _timezone_utc():  # noqa: ANN202 — tiny indirection for test mocking
    """Return UTC tzinfo. Indirected so tests can monkey-patch without
    importing datetime.timezone everywhere."""
    from datetime import timezone

    return timezone.utc


__all__ = [
    "CostStats",
    "DEFAULT_ANALYTICS_DEFAULT_WINDOW_DAYS",
    "DEFAULT_DRIFT_MAJOR_COST_DELTA_PCT",
    "DEFAULT_DRIFT_MAJOR_PASS_RATE_DELTA_PP",
    "DEFAULT_DRIFT_MINOR_COST_DELTA_PCT",
    "DEFAULT_DRIFT_MINOR_PASS_RATE_DELTA_PP",
    "DriftSignal",
    "LatencyStats",
    "PassRateStats",
    "TenantAnalyticsService",
    "TenantSummary",
    "WorkspaceSummary",
]
