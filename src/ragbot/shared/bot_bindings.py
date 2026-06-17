"""Shared helper — create ``bot_model_bindings`` for new or synced bots.

Uses internal column names ``record_bot_id`` / ``record_model_id``;
``record_tenant_id`` is REQUIRED (3-key identity).
  - `temperature` + `max_tokens` defaults moved to `shared/constants.py`
    (zero-hardcode rule).
  - When the binding for the LLM model resolves to a `kind='rerank'`
    AI model, we still record it under `purpose='reranker'` so the
    per-bot reranker_resolver picks it up. Caller controls intent via
    `purposes` list.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import text

from ragbot.shared.constants import (
    DEFAULT_GENERATION_MAX_TOKENS,
    DEFAULT_LLM_TEMPERATURE,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


async def ensure_bot_bindings(
    session: AsyncSession,
    record_bot_id: str | UUID,
    model_id: str | None,
    embedding_model_id: str | None,
    *,
    record_tenant_id: str | UUID | None = None,
    workspace_id: str | None = None,
    rerank_model_id: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> int:
    """Create ``bot_model_bindings`` rows if missing — shared by create + sync.

    Adds bindings for ``llm_primary``, ``embedding``, and (when supplied)
    ``reranker`` purpose. Idempotent: skips a row whose
    ``(record_bot_id, purpose, active=true)`` already exists.

    ``bot_model_bindings.record_tenant_id`` is NOT NULL in the live
    schema. Callers MUST pass ``record_tenant_id`` for any new-bot flow
    where the tenant UUID is known; passing ``None`` will fail the
    INSERT with an IntegrityError, which is the correct behaviour
    (silent NULL would break tenant isolation on the bindings table).

    @param session: DB session (in an open transaction)
    @param record_bot_id: bot UUID PK (``bots.id``)
    @param record_tenant_id: owning tenant UUID. NOT optional in
        practice — defaulted to None only for backward-compat with
        callers that haven't yet been updated. Required for new flows.
    @param model_id: ``llm_primary`` model UUID (None → skip purpose)
    @param embedding_model_id: ``embedding`` model UUID (None → skip)
    @param rerank_model_id: ``reranker`` model UUID. None → skip → bot
        falls back to ``NullReranker`` (RRF-only retrieval, no
        cross-encoder rerank).
    @param temperature: LLM temperature (None → DEFAULT_LLM_TEMPERATURE)
    @param max_tokens: LLM max tokens (None → DEFAULT_GENERATION_MAX_TOKENS)
    @return: number of bindings inserted (0..3)
    """
    if temperature is None:
        temperature = DEFAULT_LLM_TEMPERATURE
    if max_tokens is None:
        max_tokens = DEFAULT_GENERATION_MAX_TOKENS

    from ragbot.application.dto.ai_specs import BindingPurpose

    bindings = (
        (BindingPurpose.LLM_PRIMARY.value, model_id),
        (BindingPurpose.EMBEDDING.value, embedding_model_id),
        (BindingPurpose.RERANK.value, rerank_model_id),
    )
    created = 0
    for purpose, mid in bindings:
        if mid is None:
            continue
        check = await session.execute(
            text(
                "SELECT 1 FROM bot_model_bindings "
                "WHERE record_bot_id = :bid AND purpose = :p AND active = true LIMIT 1",
            ),
            {"bid": str(record_bot_id), "p": purpose},
        )
        if check.fetchone():
            continue
        # workspace_id is NOT NULL on bot_model_bindings. Lift it from the
        # parent bots row when the caller didn't pass it explicitly so the
        # binding always lands in the same workspace as its bot.
        ws = workspace_id
        if ws is None:
            row = await session.execute(
                text("SELECT workspace_id FROM bots WHERE id = :bid"),
                {"bid": str(record_bot_id)},
            )
            ws = row.scalar()
        await session.execute(
            text(
                """
                INSERT INTO bot_model_bindings (
                    id, record_tenant_id, workspace_id, record_bot_id, purpose, record_model_id,
                    rank, weight, temperature, max_tokens, top_p,
                    extra_params, active, version, effective_from
                )
                VALUES (
                    :id, :tid, :ws, :bid, :purpose, :mid,
                    0, 100, :temp, :max_tok, 1.0,
                    CAST(:extra AS jsonb), true, 1, now()
                )
                """,
            ),
            {
                "id": uuid.uuid4(),
                "tid": str(record_tenant_id) if record_tenant_id else None,
                "ws": ws,
                "bid": str(record_bot_id),
                "purpose": purpose,
                "mid": mid,
                "temp": temperature,
                "max_tok": max_tokens,
                "extra": "{}",
            },
        )
        created += 1
    return created
