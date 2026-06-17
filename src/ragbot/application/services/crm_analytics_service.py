"""CRM analytics service — read-layer over existing monitoring tables.

EVOLVE-not-REWRITE (z-luannt-new-feature.txt reality-check): every metric is
derived from append-only tables the platform already writes —

* ``request_logs``  — one row per request: 4-key identity, tokens, cost,
  duration, status, refusal_reason, quality (is_correct / feedback_score).
* ``request_steps`` — one row per LangGraph node: step_name, duration,
  input/output tokens, cost, status (the node-level bottleneck source).
* ``token_budgets`` — config (alembic 0219): per-level token/cost limit.

The masterplan's proposed ``token_usage_events`` / ``request_events`` /
``trace_logs`` tables would duplicate the first two, so they are NOT created.

This class is pure data: it takes a ``session_factory`` and returns plain
JSON-friendly dicts. No FastAPI / Request coupling → unit-testable in
isolation. Tenant isolation is enforced by the caller passing
``record_tenant_id`` (lifted from JWT, never from the body); ``None`` means a
platform super-admin cross-tenant view. Every query carries the
``(:tid IS NULL OR record_tenant_id = :tid)`` clause so a tenant-scoped caller
can never read another tenant's rows.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text

from ragbot.shared.constants import (
    DEFAULT_CRM_TOP_N,
    DEFAULT_CRM_WINDOW_DAYS,
    MAX_CRM_TOP_N,
    MAX_CRM_WINDOW_DAYS,
)


def _clamp(value: int | None, default: int, lo: int, hi: int) -> int:
    """Clamp an optional caller int into [lo, hi], falling back to default."""
    return max(lo, min(int(value if value is not None else default), hi))


class CrmAnalyticsService:
    """Aggregations for the CRM dashboard, scoped per tenant.

    @param session_factory: async-session factory from the DI container.
    """

    def __init__(self, session_factory: Any) -> None:
        self._sf = session_factory

    # -- shared filters -----------------------------------------------------
    @staticmethod
    def _scope(record_tenant_id: UUID | None, bot_id: str | None) -> dict[str, Any]:
        """Bind params shared by the request_logs aggregates."""
        return {
            "tid": str(record_tenant_id) if record_tenant_id is not None else None,
            "bot": (bot_id or "").strip() or None,
        }

    # request_logs (rl) tenant + bot-slug filter
    _RL_WHERE = (
        "rl.started_at > now() - make_interval(days => CAST(:d AS int)) "
        "AND (CAST(:tid AS uuid) IS NULL OR rl.record_tenant_id = CAST(:tid AS uuid)) "
        "AND (CAST(:bot AS text) IS NULL OR b.bot_id = CAST(:bot AS text))"
    )

    # ----------------------------------------------------------------------
    async def tokens(
        self, *, record_tenant_id: UUID | None, days: int | None = None,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        """Token + cost: totals, daily timeline, workspace + bot/channel rollup."""
        p = self._scope(record_tenant_id, bot_id)
        p["d"] = _clamp(days, DEFAULT_CRM_WINDOW_DAYS, 1, MAX_CRM_WINDOW_DAYS)
        w = self._RL_WHERE
        async with self._sf() as session:
            totals = (await session.execute(text(
                "SELECT count(*) n, coalesce(sum(rl.total_tokens),0) tokens, "
                "coalesce(sum(rl.prompt_tokens),0) prompt, "
                "coalesce(sum(rl.completion_tokens),0) completion, "
                "round(coalesce(sum(rl.cost_usd),0)::numeric,5) cost "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {w}"), p)).fetchone()
            by_day = (await session.execute(text(
                "SELECT date_trunc('day', rl.started_at) AS bucket, count(*) n, "
                "coalesce(sum(rl.total_tokens),0) tokens, "
                "round(coalesce(sum(rl.cost_usd),0)::numeric,5) cost "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {w} GROUP BY bucket ORDER BY bucket DESC"), p)).fetchall()
            by_ws = (await session.execute(text(
                "SELECT rl.workspace_id, count(*) n, coalesce(sum(rl.total_tokens),0) tokens, "
                "round(coalesce(sum(rl.cost_usd),0)::numeric,5) cost "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {w} GROUP BY rl.workspace_id ORDER BY cost DESC NULLS LAST"), p)).fetchall()
            by_bc = (await session.execute(text(
                "SELECT coalesce(b.bot_id, rl.record_bot_id::text) bot, rl.channel_type, "
                "count(*) n, coalesce(sum(rl.total_tokens),0) tokens, "
                "round(coalesce(sum(rl.cost_usd),0)::numeric,5) cost, round(avg(rl.duration_ms)) avg_ms "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {w} GROUP BY bot, rl.channel_type ORDER BY cost DESC NULLS LAST"), p)).fetchall()
        return {
            "totals": {"requests": totals[0], "total_tokens": int(totals[1]),
                       "prompt_tokens": int(totals[2]), "completion_tokens": int(totals[3]),
                       "cost_usd": float(totals[4] or 0)},
            "by_day": [{"day": r[0].isoformat() if r[0] else None, "requests": r[1],
                        "tokens": int(r[2]), "cost_usd": float(r[3] or 0)} for r in by_day],
            "by_workspace": [{"workspace_id": r[0], "requests": r[1], "tokens": int(r[2]),
                              "cost_usd": float(r[3] or 0)} for r in by_ws],
            "by_bot_channel": [{"bot_id": r[0], "channel_type": r[1], "requests": r[2],
                                "tokens": int(r[3]), "cost_usd": float(r[4] or 0),
                                "avg_duration_ms": int(r[5] or 0)} for r in by_bc],
        }

    async def latency(
        self, *, record_tenant_id: UUID | None, days: int | None = None,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        """End-to-end latency p50/p95/p99 per (bot, channel)."""
        p = self._scope(record_tenant_id, bot_id)
        p["d"] = _clamp(days, DEFAULT_CRM_WINDOW_DAYS, 1, MAX_CRM_WINDOW_DAYS)
        async with self._sf() as session:
            rows = (await session.execute(text(
                "SELECT coalesce(b.bot_id, rl.record_bot_id::text) bot, rl.channel_type, count(*) n, "
                "percentile_cont(0.5)  within group (order by rl.duration_ms)::int p50, "
                "percentile_cont(0.95) within group (order by rl.duration_ms)::int p95, "
                "percentile_cont(0.99) within group (order by rl.duration_ms)::int p99, "
                "round(avg(rl.duration_ms))::int avg_ms, max(rl.duration_ms) max_ms, "
                "sum(case when rl.status in ('refused','moderated') then 1 else 0 end) refusals, "
                "sum(case when rl.status in ('failed','timeout') then 1 else 0 end) errors "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {self._RL_WHERE} GROUP BY bot, rl.channel_type ORDER BY p95 DESC NULLS LAST"),
                p)).fetchall()
        return {"by_bot_channel": [
            {"bot_id": r[0], "channel_type": r[1], "requests": r[2], "p50_ms": r[3],
             "p95_ms": r[4], "p99_ms": r[5], "avg_ms": r[6], "max_ms": r[7],
             "refusals": r[8], "errors": r[9]} for r in rows]}

    async def nodes(
        self, *, record_tenant_id: UUID | None, days: int | None = None,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        """Per-LangGraph-node latency/token breakdown — the bottleneck view."""
        p = self._scope(record_tenant_id, bot_id)
        p["d"] = _clamp(days, DEFAULT_CRM_WINDOW_DAYS, 1, MAX_CRM_WINDOW_DAYS)
        where = (
            "rs.started_at > now() - make_interval(days => CAST(:d AS int)) "
            "AND (CAST(:tid AS uuid) IS NULL OR rs.record_tenant_id = CAST(:tid AS uuid)) "
            "AND (CAST(:bot AS text) IS NULL OR b.bot_id = CAST(:bot AS text))"
        )
        async with self._sf() as session:
            rows = (await session.execute(text(
                "SELECT rs.step_name, count(*) calls, round(avg(rs.duration_ms))::int avg_ms, "
                "percentile_cont(0.95) within group (order by rs.duration_ms)::int p95, "
                "coalesce(sum(rs.input_tokens + rs.output_tokens),0) tokens, "
                "round(coalesce(sum(rs.cost_usd),0)::numeric,5) cost, "
                "sum(case when rs.status not in ('success','') then 1 else 0 end) errors "
                "FROM request_steps rs LEFT JOIN request_logs rl ON rl.request_id = rs.record_request_id "
                "LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {where} GROUP BY rs.step_name ORDER BY avg_ms DESC"), p)).fetchall()
        total_avg = sum(r[2] or 0 for r in rows) or 1
        return {"by_node": [
            {"step_name": r[0], "calls": r[1], "avg_ms": r[2], "p95_ms": r[3],
             "tokens": int(r[4]), "cost_usd": float(r[5] or 0), "errors": r[6],
             "pct_of_total": round(100.0 * (r[2] or 0) / total_avg, 1)} for r in rows]}

    async def top_questions(
        self, *, record_tenant_id: UUID | None, days: int | None = None,
        bot_id: str | None = None, n: int | None = None,
    ) -> dict[str, Any]:
        """Top-N token-expensive question groups (by question_hash — PII-safe)."""
        p = self._scope(record_tenant_id, bot_id)
        p["d"] = _clamp(days, DEFAULT_CRM_WINDOW_DAYS, 1, MAX_CRM_WINDOW_DAYS)
        p["n"] = _clamp(n, DEFAULT_CRM_TOP_N, 1, MAX_CRM_TOP_N)
        async with self._sf() as session:
            rows = (await session.execute(text(
                "SELECT rl.question_hash, count(*) ask_count, "
                "coalesce(sum(rl.total_tokens),0) total_tokens, "
                "round(avg(rl.total_tokens))::int avg_tokens, "
                "round(coalesce(sum(rl.cost_usd),0)::numeric,5) total_cost, "
                "round(avg(rl.duration_ms))::int avg_latency "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {self._RL_WHERE} GROUP BY rl.question_hash "
                "ORDER BY total_tokens DESC LIMIT CAST(:n AS int)"), p)).fetchall()
        return {"top": [
            {"question_hash": r[0], "ask_count": r[1], "total_tokens": int(r[2]),
             "avg_tokens": r[3], "total_cost_usd": float(r[4] or 0),
             "avg_latency_ms": r[5]} for r in rows]}

    async def quality(
        self, *, record_tenant_id: UUID | None, days: int | None = None,
        bot_id: str | None = None,
    ) -> dict[str, Any]:
        """Status distribution, refusal/error rate, feedback summary."""
        p = self._scope(record_tenant_id, bot_id)
        p["d"] = _clamp(days, DEFAULT_CRM_WINDOW_DAYS, 1, MAX_CRM_WINDOW_DAYS)
        w = self._RL_WHERE
        async with self._sf() as session:
            by_status = (await session.execute(text(
                "SELECT rl.status, count(*) n FROM request_logs rl "
                "LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {w} GROUP BY rl.status ORDER BY n DESC"), p)).fetchall()
            fb = (await session.execute(text(
                "SELECT count(*) n, "
                "sum(case when rl.is_correct then 1 else 0 end) correct, "
                "count(rl.is_correct) judged, "
                "round(avg(rl.feedback_score)::numeric,2) avg_feedback "
                "FROM request_logs rl LEFT JOIN bots b ON b.id = rl.record_bot_id "
                f"WHERE {w}"), p)).fetchone()
        total = sum(r[1] for r in by_status) or 0
        refused = sum(r[1] for r in by_status if r[0] in ("refused", "moderated"))
        errored = sum(r[1] for r in by_status if r[0] in ("failed", "timeout"))
        return {
            "totals": {"requests": total, "judged": int(fb[2] or 0),
                       "correct": int(fb[1] or 0),
                       "avg_feedback_score": float(fb[3]) if fb[3] is not None else None},
            "by_status": [{"status": r[0], "count": r[1],
                           "pct": round(100.0 * r[1] / total, 1) if total else 0.0}
                          for r in by_status],
            "refusal_rate": round(100.0 * refused / total, 1) if total else 0.0,
            "error_rate": round(100.0 * errored / total, 1) if total else 0.0,
        }

    async def budget_status(self, *, record_tenant_id: UUID | None) -> dict[str, Any]:
        """Active token budgets vs current-period usage (config 0219 + live use)."""
        tid = str(record_tenant_id) if record_tenant_id is not None else None
        async with self._sf() as session:
            budgets = (await session.execute(text(
                "SELECT tb.id, tb.budget_level, tb.period_type, tb.token_limit, "
                "tb.cost_limit_usd, tb.alert_at_pct, tb.hard_cap, tb.record_tenant_id, "
                "tb.workspace_id, tb.record_bot_id, coalesce(b.bot_id, tb.record_bot_id::text) bot_slug "
                "FROM token_budgets tb LEFT JOIN bots b ON b.id = tb.record_bot_id "
                "WHERE tb.is_active = true "
                "AND (CAST(:tid AS uuid) IS NULL OR tb.record_tenant_id = CAST(:tid AS uuid)) "
                "ORDER BY tb.budget_level, tb.period_type"), {"tid": tid})).fetchall()
            out: list[dict[str, Any]] = []
            for r in budgets:
                (bid, level, period, limit, cost_limit, alert_pct, hard_cap,
                 rt, ws, rb, bot_slug) = r
                win = ("date_trunc('month', now())" if period == "monthly"
                       else "date_trunc('day', now())")
                cond = ["record_tenant_id = CAST(:rt AS uuid)", f"started_at >= {win}"]
                up: dict[str, Any] = {"rt": str(rt)}
                scope = str(rt)
                if level == "workspace" and ws is not None:
                    cond.append("workspace_id = :ws")
                    up["ws"] = ws
                    scope = ws
                elif level == "bot" and rb is not None:
                    cond.append("record_bot_id = CAST(:rb AS uuid)")
                    up["rb"] = str(rb)
                    scope = bot_slug
                used = (await session.execute(text(
                    "SELECT coalesce(sum(total_tokens),0) tok, "
                    "round(coalesce(sum(cost_usd),0)::numeric,5) cost "
                    "FROM request_logs WHERE " + " AND ".join(cond)), up)).fetchone()
                used_tok = int(used[0] or 0)
                pct = round(100.0 * used_tok / limit, 1) if limit else 0.0
                out.append({
                    "id": bid, "level": level, "scope": scope, "period": period,
                    "token_limit": int(limit), "used_tokens": used_tok,
                    "used_cost_usd": float(used[1] or 0),
                    "cost_limit_usd": float(cost_limit) if cost_limit is not None else None,
                    "pct": pct, "alert_at_pct": alert_pct, "hard_cap": hard_cap,
                    "alerting": pct >= (alert_pct or 80),
                })
        return {"budgets": out}


__all__ = ["CrmAnalyticsService"]
