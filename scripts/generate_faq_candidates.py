#!/usr/bin/env python3
"""Generate FAQ candidates from REFUSE_NO_DOCS request_logs.

Mines refused-question rows for one bot, clusters semantically-similar
questions via embedding cosine similarity, and emits a CSV of cluster
candidates that an operator can fill answers for + upload back into the
RAG corpus.

App-mindset: this tool surfaces operator-review prompts only; it never
generates answer text on behalf of the bot owner. The output CSV has
the answer column blank — the operator fills it from authoritative
business data, then runs the existing ingest pipeline.

Usage:
    python3 scripts/generate_faq_candidates.py \\
        --bot-id <bot-slug> \\
        --tenant-id <int> \\
        --channel-type <channel> \\
        --since "7 days ago" \\
        --min-occurrences 3 \\
        --output reports/faq_candidates_<ts>.csv

Env required:
    DATABASE_URL          — async SQLAlchemy DSN (postgresql+asyncpg://...)
    OPENAI_API_KEY (etc.) — whatever the configured embedder needs

Resolves the 3-key external identity (tenant_id, bot_id, channel_type)
to ``record_bot_id`` UUID via the same SQL the BotRegistry uses (no
Redis dependency in this batch tool — the CLI runs offline).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

# Project root on sys.path for "ragbot." imports when invoked as a script.
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_SRC_ROOT = str(Path(__file__).resolve().parent.parent / "src")
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

import structlog  # noqa: E402

from ragbot.application.dto.ai_specs import EmbeddingSpec  # noqa: E402
from ragbot.application.services.faq_candidate_service import (  # noqa: E402
    FAQCandidate,
    FAQCandidateService,
    SqlRefusedQuestionRepo,
)
from ragbot.infrastructure.embedding.litellm_embedder import LiteLLMEmbedder  # noqa: E402
from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_EMBEDDING_DIM,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_TASK_QUERY,
    DEFAULT_FAQ_CLUSTER_SIMILARITY,
    DEFAULT_FAQ_MIN_OCCURRENCES,
)

logger = structlog.get_logger("generate_faq_candidates")


# Pure unit-conversion factors (seconds-per-X). These are physical
# constants tied to time-unit semantics, not tunable thresholds — kept
# as named constants per the zero-hardcode rule (no inline numbers).
_SECONDS_PER_MINUTE: int = 60
_SECONDS_PER_HOUR: int = 60 * _SECONDS_PER_MINUTE
_SECONDS_PER_DAY: int = 24 * _SECONDS_PER_HOUR


# ---- Time parsing ----------------------------------------------------------
def _parse_since(spec: str) -> datetime:
    """Parse a human-friendly ``--since`` argument.

    Supported forms:
      - "N days ago" / "N hours ago" (whitespace-insensitive)
      - ISO-8601 (e.g. ``2026-04-23T00:00:00+00:00``)
    """
    s = spec.strip().lower()
    now = datetime.now(tz=timezone.utc)
    for unit_word, factor_seconds in (
        ("days ago", _SECONDS_PER_DAY),
        ("day ago", _SECONDS_PER_DAY),
        ("hours ago", _SECONDS_PER_HOUR),
        ("hour ago", _SECONDS_PER_HOUR),
        ("minutes ago", _SECONDS_PER_MINUTE),
        ("minute ago", _SECONDS_PER_MINUTE),
    ):
        if s.endswith(unit_word):
            head = s[: -len(unit_word)].strip()
            try:
                n = int(head)
            except ValueError as exc:
                raise ValueError(
                    f"--since: expected integer before '{unit_word}', got {head!r}",
                ) from exc
            return now - timedelta(seconds=n * factor_seconds)
    # Fall back to ISO-8601.
    try:
        return datetime.fromisoformat(spec)
    except ValueError as exc:
        raise ValueError(
            f"--since: not a recognised relative or ISO-8601 timestamp: {spec!r}",
        ) from exc


# ---- Bot resolve (3-key external → record_bot_id) --------------------------
_BOT_RESOLVE_SQL = """
SELECT id::text AS record_bot_id, tenant_id::text AS record_tenant_id
FROM bots
WHERE tenant_id = :tenant_id
  AND bot_id = :bot_id
  AND channel_type = :channel_type
LIMIT 1
"""


async def _resolve_bot(
    session_factory: Any,
    *,
    tenant_id: int,
    bot_id: str,
    channel_type: str,
) -> tuple[UUID, UUID]:
    """Resolve (tenant_id INT, bot_id slug, channel_type) → (record_bot_id, record_tenant_id) UUIDs."""
    from sqlalchemy import text

    async with session_factory() as session:
        row = (
            await session.execute(
                text(_BOT_RESOLVE_SQL),
                {
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "channel_type": channel_type,
                },
            )
        ).mappings().first()
    if row is None:
        raise SystemExit(
            f"Bot not found for (tenant_id={tenant_id}, bot_id={bot_id!r}, "
            f"channel_type={channel_type!r})",
        )
    return UUID(row["record_bot_id"]), UUID(row["record_tenant_id"])


# ---- DB session factory ----------------------------------------------------
def _session_factory() -> Any:
    """Build an async SQLAlchemy session factory from ``DATABASE_URL``."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.getenv("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL env var required")
    engine = create_async_engine(url, pool_pre_ping=True)
    return async_sessionmaker(engine, expire_on_commit=False)


# ---- CSV emit --------------------------------------------------------------
def _emit_csv(path: Path, candidates: list[FAQCandidate]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "cluster_id",
                "occurrence_count",
                "avg_top_score",
                "representative_question",
                "sample_question_2",
                "sample_question_3",
                "operator_answer",  # blank for operator to fill
            ],
        )
        for c in candidates:
            samples = list(c.sample_questions)
            while len(samples) < 3:
                samples.append("")
            writer.writerow(
                [
                    c.cluster_id,
                    c.occurrence_count,
                    f"{c.avg_top_score:.4f}",
                    c.representative_question,
                    samples[1],
                    samples[2],
                    "",
                ],
            )


# ---- Main ------------------------------------------------------------------
def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="generate_faq_candidates.py",
        description=(
            "Mine REFUSE_NO_DOCS rows from request_logs, cluster similar "
            "refused questions, and emit a review-ready FAQ candidate CSV."
        ),
    )
    p.add_argument("--bot-id", required=True, help="External bot slug (e.g. demo-bot-v1)")
    p.add_argument("--tenant-id", required=True, type=int, help="External tenant_id (INT)")
    p.add_argument(
        "--channel-type", required=True, help="Channel slug (e.g. web, zalo)",
    )
    p.add_argument(
        "--since",
        default="7 days ago",
        help='Relative ("N days ago") or ISO-8601 lower-bound on started_at',
    )
    p.add_argument(
        "--min-occurrences",
        type=int,
        default=DEFAULT_FAQ_MIN_OCCURRENCES,
        help=f"Minimum cluster size to surface (default {DEFAULT_FAQ_MIN_OCCURRENCES})",
    )
    p.add_argument(
        "--cluster-similarity",
        type=float,
        default=DEFAULT_FAQ_CLUSTER_SIMILARITY,
        help=f"Cosine threshold for grouping (default {DEFAULT_FAQ_CLUSTER_SIMILARITY})",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=None,
        help="CSV output path (default: reports/faq_candidates_<UTC-ts>.csv)",
    )
    p.add_argument(
        "--embedding-model",
        default=DEFAULT_EMBEDDING_MODEL,
        help=f"Embedding model (default {DEFAULT_EMBEDDING_MODEL})",
    )
    return p


async def _run(args: argparse.Namespace) -> int:
    since = _parse_since(args.since)
    output = args.output or (
        Path("reports")
        / f"faq_candidates_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    )

    sf = _session_factory()
    record_bot_id, record_tenant_id = await _resolve_bot(
        sf,
        tenant_id=args.tenant_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
    )

    embedder = LiteLLMEmbedder(model=args.embedding_model)
    spec = EmbeddingSpec(
        binding_id=UUID(int=0),  # ad-hoc CLI run — no DB binding
        model_name=args.embedding_model,
        provider="litellm",
        dimension=DEFAULT_EMBEDDING_DIM,
        model_version=args.embedding_model,
        # Refused user questions are query-side text — asymmetric models
        # (Jina v3) need ``retrieval.query`` for correct cluster geometry.
        task=DEFAULT_EMBEDDING_TASK_QUERY,
    )
    repo = SqlRefusedQuestionRepo(sf)
    service = FAQCandidateService(
        repo=repo,
        embedder=embedder,
        embedding_spec=spec,
        logger=logger,
    )

    candidates = await service.find_candidates(
        record_tenant_id=record_tenant_id,  # type: ignore[arg-type]
        record_bot_id=record_bot_id,
        since=since,
        min_occurrences=args.min_occurrences,
        cluster_similarity=args.cluster_similarity,
    )

    _emit_csv(output, candidates)

    sys.stdout.write(
        f"Wrote {len(candidates)} FAQ candidate cluster(s) to {output}\n"
        f"  bot_id={args.bot_id} tenant_id={args.tenant_id} "
        f"channel_type={args.channel_type}\n"
        f"  since={since.isoformat()} min_occurrences={args.min_occurrences} "
        f"cluster_similarity={args.cluster_similarity}\n",
    )
    return 0


def main() -> int:
    parser = _build_arg_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
