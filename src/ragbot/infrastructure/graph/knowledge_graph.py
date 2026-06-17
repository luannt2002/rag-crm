"""Knowledge graph service — extract entity-relation triples and query them.

Extracts (subject, relation, object) triples from document chunks via LLM,
stores them in ``knowledge_edges`` table, and supports graph traversal for
multi-hop retrieval.

NOTE: LazyGraphRAG (Microsoft Research, 2024) can replace the current upfront
graph build for cost optimization. Defer to backlog (corpus >100K chunks
trigger). See plans/ROADMAP_PROPOSAL.md backlog.
Ref: https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ragbot.shared.constants import (
    DEFAULT_KG_PREVIEW_CHARS,
    DEFAULT_KG_TRIPLE_OBJ_MAX_CHARS,
    DEFAULT_KG_TRIPLE_REL_MAX_CHARS,
    DEFAULT_KG_TRIPLE_SUBJ_MAX_CHARS,
    DEFAULT_LLM_MAX_TOKENS,
)
from ragbot.shared.text_normalization import normalize_vn

logger = structlog.get_logger(__name__)

# Vietnamese-aware entity extraction prompt
_ENTITY_EXTRACTION_PROMPT = (
    "Trích xuất các mối quan hệ thực thể từ đoạn văn bản sau.\n"
    "Trả về danh sách JSON các bộ ba (triple) theo format:\n"
    '[{"subject": "...", "relation": "...", "object": "..."}]\n\n'
    "Quy tắc:\n"
    "- Subject và object là danh từ riêng, tên tổ chức, khái niệm, hoặc thực thể cụ thể\n"
    "- Relation là động từ hoặc cụm từ mô tả mối quan hệ\n"
    "- Chuẩn hóa tên thực thể (viết hoa chữ cái đầu, bỏ dấu ngoặc thừa)\n"
    "- Ưu tiên mối quan hệ rõ ràng, tránh trích xuất quá chung chung\n"
    "- Chỉ trả về JSON array, KHÔNG giải thích\n"
    "- Nếu không có mối quan hệ rõ ràng, trả về []\n"
)


class KnowledgeGraphService:
    """Extract and store entity-relation triples from document chunks."""

    async def extract_entities(
        self,
        chunk_content: str,
        document_name: str,
        llm: Any,
        model_resolver: Any,
        *,
        tenant_id: Any = None,
        record_bot_id: Any = None,
        channel_type: str = "web",
        max_triples: int = 10,
        max_preview_chars: int = DEFAULT_KG_PREVIEW_CHARS,
    ) -> list[dict]:
        """Use LLM to extract (subject, relation, object) triples from text.

        Returns list of dicts with keys: subject, relation, object, source_doc.
        Gracefully returns empty list on any LLM or parsing failure.
        """
        if not chunk_content or not chunk_content.strip():
            return []

        try:
            cfg = await model_resolver.resolve_runtime(
                record_tenant_id=tenant_id,
                record_bot_id=record_bot_id,
                purpose="entity_extraction",
            )
            messages = [
                {"role": "system", "content": _ENTITY_EXTRACTION_PROMPT},
                {"role": "user", "content": (
                    f"Tài liệu: {document_name}\n\n"
                    f"Nội dung:\n{chunk_content[:max_preview_chars]}"
                )},
            ]
            result = await llm.complete(cfg, messages=messages, temperature=0.0, max_tokens=DEFAULT_LLM_MAX_TOKENS)  # deterministic extraction
            raw_text = (result.get("text", "") or "").strip()

            # Parse JSON from LLM response
            triples = _parse_triples_json(raw_text)

            # Limit and annotate — skip self-refs, enforce length limits, deduplicate
            output: list[dict] = []
            seen_keys: set[tuple[str, str, str]] = set()
            for triple in triples[:max_triples]:
                subj = (triple.get("subject") or "").strip()[:DEFAULT_KG_TRIPLE_SUBJ_MAX_CHARS]
                rel = (triple.get("relation") or "").strip()[:DEFAULT_KG_TRIPLE_REL_MAX_CHARS]
                obj = (triple.get("object") or "").strip()[:DEFAULT_KG_TRIPLE_OBJ_MAX_CHARS]
                if not (subj and rel and obj):
                    continue
                # Skip self-references (NFC canonical — P1)
                subj_norm = normalize_vn(subj).lower()
                obj_norm = normalize_vn(obj).lower()
                if subj_norm == obj_norm:
                    continue
                # Skip duplicates within batch
                dedup_key = (subj_norm, normalize_vn(rel).lower(), obj_norm)
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
                output.append({
                    "subject": subj,
                    "relation": rel,
                    "object": obj,
                    "source_doc": document_name,
                })
            return output

        except Exception:  # noqa: BLE001 — LLM provider hierarchy varies across litellm/httpx/openai; KG extraction is best-effort, return empty list
            logger.warning(
                "entity_extraction_failed",
                document_name=document_name,
                exc_info=True,
            )
            return []

    async def store_triples(
        self,
        record_bot_id: UUID,
        triples: list[dict],
        session: AsyncSession,
        *,
        source_chunk_id: UUID | None = None,
        channel_type: str = "web",
    ) -> int:
        """Store triples in knowledge_edges table.

        Uses ON CONFLICT to avoid duplicates. Returns count of inserted rows.
        """
        if not triples:
            return 0

        inserted = 0
        for triple in triples:
            try:
                # P17 P0-3: composite key must include channel_type.
                # Without it, the same (record_bot_id, subject, relation,
                # object) collides across channels — second channel's insert
                # hits DO NOTHING and silently loses the edge. This matches
                # the unique-index shape added in migration 0037.
                result = await session.execute(
                    text("""
                        INSERT INTO knowledge_edges
                            (record_bot_id, channel_type, subject, relation, object, source_document, source_chunk_id, confidence)
                        VALUES (:record_bot_id, :channel_type, :subject, :relation, :object, :source_doc, :chunk_id, :confidence)
                        ON CONFLICT (record_bot_id, channel_type, subject, relation, object) DO NOTHING
                    """),
                    {
                        "record_bot_id": record_bot_id,
                        "channel_type": channel_type,
                        "subject": triple["subject"],
                        "relation": triple["relation"],
                        "object": triple["object"],
                        "source_doc": triple.get("source_doc", ""),
                        "chunk_id": source_chunk_id,
                        "confidence": triple.get("confidence", 1.0),
                    },
                )
                inserted += (result.rowcount or 0)
            except (SQLAlchemyError, KeyError, TypeError, ValueError):
                logger.warning(
                    "store_triple_failed",
                    subject=triple.get("subject"),
                    exc_info=True,
                )
        return inserted

    async def query_graph(
        self,
        query: str,
        record_bot_id: UUID,
        session: AsyncSession,
        *,
        max_hops: int = 2,
        max_entities: int = 20,
        channel_type: str = "web",
    ) -> list[dict]:
        """Find relevant entities and traverse relations for graph-based retrieval.

        Strategy:
        1. Extract key terms from query (simple keyword extraction)
        2. Find matching entities (subject or object) via ILIKE
        3. Traverse N hops from those entities
        4. Return context triples sorted by relevance

        Returns list of dicts with keys: subject, relation, object, source_document, hop.
        """
        if not query or not query.strip():
            return []

        # Extract keywords from query for entity matching
        keywords = _extract_query_keywords(query)
        if not keywords:
            return []

        try:
            # Hop 0: find seed entities matching query keywords
            seed_conditions = " OR ".join(
                f"LOWER(subject) LIKE :kw{i} OR LOWER(object) LIKE :kw{i}"
                for i in range(len(keywords))
            )
            params: dict[str, Any] = {"record_bot_id": record_bot_id}
            for i, kw in enumerate(keywords):
                kw_normalized = normalize_vn(kw).lower()
                params[f"kw{i}"] = f"%{kw_normalized}%"

            seed_result = await session.execute(
                text(f"""
                    SELECT subject, relation, object, source_document
                    FROM knowledge_edges
                    WHERE record_bot_id = :record_bot_id AND ({seed_conditions})
                    LIMIT 50
                """),
                params,
            )
            seed_rows = seed_result.fetchall()

            if not seed_rows:
                return []

            context: list[dict] = []
            context_keys: set[tuple[str, str, str]] = set()
            seen_entities: set[str] = set()
            current_entities: set[str] = set()

            # Collect hop-0 results
            for row in seed_rows:
                s_norm = normalize_vn(row[0]).lower()
                r_norm = normalize_vn(row[1]).lower()
                o_norm = normalize_vn(row[2]).lower()
                # Skip self-loops
                if s_norm == o_norm:
                    continue
                triple_key = (s_norm, r_norm, o_norm)
                if triple_key in context_keys:
                    continue
                context_keys.add(triple_key)
                context.append({
                    "subject": row[0],
                    "relation": row[1],
                    "object": row[2],
                    "source_document": row[3] or "",
                    "hop": 0,
                })
                current_entities.add(s_norm)
                current_entities.add(o_norm)

            seen_entities.update(current_entities)

            # Multi-hop traversal
            for hop in range(1, max_hops):
                if not current_entities:
                    break

                entity_list = list(current_entities)[:max_entities]
                hop_result = await session.execute(
                    text("""
                        SELECT subject, relation, object, source_document
                        FROM knowledge_edges
                        WHERE record_bot_id = :record_bot_id
                          AND (LOWER(subject) = ANY(:entities) OR LOWER(object) = ANY(:entities))
                        LIMIT 50
                    """),
                    {"record_bot_id": record_bot_id, "entities": entity_list},
                )
                hop_rows = hop_result.fetchall()

                next_entities: set[str] = set()
                for row in hop_rows:
                    s_norm = normalize_vn(row[0]).lower()
                    r_norm = normalize_vn(row[1]).lower()
                    o_norm = normalize_vn(row[2]).lower()
                    # Skip self-loops
                    if s_norm == o_norm:
                        continue
                    triple_key = (s_norm, r_norm, o_norm)
                    # Avoid duplicate triples in context — O(1) lookup
                    if triple_key not in context_keys:
                        context_keys.add(triple_key)
                        context.append({
                            "subject": row[0],
                            "relation": row[1],
                            "object": row[2],
                            "source_document": row[3] or "",
                            "hop": hop,
                        })
                    if s_norm not in seen_entities:
                        next_entities.add(s_norm)
                    if o_norm not in seen_entities:
                        next_entities.add(o_norm)

                # Break early if no new entities found
                if not next_entities:
                    break

                seen_entities.update(next_entities)
                current_entities = next_entities

            return context

        except (SQLAlchemyError, ValueError, TypeError):
            logger.warning("graph_query_failed", query=query[:80], exc_info=True)
            return []


def _parse_triples_json(raw_text: str) -> list[dict]:
    """Parse JSON array of triples from LLM response, tolerating markdown fences."""
    text_clean = raw_text.strip()
    # Strip markdown code fences
    if text_clean.startswith("```"):
        lines = text_clean.split("\n")
        # Remove first and last fence lines
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text_clean = "\n".join(lines).strip()

    # Find JSON array
    start = text_clean.find("[")
    end = text_clean.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []

    try:
        parsed = json.loads(text_clean[start : end + 1])
        if isinstance(parsed, list):
            # Enforce max length limits and skip self-references at parse level
            validated: list[dict] = []
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                subj = (item.get("subject") or "").strip()[:DEFAULT_KG_TRIPLE_SUBJ_MAX_CHARS]
                rel = (item.get("relation") or "").strip()[:DEFAULT_KG_TRIPLE_REL_MAX_CHARS]
                obj = (item.get("object") or "").strip()[:DEFAULT_KG_TRIPLE_OBJ_MAX_CHARS]
                if not (subj and rel and obj):
                    continue
                if normalize_vn(subj).lower() == normalize_vn(obj).lower():
                    continue
                validated.append({"subject": subj, "relation": rel, "object": obj})
            return validated
    except (json.JSONDecodeError, ValueError):
        pass
    return []


def _extract_query_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a query for entity matching.

    Simple approach: split on whitespace, filter stopwords and short tokens.
    """
    # Vietnamese + English stopwords (minimal set)
    stopwords = {
        "la", "cua", "va", "co", "trong", "nhu", "cho", "den",
        "nao", "gi", "the", "nay", "do", "se", "da", "dang",
        "khong", "duoc", "mot", "nhung", "voi", "tu", "bao",
        "is", "the", "a", "an", "of", "in", "to", "for", "and",
        "or", "with", "by", "on", "at", "from", "what", "how",
        "who", "which", "that", "this", "there",
    }
    words = query.strip().split()
    keywords = [
        w for w in words
        if len(w) > 2 and w.lower().strip("?.!,;:") not in stopwords
    ]
    # Return cleaned keywords (strip punctuation)
    return [w.strip("?.!,;:\"'()[]{}") for w in keywords if w.strip("?.!,;:\"'()[]{}")]


__all__ = ["KnowledgeGraphService"]
