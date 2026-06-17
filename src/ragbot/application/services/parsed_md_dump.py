"""Parsed-Markdown dump — write ``documents.raw_content`` to disk for debug.

Operator convenience artefact (2026-05-18). Every Action 1 upload writes
the parsed Markdown to ``{PARSED_MD_DIR}/{record_tenant_id}/{doc_id}.md``
in addition to ``documents.raw_content`` so operators can open the file
in any text editor (VSCode, vim) to debug chunking decisions:

- Table-with-footer pattern (footer "Khuyến mãi: ..." after a CSV table)
- Heading hierarchy (HDT strategy depends on # / ## / ### structure)
- Line-break / encoding issues that bias retrieval

The DB column ``raw_content`` is still the source of truth — this file
is a SIDE artefact. Losing the file is harmless: the next upload of the
same source URL regenerates it. The reverse is also true: missing DB
row means the file is orphaned and the cleanup helper sweeps it.

Multi-tenant isolation
----------------------
Files land under a tenant-scoped subdirectory so two tenants cannot
inspect each other's parsed Markdown via filesystem ACLs alone. The
download endpoint (``GET /admin/documents/{id}/markdown``) re-checks
``record_tenant_id`` ownership before serving.

Fail-soft
---------
File write failure (disk full, permission denied) MUST NOT block the
upload — the DB row is already committed and the worker can still
ingest from ``raw_content``. The dump path is best-effort + log warning.
Operators that want to disable the dump entirely set
``RAGBOT_PARSED_MD_DIR=`` (empty string).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from ragbot.shared.constants import (
    DEFAULT_PARSED_MD_DIR,
    DEFAULT_PARSED_MD_SUFFIX,
)

if TYPE_CHECKING:
    from datetime import datetime

logger = structlog.get_logger(__name__)


def get_dump_root() -> Path | None:
    """Return the configured dump root, or ``None`` if dump is disabled.

    Resolution order:
    1. Env ``RAGBOT_PARSED_MD_DIR`` (operator override — empty disables).
    2. ``DEFAULT_PARSED_MD_DIR`` constant fallback.

    Relative paths resolve against the current working directory
    (typically the repo root when systemd ``WorkingDirectory=`` is set).
    """
    raw = os.getenv("RAGBOT_PARSED_MD_DIR")
    if raw is None:
        return Path(DEFAULT_PARSED_MD_DIR)
    raw = raw.strip()
    if not raw:
        return None  # explicit empty → operator disabled
    return Path(raw)


def resolve_dump_path(
    *,
    record_tenant_id: UUID | str,
    document_id: UUID | str,
) -> Path | None:
    """Return the absolute path the dump SHOULD live at, or ``None`` if disabled.

    Two-segment layout (``{root}/{tenant}/{doc}.md``) so the filesystem
    listing is grep-friendly per tenant. Suffix is configurable via
    ``DEFAULT_PARSED_MD_SUFFIX``.
    """
    root = get_dump_root()
    if root is None:
        return None
    return root / str(record_tenant_id) / f"{document_id}{DEFAULT_PARSED_MD_SUFFIX}"


def _format_header(
    *,
    document_id: UUID | str,
    record_tenant_id: UUID | str,
    document_name: str | None,
    source_url: str | None,
    bot_id: str | None,
    channel_type: str | None,
    content_chars: int,
    uploaded_at: "datetime | str | None",
) -> str:
    """Build the Markdown front-matter block.

    Front-matter is plain ``# Comment`` lines (NOT YAML --- block) so the
    file renders correctly in any Markdown viewer without breaking heading
    hierarchy that the HDT chunker relies on.
    """
    lines = [
        f"# Document parsed: {document_name or '(no name)'}",
        "",
        f"- **Document ID**: `{document_id}`",
        f"- **Tenant**: `{record_tenant_id}`",
    ]
    if bot_id:
        lines.append(f"- **Bot**: `{bot_id}` / channel `{channel_type or '?'}`")
    if source_url:
        lines.append(f"- **Source URL**: <{source_url}>")
    if uploaded_at is not None:
        lines.append(f"- **Uploaded**: {uploaded_at}")
    lines.append(f"- **Content chars**: {content_chars:,}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def dump_parsed_md(
    *,
    record_tenant_id: UUID | str,
    document_id: UUID | str,
    document_name: str | None,
    source_url: str | None,
    bot_id: str | None,
    channel_type: str | None,
    content: str,
    uploaded_at: "datetime | str | None" = None,
) -> Path | None:
    """Write the parsed Markdown to disk. Returns the path on success.

    Fail-soft: any IO error is logged but does NOT raise — the upload
    pipeline must continue regardless. Returns ``None`` when the dump
    is disabled (env override) or when the write fails.
    """
    path = resolve_dump_path(
        record_tenant_id=record_tenant_id,
        document_id=document_id,
    )
    if path is None:
        return None

    header = _format_header(
        document_id=document_id,
        record_tenant_id=record_tenant_id,
        document_name=document_name,
        source_url=source_url,
        bot_id=bot_id,
        channel_type=channel_type,
        content_chars=len(content or ""),
        uploaded_at=uploaded_at,
    )
    payload = header + (content or "")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        # Disk full, permission denied, path traversal blocked by OS, …
        # Upload must continue; the DB row is already the source of truth.
        logger.warning(
            "parsed_md_dump_failed",
            document_id=str(document_id),
            record_tenant_id=str(record_tenant_id),
            target_path=str(path),
            error_type=type(exc).__name__,
            error=str(exc)[:200],
        )
        return None

    logger.info(
        "parsed_md_dump_written",
        document_id=str(document_id),
        record_tenant_id=str(record_tenant_id),
        path=str(path),
        bytes=len(payload),
    )
    return path


def read_dump(
    *,
    record_tenant_id: UUID | str,
    document_id: UUID | str,
) -> str | None:
    """Read the dumped Markdown back for the download endpoint.

    Returns ``None`` when the file is missing or the dump is disabled.
    Caller (route handler) MUST also verify ``record_tenant_id`` matches
    the document's owner before serving — this helper trusts the
    arguments and does NOT re-check tenant ownership.
    """
    path = resolve_dump_path(
        record_tenant_id=record_tenant_id,
        document_id=document_id,
    )
    if path is None or not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "parsed_md_read_failed",
            document_id=str(document_id),
            target_path=str(path),
            error_type=type(exc).__name__,
        )
        return None


__all__ = [
    "dump_parsed_md",
    "get_dump_root",
    "read_dump",
    "resolve_dump_path",
]
