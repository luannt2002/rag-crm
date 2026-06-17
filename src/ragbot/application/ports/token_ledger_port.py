"""Port — token-log-center sink for every token-spending action.

A single ``emit`` boundary that the LLM router + embedding/rerank adapters call
once per call. Implementations MUST be fire-and-forget (never block the caller's
LLM coroutine) and MUST degrade silently — an audit sink can never kill the
money-path it observes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TokenLedgerEntry:
    """One token-spending action (LLM / embedding / rerank). All fields snapshot
    at emit-time so the row is a self-contained immutable fact (no later JOINs)."""

    mode: str                       # 'ingest' | 'query' | ...
    action: str                     # 'llm' | 'embedding' | 'rerank'
    purpose: str | None = None      # 'cr_enrichment'|'narrate'|'generate'|'embed'|...
    provider: str | None = None
    model: str | None = None
    # 4-key identity snapshot (value-copied, no FK)
    record_tenant_id: UUID | None = None
    record_bot_id: UUID | None = None
    bot_id: str | None = None
    workspace_id: str | None = None
    channel_type: str | None = None
    request_id: UUID | None = None
    document_id: UUID | None = None
    trace_id: str | None = None
    # token counts
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0
    # timing
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None
    # unit-price snapshot ($/1k) — cost computed on demand
    input_unit_price: float | None = None
    output_unit_price: float | None = None
    cached_unit_price: float | None = None
    cost_usd: float | None = None
    status: str = "active"
    finish_reason: str | None = None


class TokenLedgerPort(Protocol):
    """Append-only sink. ``emit`` returns immediately (fire-and-forget)."""

    def emit(self, entry: TokenLedgerEntry) -> None: ...
