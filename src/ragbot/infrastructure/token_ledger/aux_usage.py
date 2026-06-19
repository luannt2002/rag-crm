"""Emit a rerank / embedding usage row to the token ledger from an adapter.

Mirrors the LLM router's ledger emit: snapshots the per-request 4-key identity
from ContextVars so the ledger row is a self-contained immutable fact. The
ledger is an auxiliary, fire-and-forget sink — this helper NEVER raises into
the adapter's hot path (graceful-degradation: an aux dependency must not break
the primary rerank/embed call).
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
    total_tokens: int = 0,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    status: str = "active",
    purpose: str | None = None,
) -> None:
    """Snapshot the current request context and emit one rerank/embed row."""
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
                trace_id=(trace_id_ctx.get() or None),
                input_tokens=input_tokens,
                total_tokens=total_tokens or input_tokens,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                status=status,
            )
        )


__all__ = ["emit_aux_usage"]
