"""Step tracker — records timing + tokens for each pipeline stage.

Used by chat_worker / document_worker to populate `request_steps` table.

Phase-B B4 — optional ``batch_enabled`` mode buffers all step rows in
memory and flushes via a single ``add_steps_batch()`` call after the
pipeline finishes. Caller is responsible for invoking ``flush()`` exactly
once at end of turn (e.g. after ``graph.ainvoke``); the default OFF mode
keeps per-step INSERT semantics so callers that want immediate visibility
(e.g. document_worker / ingest jobs) see step rows the moment a stage exits.
"""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

from ragbot.application.ports.metrics_port import MetricsPort
from ragbot.shared.constants import PII_SURFACE_REQUEST_STEPS
from ragbot.shared.pii_universal import redact_mapping, redact_text
from ragbot.shared.types import TenantId

logger = logging.getLogger(__name__)


class StepTracker:
    """Accumulator for step-level telemetry within a single request.

    ``kind`` distinguishes the parent log namespace:

    - ``"query"`` (default) — chat / query path. Parent ``request_logs`` row
      is the user-facing turn (``message_id`` = upstream chat message id).
    - ``"ingest"`` — document ingestion path (Phase D observability). Parent
      ``request_logs`` row is created by ``document_worker`` per ingest job
      with ``connect_id="ingest"``; ``record_request_id`` here is the ingest
      job UUID. Each step row carries ``metadata_json.step_kind="ingest"``
      so analytics dashboards can split ingest vs query without joining
      back to ``request_logs``.

    ``batch_enabled`` (Phase-B B4) — when True, ``step()`` exits buffer
    the row in ``_buffer`` instead of issuing an INSERT. The caller MUST
    call ``flush()`` at end-of-turn to persist the buffered rows in a
    single ``add_steps_batch()`` round-trip. If ``flush()`` is never
    called, the buffered rows are dropped (degraded observability). The
    flag is per-tracker (per-request) so live A/B is safe.
    """

    def __init__(
        self,
        *,
        request_id: UUID,
        record_tenant_id: TenantId,
        repo: Any,  # RequestLogRepository — avoid circular import
        kind: str = "query",
        metrics: MetricsPort | None = None,
        pii_redactor: Any | None = None,
        bot_cfg: Any | None = None,
        record_bot_id: Any | None = None,
        batch_enabled: bool = False,
    ) -> None:
        """Init step accumulator.

        @param pii_redactor: optional ``PiiRedactorPort``. When provided
            AND ``bot_cfg`` has ``plan_limits.pii_redaction_universal=True``,
            the step ``metadata`` dict + ``error`` text are masked before
            the row hits ``request_steps`` (Phase D2 universal coverage).
            Falsy / NullPiiRedactor = passthrough.
        @param bot_cfg: BotConfig DTO with ``plan_limits``. Required to
            evaluate the toggle; ``None`` ⇒ universal redaction skipped.
        @param record_bot_id: bot UUID for the structured ``pii_redacted``
            audit event surface tag.
        """
        self._request_id = request_id
        self._tenant_id = record_tenant_id
        self._repo = repo
        self._order = 0
        self._kind = kind
        self._metrics = metrics
        self._pii_redactor = pii_redactor
        self._bot_cfg = bot_cfg
        self._record_bot_id = record_bot_id
        # Phase-B B4 — buffered-batch flush state. Default OFF preserves the
        # legacy per-step INSERT semantics so callers (e.g. document_worker)
        # see step rows immediately. The ``batch_enabled`` and
        # ``buffer_size`` properties read these attrs directly; missing
        # initialisation here previously raised AttributeError on first access.
        self._batch_enabled: bool = bool(batch_enabled)
        self._buffer: list[dict] = []

    @property
    def kind(self) -> str:
        """Tracker namespace — ``"query"`` or ``"ingest"``."""
        return self._kind

    @property
    def batch_enabled(self) -> bool:
        """True when buffered batch flush mode is active."""
        return self._batch_enabled

    @property
    def buffer_size(self) -> int:
        """Pending row count (visible for tests / metrics)."""
        return len(self._buffer)

    @asynccontextmanager
    async def step(
        self,
        name: str,
        *,
        model_used: str | None = None,
        binding_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator["StepContext"]:
        self._order += 1
        ctx = StepContext(
            name=name,
            order=self._order,
            model_used=model_used,
            binding_id=binding_id,
            metadata=dict(metadata or {}),
        )
        ctx._t0 = time.monotonic()
        try:
            yield ctx
            ctx.status = "success"
        except Exception as exc:  # noqa: BLE001 — observability decorator: must record any failure type then reraise unchanged so the caller still sees the original exception.
            ctx.status = "failed"
            ctx.error = str(exc)
            raise
        finally:
            ctx.duration_ms = int((time.monotonic() - ctx._t0) * 1000)
            if self._metrics is not None:
                # MetricsPort impls are responsible for swallowing
                # prometheus errors internally — no try/except here.
                self._metrics.observe_step_duration(
                    ctx.name, ctx.duration_ms / 1000.0,
                )
            # Inject ``step_kind`` into metadata so the persisted row
            # records whether it belongs to a chat-query or ingest job.
            # No schema change — rides on existing ``metadata_json`` JSONB.
            _meta = dict(ctx.metadata)
            _meta.setdefault("step_kind", self._kind)
            # Phase D2 — universal PII coverage. When the owning bot
            # opted into ``pii_redaction_universal=True``, any user-text
            # leaking through ``ctx.set_metadata(...)`` or the failure
            # message in ``ctx.error`` is masked at the persistence
            # boundary. Best-effort: a misconfigured redactor must
            # never break ``request_steps`` (observability is
            # graceful-degrade per CLAUDE.md).
            error_text = ctx.error
            if self._pii_redactor is not None and self._bot_cfg is not None:
                _meta = redact_mapping(
                    _meta,
                    redactor=self._pii_redactor,
                    bot_cfg=self._bot_cfg,
                    surface=PII_SURFACE_REQUEST_STEPS,
                    record_tenant_id=self._tenant_id,
                    record_bot_id=self._record_bot_id,
                    extra={"step_name": ctx.name, "step_kind": self._kind,
                           "step_field": "metadata"},
                ) or _meta
                error_text = redact_text(
                    error_text,
                    redactor=self._pii_redactor,
                    bot_cfg=self._bot_cfg,
                    surface=PII_SURFACE_REQUEST_STEPS,
                    record_tenant_id=self._tenant_id,
                    record_bot_id=self._record_bot_id,
                    extra={"step_name": ctx.name, "step_kind": self._kind,
                           "step_field": "error"},
                )
            row = {
                "step_name": ctx.name,
                "step_order": ctx.order,
                "model_used": ctx.model_used,
                "record_binding_id": ctx.binding_id,
                "input_tokens": ctx.input_tokens,
                "output_tokens": ctx.output_tokens,
                "cost_usd": ctx.cost_usd,
                "duration_ms": ctx.duration_ms,
                "status": ctx.status,
                "error": error_text,
                "metadata": _meta,
            }
            if self._batch_enabled:
                # Phase-B B4 buffered path — defer the INSERT to ``flush()``.
                # Caller (chat_worker) MUST call flush() at end-of-turn or
                # the rows are dropped (graceful-degrade observability).
                # NB: ``finally`` body, so a bare ``return`` here would
                # swallow the in-flight exception from the ``raise`` above
                # — append-only, let the exception propagate.
                self._buffer.append(row)
            else:
                await self._repo.add_step(
                    request_id=self._request_id,
                    record_tenant_id=self._tenant_id,
                    **row,
                )

    async def flush(self) -> int:
        """Phase-B B4 — drain the buffered rows in one ``add_steps_batch``
        round-trip. Idempotent: a second flush on an empty buffer is a
        no-op and returns 0 without touching the repo. Best-effort:
        ``add_steps_batch`` failures are logged + swallowed (the
        user-facing answer has already been emitted; observability loss
        is preferable to crashing post-response cleanup per CLAUDE.md
        graceful-degrade rule). Returns the number of rows successfully
        persisted (== ``len(buffer)`` on success, ``0`` on repo failure
        or empty buffer).
        """
        if not self._buffer:
            return 0
        pending = self._buffer
        self._buffer = []
        try:
            written = await self._repo.add_steps_batch(
                request_id=self._request_id,
                record_tenant_id=self._tenant_id,
                steps=pending,
            )
        except Exception as exc:  # noqa: BLE001 — post-response cleanup, observability degrades silent
            logger.warning(
                "step_tracker_batch_flush_failed request_id=%s buffered=%d "
                "error_type=%s error=%s",
                self._request_id,
                len(pending),
                type(exc).__name__,
                exc,
            )
            return 0
        return int(written) if written is not None else len(pending)


class StepContext:
    def __init__(
        self,
        *,
        name: str,
        order: int,
        model_used: str | None,
        binding_id: UUID | None,
        metadata: dict[str, Any],
    ) -> None:
        self.name = name
        self.order = order
        self.model_used = model_used
        self.binding_id = binding_id
        self.metadata = metadata
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self.duration_ms = 0
        self.status = "running"
        self.error: str | None = None
        self._t0 = 0.0

    def add_tokens(self, *, prompt: int = 0, completion: int = 0, cost_usd: float = 0.0) -> None:
        self.input_tokens += prompt
        self.output_tokens += completion
        self.cost_usd += cost_usd

    def set_metadata(self, **kwargs: Any) -> None:  # noqa: ANN401
        self.metadata.update(kwargs)

    def record_llm(
        self,
        *,
        model_used: str | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """One-shot helper to attach model + token + cost to an LLM-bound step.

        Wave M3.2 2026-05-20: replaces the boilerplate
        ``ctx.model_used = ...; ctx.add_tokens(...)`` pair across 10 LLM
        steps (understand_query, multi_query_fanout, grade, generate,
        grounding_check, rewrite, rewrite_retry, adaptive_decompose,
        reflect, condense_question). Pre-fix, ``request_steps.model_used``
        / ``cost_usd`` / ``input_tokens`` were NULL on every row except
        ``generate`` (and even that was set only via the ``step()`` kwarg
        path which fired BEFORE the LLM call resolved its model).
        """
        if model_used is not None:
            self.model_used = model_used
        self.input_tokens += prompt_tokens
        self.output_tokens += completion_tokens
        self.cost_usd += cost_usd


__all__ = ["StepContext", "StepTracker"]
