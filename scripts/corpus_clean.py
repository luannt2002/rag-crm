#!/usr/bin/env python3
"""Corpus enrich helper — detect duplicate chunks, conflicting prices,
empty embeddings, and per-doc RAG-friendliness.

Five subcommands, all read-only by default; mutation requires ``--apply``.

* ``find-duplicate-chunks`` — group chunks by ``content_hash`` per bot.
* ``find-conflict-prices`` — extract numeric prices, group by service mention.
* ``find-empty-embeddings`` — chunks where ``embedding IS NULL``.
* ``re-embed-bot`` — thin wrapper that *exec*s ``scripts/reembed_bot_corpus.py``
  so the helper does not duplicate the embedder pipeline.
* ``validate-rag-friendly`` — score one document against the heuristics in
  ``docs/templates/RAG_FRIENDLY_SHEET_TEMPLATE.md``.

Bot identity is the 4-key tuple ``(record_tenant_id, workspace_id, bot_id,
channel_type)`` resolved through ``BotRegistryService.lookup``. As a
debugging convenience, ``--bot-uuid`` may be passed instead — but this
bypasses the workspace check, so it requires ``--allow-uuid`` (audit
trail). Output format: JSON to stdout or markdown table via ``--format``.

Sacred (CLAUDE.md): domain-neutral (no brand literal in code), zero
hardcoded thresholds (all from ``shared/constants.py``), tenant-scoped
sessions via ``session_with_tenant``, narrow exception types.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text as sql_text

from ragbot.bootstrap import Container
from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.shared.constants import (
    DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS,
    DEFAULT_CORPUS_CLEAN_PRICE_REGEX,
    DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS,
    DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS,
    DEFAULT_CORPUS_CLEAN_SERVICE_MIN_CHARS,
)

logger = structlog.get_logger(__name__)

# --- Helpers ---------------------------------------------------------------


def _excerpt(text: str, *, max_chars: int) -> str:
    """Trim chunk content for human-reviewable output."""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


async def _resolve_bot_uuid(
    *,
    container: Container,
    record_tenant_id: UUID | None,
    workspace_id: str | None,
    bot_id: str | None,
    channel_type: str | None,
    bot_uuid: str | None,
    allow_uuid: bool,
) -> tuple[UUID, UUID]:
    """Return ``(record_bot_id, record_tenant_id)`` — either via 4-key or direct UUID.

    ``--bot-uuid`` requires ``--allow-uuid`` to acknowledge the workspace
    bypass. Returns the *resolved* tenant so downstream queries can scope
    via ``session_with_tenant``.
    """
    if bot_uuid is not None:
        if not allow_uuid:
            raise ValueError(
                "--bot-uuid bypasses the 4-key check; pass --allow-uuid to confirm.",
            )
        # Look up tenant from the bot row so RLS scope is correct.
        sf = container.session_factory()
        async with sf() as session:
            row = await session.execute(
                sql_text(
                    "SELECT id, record_tenant_id "
                    "FROM bots WHERE id = :id AND is_deleted = false",
                ),
                {"id": bot_uuid},
            )
            r = row.first()
        if r is None:
            raise ValueError(f"bot UUID {bot_uuid!r} not found or deleted")
        return UUID(str(r.id)), UUID(str(r.record_tenant_id))

    if not (record_tenant_id and workspace_id and bot_id and channel_type):
        raise ValueError(
            "Need either --bot-uuid (with --allow-uuid) or all four of "
            "--record-tenant-id, --workspace-id, --bot-id, --channel-type",
        )
    registry = container.bot_registry()
    cfg = await registry.lookup(
        record_tenant_id, workspace_id, bot_id, channel_type,
    )
    if cfg is None:
        raise ValueError(
            f"bot not found by 4-key: tenant={record_tenant_id} ws={workspace_id} "
            f"bot_id={bot_id} channel={channel_type}",
        )
    return cfg.record_bot_id, cfg.record_tenant_id


def _emit(rows: list[dict[str, Any]], *, header: dict[str, Any], fmt: str) -> None:
    """Print result rows as JSON (default) or markdown table."""
    if fmt == "json":
        print(json.dumps({"header": header, "rows": rows}, ensure_ascii=False, indent=2))
        return
    # markdown table
    print(f"# {header.get('subcommand', '?')}")
    for k, v in header.items():
        if k == "subcommand":
            continue
        print(f"- **{k}**: {v}")
    if not rows:
        print("\n_(no findings)_")
        return
    cols = list(rows[0].keys())
    print()
    print("| " + " | ".join(cols) + " |")
    print("| " + " | ".join("---" for _ in cols) + " |")
    for r in rows:
        cells = [str(r.get(c, "")).replace("|", "\\|").replace("\n", " ") for c in cols]
        print("| " + " | ".join(cells) + " |")


# --- Subcommand: find-duplicate-chunks -------------------------------------


async def cmd_find_duplicate_chunks(args: argparse.Namespace) -> int:
    """Group ``document_chunks`` by ``content_hash`` per bot; report dup groups."""
    container = Container()
    record_bot_id, record_tenant_id = await _resolve_bot_uuid(
        container=container,
        record_tenant_id=args.record_tenant_id,
        workspace_id=args.workspace_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        bot_uuid=args.bot_uuid,
        allow_uuid=args.allow_uuid,
    )

    sql = sql_text(
        """
        SELECT dc.content_hash,
               COUNT(*) AS dup_count,
               array_agg(dc.id::text ORDER BY dc.created_at) AS chunk_ids,
               (array_agg(d.document_name ORDER BY dc.created_at))[1] AS sample_doc,
               (array_agg(dc.content ORDER BY dc.created_at))[1] AS sample_content
        FROM document_chunks dc
        JOIN documents d ON dc.record_document_id = d.id
        WHERE d.record_bot_id = :bot
          AND d.deleted_at IS NULL
          AND dc.content_hash IS NOT NULL
          AND dc.content_hash <> ''
        GROUP BY dc.content_hash
        HAVING COUNT(*) > 1
        ORDER BY dup_count DESC
        """,
    )
    sf = container.session_factory()
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        result = await session.execute(sql, {"bot": record_bot_id})
        groups = result.all()

    rows = [
        {
            "content_hash": g.content_hash,
            "dup_count": int(g.dup_count),
            "sample_doc": g.sample_doc,
            "excerpt": _excerpt(g.sample_content, max_chars=DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS),
            "suggested_action": "merge / delete extras (keep oldest)",
            "chunk_ids": list(g.chunk_ids),
        }
        for g in groups
    ]
    _emit(
        rows,
        header={
            "subcommand": "find-duplicate-chunks",
            "record_bot_id": str(record_bot_id),
            "groups_with_duplicates": len(rows),
        },
        fmt=args.format,
    )
    return 0


# --- Subcommand: find-conflict-prices --------------------------------------


def _extract_prices(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Pull all price-like tokens from a chunk body."""
    return [m.group(0) for m in pattern.finditer(text or "")]


def _service_key(text: str, *, head_chars: int) -> str:
    """Cheap service identifier — first ``head_chars`` of normalised text.

    The helper does NOT pretend to be a domain NER; it is a coarse bucket so
    the owner sees clusters they can clean up. Real disambiguation lives in
    bot owner data, not platform code.
    """
    normalised = re.sub(r"\s+", " ", (text or "").strip().lower())
    return normalised[:head_chars]


async def cmd_find_conflict_prices(args: argparse.Namespace) -> int:
    """Extract prices via regex, bucket by service-key prefix, flag mismatches."""
    container = Container()
    record_bot_id, record_tenant_id = await _resolve_bot_uuid(
        container=container,
        record_tenant_id=args.record_tenant_id,
        workspace_id=args.workspace_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        bot_uuid=args.bot_uuid,
        allow_uuid=args.allow_uuid,
    )
    pattern = re.compile(args.regex or DEFAULT_CORPUS_CLEAN_PRICE_REGEX)

    sql = sql_text(
        """
        SELECT dc.id, dc.content, d.document_name
        FROM document_chunks dc
        JOIN documents d ON dc.record_document_id = d.id
        WHERE d.record_bot_id = :bot
          AND d.deleted_at IS NULL
          AND dc.content IS NOT NULL
          AND length(dc.content) > 0
        ORDER BY d.document_name, dc.created_at
        """,
    )
    sf = container.session_factory()
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        result = await session.execute(sql, {"bot": record_bot_id})
        chunks = result.all()

    buckets: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        prices = _extract_prices(c.content, pattern)
        if not prices:
            continue
        key = _service_key(c.content, head_chars=args.service_min_chars)
        if len(key) < args.service_min_chars:
            continue
        buckets.setdefault(key, []).append(
            {
                "chunk_id": str(c.id),
                "document_name": c.document_name,
                "prices": prices,
                "excerpt": _excerpt(c.content, max_chars=DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS),
            },
        )

    rows: list[dict[str, Any]] = []
    for key, members in buckets.items():
        unique_price_sets = {tuple(sorted(set(m["prices"]))) for m in members}
        if len(unique_price_sets) <= 1:
            continue  # all chunks for this service agree
        for m in members:
            rows.append(
                {
                    "service_key": key,
                    "chunk_id": m["chunk_id"],
                    "document_name": m["document_name"],
                    "prices": ",".join(m["prices"]),
                    "excerpt": m["excerpt"],
                    "suggested_action": "owner pick canonical price; delete or annotate stale",
                },
            )

    _emit(
        rows,
        header={
            "subcommand": "find-conflict-prices",
            "record_bot_id": str(record_bot_id),
            "regex": pattern.pattern,
            "service_min_chars": args.service_min_chars,
            "conflicting_chunks": len(rows),
        },
        fmt=args.format,
    )
    return 0


# --- Subcommand: find-empty-embeddings -------------------------------------


async def cmd_find_empty_embeddings(args: argparse.Namespace) -> int:
    """List chunks where ``embedding IS NULL`` for one bot."""
    container = Container()
    record_bot_id, record_tenant_id = await _resolve_bot_uuid(
        container=container,
        record_tenant_id=args.record_tenant_id,
        workspace_id=args.workspace_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        bot_uuid=args.bot_uuid,
        allow_uuid=args.allow_uuid,
    )

    sql = sql_text(
        """
        SELECT dc.id, dc.content, d.document_name
        FROM document_chunks dc
        JOIN documents d ON dc.record_document_id = d.id
        WHERE d.record_bot_id = :bot
          AND d.deleted_at IS NULL
          AND dc.embedding IS NULL
        ORDER BY d.document_name, dc.created_at
        """,
    )
    sf = container.session_factory()
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        result = await session.execute(sql, {"bot": record_bot_id})
        rows_db = result.all()

    rows = [
        {
            "chunk_id": str(r.id),
            "document_name": r.document_name,
            "excerpt": _excerpt(r.content, max_chars=DEFAULT_CORPUS_CLEAN_EXCERPT_CHARS),
            "suggested_action": "run re-embed-bot --apply",
        }
        for r in rows_db
    ]
    _emit(
        rows,
        header={
            "subcommand": "find-empty-embeddings",
            "record_bot_id": str(record_bot_id),
            "empty_count": len(rows),
        },
        fmt=args.format,
    )
    return 0


# --- Subcommand: re-embed-bot ----------------------------------------------


async def cmd_re_embed_bot(args: argparse.Namespace) -> int:
    """Delegate to ``scripts/reembed_bot_corpus.py`` — never re-implement the embed pipeline.

    The helper resolves the bot UUID first so the inner script always sees
    a UUID, regardless of how the user invoked us (4-key or direct).
    """
    container = Container()
    record_bot_id, _record_tenant_id = await _resolve_bot_uuid(
        container=container,
        record_tenant_id=args.record_tenant_id,
        workspace_id=args.workspace_id,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        bot_uuid=args.bot_uuid,
        allow_uuid=args.allow_uuid,
    )
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "reembed_bot_corpus.py")
    if not os.path.exists(target):
        print(f"ERROR: cannot find {target}", file=sys.stderr)
        return 2
    cmd = [sys.executable, target, "--bot-uuid", str(record_bot_id)]
    if args.apply:
        cmd.append("--apply")
    print("DELEGATE: " + " ".join(cmd))
    if args.dry_run:
        return 0
    return subprocess.run(cmd, check=False).returncode


# --- Subcommand: validate-rag-friendly -------------------------------------


_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)
_NUMBER_RE = re.compile(r"\d")


def _score_doc(content: str, *, min_words: int, max_words: int) -> dict[str, Any]:
    """Heuristic scoring against ``RAG_FRIENDLY_SHEET_TEMPLATE.md`` rules."""
    text = content or ""
    words = re.findall(r"\w+", text, re.UNICODE)
    word_count = len(words)
    heading_count = len(_HEADING_RE.findall(text))
    has_explicit_numbers = bool(_NUMBER_RE.search(text))
    in_word_band = min_words <= word_count <= max_words

    findings: list[str] = []
    if heading_count == 0:
        findings.append("R1 missing — no markdown heading (## / # ...)")
    if not in_word_band:
        findings.append(
            f"word_count={word_count} outside band [{min_words}, {max_words}]",
        )
    if not has_explicit_numbers:
        findings.append("R6 hint — no explicit numbers (price/duration) in body")

    return {
        "word_count": word_count,
        "heading_count": heading_count,
        "has_explicit_numbers": has_explicit_numbers,
        "in_word_band": in_word_band,
        "findings": findings,
        "rag_friendly": not findings,
    }


async def cmd_validate_rag_friendly(args: argparse.Namespace) -> int:
    """Score one ``documents.id`` against RAG-friendliness heuristics."""
    container = Container()
    sf = container.session_factory()
    async with sf() as session:
        row = await session.execute(
            sql_text(
                "SELECT id, record_tenant_id, document_name, raw_content "
                "FROM documents WHERE id = :id AND deleted_at IS NULL",
            ),
            {"id": args.doc_id},
        )
        doc = row.first()
    if doc is None:
        print(f"ERROR: document {args.doc_id} not found / deleted", file=sys.stderr)
        return 2

    # Re-open under tenant scope so any further reads honour RLS.
    record_tenant_id = UUID(str(doc.record_tenant_id))
    async with session_with_tenant(sf, record_tenant_id=record_tenant_id) as session:
        if doc.raw_content:
            content = doc.raw_content
        else:
            # Fall back to concatenated chunk content for legacy rows.
            r = await session.execute(
                sql_text(
                    "SELECT string_agg(content, E'\\n\\n' ORDER BY chunk_index) "
                    "AS body FROM document_chunks "
                    "WHERE record_document_id = :id",
                ),
                {"id": args.doc_id},
            )
            content = (r.scalar() or "")

    score = _score_doc(
        content,
        min_words=args.min_words,
        max_words=args.max_words,
    )
    rows = [
        {
            "doc_id": str(doc.id),
            "document_name": doc.document_name,
            "rag_friendly": score["rag_friendly"],
            "word_count": score["word_count"],
            "heading_count": score["heading_count"],
            "has_explicit_numbers": score["has_explicit_numbers"],
            "findings": " | ".join(score["findings"]) or "(none)",
        },
    ]
    _emit(
        rows,
        header={
            "subcommand": "validate-rag-friendly",
            "min_words": args.min_words,
            "max_words": args.max_words,
        },
        fmt=args.format,
    )
    return 0 if score["rag_friendly"] else 1


# --- CLI plumbing ----------------------------------------------------------


def _add_bot_identity_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--record-tenant-id", type=lambda s: UUID(s), default=None)
    p.add_argument("--workspace-id", default=None)
    p.add_argument("--bot-id", default=None, help="External bot slug (e.g. 'support')")
    p.add_argument("--channel-type", default=None, help="External channel ('web', 'zalo'...)")
    p.add_argument(
        "--bot-uuid",
        default=None,
        help="Direct internal record_bot_id UUID (debug / migration path)",
    )
    p.add_argument(
        "--allow-uuid",
        action="store_true",
        help="Acknowledge that --bot-uuid bypasses the 4-key workspace check.",
    )


def _add_format_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--format",
        choices=("json", "md"),
        default="json",
        help="Output format (json default; md = markdown table)",
    )


def build_parser() -> argparse.ArgumentParser:
    """Construct the top-level argparse tree (factory so tests can inject)."""
    ap = argparse.ArgumentParser(prog="corpus_clean", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dup = sub.add_parser("find-duplicate-chunks", help="Group chunks by content_hash.")
    _add_bot_identity_args(p_dup)
    _add_format_args(p_dup)
    p_dup.add_argument("--dry-run", action="store_true", help="No-op flag; this command is read-only.")
    p_dup.set_defaults(_handler=cmd_find_duplicate_chunks)

    p_price = sub.add_parser(
        "find-conflict-prices",
        help="Find chunks with conflicting prices for the same service.",
    )
    _add_bot_identity_args(p_price)
    _add_format_args(p_price)
    p_price.add_argument(
        "--regex",
        default=None,
        help=f"Override price regex; default = {DEFAULT_CORPUS_CLEAN_PRICE_REGEX!r}",
    )
    p_price.add_argument(
        "--service-min-chars",
        type=int,
        default=DEFAULT_CORPUS_CLEAN_SERVICE_MIN_CHARS,
        help="Minimum service-key prefix length used to bucket chunks.",
    )
    p_price.add_argument("--dry-run", action="store_true", help="No-op flag; this command is read-only.")
    p_price.set_defaults(_handler=cmd_find_conflict_prices)

    p_empty = sub.add_parser("find-empty-embeddings", help="List chunks where embedding IS NULL.")
    _add_bot_identity_args(p_empty)
    _add_format_args(p_empty)
    p_empty.add_argument("--dry-run", action="store_true", help="No-op flag; this command is read-only.")
    p_empty.set_defaults(_handler=cmd_find_empty_embeddings)

    p_reembed = sub.add_parser(
        "re-embed-bot",
        help="Wrapper for scripts/reembed_bot_corpus.py (delegates the embed pipeline).",
    )
    _add_bot_identity_args(p_reembed)
    _add_format_args(p_reembed)
    p_reembed.add_argument(
        "--apply",
        action="store_true",
        help="Pass through to the inner script; otherwise inner runs DRY-RUN.",
    )
    p_reembed.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the delegated command without executing.",
    )
    p_reembed.set_defaults(_handler=cmd_re_embed_bot)

    p_rag = sub.add_parser(
        "validate-rag-friendly",
        help="Score one document against RAG-friendly heuristics.",
    )
    p_rag.add_argument("--doc-id", required=True, help="documents.id (UUID)")
    p_rag.add_argument(
        "--min-words",
        type=int,
        default=DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MIN_WORDS,
    )
    p_rag.add_argument(
        "--max-words",
        type=int,
        default=DEFAULT_CORPUS_CLEAN_RAG_FRIENDLY_MAX_WORDS,
    )
    _add_format_args(p_rag)
    p_rag.add_argument("--dry-run", action="store_true", help="No-op flag; this command is read-only.")
    p_rag.set_defaults(_handler=cmd_validate_rag_friendly)

    return ap


async def _amain(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler = args._handler
    return await handler(args)


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(argv))


if __name__ == "__main__":
    sys.exit(main())
