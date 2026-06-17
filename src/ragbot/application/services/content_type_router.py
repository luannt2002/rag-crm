"""Block-type dispatch router — group blocks by type for per-type strategy.

Inspired by RAG-Anything M23 (``raganything/utils.py:separate_content``).
Pure routing; **no LLM call, no DB, no I/O**. Emits a single structlog
``content_type_histogram`` event so operators can see corpus modality
distribution per ingest — input for the M25 ingest-stats roll-up.

Adoption is gradual: callers can branch on the returned dict to apply
different chunking/enrichment per type (text vs table vs code), but the
helper itself is observability-only. Today's downstream consumers are
``DocumentService`` (M25 stats event) and any future per-type chunker.

Why a tiny helper and not a `Strategy` registry?
-----------------------------------------------
At this point we only **group** — we do not **dispatch** to a strategy
yet. Promoting to a registry would be premature abstraction (CLAUDE.md
T3 < T1) before there is a second per-type chunker to inject. When the
second strategy lands, we lift this into ``ports/`` + ``infrastructure``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

import structlog

# A1's M10 lifts the canonical chunk-type literal to ``shared/constants``.
# Until A1 lands, fall back to the spec value ``"text"`` so this helper is
# importable on its own branch. The fallback becomes dead code once A1
# merges — the Auditor-Chief sweep removes it then.
try:
    from ragbot.shared.constants import DEFAULT_CHUNK_TYPE_TEXT
except ImportError:  # pragma: no cover — drops once A1's M10 lands
    DEFAULT_CHUNK_TYPE_TEXT = "text"

logger = structlog.get_logger(__name__)


def group_by_block_type(
    blocks: Iterable[Any],
    *,
    type_attr: str = "block_type",
    type_default: str | None = None,
) -> dict[str, list[Any]]:
    """Group blocks by ``getattr(block, type_attr)``.

    Args:
        blocks: Iterable of block-like objects (DTOs, dataclasses,
            ORM rows — anything supporting ``getattr``). Items lacking
            the attribute fall into ``type_default``.
        type_attr: Attribute name to read for the type discriminator.
            Defaults to ``"block_type"`` — the convention used by A4's
            ``application/dto/block.py``.
        type_default: Type label assigned when the block lacks the
            attribute or it is falsy. Defaults to
            ``DEFAULT_CHUNK_TYPE_TEXT`` (typically ``"text"``).

    Returns:
        A regular ``dict[str, list[Any]]`` (defaultdict collapsed to
        dict before return so downstream code does not silently create
        empty groups via attribute access).
    """
    default = type_default if type_default is not None else DEFAULT_CHUNK_TYPE_TEXT
    out: dict[str, list[Any]] = defaultdict(list)
    for b in blocks:
        t = getattr(b, type_attr, None) or default
        out[t].append(b)
    return dict(out)


def emit_type_histogram(
    groups: dict[str, list[Any]],
    *,
    document_id: str | None = None,
) -> dict[str, int]:
    """Emit a ``content_type_histogram`` structlog event + return histogram.

    Side effect: one ``logger.info`` call with the histogram payload so
    Loki / structlog sinks pick it up. Returns the histogram dict so
    callers can attach it to a request_steps row without re-iterating
    ``groups``.

    Args:
        groups: Output of :func:`group_by_block_type`.
        document_id: Optional document UUID (string form) for log
            correlation. ``None`` is allowed for ingest-time use where
            the document ID has not yet been persisted.

    Returns:
        ``{type_label: count}`` — one entry per non-empty group.
    """
    hist = {t: len(v) for t, v in groups.items()}
    logger.info(
        "content_type_histogram",
        document_id=document_id,
        histogram=hist,
        total_blocks=sum(hist.values()),
    )
    return hist


__all__ = ["emit_type_histogram", "group_by_block_type"]
