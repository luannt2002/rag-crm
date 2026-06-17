"""ModelInvocationLogger — wrapper cho mọi LLM/embed/rerank call (INVARIANT #2).

Mọi call qua wrapper này sẽ ghi 1 row vào `model_invocations` với full chain
audit: invocation_id, message_id (khách), record_request_id, record_tenant_id (nullable),
step_id, attempt_no, purpose, provider, model_id/version, params, prompt/
response hash, retrieved_chunk_ids, tokens, cost, timing, status, finish_reason,
cached flag.

Usage::

    async with logger.invoke_model(
        message_id=123,  # example only
        record_tenant_id=tid,
        record_request_id=rid,
        purpose="generation",
        provider="<provider>",
        model_id="<model-id>",  # resolved from cfg, never hardcoded
        user_prompt="hello",
    ) as ctx:
        # ... thực hiện LLM call thật ...
        ctx.record(
            response="hi there",
            prompt_tokens=10,
            completion_tokens=5,
            cost_usd=0.001,
            finish_reason="stop",
        )

KHÔNG tự call LLM — chỉ audit. Task 2 sẽ gói LLM call thật dùng wrapper này.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.models_invocation import (
    ModelInvocationModel,
)
from ragbot.infrastructure.db.models_monitoring import (
    RequestLogModel,
    RequestStepModel,
)
from ragbot.infrastructure.observability.metrics import (
    cost_usd_total,
    model_invocation_total,
    tokens_used_total,
)
from ragbot.infrastructure.observability.tracing import get_tracer
from ragbot.shared.constants import FEATURE_NAME_MAX_LEN, WORKSPACE_SYSTEM_SLUG
from ragbot.shared.hashing import content_hash, content_hash_required

_tracer = get_tracer("ragbot.invocation_logger")


@dataclass
class InvocationContext:
    """Handle returned by `invoke_model`. Call `.record(...)` once inside
    the async-with block to capture the response + usage + cost."""

    invocation_id: uuid.UUID
    _recorded: bool = field(default=False, init=False)
    response_text: str | None = field(default=None, init=False)
    response_hash: str | None = field(default=None, init=False)
    prompt_tokens: int = field(default=0, init=False)
    completion_tokens: int = field(default=0, init=False)
    cost_usd: Decimal = field(default=Decimal("0"), init=False)
    finish_reason: str | None = field(default=None, init=False)
    cached: bool = field(default=False, init=False)

    def record(
        self,
        response: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float | Decimal,
        finish_reason: str = "stop",
        cached: bool = False,
    ) -> None:
        self.response_text = response
        self.response_hash = content_hash(response)
        self.prompt_tokens = int(prompt_tokens)
        self.completion_tokens = int(completion_tokens)
        self.cost_usd = (
            cost_usd if isinstance(cost_usd, Decimal) else Decimal(str(cost_usd))
        )
        self.finish_reason = finish_reason
        self.cached = cached
        self._recorded = True


class InvocationLogger:
    """Persist model invocations. All writes via `session_factory`."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @asynccontextmanager
    async def invoke_model(
        self,
        *,
        message_id: int,
        record_tenant_id: uuid.UUID | None,
        record_request_id: uuid.UUID | None,
        workspace_id: str | None = None,
        attempt_no: int = 1,
        purpose: str,
        provider: str,
        model_id: str,
        model_version: str | None = None,
        user_prompt: str | None = None,
        feature_name: str | None = None,
    ) -> AsyncIterator[InvocationContext]:
        """Insert row status='running', yield ctx, then update on exit.

        ``feature_name`` is an optional high-level subsystem / product
        feature label (``query.generation``, ``ingest.enrich``,
        ``router.classify``) persisted to ``model_invocations.feature_name``
        so cost audit can roll calls up by feature. ``None`` lets the row
        stay NULL — cost audit groups those under ``unset``. Values
        longer than ``FEATURE_NAME_MAX_LEN`` are truncated rather than
        raising (observability MUST never break the LLM call).
        """
        invocation_id = uuid.uuid4()
        started_at = datetime.now(timezone.utc)
        started_perf = time.perf_counter()

        _span_cm = _tracer.start_as_current_span("model_invocation")
        _span = _span_cm.__enter__()
        try:
            _span.set_attribute("invocation_id", str(invocation_id))
            _span.set_attribute("message_id", int(message_id))
            if record_tenant_id is not None:
                _span.set_attribute("tenant_id", str(record_tenant_id))
            _span.set_attribute("purpose", purpose)
            _span.set_attribute("provider", provider)
            _span.set_attribute("model_id", model_id)
        except Exception:  # noqa: BLE001 — tracing never breaks business logic
            pass

        user_prompt_hash = (
            content_hash_required(user_prompt) if user_prompt is not None else None
        )
        full_payload_hash = content_hash_required(
            f"{purpose}|{provider}|{model_id}|{model_version or ''}|{user_prompt or ''}"
        )

        # Truncate rather than raise — observability MUST never break the LLM
        # call. Empty string is normalised to NULL so legacy + new-empty rows
        # roll up under the same "unset" bucket in cost audit.
        _feature_name: str | None
        if feature_name is None:
            _feature_name = None
        else:
            trimmed = feature_name.strip()
            _feature_name = trimmed[:FEATURE_NAME_MAX_LEN] if trimmed else None

        # Atomic-write design (Bug 1 P0 fix):
        # Old design did INSERT(running) + UPDATE(final) in two separate
        # sessions. Process kill / SIGTERM / OOM between the two sessions
        # left rows stuck status='running' forever, poisoning audit
        # dashboards and bloating the table. New design does a SINGLE
        # INSERT after the yield with the final status — process kill
        # mid-yield → row never inserted at all (clean: no half-truth in
        # DB). On the rare case of a kill AFTER yield but BEFORE commit,
        # the janitor (scripts/cleanup_stuck_invocations.py) provides a
        # second line of defense by sweeping any orphaned 'running' rows
        # older than DEFAULT_INVOCATION_STUCK_TIMEOUT_S — though under
        # this design no row should ever land in 'running' on disk.
        ctx = InvocationContext(invocation_id=invocation_id)
        status = "success"
        try:
            yield ctx
        except Exception:  # noqa: BLE001 — mark invocation failed then re-raise
            status = "failed"
            raise
        finally:
            finished_at = datetime.now(timezone.utc)
            duration_ms = int((time.perf_counter() - started_perf) * 1000)
            if ctx._recorded:
                final_status = "cached" if ctx.cached else status
            else:
                # consumer never called record() — treat as failed unless
                # exception already set it.
                final_status = status if status != "success" else "failed"

            # Single-session INSERT with final values. ON CONFLICT DO
            # UPDATE is defensive against an extremely unlikely UUID
            # collision; under normal operation this is a plain INSERT.
            # ``model_invocations`` is FK-chain scoped (record_request_id →
            # request_logs → bots). Caller is expected to thread the slug
            # from the bot config; tenant-level diagnostic invocations
            # (preflight smoke, eval runners) without a bot context fall
            # back to the system slug to satisfy the NOT NULL CHECK.
            stmt = pg_insert(ModelInvocationModel).values(
                invocation_id=invocation_id,
                message_id=message_id,
                record_request_id=record_request_id,
                record_tenant_id=record_tenant_id,
                workspace_id=workspace_id or WORKSPACE_SYSTEM_SLUG,
                attempt_no=attempt_no,
                purpose=purpose,
                feature_name=_feature_name,
                provider=provider,
                model_id=model_id,
                model_version=model_version,
                user_prompt_hash=user_prompt_hash,
                full_payload_hash=full_payload_hash,
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=duration_ms,
                response_hash=ctx.response_hash,
                prompt_tokens=ctx.prompt_tokens,
                completion_tokens=ctx.completion_tokens,
                cost_usd=ctx.cost_usd,
                status=final_status,
                finish_reason=ctx.finish_reason,
                cached=ctx.cached,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=[ModelInvocationModel.invocation_id],
                set_={
                    "finished_at": stmt.excluded.finished_at,
                    "duration_ms": stmt.excluded.duration_ms,
                    "response_hash": stmt.excluded.response_hash,
                    "prompt_tokens": stmt.excluded.prompt_tokens,
                    "completion_tokens": stmt.excluded.completion_tokens,
                    "cost_usd": stmt.excluded.cost_usd,
                    "status": stmt.excluded.status,
                    "finish_reason": stmt.excluded.finish_reason,
                    "cached": stmt.excluded.cached,
                },
            )
            async with self._sf() as session:
                await session.execute(stmt)
                await session.commit()

            # --- Prometheus emit (low cardinality labels only) -----------
            try:
                model_invocation_total.labels(
                    purpose=purpose,
                    provider=provider,
                    status=final_status,
                ).inc()
                if ctx.prompt_tokens:
                    tokens_used_total.labels(
                        purpose=purpose, model_id=model_id, kind="prompt",
                    ).inc(ctx.prompt_tokens)
                if ctx.completion_tokens:
                    tokens_used_total.labels(
                        purpose=purpose, model_id=model_id, kind="completion",
                    ).inc(ctx.completion_tokens)
                if ctx.cost_usd and float(ctx.cost_usd) > 0:
                    cost_usd_total.labels(
                        purpose=purpose, model_id=model_id,
                    ).inc(float(ctx.cost_usd))
            except Exception:  # noqa: BLE001 — metrics must never break pipeline
                pass

            # Close tracing span (status attribute reflects outcome).
            try:
                _span.set_attribute("status", final_status)
                _span.set_attribute("duration_ms", duration_ms)
            except Exception:  # noqa: BLE001
                pass
            _span_cm.__exit__(None, None, None)

    # --- Query helpers -----------------------------------------------------
    async def fetch_by_message_id(
        self,
        message_id: int,
        *,
        record_tenant_id: uuid.UUID,
    ) -> dict[str, list[dict]]:
        """Return request_logs + request_steps + model_invocations liên quan
        ``message_id`` — **scoped to ``record_tenant_id``**.

        ``record_tenant_id`` is keyword-only and REQUIRED: ``message_id`` is
        the upstream BIGINT (guessable, not UUID), so without a tenant
        filter any tenant-admin could read another tenant's pipeline trace
        by iterating ids. closes that hole; the
        repo is now the single source of truth for the tenant filter, the
        route just supplies the JWT-resolved tenant.
        """

        async def _fetch_logs() -> list[RequestLogModel]:
            async with self._sf() as s:
                return (
                    await s.execute(
                        select(RequestLogModel).where(
                            RequestLogModel.message_id == message_id,
                            RequestLogModel.record_tenant_id == record_tenant_id,
                        )
                    )
                ).scalars().all()

        async def _fetch_invocations() -> list[ModelInvocationModel]:
            async with self._sf() as s:
                return (
                    await s.execute(
                        select(ModelInvocationModel).where(
                            ModelInvocationModel.message_id == message_id,
                            ModelInvocationModel.record_tenant_id == record_tenant_id,
                        )
                    )
                ).scalars().all()

        logs, invocations = await asyncio.gather(
            _fetch_logs(), _fetch_invocations(),
        )

        req_ids = [r.request_id for r in logs]
        steps: list[RequestStepModel] = []
        if req_ids:
            async with self._sf() as session:
                steps = (
                    await session.execute(
                        select(RequestStepModel).where(
                            RequestStepModel.record_request_id.in_(req_ids),
                            RequestStepModel.record_tenant_id == record_tenant_id,
                        )
                    )
                ).scalars().all()

        return {
            "request_logs": [_row_to_dict(r) for r in logs],
            "request_steps": [_row_to_dict(s) for s in steps],
            "model_invocations": [_row_to_dict(i) for i in invocations],
        }


def _row_to_dict(row: object) -> dict:
    """SQLAlchemy row -> plain dict (no relationships)."""
    out: dict = {}
    for col in row.__table__.columns:  # type: ignore[attr-defined]
        val = getattr(row, col.name)
        if isinstance(val, (uuid.UUID,)):
            val = str(val)
        elif isinstance(val, datetime):
            val = val.isoformat()
        elif isinstance(val, Decimal):
            val = float(val)
        elif isinstance(val, bytes):
            val = f"<{len(val)} bytes>"
        out[col.name] = val
    return out


__all__ = ["InvocationContext", "InvocationLogger"]
