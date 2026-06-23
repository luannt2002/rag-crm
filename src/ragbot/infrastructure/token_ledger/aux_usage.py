"""Emit a rerank / embedding usage row to the token ledger from an adapter.

Mirrors the LLM router's ledger emit: snapshots the per-request 4-key identity
(+ ``request_id`` for per-turn CRM reconciliation) from ContextVars so the
ledger row is a self-contained immutable fact. The ledger is an auxiliary,
fire-and-forget sink — this helper NEVER raises into the adapter's hot path
(graceful-degradation: an aux dependency must not break the primary
rerank/embed call).
"""
from __future__ import annotations

import contextlib
from datetime import datetime
from uuid import UUID

from ragbot.application.ports.token_ledger_port import TokenLedgerEntry, TokenLedgerPort
from ragbot.config.logging import (
    bot_id_ctx,
    channel_type_ctx,
    mode_ctx,
    record_bot_id_ctx,
    request_id_ctx,
    tenant_id_ctx,
    trace_id_ctx,
    workspace_id_ctx,
)


def _safe_uuid(val: str | None) -> UUID | None:
    if not val or val == "UNSET":
        return None
    try:
        return UUID(val)
    except (ValueError, TypeError):
        return None


def emit_aux_usage(
    ledger: TokenLedgerPort | None,
    *,
    action: str,
    provider: str | None,
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    status: str = "success",
    purpose: str | None = None,
    input_unit_price: float | None = None,
    cost_usd: float | None = None,
) -> None:
    """Snapshot the current request context and emit one rerank/embed row.

    ``status`` defaults to ``"success"`` — the emit fires only after a
    completed adapter call. ``input_unit_price`` / ``cost_usd`` snapshot the
    per-call price so embed/rerank cost is non-NULL in the ledger (the cost
    dashboard sums ``cost_usd``; a NULL there silently under-reports spend).
    ``request_id`` is snapshot from the contextvar so the row joins back to
    the turn-level request_logs row.
    """
    if ledger is None:
        return
    duration_ms: int | None = None
    if started_at is not None and finished_at is not None:
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
    # Fire-and-forget: an aux ledger failure must never break rerank/embed.
    with contextlib.suppress(Exception):
        ledger.emit(
            TokenLedgerEntry(
                mode=(mode_ctx.get() or "query"),
                action=action,
                purpose=purpose,
                provider=provider,
                model=model,
                record_tenant_id=_safe_uuid(tenant_id_ctx.get()),
                record_bot_id=_safe_uuid(record_bot_id_ctx.get()),
                bot_id=(bot_id_ctx.get() or None),
                workspace_id=(workspace_id_ctx.get() or None),
                channel_type=(channel_type_ctx.get() or None),
                request_id=_safe_uuid(request_id_ctx.get()),
                trace_id=(trace_id_ctx.get() or None),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens or input_tokens,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                input_unit_price=input_unit_price,
                cost_usd=cost_usd,
                status=status,
            )
        )


__all__ = ["emit_aux_usage"]
