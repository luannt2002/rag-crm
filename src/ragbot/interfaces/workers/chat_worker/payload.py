"""Chat-worker payload helpers — PII redaction + tenant-id resolution.

Relocated verbatim from the former single-file ``chat_worker.py`` during the
god-file package split. No logic change — pure relocation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from ragbot.bootstrap import Container
from ragbot.shared.bot_limits import resolve_bot_limit

logger = structlog.get_logger(__name__)

__all__ = [
    "_maybe_redact_chat_query",
    "_resolve_record_tenant_id",
]


def _maybe_redact_chat_query(
    text: str,
    *,
    bot_cfg: Any,
    pii_redactor: Any,
    record_tenant_id: Any,
    record_bot_id: Any,
) -> str:
    """Apply PII redaction at the chat-query worker boundary when opted-in.

    Master Finding #4 fix: this is THE wire hook between ``valid.content``
    and the message-persist / request-log / LLM call so no raw PII reaches
    the DB or the model. Per-bot opt-in via
    ``plan_limits.pii_redaction_enabled`` (default False) so existing
    tenants see no behaviour change. When enabled the raw user query is
    masked (e.g. ``[EMAIL]``, ``[PHONE]``, ``[CCCD]``) BEFORE the message
    row is persisted, the request-log hash is computed, and the prompt is
    sent to the LLM.

    Audit emits ``pii_redacted`` carrying only the mask_count + per-type
    histogram (NEVER raw PII). Failure modes degrade silent
    (CLAUDE.md graceful-degradation rule):

    - missing redactor → passthrough (no audit)
    - toggle off → passthrough (no audit)
    - empty entity list → passthrough (no audit; mask_count=0 degenerate)
    - redactor.redact() raises → ``pii_redaction_failed`` audit + passthrough

    Mirrors :func:`ragbot.application.services.document_service.\\
    _maybe_redact_ingest_content` so the chat + ingest boundaries share
    identical opt-in semantics and audit shape.

    @return: masked text (when toggle on + matches) OR unchanged input
    """
    if pii_redactor is None:
        return text
    if not resolve_bot_limit(bot_cfg, "pii_redaction_enabled",
                             system_default=False):
        return text
    try:
        masked, entities = pii_redactor.redact(text)
    except Exception as exc:  # noqa: BLE001 — redactor failure must never 5xx the chat. Log + skip.
        logger.warning(
            "pii_redaction_failed",
            surface="chat_query",
            stage="redact",
            record_tenant_id=str(record_tenant_id) if record_tenant_id is not None else None,
            record_bot_id=str(record_bot_id) if record_bot_id is not None else None,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return text
    if not entities:
        return text
    mask_types: dict[str, int] = {}
    for ent in entities:
        kind = str(ent.get("type", "UNKNOWN"))
        mask_types[kind] = mask_types.get(kind, 0) + 1
    logger.info(
        "pii_redacted",
        surface="chat_query",
        record_tenant_id=str(record_tenant_id) if record_tenant_id is not None else None,
        record_bot_id=str(record_bot_id) if record_bot_id is not None else None,
        mask_count=len(entities),
        mask_types=mask_types,
        provider=pii_redactor.get_provider_name(),
    )
    return masked


async def _resolve_record_tenant_id(
    payload: dict[str, Any], container: Container,
) -> UUID | None:
    """Resolve record_tenant_id UUID from payload — UUID claim wins over legacy INT."""
    raw = (
        payload.get("record_tenant_id")
        or payload.get("tenant_uuid")
    )
    if raw:
        try:
            return UUID(str(raw))
        except (TypeError, ValueError):
            logger.warning("chat_worker_invalid_record_tenant_id", raw=str(raw))
    # Upstream still sending INT — translate via tenants.config.
    upstream_int = payload.get("tenant_id")
    if upstream_int is not None:
        try:
            from ragbot.interfaces.http.middlewares.tenant_context import (
                _resolve_upstream_int_tenant,
            )
            return await _resolve_upstream_int_tenant(
                container.session_factory(), int(upstream_int),
            )
        except (TypeError, ValueError):
            logger.warning("chat_worker_upstream_int_tenant_invalid", raw=str(upstream_int))
    return None
