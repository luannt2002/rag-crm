"""M11 — Blocks API: structured retrieval result wrapper.

Inspired by RAG-Anything ``raganything/types.py::Block`` (HKUDS, 2025).
RAG-Anything's retrieval pipeline returns typed ``Block`` objects instead
of opaque dict chunks; downstream consumers (LLM context builder, citation
extractor, modality-aware reranker) all read ``block.type`` /
``block.metadata`` directly instead of fishing through nested dicts.

Why a dataclass (and not just a TypedDict)
------------------------------------------
1. **Type safety** — ``chunk_type`` is constrained to a documented
   ``Literal`` so the typechecker catches typos at lift sites.
2. **Method surface** — helpers like :meth:`Block.as_dict` keep dict
   serialisation consistent across persistence + audit log writers.
3. **Backward compatibility** — every legacy ``chunk["content"]`` /
   ``chunk.get("score")`` call site in ``query_graph.py`` (300+ sites)
   keeps working because ``__getitem__`` + ``get`` proxy to the dataclass
   fields plus the metadata dict.

When is wrapping enabled?
-------------------------
Per-bot opt-in via ``bots.plan_limits.blocks_api_enabled``. Default OFF
(``DEFAULT_BLOCKS_API_ENABLED = False``) so the production code path stays
byte-identical until bot owners flip the flag. The wrapper is a pure
helper: it never modifies content, scores, or metadata — only re-shapes
the in-memory representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


# Block type taxonomy. Mirrors ``chunking._ATOMIC_BLOCK_TYPES`` plus
# the table-row variant emitted by the CSV/Excel parser. Kept as a
# string ``Literal`` so static analysis catches typos at every lift
# site without forcing a runtime ``Enum`` dependency.
ChunkType = Literal["text", "table", "table_row", "code", "figure", "equation"]

# Default type when a legacy chunk dict has no explicit ``type`` field —
# matches the parser default in ``chunking._split_into_blocks`` where
# unclassified prose is tagged ``"text"``.
_DEFAULT_TYPE: ChunkType = "text"

# Set of legal chunk_type values used by :func:`from_chunk_dict` to
# guard against stray label drift in legacy dict payloads.
_KNOWN_TYPES: frozenset[str] = frozenset(
    {"text", "table", "table_row", "code", "figure", "equation"}
)


@dataclass(slots=True)
class Block:
    """Structured retrieval result row.

    Mirrors RAG-Anything's ``Block`` shape — typed chunk_id + content +
    type + metadata. ``references`` is reserved for the M19 multimodal
    entity linkage phase (figure ↔ caption ↔ description triples) and
    is currently always empty.

    Backward compatibility contract
    -------------------------------
    Legacy node code uses dict access patterns inherited from the days
    when chunks were ``dict[str, Any]``. Three idioms are preserved:

    * ``block["content"]`` → returns ``self.content``
    * ``block.get("score", 0)`` → falls through to ``self.metadata``
    * ``"id" in block`` → True when ``self.chunk_id`` is set

    Sites that already migrate to dataclass attribute access
    (``block.content``) get the type-checker's full power.
    """

    chunk_id: str
    content: str
    type: ChunkType = _DEFAULT_TYPE
    metadata: dict[str, Any] = field(default_factory=dict)
    # Reserved for M19 entity linkage (figure ↔ caption ↔ description).
    # Always empty until that wave ships; kept on the dataclass so the
    # shape contract does not break when the field is finally populated.
    references: list[str] = field(default_factory=list)

    # ── Backward-compat dict interface ──────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        """Dict-like read for legacy ``chunk[key]`` call sites.

        The ``"id"`` alias resolves to ``chunk_id`` because the retrieval
        node historically used ``c["id"]`` interchangeably with
        ``c["chunk_id"]`` depending on which infrastructure layer
        produced the dict. Unknown keys fall through to ``metadata`` so
        per-chunk metadata fields (e.g. ``score``, ``rerank_score``,
        ``document_id``) keep their callers untouched.

        :raises KeyError: when the key is not a known field and absent
            from ``metadata``. Matches dict semantics so ``in`` checks
            and ``[key]`` raises behave identically to a plain dict.
        """
        if key in ("id", "chunk_id"):
            return self.chunk_id
        if key == "content":
            return self.content
        if key == "type":
            return self.type
        if key == "metadata":
            return self.metadata
        if key == "references":
            return self.references
        if key in self.metadata:
            return self.metadata[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        """Dict-like ``get`` for legacy call sites.

        Falls through to ``metadata.get`` for unknown keys so call
        patterns such as ``chunk.get("score", 0)`` continue to work
        whether the underlying object is a raw dict or a Block.
        """
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key: object) -> bool:
        """Dict-like ``in`` operator.

        Mirrors the lookup chain in :meth:`__getitem__`: structural
        fields first, then ``metadata`` keys. Returning a bool rather
        than raising keeps ``if "doc_id" in chunk:`` patterns safe.
        """
        if not isinstance(key, str):
            return False
        if key in {"id", "chunk_id", "content", "type", "metadata", "references"}:
            return True
        return key in self.metadata

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a flat dict.

        Used by the audit log writer + cache persistence path which
        both expect a JSON-able mapping. Metadata is merged at the top
        level for downstream symmetry with the legacy chunk dict shape
        (e.g. ``score`` was historically a top-level key, not nested
        under ``metadata.score``).
        """
        out: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "id": self.chunk_id,  # legacy alias
            "content": self.content,
            "type": self.type,
        }
        # Merge metadata at top level for backward compat with code that
        # reads ``chunk["score"]`` directly. Explicit dataclass fields
        # win over metadata entries with the same key (defensive copy).
        for k, v in self.metadata.items():
            if k not in out:
                out[k] = v
        if self.references:
            out["references"] = list(self.references)
        return out


def from_chunk_dict(chunk: dict[str, Any]) -> Block:
    """Lift a legacy chunk dict into a :class:`Block`.

    Accepts both ``"chunk_id"`` and ``"id"`` as the identity key. The
    chunk's ``type`` field is honored if present; otherwise it falls
    back to the default ``"text"``. All keys other than the four
    structural fields are folded into ``metadata`` so backward-compat
    dict access keeps working through :meth:`Block.__getitem__`.

    This helper is the canonical lift point — every wrap site (rerank
    output, MMR output, generate input) should funnel through it so
    the shape contract stays consistent.
    """
    raw_id = (
        chunk.get("chunk_id")
        or chunk.get("id")
        or ""
    )
    chunk_id = str(raw_id)
    content = str(chunk.get("content") or chunk.get("text") or "")
    chunk_type_raw = chunk.get("type") or chunk.get("chunk_type") or _DEFAULT_TYPE
    # Defensive: collapse unknown labels to the safe ``text`` default
    # rather than carry a stray string the typechecker would reject.
    chunk_type: ChunkType = chunk_type_raw if chunk_type_raw in _KNOWN_TYPES else _DEFAULT_TYPE  # type: ignore[assignment]
    structural_keys = {"chunk_id", "id", "content", "text", "type", "chunk_type", "references"}
    metadata = {k: v for k, v in chunk.items() if k not in structural_keys}
    references = list(chunk.get("references") or [])
    return Block(
        chunk_id=chunk_id,
        content=content,
        type=chunk_type,
        metadata=metadata,
        references=references,
    )


__all__ = ["Block", "ChunkType", "from_chunk_dict"]
