"""Repository for the ``document_service_index`` stats table.

Implements multi-tenant bulk insert, delete-before-reingest, price-range
query, count, and list-all operations.  All DB calls are scoped to the
caller-supplied tenant / bot identity — no cross-tenant data leakage
by construction (``record_tenant_id`` + ``record_bot_id`` filters on every
query, and the session is opened via ``session_with_tenant`` which sets the
Postgres RLS ``app.current_tenant_id`` parameter).

Schema (alembic 0118):
    document_service_index (
        id                UUID PK,
        record_tenant_id  UUID NOT NULL,
        workspace_id      VARCHAR(64) NOT NULL,
        record_bot_id     UUID NOT NULL,
        record_document_id UUID NOT NULL,
        record_chunk_id   UUID nullable (source chunk FK),
        entity_name       TEXT NOT NULL,
        entity_category   TEXT nullable,
        price_primary     NUMERIC nullable,
        price_secondary   NUMERIC nullable,
        attributes_json   JSONB NOT NULL DEFAULT '{}',
        created_at        TIMESTAMPTZ,
    )
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ragbot.infrastructure.db.engine import session_with_tenant
from ragbot.shared.constants import (
    DEFAULT_STATS_INDEX_QUERY_LIMIT,
    DEFAULT_STATS_REVERSE_MATCH_LIMIT,
    DEFAULT_STATS_REVERSE_MATCH_MIN_LEN,
    DEFAULT_STATS_REVERSE_MATCH_SHORT_FLOOR,
)
from ragbot.shared.document_stats import ParsedEntity

logger = structlog.get_logger(__name__)


class StatsIndexRepository:
    """CRUD for ``document_service_index`` — stats index per document."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Initialise with an async session factory.

        @param session_factory: SQLAlchemy async session maker (from DI).
        """
        self._sf = session_factory

    async def bulk_insert(
        self,
        *,
        record_tenant_id: uuid.UUID,
        workspace_id: str,
        record_bot_id: uuid.UUID,
        record_document_id: uuid.UUID,
        entities: list[ParsedEntity],
    ) -> None:
        """INSERT one row per entity into ``document_service_index``.

        Idempotent when called after ``delete_by_document`` — the caller is
        responsible for the delete-before-insert ordering on re-ingest.
        No-op when ``entities`` is empty.

        Multi-tenant safety: session opened via ``session_with_tenant`` which
        sets ``app.current_tenant_id`` so Postgres RLS applies on every DML.
        Additionally each row carries explicit ``record_tenant_id``,
        ``workspace_id``, and ``record_bot_id`` columns for application-level
        double-scoping.

        ParsedEntity fields mapped to columns:
            entity.name         → entity_name  (NOT NULL, empty string allowed)
            entity.category     → entity_category
            entity.price_primary   → price_primary
            entity.price_secondary → price_secondary
            entity.attributes   → attributes_json (JSONB)
            entity.chunk_index  → (not a direct column; stored in attributes_json)
        """
        if not entities:
            return

        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            params: dict[str, Any] = {
                "tenant_id": record_tenant_id,
                "workspace_id": workspace_id,
                "bot_id": record_bot_id,
                "doc_id": record_document_id,
            }
            value_clauses: list[str] = []
            for i, entity in enumerate(entities):
                # record_chunk_id is resolved from (record_document_id,
                # chunk_index) at INSERT time so the stats route can attribute
                # each entity to its REAL source chunk (STEP-5 / CHUNK_RECALL).
                # NULL when chunk_index has no matching chunk (defensive).
                value_clauses.append(
                    f"(:tenant_id, :workspace_id, :bot_id, :doc_id, "
                    f":entity_name_{i}, :entity_category_{i}, "
                    f":price_primary_{i}, :price_secondary_{i}, "
                    f"CAST(:attributes_json_{i} AS jsonb), "
                    f":entity_synonyms_{i}, "
                    f"(SELECT id FROM document_chunks "
                    f"WHERE record_document_id = :doc_id "
                    f"AND chunk_index = :chunk_index_{i} LIMIT 1))"
                )
                params[f"entity_name_{i}"] = entity.name or ""
                params[f"entity_category_{i}"] = entity.category
                params[f"price_primary_{i}"] = entity.price_primary
                params[f"price_secondary_{i}"] = entity.price_secondary
                params[f"chunk_index_{i}"] = entity.chunk_index
                # Aliases/synonym search variants → entity_synonyms (NULL when the
                # catalog has no aliases column). Captured by document_stats; this is
                # the searchable backing column query_by_name_keyword ORs against.
                params[f"entity_synonyms_{i}"] = getattr(entity, "aliases", None)
                # Merge chunk_index into attributes_json for traceability.
                attrs = dict(entity.attributes) if entity.attributes else {}
                attrs["chunk_index"] = entity.chunk_index
                params[f"attributes_json_{i}"] = json.dumps(attrs)

            sql = (
                "INSERT INTO document_service_index "
                "(record_tenant_id, workspace_id, record_bot_id, "
                "record_document_id, entity_name, entity_category, "
                "price_primary, price_secondary, attributes_json, "
                "entity_synonyms, record_chunk_id) "
                f"VALUES {', '.join(value_clauses)}"
            )
            await session.execute(text(sql), params)
            await session.commit()

        logger.info(
            "stats_index_bulk_insert",
            record_bot_id=str(record_bot_id),
            record_document_id=str(record_document_id),
            n_entities=len(entities),
        )

    async def delete_by_document(self, record_document_id: uuid.UUID) -> int:
        """DELETE all index rows for a given document.

        Used before re-ingest to avoid stale entities from the old version
        polluting price queries.

        Returns the number of rows deleted.

        Note: this call does NOT require record_tenant_id because
        ``record_document_id`` is a UUID PK that is globally unique — two
        tenants cannot have the same ``record_document_id``.  The Postgres
        RLS is not enforced here (no tenant parameter), so this method MUST
        only be called from a trusted internal path (ingest pipeline),
        never from a user-facing endpoint.
        """
        async with self._sf() as session:
            result = await session.execute(
                text(
                    "DELETE FROM document_service_index "
                    "WHERE record_document_id = :doc_id"
                ),
                {"doc_id": record_document_id},
            )
            await session.commit()
            deleted = result.rowcount or 0

        logger.info(
            "stats_index_delete_by_document",
            record_document_id=str(record_document_id),
            rows_deleted=deleted,
        )
        return deleted

    async def query_by_price_range(
        self,
        *,
        record_tenant_id: uuid.UUID,
        record_bot_id: uuid.UUID,
        price_min: int | None,
        price_max: int | None,
        price_column: Literal["primary", "secondary", "any"] = "any",
        limit: int = DEFAULT_STATS_INDEX_QUERY_LIMIT,
    ) -> list[dict]:
        """SELECT entities within a price range.

        Args:
            record_tenant_id: tenant UUID — opens the session via
                ``session_with_tenant`` (RLS GUC) and is also asserted in the
                WHERE clause as application-level defence-in-depth.
            record_bot_id: bot UUID — scopes the query to this bot.
            price_min: minimum price (inclusive); None = no lower bound.
            price_max: maximum price (inclusive); None = no upper bound.
            price_column: which price column to filter on.
                ``"primary"`` → ``price_primary``
                ``"secondary"`` → ``price_secondary``
                ``"any"`` → either column (OR condition)
            limit: maximum rows returned (capped at
                ``DEFAULT_STATS_INDEX_QUERY_LIMIT``).

        Returns:
            List of row dicts with keys: id, record_document_id,
            entity_name, entity_category, price_primary, price_secondary.
        """
        effective_limit = min(limit, DEFAULT_STATS_INDEX_QUERY_LIMIT)
        params: dict[str, Any] = {
            "tenant_id": record_tenant_id,
            "bot_id": record_bot_id,
            "limit": effective_limit,
        }

        price_clauses: list[str] = []
        if price_column == "primary":
            if price_min is not None:
                price_clauses.append("price_primary >= :price_min")
                params["price_min"] = price_min
            if price_max is not None:
                price_clauses.append("price_primary <= :price_max")
                params["price_max"] = price_max
        elif price_column == "secondary":
            if price_min is not None:
                price_clauses.append("price_secondary >= :price_min")
                params["price_min"] = price_min
            if price_max is not None:
                price_clauses.append("price_secondary <= :price_max")
                params["price_max"] = price_max
        else:  # "any"
            if price_min is not None:
                price_clauses.append(
                    "(price_primary >= :price_min OR price_secondary >= :price_min)"
                )
                params["price_min"] = price_min
            if price_max is not None:
                price_clauses.append(
                    "(price_primary <= :price_max OR price_secondary <= :price_max)"
                )
                params["price_max"] = price_max

        where_parts = [
            "record_tenant_id = :tenant_id",
            "record_bot_id = :bot_id",
        ]
        where_parts.extend(price_clauses)
        where_sql = " AND ".join(where_parts)

        sql = (
            "SELECT id, record_document_id, entity_name, "
            "entity_category, price_primary, price_secondary, attributes_json, "
            "record_chunk_id "
            f"FROM document_service_index WHERE {where_sql} "
            f"ORDER BY price_primary ASC NULLS LAST "
            f"LIMIT :limit"
        )
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()

        return [
            {
                "id": row[0],
                "record_document_id": row[1],
                "entity_name": row[2],
                "entity_category": row[3],
                "price_primary": row[4],
                "price_secondary": row[5],
                "attributes_json": row[6],
                "record_chunk_id": row[7],
            }
            for row in rows
        ]

    async def top_by_price(
        self,
        *,
        record_tenant_id: uuid.UUID,
        record_bot_id: uuid.UUID,
        direction: Literal["max", "min"],
        limit: int = DEFAULT_STATS_INDEX_QUERY_LIMIT,
        price_column: Literal["primary", "secondary", "any"] = "any",
    ) -> list[dict]:
        """SELECT the top-N entities ranked by price (superlative route).

        Powers "đắt nhất" / "rẻ nhất" queries, which carry no numeric bound:
        the parser emits ``operation="max"/"min"`` and the route runs
        ``ORDER BY price <DESC|ASC> LIMIT N`` against the clean pre-extracted
        prices — instead of re-parsing raw retrieved chunks, which fails on
        CSV price rows like ``Laser Carbon,1200000``.

        Args:
            direction: ``"max"`` → most expensive first; ``"min"`` → cheapest.
            limit: top-N rows (capped at ``DEFAULT_STATS_INDEX_QUERY_LIMIT``).
            price_column: which column to rank by (``"any"`` =
                ``COALESCE(price_primary, price_secondary)``).

        NULL-priced rows are excluded, so a bot whose corpus has no prices
        (e.g. a legal Thông tư) returns ``[]`` and the caller falls back to
        vector retrieve. Multi-tenant: scoped by ``record_bot_id`` (unique).
        """
        order = "DESC" if direction == "max" else "ASC"
        if price_column == "primary":
            price_expr = "price_primary"
            not_null = "price_primary IS NOT NULL"
        elif price_column == "secondary":
            price_expr = "price_secondary"
            not_null = "price_secondary IS NOT NULL"
        else:
            price_expr = "COALESCE(price_primary, price_secondary)"
            not_null = (
                "(price_primary IS NOT NULL OR price_secondary IS NOT NULL)"
            )
        effective_limit = min(limit, DEFAULT_STATS_INDEX_QUERY_LIMIT)
        sql = (
            "SELECT id, record_document_id, record_chunk_id, entity_name, "
            "entity_category, price_primary, price_secondary, attributes_json "
            "FROM document_service_index "
            f"WHERE record_tenant_id = :tenant_id AND record_bot_id = :bot_id "
            f"AND {not_null} "
            f"ORDER BY {price_expr} {order} "
            "LIMIT :limit"
        )
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(
                text(sql),
                {
                    "tenant_id": record_tenant_id,
                    "bot_id": record_bot_id,
                    "limit": effective_limit,
                },
            )
            rows = result.fetchall()

        return [
            {
                "id": row[0],
                "record_document_id": row[1],
                "record_chunk_id": row[2],
                "entity_name": row[3],
                "entity_category": row[4],
                "price_primary": row[5],
                "price_secondary": row[6],
                "attributes_json": row[7],
            }
            for row in rows
        ]

    async def count_by_price_range(
        self,
        *,
        record_tenant_id: uuid.UUID,
        record_bot_id: uuid.UUID,
        price_min: int | None,
        price_max: int | None,
        price_column: Literal["primary", "secondary", "any"] = "any",
    ) -> int:
        """COUNT entities within a price range.

        Same semantics as ``query_by_price_range`` but returns only the count.
        Used by query routing for planning (avoids fetching rows unnecessarily).
        """
        params: dict[str, Any] = {
            "tenant_id": record_tenant_id,
            "bot_id": record_bot_id,
        }

        price_clauses: list[str] = []
        if price_column == "primary":
            if price_min is not None:
                price_clauses.append("price_primary >= :price_min")
                params["price_min"] = price_min
            if price_max is not None:
                price_clauses.append("price_primary <= :price_max")
                params["price_max"] = price_max
        elif price_column == "secondary":
            if price_min is not None:
                price_clauses.append("price_secondary >= :price_min")
                params["price_min"] = price_min
            if price_max is not None:
                price_clauses.append("price_secondary <= :price_max")
                params["price_max"] = price_max
        else:  # "any"
            if price_min is not None:
                price_clauses.append(
                    "(price_primary >= :price_min OR price_secondary >= :price_min)"
                )
                params["price_min"] = price_min
            if price_max is not None:
                price_clauses.append(
                    "(price_primary <= :price_max OR price_secondary <= :price_max)"
                )
                params["price_max"] = price_max

        where_parts = [
            "record_tenant_id = :tenant_id",
            "record_bot_id = :bot_id",
        ]
        where_parts.extend(price_clauses)
        where_sql = " AND ".join(where_parts)

        sql = (
            "SELECT COUNT(*) FROM document_service_index "
            f"WHERE {where_sql}"
        )
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(text(sql), params)
            row = result.fetchone()
            return int(row[0]) if row else 0

    async def list_all_entities(
        self,
        *,
        record_tenant_id: uuid.UUID,
        record_bot_id: uuid.UUID,
        limit: int = DEFAULT_STATS_INDEX_QUERY_LIMIT,
    ) -> list[dict]:
        """Return all entities for a bot.

        Args:
            record_tenant_id: tenant UUID — RLS session + WHERE-clause fence.
            record_bot_id: bot UUID.
            limit: maximum rows (capped at ``DEFAULT_STATS_INDEX_QUERY_LIMIT``).

        Returns:
            List of row dicts (same shape as ``query_by_price_range``).
        """
        effective_limit = min(limit, DEFAULT_STATS_INDEX_QUERY_LIMIT)
        sql = (
            "SELECT id, record_document_id, entity_name, "
            "entity_category, price_primary, price_secondary, record_chunk_id "
            "FROM document_service_index "
            "WHERE record_tenant_id = :tenant_id AND record_bot_id = :bot_id "
            "ORDER BY created_at ASC "
            "LIMIT :limit"
        )
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(
                text(sql),
                {
                    "tenant_id": record_tenant_id,
                    "bot_id": record_bot_id,
                    "limit": effective_limit,
                },
            )
            rows = result.fetchall()

        return [
            {
                "id": row[0],
                "record_document_id": row[1],
                "entity_name": row[2],
                "entity_category": row[3],
                "price_primary": row[4],
                "price_secondary": row[5],
                "record_chunk_id": row[6],
            }
            for row in rows
        ]

    async def query_by_name_keyword(
        self,
        *,
        record_tenant_id: uuid.UUID,
        record_bot_id: uuid.UUID,
        keyword: str,
        synonyms: list[str] | None = None,
        limit: int = DEFAULT_STATS_INDEX_QUERY_LIMIT,
    ) -> list[dict]:
        """SELECT every entity whose name OR category contains *keyword*.

        Powers list/count/category queries ("liệt kê dịch vụ tẩy da chết",
        "tư vấn về da", "có bao nhiêu dịch vụ X"): vector/BM25 retrieve only
        surfaces top-k chunks so the LLM can never list/count ALL matching
        services. This returns EVERY matching record from the clean structured
        index, deterministic + complete. Truly accent-insensitive via
        ``unaccent()`` (folds đ→d, ế→e, …) so a corpus diacritic/typo variant
        ("Tẩy đa chết body" vs the query "tẩy da chết") still matches — plain
        ILIKE folds CASE only, not ACCENTS, and silently dropped such a service
        from a list/count answer.

        ``synonyms`` (per-bot ``custom_vocabulary["synonyms"]``) widen the match
        set: a generic keyword ("da") OR-expands to the owner-taught variants
        ("da chết", "chăm sóc da") so a "về da" list returns ALL skin services
        rather than only exact-substring hits. Empty/None → raw keyword only
        (behaviour unchanged). Domain-neutral — the owner supplies the synonym
        map; no hard-coded service list here. Each variant is a BOUND param
        (``:kw{i}``); only the controlled index is interpolated, never values.

        Scoped by record_tenant_id (RLS session + WHERE fence) AND record_bot_id.
        ``unaccent`` by alembic 0240.
        """
        kw = (keyword or "").strip()
        if not kw:
            return []
        # Build the de-duplicated match set: raw keyword + per-bot synonyms.
        _seen: set[str] = set()
        variants: list[str] = []
        for term in [kw, *(synonyms or [])]:
            t = (term or "").strip()
            if t and t.lower() not in _seen:
                _seen.add(t.lower())
                variants.append(t)
        effective_limit = min(limit, DEFAULT_STATS_INDEX_QUERY_LIMIT)
        params: dict = {
            "tenant_id": record_tenant_id,
            "bot_id": record_bot_id,
            "limit": effective_limit,
        }
        # Notation-variant folding: collapse a single separator BETWEEN two digits
        # so a size/code asked in one notation matches the row stored in another
        # ("205/55R16" ≡ "205/55/16" ≡ "205 55 16"). Domain-neutral — folds ANY
        # single non-digit between digits, no tire/size vocabulary. Applied twice
        # to catch overlapping digit-sep-digit triples. Without this the forward
        # ILIKE only matches the same-notation row, which for some products is the
        # NULL-price variant while the price sits on a different-notation sibling.
        def _fold(expr: str) -> str:
            once = f"regexp_replace(lower({expr}), '([0-9])[^0-9]([0-9])', '\\1\\2', 'g')"
            return f"regexp_replace({once}, '([0-9])[^0-9]([0-9])', '\\1\\2', 'g')"

        or_clauses: list[str] = []
        for i, v in enumerate(variants):
            or_clauses.append(
                f"unaccent(entity_name) ILIKE unaccent(:kw{i}) "
                f"OR unaccent(entity_category) ILIKE unaccent(:kw{i}) "
                # ALIASES/synonym column: an entity whose search variants list the
                # asked notation matches even when entity_name uses another ("265/50ZR20"
                # name, but the row's Aliases carry "265/50R20" = the query). The fold
                # collapses single digit-separators so notation-variants still hit.
                f"OR unaccent(entity_synonyms) ILIKE unaccent(:kw{i}) "
                f"OR {_fold('entity_name')} LIKE '%' || {_fold(f':kwn{i}')} || '%' "
                f"OR {_fold('entity_synonyms')} LIKE '%' || {_fold(f':kwn{i}')} || '%'"
            )
            params[f"kw{i}"] = f"%{v}%"
            params[f"kwn{i}"] = v
        where_match = " OR ".join(f"({c})" for c in or_clauses)
        sql = (
            "SELECT id, record_document_id, record_chunk_id, entity_name, "
            "entity_category, price_primary, price_secondary, attributes_json "
            "FROM document_service_index "
            "WHERE record_tenant_id = :tenant_id AND record_bot_id = :bot_id "
            f"AND ({where_match}) "
            # Prefer a priced row: a price query must never surface a NULL-price
            # notation-variant when a priced sibling also matches the fold.
            "ORDER BY (price_primary IS NOT NULL OR price_secondary IS NOT NULL) DESC, "
            "entity_name ASC "
            "LIMIT :limit"
        )
        async with session_with_tenant(
            self._sf, record_tenant_id=record_tenant_id,
        ) as session:
            result = await session.execute(text(sql), params)
            rows = result.fetchall()
            # Reverse/token fallback: the forward match (entity name CONTAINS the
            # keyword) misses a GRANULAR entity whose NAME is a word INSIDE the
            # query — e.g. query "Triệt lông nách combo 10 buổi" vs entity "Nách".
            # When forward finds nothing, match entities whose name is a substring
            # of the query keyword, guarded by a min length so 1-3 char zone words
            # ("Mép", "sâu") can't over-match. Only fires on an EMPTY forward
            # result → cannot regress a working forward lookup. ORDER BY length
            # DESC prefers the most specific (longest) entity name.
            if not rows and kw:
                # A short zone name ("Mặt"/"Tay"/"Râu", 3 chars) is the TARGET of a
                # category-qualified query ("triệt lông mặt") but the plain length
                # guard dropped it AND a CONTAINS match over-picks a category word in
                # the MIDDLE ("lông"). Accept a short name when the keyword ENDS with
                # it (trailing = the qualifying zone), and ORDER trailing matches
                # first, then priced rows — so "triệt lông mặt" → "Mặt" (priced), not
                # the null-price "lông". Reverse only fires on an empty forward result.
                rev_sql = (
                    "SELECT id, record_document_id, record_chunk_id, entity_name, "
                    "entity_category, price_primary, price_secondary, attributes_json "
                    "FROM document_service_index "
                    "WHERE record_tenant_id = :tenant_id AND record_bot_id = :bot_id "
                    "AND unaccent(:kwfull) ILIKE '%' || unaccent(entity_name) || '%' "
                    "AND (char_length(entity_name) >= :min_len "
                    "     OR (char_length(entity_name) >= :short_floor "
                    "         AND unaccent(:kwfull) ILIKE '%' || unaccent(entity_name))) "
                    "ORDER BY (unaccent(:kwfull) ILIKE '%' || unaccent(entity_name)) DESC, "
                    "(price_primary IS NOT NULL OR price_secondary IS NOT NULL) DESC, "
                    "char_length(entity_name) DESC "
                    "LIMIT :rev_limit"
                )
                result = await session.execute(text(rev_sql), {
                    "tenant_id": record_tenant_id,
                    "bot_id": record_bot_id,
                    "min_len": DEFAULT_STATS_REVERSE_MATCH_MIN_LEN,
                    "short_floor": DEFAULT_STATS_REVERSE_MATCH_SHORT_FLOOR,
                    "kwfull": kw,
                    "rev_limit": min(effective_limit, DEFAULT_STATS_REVERSE_MATCH_LIMIT),
                })
                rows = result.fetchall()

        return [
            {
                "id": row[0],
                "record_document_id": row[1],
                "record_chunk_id": row[2],
                "entity_name": row[3],
                "entity_category": row[4],
                "price_primary": row[5],
                "price_secondary": row[6],
                "attributes_json": row[7],
            }
            for row in rows
        ]


__all__ = ["StatsIndexRepository"]
