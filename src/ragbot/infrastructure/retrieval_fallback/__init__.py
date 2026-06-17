"""Retrieval-fallback strategies — multi-stage Port chain (Stream S8).

Default OFF (``system_config.retrieval_multistage_enabled = False``).
When flipped on, ``query_graph.retrieve`` walks the chain:
1. ``hybrid_stage1`` (vector + BM25, the only stage that needs an embedding)
2. ``bm25_only_stage2`` (sparse-only, survives embedder outage)
3. ``keyword_stage3`` (regex anchor for structural references)
4. ``parent_expand_stage4`` (parent-chunk lift over prior result)

Each stage implements ``RetrievalFallbackPort``. Add a new stage = drop a
file in this package and register it in ``registry.py``.
"""

from ragbot.infrastructure.retrieval_fallback.bm25_only_stage2 import (
    BM25OnlyStage2Retriever,
)
from ragbot.infrastructure.retrieval_fallback.hybrid_stage1 import (
    HybridStage1Retriever,
)
from ragbot.infrastructure.retrieval_fallback.keyword_stage3 import (
    KeywordStage3Retriever,
)
from ragbot.infrastructure.retrieval_fallback.null_stage import NullRetrievalStage
from ragbot.infrastructure.retrieval_fallback.parent_expand_stage4 import (
    ParentExpandStage4Retriever,
)
from ragbot.infrastructure.retrieval_fallback.registry import (
    build_retrieval_fallback,
    list_stages,
)

__all__ = [
    "BM25OnlyStage2Retriever",
    "HybridStage1Retriever",
    "KeywordStage3Retriever",
    "NullRetrievalStage",
    "ParentExpandStage4Retriever",
    "build_retrieval_fallback",
    "list_stages",
]
