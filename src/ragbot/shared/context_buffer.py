"""Context buffer for atomic blocks (AdapChunk Layer 2).

Populate ``Block.context_before`` + ``Block.context_after`` with 1-2
sentences taken from neighbouring TEXT blocks. The window is configurable
and the whole feature is gated by ``context_buffer_atomic_enabled`` —
when OFF the function is a no-op so legacy ingest paths behave
identically.

Why atomic blocks need a context buffer:

* TABLE / FORMULA / IMAGE / CODE chunks lose their introducing sentence
  ("Theo định lý Bayes, ta có:") and trailing interpretation
  ("Trong đó x là...") when the chunker treats them as opaque units.
* Retrieval over these chunks suffers because the lexical / semantic
  signal is only inside the surrounding prose, not the atomic payload.
* Persisting 1-2 sentences of prose lets the retriever match on the
  intro/outro WITHOUT splitting the atomic block.

Inspired by:

* AdapChunk Layer 2 internal blueprint (PhD thesis private) — "vùng
  cấm cắt" with sentence-window context preservation.
* RAG-Anything (HKUDS, 06/2025) — atomic semantic units pattern.
* Anthropic Contextual Retrieval (2024-09) — chunk-level context
  injection lifts retrieval by 35-49% on benchmarks.

Domain-neutral: no brand / industry / language-content assumption. The
sentence splitter is regex-based (cheap), works for VN + EN + most
European languages. Upgrade to ``underthesea.sent_tokenize`` is a
future cost trade-off (out of scope for this stream).
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

import structlog

from ragbot.domain.entities.document import Block
from ragbot.shared.constants import (
    DEFAULT_CONTEXT_BUFFER_ATOMIC_ENABLED,
    DEFAULT_CONTEXT_BUFFER_SENTENCE_WINDOW,
)

logger = structlog.get_logger(__name__)

# Env var name that mirrors the ``context_buffer_atomic_enabled`` system_config
# key — operators set this at deploy time so parser constructors (sync init)
# can read the toggle without async DB calls. Single source of truth = DB;
# env var = boot-time mirror, same pattern as ``RAGBOT_PARSER_ENGINE``.
ENV_CONTEXT_BUFFER_ENABLED: str = "RAGBOT_CONTEXT_BUFFER_ATOMIC_ENABLED"
ENV_CONTEXT_BUFFER_WINDOW: str = "RAGBOT_CONTEXT_BUFFER_SENTENCE_WINDOW"

# Sentence terminator regex — splits on . ! ? followed by whitespace, plus
# Vietnamese / Chinese full stops (。). Keeps the terminator with the
# preceding sentence by using lookbehind. Empty results filtered out.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。])\s+|(?<=[.!?。])$")


def _split_sentences(text: str) -> list[str]:
    """Split ``text`` into sentences via regex terminators.

    Cheap, deterministic, domain-neutral splitter. Falls back to a
    single-element list when the text has no terminator (treats the
    whole block as one sentence). Empty / whitespace-only entries are
    dropped so callers can safely ``[-n:]`` / ``[:n]`` the result.

    @param text: source string (may be empty)
    @return: list of trimmed sentences in source order
    """
    if not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def _resolve_enabled(explicit: bool | None) -> bool:
    """Resolve the enabled flag: explicit override > env var > constant default."""
    if explicit is not None:
        return explicit
    raw = os.environ.get(ENV_CONTEXT_BUFFER_ENABLED)
    if raw is None:
        return DEFAULT_CONTEXT_BUFFER_ATOMIC_ENABLED
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _resolve_window(explicit: int | None) -> int:
    """Resolve sentence window: explicit override > env var > constant default."""
    if explicit is not None and explicit > 0:
        return explicit
    raw = os.environ.get(ENV_CONTEXT_BUFFER_WINDOW)
    if raw is not None:
        try:
            val = int(raw)
            if val > 0:
                return val
        except ValueError:
            # Malformed env value → fall through to default rather than crash
            # ingest. Logged so ops sees the typo.
            logger.warning(
                "context_buffer_window_env_invalid",
                env=ENV_CONTEXT_BUFFER_WINDOW,
                value=raw[:40],
            )
    return DEFAULT_CONTEXT_BUFFER_SENTENCE_WINDOW


def attach_context_buffer(
    blocks: list[Block],
    *,
    enabled: bool | None = None,
    window: int | None = None,
) -> list[Block]:
    """Populate ``context_before`` / ``context_after`` on atomic blocks.

    Walks ``blocks`` in order. For every block where ``is_atomic`` is
    True, copies the last ``window`` sentences of the previous TEXT
    block into ``context_before`` and the first ``window`` sentences of
    the next TEXT block into ``context_after``. Non-atomic blocks are
    returned unchanged. When the feature flag is OFF, the input list is
    returned as-is (zero allocation, zero side effects).

    ``Block`` is a frozen dataclass — we use ``dataclasses.replace`` to
    build a new instance per modified atomic block. Order is preserved.

    Edge cases:

    * Empty input → returns empty list.
    * Atomic block at index 0 → no ``context_before`` (stays "").
    * Atomic block at last index → no ``context_after`` (stays "").
    * Neighbouring block is not TEXT (e.g. two TABLEs in a row) → that
      side stays "". This is intentional: copying a TABLE's content
      into another TABLE's context buffer would be noise, not context.
    * Block already has non-empty context (e.g. another upstream
      populator wrote it) → preserved, NOT overwritten.

    @param blocks: list of parsed blocks (parser output)
    @param enabled: explicit override; ``None`` = read env / constant
    @param window: explicit sentence window; ``None`` = read env / constant
    @return: list of blocks with atomic-block context fields populated
    """
    if not _resolve_enabled(enabled):
        return blocks
    if not blocks:
        return blocks

    win = _resolve_window(window)
    out: list[Block] = []
    atomic_count = 0
    context_chars_total = 0

    for idx, block in enumerate(blocks):
        if not block.is_atomic:
            out.append(block)
            continue

        before = block.context_before
        after = block.context_after

        # Look-back: previous block must exist AND be TEXT type.
        if not before and idx > 0 and blocks[idx - 1].type == "TEXT":
            sentences = _split_sentences(blocks[idx - 1].content)
            if sentences:
                before = " ".join(sentences[-win:])

        # Look-ahead: next block must exist AND be TEXT type.
        if not after and idx < len(blocks) - 1 and blocks[idx + 1].type == "TEXT":
            sentences = _split_sentences(blocks[idx + 1].content)
            if sentences:
                after = " ".join(sentences[:win])

        if before != block.context_before or after != block.context_after:
            out.append(replace(block, context_before=before, context_after=after))
            atomic_count += 1
            context_chars_total += len(before) + len(after)
        else:
            out.append(block)

    logger.info(
        "context_buffer_populate",
        step_name="context_buffer_populate",
        feature_flag="context_buffer_atomic_enabled",
        atomic_block_count=atomic_count,
        context_chars_total=context_chars_total,
        window=win,
        total_blocks=len(blocks),
    )
    return out


__all__ = [
    "ENV_CONTEXT_BUFFER_ENABLED",
    "ENV_CONTEXT_BUFFER_WINDOW",
    "attach_context_buffer",
]
