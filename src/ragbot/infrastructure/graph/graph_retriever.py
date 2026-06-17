"""Graph-based retrieval node for the LangGraph pipeline.

Only called when ``graph_rag_mode != 'disabled'`` and (in adaptive mode)
when intent is ``multi_hop`` or ``aggregation``.
"""

from __future__ import annotations

from typing import Any

import structlog

from ragbot.orchestration.state import GraphState
from ragbot.shared.constants import DEFAULT_INTENT_FALLBACK, INTENT_SYNTHESIS

logger = structlog.get_logger(__name__)


async def graph_retrieve(
    state: GraphState,
    *,
    kg_service: Any,
    session_factory: Any,
) -> dict:
    """Retrieve additional context via knowledge graph traversal.

    Gracefully returns empty context if no triples exist or on any failure.
    Merges graph context into retrieved_chunks so downstream rerank/grade
    can process them uniformly.
    """
    # Read config from pipeline_config
    pcfg = state.get("pipeline_config") or {}
    graph_mode = pcfg.get("graph_rag_mode", "disabled")
    max_hops = int(pcfg.get("graph_rag_max_hops", 2))

    if graph_mode == "disabled":
        return {"graph_context": []}

    # In adaptive mode, only run for complex query intents
    intent = state.get("intent", DEFAULT_INTENT_FALLBACK)
    if graph_mode == "adaptive" and intent not in INTENT_SYNTHESIS:
        logger.debug(
            "graph_retrieve_skipped_adaptive",
            intent=intent,
            graph_mode=graph_mode,
        )
        return {"graph_context": []}

    record_bot_id = state.get("record_bot_id")
    if not record_bot_id:
        return {"graph_context": []}

    query_text = state.get("rewritten_query") or state.get("query", "")
    if not query_text:
        return {"graph_context": []}

    try:
        async with session_factory() as session:
            triples = await kg_service.query_graph(
                query=query_text,
                bot_id=record_bot_id,
                session=session,
                max_hops=max_hops,
            )

        if not triples:
            logger.debug("graph_retrieve_no_triples", record_bot_id=str(record_bot_id))
            return {"graph_context": []}

        # Build context chunks from triples for merging into retrieved_chunks
        graph_chunks: list[dict] = []
        for triple in triples:
            # Synthesize a text representation of the triple
            text_repr = (
                f"{triple['subject']} {triple['relation']} {triple['object']}"
            )
            source_doc = triple.get("source_document", "")
            graph_chunks.append({
                "content": text_repr,
                "text": text_repr,
                "score": 0.5,  # neutral score — let reranker decide relevance
                "document_name": source_doc,
                "chunk_id": None,
                "document_id": None,
                "chunk_index": "",
                "source": "graph_rag",
                "hop": triple.get("hop", 0),
                "is_graph_synthesized": True,
            })

        # Merge into existing retrieved_chunks
        existing_chunks = list(state.get("retrieved_chunks") or [])
        merged = existing_chunks + graph_chunks

        logger.info(
            "graph_retrieve_ok",
            triples=len(triples),
            graph_chunks=len(graph_chunks),
            total_chunks=len(merged),
            record_bot_id=str(record_bot_id),
        )
        return {
            "graph_context": triples,
            "retrieved_chunks": merged,
        }

    except Exception:  # noqa: BLE001
        logger.warning("graph_retrieve_failed", exc_info=True)
        return {"graph_context": []}


__all__ = ["graph_retrieve"]
