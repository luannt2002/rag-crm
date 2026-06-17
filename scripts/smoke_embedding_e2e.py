"""End-to-end retrieval smoke for a bot — top_score floor on known queries.

Uses ``ModelResolverService`` + ``LiteLLMEmbedder`` + ``PgVectorStore.search``
with the per-bot embedding spec resolved from the binding. Asserts
``top_score`` >= ``_TOP_SCORE_FLOOR`` so a regression in either the
embedder, the index, or the routing path surfaces immediately.

Run:
    set -a && source .env && set +a
    python3 scripts/smoke_embedding_e2e.py <record_bot_uuid> [<record_tenant_uuid>]

Domain-neutral: no brand / industry literal in the queries — they are
sourced from the operator-curated golden set. CLI accepts the queries
from stdin one per line; default falls back to three generic
service-question stems.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import structlog

from ragbot.bootstrap import Container
from ragbot.shared.constants import (
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_EMBEDDING_TASK_QUERY,
)

logger = structlog.get_logger(__name__)

# Generic service-question stems (Vietnamese) — bot-agnostic placeholders.
# Operator overrides via stdin for the live golden set.
_DEFAULT_QUERIES: tuple[str, ...] = (
    "giá dịch vụ bao nhiêu",
    "địa chỉ chi nhánh ở đâu",
    "thời gian làm việc thế nào",
)
_TOP_SCORE_FLOOR: float = 0.30


async def smoke(record_bot_id: uuid.UUID, record_tenant_id: uuid.UUID | None) -> int:
    container = Container()
    embedder = container.embedder()
    vector_store = container.vector_store()
    resolver = container.model_resolver()

    spec = await resolver.resolve_embedding(
        record_bot_id=record_bot_id,
        record_tenant_id=record_tenant_id,
    )
    print(
        f"resolved spec: model={spec.model_name} dim={spec.dimension} "
        f"task_default={spec.task} column={DEFAULT_EMBEDDING_COLUMN}",
    )

    queries = [q.strip() for q in sys.stdin.read().splitlines() if q.strip()]
    if not queries:
        queries = list(_DEFAULT_QUERIES)
    print(f"running {len(queries)} queries (top_score floor = {_TOP_SCORE_FLOOR})")

    failures = 0
    for q in queries:
        query_spec = spec.model_copy(update={"task": DEFAULT_EMBEDDING_TASK_QUERY})
        emb = await embedder.embed_one(
            q, spec=query_spec, record_tenant_id=record_tenant_id,
        )
        if not emb:
            print(f"  q={q!r} EMBED_FAILED")
            failures += 1
            continue
        rows = await vector_store.search(
            query_embedding=emb,
            record_bot_id=record_bot_id,
            top_k=5,
            embedding_column=DEFAULT_EMBEDDING_COLUMN,
        )
        top = float(rows[0]["score"]) if rows else 0.0
        verdict = "PASS" if top >= _TOP_SCORE_FLOOR else "FAIL"
        print(f"  q={q!r:30s} top_score={top:.4f} hits={len(rows)} {verdict}")
        if top < _TOP_SCORE_FLOOR:
            failures += 1

    return 0 if failures == 0 else 1


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "Usage: smoke_embedding_e2e.py <record_bot_uuid> [<record_tenant_uuid>]",
            file=sys.stderr,
        )
        return 2
    bot_id = uuid.UUID(sys.argv[1].strip())
    tenant_id = uuid.UUID(sys.argv[2].strip()) if len(sys.argv) > 2 else None
    return asyncio.run(smoke(bot_id, tenant_id))


if __name__ == "__main__":
    raise SystemExit(main())
