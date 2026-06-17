"""Per-tenant monthly token cost-cap alerter — read-only ops service.

Aggregates ``request_logs.total_tokens`` per ``record_tenant_id`` over a
trailing window and compares the sum against ``tenants.quota_monthly_tokens``.
Tenants whose ratio crosses the warn or exceed thresholds yield a
``CostCapEvent``; usage is also emitted via the injected structlog-style
logger so downstream sinks (alerting, ops dashboards) can subscribe.

Sacred contracts honoured here:
  * No DB-side-effects — every query is ``SELECT`` only.
  * No hot-path coupling — the chat worker never imports this module.
  * Strategy + DI — the SQLAlchemy session and the logger are both
    injected; tests substitute fakes without touching real engines.
  * Domain-neutral — no brand / industry literal anywhere.
  * Zero-hardcode — every threshold lives in ``shared.constants`` and is
    overridable per call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ragbot.infrastructure.db.models import TenantModel
from ragbot.infrastructure.db.models_monitoring import RequestLogModel
from ragbot.shared.constants import (
    DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS,
    DEFAULT_COST_CAP_EXCEED_RATIO,
    DEFAULT_COST_CAP_WARN_RATIO,
)

# ---------------------------------------------------------------------------
# Event names — operator-facing strings consumed by alerting sinks.
# Kept as module-level so callers (CLI, dashboards, tests) reference one
# canonical token rather than re-typing literals.
# ---------------------------------------------------------------------------
COST_CAP_WARNING_EVENT: str = "cost_cap_warning"
COST_CAP_EXCEEDED_EVENT: str = "cost_cap_exceeded"


# ---------------------------------------------------------------------------
# Logger Port — minimal Protocol so tests can inject a recorder without
# pulling structlog's full BoundLogger surface.
# ---------------------------------------------------------------------------
class CostCapLogger(Protocol):
    """Subset of ``structlog.BoundLogger`` we depend on."""

    def warning(self, event: str, /, **kwargs: object) -> None: ...

    def error(self, event: str, /, **kwargs: object) -> None: ...


# ---------------------------------------------------------------------------
# Event DTO — frozen + slots for memory efficiency on multi-tenant lists.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class CostCapEvent:
    """Single per-tenant cost-cap signal.

    ``severity`` matches the structured-log event name we emit so an
    alert sink can route on either ``severity`` or the original log
    ``event`` string without normalising.
    """

    record_tenant_id: UUID
    tenant_name: str
    used_tokens: int
    quota_tokens: int
    ratio: float
    severity: str  # COST_CAP_WARNING_EVENT | COST_CAP_EXCEEDED_EVENT
    window_days: int


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
async def evaluate_tenants(
    *,
    session: AsyncSession,
    logger: CostCapLogger,
    since_days: int = DEFAULT_COST_CAP_AUDIT_WINDOW_DAYS,
    warn_ratio: float = DEFAULT_COST_CAP_WARN_RATIO,
    exceed_ratio: float = DEFAULT_COST_CAP_EXCEED_RATIO,
    now: datetime | None = None,
) -> list[CostCapEvent]:
    """Evaluate per-tenant token usage vs quota and emit cap signals.

    @param session: open async SQLAlchemy session (read-only access).
    @param logger: structlog-style logger; one structured event is emitted
        per returned ``CostCapEvent`` (warning vs exceeded uses different
        log levels so PagerDuty/alert rules can route on severity).
    @param since_days: trailing window in days for the usage aggregate.
        Must be positive.
    @param warn_ratio: usage / quota ratio at/above which a tenant is
        flagged ``cost_cap_warning``. Must be in (0, 1].
    @param exceed_ratio: usage / quota ratio at/above which a tenant is
        flagged ``cost_cap_exceeded``. Must be > 0 and >= warn_ratio.
    @param now: injection point for deterministic tests; defaults to
        ``datetime.now(tz=UTC)``.

    Returns the ordered list of events (one per flagged tenant). Tenants
    with ``quota_monthly_tokens`` IS NULL are skipped (no cap configured).
    Tenants with quota = 0 are also skipped — a zero quota means "block
    all" and is enforced elsewhere; emitting a divide-by-zero alert here
    would be noise.

    Why the entire aggregation is one query: ops dashboards may be
    invoked across hundreds of tenants and request_logs is large — a
    single GROUP BY over the trailing window keeps this cheap.
    """
    if since_days <= 0:
        raise ValueError("since_days must be positive")
    if not (0.0 < warn_ratio <= 1.0):
        raise ValueError("warn_ratio must be in (0, 1]")
    if exceed_ratio <= 0.0:
        raise ValueError("exceed_ratio must be positive")
    if exceed_ratio < warn_ratio:
        raise ValueError("exceed_ratio must be >= warn_ratio")

    when = now if now is not None else datetime.now(tz=UTC)
    since = when - timedelta(days=since_days)

    usage_subq = (
        select(
            RequestLogModel.record_tenant_id.label("tid"),
            func.coalesce(
                func.sum(RequestLogModel.total_tokens), 0,
            ).label("used"),
        )
        .where(RequestLogModel.started_at >= since)
        .group_by(RequestLogModel.record_tenant_id)
        .subquery()
    )
    stmt = (
        select(
            TenantModel.id.label("tenant_id"),
            TenantModel.name.label("tenant_name"),
            TenantModel.quota_monthly_tokens.label("quota"),
            func.coalesce(usage_subq.c.used, 0).label("used"),
        )
        .select_from(
            TenantModel.__table__.outerjoin(
                usage_subq, TenantModel.id == usage_subq.c.tid,
            ),
        )
    )
    rows = (await session.execute(stmt)).all()

    events: list[CostCapEvent] = []
    for row in rows:
        quota = row.quota
        # NULL quota → tenant opted out of caps; skip without noise.
        # Zero quota is a separate enforcement signal handled at write
        # time — flagging here would yield a divide-by-zero ratio.
        if quota is None or quota == 0:
            continue

        used = int(row.used or 0)
        ratio = used / float(quota)

        # Exceeded supersedes warn — order matters because at usage
        # >= 100% of quota we want the louder ``error``-level signal.
        if ratio >= exceed_ratio:
            severity = COST_CAP_EXCEEDED_EVENT
        elif ratio >= warn_ratio:
            severity = COST_CAP_WARNING_EVENT
        else:
            continue

        evt = CostCapEvent(
            record_tenant_id=row.tenant_id,
            tenant_name=str(row.tenant_name),
            used_tokens=used,
            quota_tokens=int(quota),
            ratio=ratio,
            severity=severity,
            window_days=since_days,
        )
        events.append(evt)

        log_kwargs: dict[str, object] = {
            "record_tenant_id": str(evt.record_tenant_id),
            "tenant_name": evt.tenant_name,
            "used_tokens": evt.used_tokens,
            "quota_tokens": evt.quota_tokens,
            "ratio": evt.ratio,
            "window_days": evt.window_days,
        }
        if severity == COST_CAP_EXCEEDED_EVENT:
            logger.error(severity, **log_kwargs)
        else:
            logger.warning(severity, **log_kwargs)

    return events


__all__ = [
    "COST_CAP_EXCEEDED_EVENT",
    "COST_CAP_WARNING_EVENT",
    "CostCapEvent",
    "CostCapLogger",
    "evaluate_tenants",
]
