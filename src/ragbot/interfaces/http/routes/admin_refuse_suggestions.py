"""Admin endpoints: refuse-loop read path (active learning, SQL + FAQ).

GET /admin/bots/{bot_id}/refuse_suggestions  → list[RefuseSuggestion]
GET /admin/bots/{bot_id}/faq_candidates      → list[FAQCandidate]

RBAC level: 60 (admin). Both are read-only closing-the-loop surfaces:
they aggregate refused-question rows so the bot owner can spot corpus
gaps and fill answers themselves — the application NEVER injects an
answer on the owner's behalf (App-mindset sacred #10).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import text as sa_text

from ragbot.application.services.faq_candidate_service import (
    FAQCandidateService,
    SqlRefusedQuestionRepo,
)
from ragbot.shared.constants import (
    DEFAULT_ADMIN_LEVEL,
    DEFAULT_FAQ_CANDIDATE_WINDOW_DAYS,
    DEFAULT_FAQ_MIN_OCCURRENCES,
    MAX_FAQ_CANDIDATE_WINDOW_DAYS,
)
from ragbot.shared.rbac import require_min_level

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["admin-refuse-suggestions"])


class RefuseSuggestionOut(BaseModel):
    query_intent: str
    refuse_count: int
    last_seen: str | None = None
    sample_query: str | None = None

    model_config = {"from_attributes": True}


async def _require_admin(request: Request) -> None:
    """RBAC gate: refuse-suggestions read is admin-level (60).

    Previously declared as ``require_permission_dep(60)`` which silently
    accepts the int but blows up at request time (factory expects two str
    args ``(module, permission)``) — replaced with the numeric-level guard
    that mirrors the seeded module_permissions row for refuse_suggestions.
    """
    require_min_level(request, DEFAULT_ADMIN_LEVEL)


@router.get(
    "/admin/bots/{bot_id}/refuse_suggestions",
    response_model=list[RefuseSuggestionOut],
    dependencies=[Depends(_require_admin)],
)
async def get_refuse_suggestions(
    request: Request,
    bot_id: UUID,
) -> list[dict[str, Any]]:
    """Aggregate refused-query intents for a bot from request_logs.

    Groups by intent where ``answer_type IN ('no_context','blocked')``,
    ordered by refuse_count DESC. Returns up to 50 rows. Target P95 < 100 ms.
    """
    record_tenant_id = getattr(request.state, "record_tenant_id", None)
    if record_tenant_id is None:
        raise HTTPException(status_code=400, detail="Missing tenant context")

    query = sa_text("""
        SELECT
            COALESCE(rl.intent, 'unknown') AS query_intent,
            COUNT(*)                         AS refuse_count,
            MAX(rl.created_at)               AS last_seen,
            (SELECT rl2.question
               FROM request_logs rl2
              WHERE rl2.record_bot_id     = rl.record_bot_id
                AND rl2.record_tenant_id  = rl.record_tenant_id
                AND COALESCE(rl2.intent, 'unknown') = COALESCE(rl.intent, 'unknown')
                AND rl2.answer_type      IN ('no_context', 'blocked')
              ORDER BY rl2.created_at DESC
              LIMIT 1)                      AS sample_query
          FROM request_logs rl
         WHERE rl.record_bot_id     = :bot_id
           AND rl.record_tenant_id  = :tenant_id
           AND rl.answer_type      IN ('no_context', 'blocked')
         GROUP BY COALESCE(rl.intent, 'unknown')
         ORDER BY refuse_count DESC
         LIMIT 50
    """)

    sf = request.app.state.container.session_factory()
    async with sf() as session:
        result = await session.execute(
            query,
            {"bot_id": bot_id, "tenant_id": record_tenant_id},
        )
        rows = result.fetchall()

    suggestions: list[dict[str, Any]] = []
    for row in rows:
        suggestions.append({
            "query_intent": row.query_intent,
            "refuse_count": row.refuse_count,
            "last_seen": str(row.last_seen) if row.last_seen else None,
            "sample_query": row.sample_query,
        })

    return suggestions


class FAQCandidateOut(BaseModel):
    """One operator-reviewable FAQ candidate cluster.

    Surfaced for review only — the operator writes the answer + uploads
    it as supplementary corpus. The application never authors the answer.
    """

    cluster_id: str
    representative_question: str
    sample_questions: list[str]
    occurrence_count: int
    avg_top_score: float

    model_config = {"from_attributes": True}


@router.get(
    "/admin/bots/{bot_id}/faq_candidates",
    response_model=list[FAQCandidateOut],
    dependencies=[Depends(_require_admin)],
)
async def get_faq_candidates(
    request: Request,
    bot_id: UUID,
    since_days: int = Query(
        default=DEFAULT_FAQ_CANDIDATE_WINDOW_DAYS,
        ge=1,
        le=MAX_FAQ_CANDIDATE_WINDOW_DAYS,
    ),
    min_occurrences: int = Query(default=DEFAULT_FAQ_MIN_OCCURRENCES, ge=1),
) -> list[FAQCandidateOut]:
    """Cluster refused questions into FAQ candidates for operator review.

    Closes the D12 loop: mines ``request_logs`` rows refused in the last
    ``since_days`` days, embeds + greedy-clusters near-paraphrases, and
    returns clusters at/above ``min_occurrences``. This is a read/observe
    surface — it does NOT inject answers; the operator fills them in and
    re-uploads as corpus.

    The embedding spec is resolved per-bot via the model resolver so the
    candidate vectors share the bot's embedding space (same model as the
    live retrieval path).
    """
    record_tenant_id = getattr(request.state, "record_tenant_id", None)
    if record_tenant_id is None:
        raise HTTPException(status_code=400, detail="Missing tenant context")

    container = request.app.state.container
    embedding_spec = await container.model_resolver().resolve_embedding(
        bot_id, record_tenant_id=record_tenant_id,
    )
    service = FAQCandidateService(
        repo=SqlRefusedQuestionRepo(container.session_factory()),
        embedder=container.embedder(),
        embedding_spec=embedding_spec,
    )
    since = datetime.now(tz=UTC) - timedelta(days=since_days)
    candidates = await service.find_candidates(
        record_tenant_id=record_tenant_id,
        record_bot_id=bot_id,
        since=since,
        min_occurrences=min_occurrences,
    )
    return [
        FAQCandidateOut(
            cluster_id=c.cluster_id,
            representative_question=c.representative_question,
            sample_questions=c.sample_questions,
            occurrence_count=c.occurrence_count,
            avg_top_score=c.avg_top_score,
        )
        for c in candidates
    ]
