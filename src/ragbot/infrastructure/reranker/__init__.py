"""Reranker adapters (optional — reranking can be skipped).

Strategy pattern: ``build_reranker(provider, **kwargs)`` returns the matching
implementation. Default provider = ``"null"`` (NullReranker, no-op bypass).
"""

from ragbot.infrastructure.reranker.jina_reranker import JinaReranker
from ragbot.infrastructure.reranker.litellm_reranker import LiteLLMReranker
from ragbot.infrastructure.reranker.null_reranker import NullReranker
from ragbot.infrastructure.reranker.registry import build_reranker, list_providers
from ragbot.infrastructure.reranker.viranker_local_reranker import (
    ViRankerLocalReranker,
)
from ragbot.infrastructure.reranker.voyage_reranker import VoyageReranker
from ragbot.infrastructure.reranker.zeroentropy_reranker import ZeroEntropyReranker

__all__: list[str] = [
    "JinaReranker",
    "LiteLLMReranker",
    "NullReranker",
    "ViRankerLocalReranker",
    "VoyageReranker",
    "ZeroEntropyReranker",
    "build_reranker",
    "list_providers",
]
