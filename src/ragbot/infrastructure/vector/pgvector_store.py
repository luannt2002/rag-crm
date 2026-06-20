"""PgVector vector store — Postgres ``vector`` extension.

Mandatory ``record_bot_id`` filter (1:1 with the external 3-key triple).
HNSW index for cosine similarity (m=16, ef=64).
"""

from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, literal_column, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.infrastructure.db.models_monitoring import (
    _document_chunks_table_ref as _document_chunks,
)
from ragbot.shared.constants import (
    ALLOWED_EMBEDDING_COLUMNS,
    CHUNK_LEVEL_METADATA_FILTER_KEYS,
    DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED,
    DEFAULT_BM25_SYMBOL_PHRASE_ENABLED,
    DEFAULT_BM25_SYMBOL_PHRASE_RANK_BOOST,
    DEFAULT_EF_SEARCH,
    DEFAULT_EMBEDDING_COLUMN,
    DEFAULT_HYBRID_RRF_BM25_WEIGHT,
    DEFAULT_HYBRID_RRF_VECTOR_WEIGHT,
    DEFAULT_RERANKER_EMBEDDING_DIM,
    DEFAULT_RRF_K,
    DEFAULT_RRF_RANK_MISS_PENALTY,
    DEFAULT_TOP_K,
    MAX_EF_SEARCH,
)


# Function-call / bracketed code tokens, e.g. ``range(5)``, ``f(x)``, ``A1:B100``.
_SYMBOL_TOKEN_RE = re.compile(
    r"[A-Za-z_][\w.]*\([^()\s]*\)"            # function-call: range(5)
    r"|[A-Za-z0-9]+(?:[/.\-][A-Za-z0-9]+)+"  # spec/product code: 195/65R15, 2-R17
)


def _extract_symbol_phrase(query: str) -> str:
    """Return the first function-call-like code token in ``query`` (else "").

    ``websearch_to_tsquery('simple', 'range(5)')`` shatters the token into the
    AND-term ``range & 5`` and the surrounding natural-language words further
    AND-restrict the predicate, so a chunk holding ``range(5)`` never matches.
    Surfacing the raw token lets the caller add a ``phraseto_tsquery`` OR-branch
    (``range <-> 5``) that matches on the symbol alone. Pure/synchronous so it
    is unit-testable without a database.
    """
    if not query:
        return ""
    m = _SYMBOL_TOKEN_RE.search(query)
    return m.group(0) if m else ""


def _validate_embedding_column(column: str) -> str:
    """Reject any column not on the whitelist; return the safe name."""
    if column not in ALLOWED_EMBEDDING_COLUMNS:
        raise ValueError(
            f"unsupported embedding column {column!r}; "
            f"allowed: {sorted(ALLOWED_EMBEDDING_COLUMNS)}",
        )
    return column
from ragbot.shared.text_normalization import normalize_vn
from ragbot.shared.text_utils import strip_vn_filler_tokens
from ragbot.shared.vi_tokenizer import (
    remove_diacritics,
    segment_vi_compounds,
    tokenize_vi,
)

logger = structlog.get_logger(__name__)


def _coerce_embedding(value: Any) -> list[float] | None:
    """Normalise a pgvector embedding column into ``list[float]`` or ``None``."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        try:
            return [float(x) for x in value]
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip().lstrip("[").rstrip("]")
        if not s:
            return None
        try:
            return [float(x) for x in s.split(",") if x.strip()]
        except ValueError:
            return None
    return None


class PgVectorStore:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        dimension: int = DEFAULT_RERANKER_EMBEDDING_DIM,
    ) -> None:
        self._sf = session_factory
        self._dimension = dimension

    async def upsert_chunks(
        self,
        *,
        record_document_id: UUID,
        chunks: list[dict[str, Any]],
        record_tenant_id: UUID | None = None,
        embedding_column: str = DEFAULT_EMBEDDING_COLUMN,
    ) -> int:
        """Insert chunks bound to a tenant via ``session_with_tenant`` (RLS-enforced).

        ``record_tenant_id`` may be omitted only when a previously-bound tenant
        context exists; otherwise ``session_with_tenant`` raises. ``embedding_column``
        selects the whitelist-validated vector column.
        """
        if not chunks:
            return 0
        col = _validate_embedding_column(embedding_column)
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            await session.execute(
                text("DELETE FROM document_chunks WHERE record_document_id = :doc_id"),
                {"doc_id": record_document_id},
            )
            # INJ-1 — embedding column ``col`` is pre-validated by
            # ``_validate_embedding_column`` (strict allowlist). The remaining
            # identifiers below are static literals; the column list is
            # assembled from a fixed Python list to make the safety contract
            # explicit at the call site (no caller-controlled concatenation).
            _col_list = [
                "record_document_id",
                "chunk_index",
                "content",
                "content_hash",
                col,
                "metadata_json",
            ]
            insert_sql = (
                f"INSERT INTO document_chunks ({', '.join(_col_list)}) "
                "VALUES (:doc_id, :idx, :content, :hash, CAST(:emb AS vector), :meta::jsonb)"
            )
            params = [
                {
                    "doc_id": record_document_id,
                    "idx": c.get("chunk_index", i),
                    "content": c["content"],
                    "hash": c.get("content_hash", ""),
                    "emb": str(c["embedding"]) if c.get("embedding") else None,
                    "meta": json.dumps(c.get("metadata", {})),
                }
                for i, c in enumerate(chunks)
            ]
            if params:
                await session.execute(text(insert_sql), params)
            await session.commit()
            return len(params)

    async def delete_by_document(
        self,
        record_document_id: UUID,
        *,
        record_tenant_id: UUID | None = None,
    ) -> int:
        """Delete chunks for a document — tenant-scoped via ``session_with_tenant``."""
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(
                text("DELETE FROM document_chunks WHERE record_document_id = :doc_id"),
                {"doc_id": record_document_id},
            )
            await session.commit()
            return result.rowcount or 0

    async def delete_by_tool_name(
        self,
        record_bot_id: UUID,
        tool_name: str,
        *,
        record_tenant_id: UUID | None = None,
    ) -> int:
        """Delete every chunk of a bot's documents matching ``tool_name``.

        Required by ``VectorStorePort`` and called from the canonical
        ``DELETE /documents`` use-case. ``tool_name`` lives on ``documents``,
        so we delete chunks whose ``record_document_id`` resolves to a matching
        document — bot-scoped on both sides. Tenant-scoped via
        ``session_with_tenant`` (RLS). Returns the number of chunks deleted.
        """
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(
                text(
                    "DELETE FROM document_chunks "
                    "WHERE record_bot_id = :bot_id "
                    "AND record_document_id IN ("
                    "  SELECT id FROM documents "
                    "  WHERE record_bot_id = :bot_id AND tool_name = :tool_name)"
                ),
                {"bot_id": record_bot_id, "tool_name": tool_name},
            )
            await session.commit()
            return result.rowcount or 0

    def _doc_filter_sql(
        self,
        record_bot_id: UUID,
        metadata_filter: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build a WHERE-fragment filtering chunks by ``record_bot_id``.

        After alembic 0108 (MEGA-1 / G14) the ``record_bot_id`` column lives
        directly on ``document_chunks``. The fast path therefore filters on
        the local column — ``record_bot_id = :record_bot_id`` — which the
        planner can push INTO the HNSW operator. Pre-0108 this filter sat
        behind a ``record_document_id IN (SELECT id FROM documents …)``
        subquery which prevented the planner from activating the HNSW index
        (live evidence: ``ix_chunks_embedding_hnsw idx_scan = 0`` over a
        22 MB index).

        Doc-level metadata keys (``document_type``, ``entity``, …) live on
        ``documents.metadata_json`` and ``documents.deleted_at`` is the
        canonical soft-delete flag, so when those filters are present we
        AND-in a documents subquery alongside the local bot filter — the
        planner uses ``ix_chunks_bot`` first, then prunes via the GIN on
        documents. When no doc-level filter is requested the subquery is
        omitted entirely and HNSW activates.

        ``metadata_filter`` is split by key name:
        - keys in :data:`CHUNK_LEVEL_METADATA_FILTER_KEYS` (``article_no``,
          ``clause_no``, ``section_no``, ``appendix_no``, ``chapter_no``) →
          AND-ed ``metadata_json @> ...`` predicate on the chunk row
          (uses ``ix_chunks_metadata_gin``).
        - remaining keys → AND-ed ``documents.metadata_json @> ...`` inside
          the doc-level subquery (uses ``ix_documents_metadata_gin``).
        """
        params: dict[str, Any] = {"record_bot_id": record_bot_id}
        doc_meta: dict[str, Any] = {}
        chunk_meta: dict[str, Any] = {}
        if metadata_filter:
            for _k, _v in metadata_filter.items():
                if _k in CHUNK_LEVEL_METADATA_FILTER_KEYS:
                    chunk_meta[_k] = _v
                else:
                    doc_meta[_k] = _v
        # Local bot filter — the predicate that lets HNSW pushdown activate.
        clause = "record_bot_id = :record_bot_id"
        # Soft-delete gate. Wave M3.5-C 2026-05-20: denormalised
        # ``documents.deleted_at`` onto ``document_chunks.doc_deleted_at``
        # (migration 010p) + maintained via trigger on documents UPDATE.
        # Pre-fix the correlated EXISTS subquery executed per-candidate
        # chunk → ~80% of the retrieve p50 1.6s cost. Post-fix the soft-
        # delete check is a direct column comparison the partial index
        # ``ix_chunks_bot_active`` covers natively (no JOIN, no subquery).
        #
        # When doc_meta is present we still need a documents JOIN for the
        # JSONB containment check, but the deleted_at gate moves to the
        # outer clause so the documents subquery filter is smaller too.
        clause = f"{clause} AND doc_deleted_at IS NULL"
        if doc_meta:
            params["metadata_filter"] = json.dumps(doc_meta)
            clause = (
                f"{clause} AND record_document_id IN ("
                "SELECT id FROM documents "
                "WHERE record_bot_id = :record_bot_id "
                "AND metadata_json @> CAST(:metadata_filter AS jsonb))"
            )
        # Chunk-level containment clause — AND-ed onto the outer WHERE.
        if chunk_meta:
            params["chunk_metadata_filter"] = json.dumps(chunk_meta)
            clause = (
                f"{clause} "
                "AND metadata_json @> CAST(:chunk_metadata_filter AS jsonb)"
            )
        return clause, params

    async def search(
        self,
        *,
        query_embedding: list[float],
        record_bot_id: UUID,
        top_k: int = DEFAULT_TOP_K,
        ef_search: int = DEFAULT_EF_SEARCH,
        metadata_filter: dict[str, Any] | None = None,
        embedding_column: str = DEFAULT_EMBEDDING_COLUMN,
        record_tenant_id: UUID | None = None,
    ) -> list[dict[str, Any]]:
        """Cosine similarity search filtered by ``record_bot_id``.

        Optional ``metadata_filter`` adds a JSONB containment clause.
        ``embedding_column`` selects the whitelist-validated vector column.

        ``record_tenant_id`` may be omitted only when a previously-bound
        tenant context exists; otherwise ``session_with_tenant`` raises.
        Routing through ``session_with_tenant`` makes the SELECT visible to
        the RLS policy on ``documents`` / ``document_chunks`` — without it,
        the unprivileged ``ragbot_app`` runtime role would see zero rows.
        """
        if record_bot_id is None:
            raise ValueError("record_bot_id is required for vector search isolation")
        col = _validate_embedding_column(embedding_column)
        ef_val = max(1, min(int(ef_search), MAX_EF_SEARCH))
        # SET can't use bind parameters; enforce bounds explicitly so the
        # f-string below is safe by construction.
        assert isinstance(ef_val, int) and 1 <= ef_val <= MAX_EF_SEARCH, \
            f"ef_val out of range: {ef_val}"
        doc_filter, doc_params = self._doc_filter_sql(record_bot_id, metadata_filter)
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            await session.execute(text(f"SET hnsw.ef_search = {ef_val}"))

            params: dict[str, Any] = {
                "emb": str(query_embedding),
                "top_k": top_k,
                **doc_params,
            }

            sql = f"""
                SELECT id, record_document_id, chunk_index, content, metadata_json,
                       1 - ({col} <=> CAST(:emb AS vector)) AS score
                FROM document_chunks
                WHERE {doc_filter}
                  AND {col} IS NOT NULL
                ORDER BY {col} <=> CAST(:emb AS vector)
                LIMIT :top_k
            """
            result = await session.execute(text(sql), params)
            return [
                {
                    "chunk_id": str(r["id"]),
                    "document_id": str(r["record_document_id"]),
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "score": float(r["score"]) if r["score"] else 0.0,
                    "metadata": dict(r["metadata_json"] or {}),
                }
                for r in result.mappings().all()
            ]

    async def hybrid_search(
        self,
        *,
        query_text: str,
        query_embedding: list[float],
        record_bot_id: UUID,
        top_k: int = DEFAULT_TOP_K,
        rrf_k: int = DEFAULT_RRF_K,
        rrf_miss: int = DEFAULT_RRF_RANK_MISS_PENALTY,
        ef_search: int = DEFAULT_EF_SEARCH,
        bm25_use_cover_density: bool = True,
        bm25_normalization_flags: int = 5,
        bm25_substring_fallback_enabled: bool = DEFAULT_BM25_SUBSTRING_FALLBACK_ENABLED,
        bm25_symbol_phrase_enabled: bool = DEFAULT_BM25_SYMBOL_PHRASE_ENABLED,
        bm25_weight: float = DEFAULT_HYBRID_RRF_BM25_WEIGHT,
        vector_weight: float = DEFAULT_HYBRID_RRF_VECTOR_WEIGHT,
        metadata_filter: dict[str, Any] | None = None,
        embedding_column: str = DEFAULT_EMBEDDING_COLUMN,
        record_tenant_id: UUID | None = None,
        structural_filter_patterns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Hybrid search: dense (cosine) + sparse (tsvector BM25-approx) fused via RRF.

        Filtered by ``record_bot_id``. Optional ``metadata_filter`` adds a
        JSONB containment clause to both subqueries.

        ``record_tenant_id`` is threaded into ``session_with_tenant`` so the
        ``SET LOCAL app.tenant_id`` runs before the dense + sparse subqueries
        execute — required when the runtime DSN is the unprivileged
        ``ragbot_app`` role with RLS enforced on ``documents`` /
        ``document_chunks``.
        """
        # NFC normalize to match ingest path; NFD inputs (macOS/mobile)
        # would otherwise miss NFC-indexed content.
        query_text = normalize_vn(query_text)
        if record_bot_id is None:
            raise ValueError("record_bot_id is required for hybrid search isolation")
        col = _validate_embedding_column(embedding_column)
        ef_val = max(1, min(int(ef_search), MAX_EF_SEARCH))
        # SET can't use bind parameters; enforce bounds explicitly so the
        # f-string below is safe by construction.
        assert isinstance(ef_val, int) and 1 <= ef_val <= MAX_EF_SEARCH, \
            f"ef_val out of range: {ef_val}"
        doc_filter, doc_params = self._doc_filter_sql(record_bot_id, metadata_filter)
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            await session.execute(text(f"SET hnsw.ef_search = {ef_val}"))

            # Symmetric tokenization: ingest indexes `content_segmented`
            # (compound joined via `_`); query side must mirror that or
            # the `simple` parser produces non-overlapping lexemes.
            tokenized_query = segment_vi_compounds(query_text)
            # Sparse branch only: strip VN filler tokens (e.g. "nói gì",
            # "ra sao") so websearch_to_tsquery AND-of-N doesn't over-
            # restrict recall on natural-language queries. Dense branch
            # keeps the original `query_text` semantics intact. Falls
            # back to the diacritic-stripped query when strip yields ''.
            _stripped_for_sparse = strip_vn_filler_tokens(query_text) or query_text
            normalized_query = remove_diacritics(_stripped_for_sparse)
            tokenized_normalized = segment_vi_compounds(normalized_query)
            safe_raw = query_text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            safe_raw_normalized = normalized_query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            # Clamp RRF weights to a sane range — negative weights would flip
            # the score ordering; >1.0 still works mathematically but is rare
            # in practice. Caller-passed values respected; defaults come from
            # ``shared/constants.py`` so operators tune via system_config.
            _bm25_w = max(0.0, float(bm25_weight))
            _vec_w = max(0.0, float(vector_weight))
            params: dict[str, Any] = {
                "emb": str(query_embedding),
                "query": tokenized_query,
                "query_normalized": tokenized_normalized,
                "raw_query": safe_raw,
                "raw_query_normalized": safe_raw_normalized,
                "top_k": top_k * 2,
                "rrf_k": rrf_k,
                "rrf_miss": rrf_miss,
                "bm25_w": _bm25_w,
                "vec_w": _vec_w,
                **doc_params,
            }

            _norm = max(0, min(int(bm25_normalization_flags), 63))
            _VALID_RANK_FNS = {"ts_rank_cd", "ts_rank"}
            _rank_fn = "ts_rank_cd" if bm25_use_cover_density else "ts_rank"
            if _rank_fn not in _VALID_RANK_FNS:
                _rank_fn = "ts_rank_cd"
            # websearch_to_tsquery preserves phrase ("..."), negation (-word)
            # and OR operators that the simpler tsquery builder strips.
            _rank_expr = f"{_rank_fn}(search_vector, websearch_to_tsquery('simple', :query), {_norm})"

            # Default sparse predicate uses GIN-indexable tsquery only. ILIKE
            # OR-branches force a seq-scan and are gated behind an opt-in
            # per-bot flag for legacy small-corpus use cases.
            _sparse_predicate = (
                "search_vector @@ websearch_to_tsquery('simple', :query)"
                " OR search_vector @@ websearch_to_tsquery('simple', :query_normalized)"
            )
            if bm25_substring_fallback_enabled:
                _sparse_predicate = (
                    f"{_sparse_predicate}"
                    " OR content ILIKE '%' || :raw_query || '%'"
                    " OR content ILIKE '%' || :raw_query_normalized || '%'"
                )

            # Symbol/code-token phrase branch (e.g. ``range(5)`` → ``range <-> 5``)
            # so a code-bearing chunk is retrievable even when the surrounding
            # natural-language words AND-restrict the main predicate. GIN-indexable
            # (phraseto_tsquery), so no seq-scan penalty. Only added when the query
            # actually carries such a token.
            _symbol_phrase = (
                _extract_symbol_phrase(query_text)
                if bm25_symbol_phrase_enabled
                else ""
            )
            if _symbol_phrase:
                params["symbol_phrase"] = _symbol_phrase
                _sparse_predicate = (
                    f"{_sparse_predicate}"
                    " OR search_vector @@ phraseto_tsquery('simple', :symbol_phrase)"
                )
                # Boost the exact-code match in the sparse RANK too: the main
                # rank scores on the AND-query, so a chunk holding the code but
                # not the surrounding words ranks 0 and is drowned. Credit the
                # symbol-phrase match so it surfaces to the top of the sparse arm.
                params["symbol_boost"] = float(DEFAULT_BM25_SYMBOL_PHRASE_RANK_BOOST)
                _rank_expr = (
                    f"({_rank_expr} + CASE WHEN search_vector @@ "
                    "phraseto_tsquery('simple', :symbol_phrase) "
                    "THEN :symbol_boost ELSE 0 END)"
                )

            # 2026-05-27 — VN structural pre-filter (Fix 3). When the caller
            # detects ``(Chương|Mục|Phần|Điều) N`` in the query, the dense
            # branch restricts ``content`` to chunks under that structural
            # anchor. Bypasses the embedding model's weak grasp of structural
            # identifiers (zembed-1 zero-shot). Sparse branch is NOT touched
            # — BM25 already matches the literal token. Falls back to the
            # unfiltered query if the prefilter yields 0 rows so a
            # non-existent anchor (e.g. ``Chương 99`` in a 3-chapter doc)
            # gracefully degrades to normal retrieve.
            _struct_clause = ""
            _struct_params: dict[str, Any] = {}
            if structural_filter_patterns:
                _struct_or = " OR ".join(
                    f"content LIKE :struct_p{i}"
                    for i in range(len(structural_filter_patterns))
                )
                _struct_clause = f" AND ({_struct_or})"
                _struct_params = {
                    f"struct_p{i}": pat
                    for i, pat in enumerate(structural_filter_patterns)
                }
                params.update(_struct_params)
                # Sparse branch: add the structural anchor as an OR-branch (NOT
                # an AND filter). A structural-pointer query ("Điều 56 quy định
                # về việc gì?") whose natural-language tokens AND-restrict
                # websearch_to_tsquery to ZERO rows must still retrieve the
                # literal-anchor chunks. Verified 2026-06-19: that exact query →
                # 0 sparse matches; the prior assumption "BM25 already matches
                # the literal token" holds for keyword queries, not natural
                # questions. Precise (only anchor chunks, no OR-of-all-tokens
                # flood) and gated to structural queries so the LIKE seq-scan is
                # bounded.
                _sparse_predicate = f"({_sparse_predicate}) OR ({_struct_or})"

            # Embedding propagated end-to-end so downstream MMR computes true cosine
            # diversity. Cast to ``float4[]`` because SQLAlchemy returns ``vector`` as str.
            sql = f"""
            WITH dense AS (
                SELECT id, content, metadata_json, record_document_id, chunk_index,
                       {col}::float4[] AS embedding,
                       ROW_NUMBER() OVER (ORDER BY {col} <=> CAST(:emb AS vector)) AS rank_d
                FROM document_chunks
                WHERE {doc_filter} AND {col} IS NOT NULL{_struct_clause}
                ORDER BY {col} <=> CAST(:emb AS vector)
                LIMIT :top_k
            ),
            sparse AS (
                SELECT id, content, metadata_json, record_document_id, chunk_index,
                       {col}::float4[] AS embedding,
                       ROW_NUMBER() OVER (ORDER BY {_rank_expr} DESC) AS rank_s
                FROM document_chunks
                WHERE {doc_filter}
                  AND ({_sparse_predicate})
                ORDER BY {_rank_expr} DESC
                LIMIT :top_k
            ),
            fused AS (
                SELECT COALESCE(d.id, s.id) AS id,
                       COALESCE(d.content, s.content) AS content,
                       COALESCE(d.metadata_json, s.metadata_json) AS metadata_json,
                       COALESCE(d.record_document_id, s.record_document_id) AS record_document_id,
                       COALESCE(d.chunk_index, s.chunk_index) AS chunk_index,
                       COALESCE(d.embedding, s.embedding) AS embedding,
                       (:vec_w / (:rrf_k + COALESCE(d.rank_d, :rrf_miss))) +
                       (:bm25_w / (:rrf_k + COALESCE(s.rank_s, :rrf_miss))) AS rrf_score
                FROM dense d
                FULL OUTER JOIN sparse s ON d.id = s.id
            )
            SELECT id, content, metadata_json, record_document_id, chunk_index, rrf_score, embedding
            FROM fused
            ORDER BY rrf_score DESC
            LIMIT :final_k
            """
            params["final_k"] = top_k
            result = await session.execute(text(sql), params)
            rows = list(result.mappings().all())
            # Structural pre-filter graceful degrade. When the LIKE clause
            # excludes every chunk (e.g. query asks about ``Chương 99`` in
            # a 3-chapter document), retry WITHOUT the structural clause so
            # the caller still receives the best-cosine candidates instead
            # of an empty list.
            if not rows and structural_filter_patterns and _struct_clause:
                logger.info(
                    "structural_prefilter_no_match_fallback",
                    patterns=structural_filter_patterns,
                )
                # Remove ONLY the dense AND-filter (the over-restrictive clause
                # that excluded every row). The sparse branch keeps its additive
                # OR-anchor, which STILL references ``:struct_pN`` — so the struct
                # params MUST stay bound. The old code stripped ``struct_p*`` from
                # the bind dict while the sparse ``OR (...)`` still referenced
                # them → InvalidRequestError "struct_p0 has no value" on every
                # structural-pointer query that hit the no-match fallback.
                sql_no_struct = sql.replace(_struct_clause, "")
                result = await session.execute(text(sql_no_struct), params)
                rows = list(result.mappings().all())
            return [
                {
                    "chunk_id": str(r["id"]),
                    "document_id": str(r["record_document_id"]),
                    "chunk_index": r["chunk_index"],
                    "content": r["content"],
                    "score": float(r["rrf_score"]),
                    "metadata": dict(r["metadata_json"] or {}),
                    "embedding": _coerce_embedding(r["embedding"]),
                }
                for r in rows
            ]

    async def count(
        self,
        record_bot_id: UUID,
        *,
        record_tenant_id: UUID | None = None,
    ) -> int:
        """Return chunk count for a bot — tenant-scoped via ``session_with_tenant``.

        ``record_tenant_id`` may be omitted only when a previously-bound
        tenant context exists; otherwise ``session_with_tenant`` raises.
        Routing through ``session_with_tenant`` makes the COUNT visible to
        the RLS policy on ``document_chunks``.

        Uses ORM ``select(func.count())`` builder against the Table shim
        (no f-string SQL). Column references are ORM attributes — column
        renames surface as Python AttributeError at compile time instead
        of runtime ``UndefinedColumnError``.
        """
        stmt = (
            select(func.count())
            .select_from(_document_chunks)
            .where(_document_chunks.c.record_bot_id == record_bot_id)
        )
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(stmt)
            return result.scalar_one() or 0
