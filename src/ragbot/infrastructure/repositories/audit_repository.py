"""Audit analytics repository — aggregate queries for auditor endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Numeric, cast, func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.models_monitoring import RequestLogModel
from ragbot.shared.pagination import page_limit


class AuditRepository:
    """Read-only aggregate queries for audit analytics."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def get_audit_overview(
        self,
        *,
        record_tenant_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        record_bot_id: UUID | None = None,
    ) -> dict[str, Any]:
        """Aggregate overview: document/chunk/query/token stats.

        P17 fix: require record_tenant_id (was missing entirely before).
        Admin level 60 used to see cross-tenant document counts because
        none of the SQL here scoped by tenant. All three document-side
        SELECTs now join documents.record_tenant_id.
        """
        async with self._sf() as session:
            # --- Query stats from request_logs ---
            base = select(RequestLogModel).where(
                RequestLogModel.record_tenant_id == record_tenant_id
            )
            if date_from:
                base = base.where(RequestLogModel.started_at >= date_from)
            if date_to:
                base = base.where(RequestLogModel.started_at <= date_to)
            if record_bot_id:
                base = base.where(RequestLogModel.record_bot_id == record_bot_id)

            sq = base.subquery()

            query_stats = select(
                func.count().label("total"),
                func.avg(sq.c.duration_ms).label("avg_latency"),
                func.percentile_cont(0.5).within_group(sq.c.duration_ms).label("p50"),
                func.percentile_cont(0.95).within_group(sq.c.duration_ms).label("p95"),
                func.percentile_cont(0.99).within_group(sq.c.duration_ms).label("p99"),
                func.count().filter(sq.c.model_name == "cache_hit").label("cache_hits"),
                func.sum(sq.c.prompt_tokens).label("total_prompt"),
                func.sum(sq.c.completion_tokens).label("total_completion"),
                func.sum(sq.c.cost_usd).label("total_cost"),
            ).select_from(sq)

            qrow = (await session.execute(query_stats)).one()
            total_queries = int(qrow.total or 0)
            cache_hits = int(qrow.cache_hits or 0)

            # --- Document stats — scoped by tenant ---
            doc_row = (await session.execute(text(
                "SELECT COUNT(*) as total, COALESCE(SUM(content_chars), 0) as total_chars, "
                "COALESCE(AVG(content_chars), 0) as avg_chars "
                "FROM documents "
                "WHERE deleted_at IS NULL AND record_tenant_id = :tid"
            ), {"tid": record_tenant_id})).one()

            # --- Chunk stats — scoped via join on tenant-scoped documents ---
            chunk_row = (await session.execute(text(
                "SELECT COUNT(*) as total, COALESCE(AVG(chunk_chars), 0) as avg_chars "
                "FROM document_chunks dc "
                "JOIN documents d ON dc.record_document_id = d.id "
                "WHERE d.deleted_at IS NULL AND d.record_tenant_id = :tid"
            ), {"tid": record_tenant_id})).one()

            # Chunk strategy distribution from metadata — same scope
            strat_rows = (await session.execute(text(
                "SELECT metadata_json->>'chunking_strategy' as strategy, COUNT(*) as cnt "
                "FROM document_chunks dc "
                "JOIN documents d ON dc.record_document_id = d.id "
                "WHERE d.deleted_at IS NULL "
                "AND d.record_tenant_id = :tid "
                "AND metadata_json->>'chunking_strategy' IS NOT NULL "
                "GROUP BY metadata_json->>'chunking_strategy'"
            ), {"tid": record_tenant_id})).all()
            strategy_dist = {r.strategy: int(r.cnt) for r in strat_rows}

            doc_total = int(doc_row.total or 0)
            chunk_total = int(chunk_row.total or 0)

            return {
                "documents": {
                    "total": doc_total,
                    "total_chars": int(doc_row.total_chars or 0),
                    "avg_chars": int(doc_row.avg_chars or 0),
                },
                "chunks": {
                    "total": chunk_total,
                    "avg_chars": int(chunk_row.avg_chars or 0),
                    "avg_per_doc": round(chunk_total / doc_total, 1) if doc_total else 0,
                    "strategy_distribution": strategy_dist,
                },
                "queries": {
                    "total": total_queries,
                    "avg_latency_ms": round(float(qrow.avg_latency or 0), 1),
                    "p50_latency_ms": round(float(qrow.p50 or 0), 1),
                    "p95_latency_ms": round(float(qrow.p95 or 0), 1),
                    "p99_latency_ms": round(float(qrow.p99 or 0), 1),
                    "cache_hit_rate": round(cache_hits / total_queries, 3) if total_queries else 0,
                },
                "tokens": {
                    "total_prompt": int(qrow.total_prompt or 0),
                    "total_completion": int(qrow.total_completion or 0),
                    "total_cost_usd": round(float(qrow.total_cost or 0), 4),
                    "avg_per_request": {
                        "prompt": round(int(qrow.total_prompt or 0) / total_queries) if total_queries else 0,
                        "completion": round(int(qrow.total_completion or 0) / total_queries) if total_queries else 0,
                        "cost_usd": round(float(qrow.total_cost or 0) / total_queries, 4) if total_queries else 0,
                    },
                },
            }

    async def get_query_detail(
        self,
        *,
        record_tenant_id: UUID,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        record_bot_id: UUID | None = None,
        cursor: datetime | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Per-query breakdown with keyset pagination (cursor = started_at).

        P17 fix: require record_tenant_id. Was missing the mandatory
        tenant scope; combined with missing RBAC it allowed any level-60
        admin to page through any tenant's request logs.
        """
        lim = page_limit(limit, default=20, max_limit=100)

        async with self._sf() as session:
            q = (
                select(
                    RequestLogModel.request_id,
                    RequestLogModel.started_at,
                    RequestLogModel.duration_ms,
                    RequestLogModel.model_name,
                    RequestLogModel.prompt_tokens,
                    RequestLogModel.completion_tokens,
                    RequestLogModel.cost_usd,
                    RequestLogModel.status,
                    RequestLogModel.metadata_json,
                )
                .where(RequestLogModel.record_tenant_id == record_tenant_id)
                .order_by(RequestLogModel.started_at.desc())
            )

            if date_from:
                q = q.where(RequestLogModel.started_at >= date_from)
            if date_to:
                q = q.where(RequestLogModel.started_at <= date_to)
            if record_bot_id:
                q = q.where(RequestLogModel.record_bot_id == record_bot_id)
            if cursor:
                q = q.where(RequestLogModel.started_at < cursor)

            q = q.limit(lim + 1)  # fetch one extra for next_cursor

            rows = (await session.execute(q)).all()
            has_more = len(rows) > lim
            items = rows[:lim]

            queries = []
            for r in items:
                meta = r.metadata_json or {}
                queries.append({
                    "request_id": str(r.request_id),
                    "started_at": r.started_at.isoformat() if r.started_at else None,
                    "duration_ms": int(r.duration_ms or 0),
                    "model_name": r.model_name,
                    "prompt_tokens": int(r.prompt_tokens or 0),
                    "completion_tokens": int(r.completion_tokens or 0),
                    "cost_usd": float(r.cost_usd or 0),
                    "status": r.status,
                    "intent": meta.get("intent"),
                    "crag_grade": meta.get("crag_grade"),
                    "cache_hit": r.model_name == "cache_hit",
                    "context_chunks": meta.get("context_chunks"),
                    "context_chars": meta.get("context_chars"),
                })

            next_cursor = items[-1].started_at.isoformat() if has_more and items else None

            return {
                "queries": queries,
                "pagination": {
                    "limit": lim,
                    "count": len(items),
                    "has_more": has_more,
                    "next_cursor": next_cursor,
                },
            }
