"""Read-only analytics over ``token_ledger`` — the log-center query layer.

Powers the usage dashboard: per-bot / per-workspace / per-tenant token + cost
roll-ups bucketed by time. ``token_ledger`` is an append-only, FK-free table
with indexes on ``(record_tenant_id, started_at)`` / ``(record_bot_id,
started_at)`` etc., so these ``date_trunc`` GROUP BYs hit an index.

Tenant scoping is enforced by the caller (the route passes the JWT-bound
``record_tenant_id``); cross-tenant ('all') is gated at the route by RBAC
level 100.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Whitelists — interpolated as SQL literals, so they MUST stay closed sets
# (never user free-text) to keep the query injection-safe.
_BUCKETS: dict[str, str] = {"hour": "hour", "day": "day", "month": "month"}
_BREAKDOWN_COLS: dict[str, str | None] = {
    "none": None,
    "model": "model",
    "action": "action",
    "provider": "provider",
    # ``purpose`` distinguishes generate / grade / grounding / embed / rerank —
    # the per-call attribution payoff of the ledger. ``workspace_id`` enables a
    # second-key roll-up under a tenant. Closed whitelist keeps interpolation
    # injection-safe (these are never user free-text).
    "purpose": "purpose",
    "workspace_id": "workspace_id",
}

# Roll-up grouping dimensions → the column the rows are grouped by. Closed
# whitelist (interpolated as a SQL literal) so it stays injection-safe.
_ROLLUP_DIMS: dict[str, str] = {
    "bot": "record_bot_id",
    "workspace": "workspace_id",
    "tenant": "record_tenant_id",
}


class TokenLedgerAnalyticsRepository:
    """Time-bucketed token/cost aggregation over ``token_ledger``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def usage_timeseries(
        self,
        *,
        record_tenant_id: UUID,
        date_from: datetime,
        date_to: datetime,
        group_by: str = "day",
        breakdown: str = "none",
        record_bot_id: UUID | None = None,
        workspace_id: str | None = None,
        all_tenants: bool = False,
    ) -> list[dict[str, Any]]:
        """Return token + cost rows bucketed by ``group_by`` (hour|day|month).

        ``breakdown`` (none|model|action|provider) adds a second GROUP BY key.
        ``all_tenants`` (route gates this on RBAC level 100) drops the tenant
        filter for the platform-wide view.
        """
        bucket = _BUCKETS.get(group_by, "day")
        if breakdown not in _BREAKDOWN_COLS:
            breakdown = "none"
        bcol = _BREAKDOWN_COLS[breakdown]

        select_key = f"{bcol} AS bucket_key," if bcol else "NULL AS bucket_key,"
        group_key = f", {bcol}" if bcol else ""

        where = ["started_at >= :date_from", "started_at < :date_to"]
        params: dict[str, Any] = {"date_from": date_from, "date_to": date_to}
        if not all_tenants:
            where.append("record_tenant_id = :tenant")
            params["tenant"] = record_tenant_id
        if record_bot_id is not None:
            where.append("record_bot_id = :bot")
            params["bot"] = record_bot_id
        if workspace_id is not None:
            where.append("workspace_id = :ws")
            params["ws"] = workspace_id

        # Interpolated tokens (bucket, select_key, WHERE fragments) come ONLY
        # from closed whitelists (_BUCKETS / _BREAKDOWN_COLS) + fixed strings;
        # every user value is a bound :param → not injectable.
        sql = text(
            f"""
            SELECT date_trunc('{bucket}', started_at) AS ts,
                   {select_key}
                   sum(input_tokens)  AS tokens_in,
                   sum(output_tokens) AS tokens_out,
                   sum(total_tokens)  AS tokens_total,
                   round(coalesce(sum(cost_usd), 0)::numeric, 8) AS cost_usd,
                   count(*) AS calls
            FROM token_ledger
            WHERE {" AND ".join(where)}
            GROUP BY ts{group_key}
            ORDER BY ts
            """
        )
        async with self._sf() as session:
            rows = (await session.execute(sql, params)).mappings().all()
        return [dict(r) for r in rows]

    async def usage_rollup(
        self,
        *,
        record_tenant_id: UUID,
        date_from: datetime,
        date_to: datetime,
        dim: str = "bot",
        breakdown: str = "none",
    ) -> list[dict[str, Any]]:
        """Tenant-scoped Σ tokens/cost roll-up grouped by ``dim``.

        ``dim`` (bot|workspace|tenant) picks the primary GROUP BY key. The row
        carries CRM cardinality (``bot_count`` / ``workspace_count`` via
        ``count(DISTINCT ...)``) and ``turns`` = ``count(DISTINCT request_id)``
        (per-turn billing granularity). ``breakdown`` adds an optional second
        GROUP BY key (e.g. ``purpose`` for per-purpose attribution). Hits the
        existing ``(record_tenant_id, started_at)`` index.
        """
        gcol = _ROLLUP_DIMS.get(dim, _ROLLUP_DIMS["bot"])
        if breakdown not in _BREAKDOWN_COLS:
            breakdown = "none"
        bcol = _BREAKDOWN_COLS[breakdown]

        select_key = f"{bcol} AS breakdown_key," if bcol else "NULL AS breakdown_key,"
        group_key = f", {bcol}" if bcol else ""
        # bot_id (external slug) only co-groups cleanly when grouping per-bot.
        bot_slug_select = "max(bot_id) AS bot_id," if gcol == "record_bot_id" else ""

        params: dict[str, Any] = {
            "tenant": record_tenant_id,
            "date_from": date_from,
            "date_to": date_to,
        }
        # Interpolated tokens (gcol, bcol, select fragments) come ONLY from the
        # closed whitelists (_ROLLUP_DIMS / _BREAKDOWN_COLS); every user value is
        # a bound :param → not injectable.
        sql = text(
            f"""
            SELECT {gcol} AS dim_key,
                   {bot_slug_select}
                   {select_key}
                   count(DISTINCT record_bot_id)   AS bot_count,
                   count(DISTINCT workspace_id)     AS workspace_count,
                   count(DISTINCT request_id)       AS turns,
                   sum(input_tokens)  AS tokens_in,
                   sum(output_tokens) AS tokens_out,
                   sum(total_tokens)  AS tokens_total,
                   round(coalesce(sum(cost_usd), 0)::numeric, 8) AS cost_usd,
                   count(*) AS calls
            FROM token_ledger
            WHERE record_tenant_id = :tenant
              AND started_at >= :date_from
              AND started_at < :date_to
            GROUP BY {gcol}{group_key}
            ORDER BY cost_usd DESC
            """
        )
        async with self._sf() as session:
            rows = (await session.execute(sql, params)).mappings().all()
        return [dict(r) for r in rows]

    async def cross_tenant_rollup(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Platform-wide per-tenant leaderboard (NO tenant filter; RBAC 100).

        One row per tenant with ``workspace_count`` / ``bot_count`` /
        ``turns`` (distinct request_id) + Σ tokens/cost, ordered by cost. The
        route gates this on level 100 — there is no tenant scoping here.
        """
        params: dict[str, Any] = {
            "date_from": date_from,
            "date_to": date_to,
            "lim": int(limit),
        }
        sql = text(
            """
            SELECT record_tenant_id,
                   count(DISTINCT workspace_id)  AS workspace_count,
                   count(DISTINCT record_bot_id) AS bot_count,
                   count(DISTINCT request_id)    AS turns,
                   sum(input_tokens)  AS tokens_in,
                   sum(output_tokens) AS tokens_out,
                   sum(total_tokens)  AS tokens_total,
                   round(coalesce(sum(cost_usd), 0)::numeric, 8) AS cost_usd,
                   count(*) AS calls
            FROM token_ledger
            WHERE started_at >= :date_from AND started_at < :date_to
            GROUP BY record_tenant_id
            ORDER BY cost_usd DESC
            LIMIT :lim
            """
        )
        async with self._sf() as session:
            rows = (await session.execute(sql, params)).mappings().all()
        return [dict(r) for r in rows]
