"""P28-α2: RRF rank-miss penalty is bound via :rrf_miss, not inlined.

BATCH 2 audit F1b: `COALESCE(d.rank_d, 1000)` had magic literal 1000.
Fix: bind DEFAULT_RRF_RANK_MISS_PENALTY as :rrf_miss parameter.
"""

from __future__ import annotations

import inspect
import re

from ragbot.infrastructure.vector import pgvector_store
from ragbot.shared.constants import DEFAULT_RRF_RANK_MISS_PENALTY


def test_default_constant_value() -> None:
    """Constant value matches BATCH 2 α spec (1000)."""
    assert DEFAULT_RRF_RANK_MISS_PENALTY == 1000


def test_sql_uses_named_bind_not_inline_literal() -> None:
    """COALESCE lines must use :rrf_miss, never literal 1000."""
    src = inspect.getsource(pgvector_store.PgVectorStore.hybrid_search)

    # The :rrf_miss bind parameter is used in COALESCE
    assert ":rrf_miss" in src, "Expected :rrf_miss named bind parameter in hybrid_search SQL"

    # Specifically check COALESCE lines don't have inline 1000
    coalesce_with_literal = re.compile(r"COALESCE\(\s*(?:d\.rank_d|s\.rank_s)\s*,\s*1000\s*\)")
    assert not coalesce_with_literal.search(src), (
        "Found inline literal 1000 in COALESCE(rank_d/rank_s, ...) — "
        "must use :rrf_miss bind parameter instead"
    )

    # Positive check: COALESCE uses :rrf_miss
    coalesce_with_bind = re.compile(r"COALESCE\(\s*(?:d\.rank_d|s\.rank_s)\s*,\s*:rrf_miss\s*\)")
    matches = coalesce_with_bind.findall(src)
    assert len(matches) >= 2, (
        f"Expected at least 2 COALESCE(..., :rrf_miss) occurrences (rank_d + rank_s), "
        f"found {len(matches)}"
    )


def test_import_present() -> None:
    """Module imports DEFAULT_RRF_RANK_MISS_PENALTY constant."""
    src = inspect.getsource(pgvector_store)
    assert "DEFAULT_RRF_RANK_MISS_PENALTY" in src, (
        "pgvector_store must import DEFAULT_RRF_RANK_MISS_PENALTY from shared.constants"
    )


def test_hybrid_search_signature_has_rrf_miss_kwarg() -> None:
    """hybrid_search exposes rrf_miss knob with correct default."""
    sig = inspect.signature(pgvector_store.PgVectorStore.hybrid_search)
    assert "rrf_miss" in sig.parameters, "hybrid_search must accept rrf_miss kwarg"
    param = sig.parameters["rrf_miss"]
    assert param.default == DEFAULT_RRF_RANK_MISS_PENALTY, (
        f"rrf_miss default must be DEFAULT_RRF_RANK_MISS_PENALTY ({DEFAULT_RRF_RANK_MISS_PENALTY}), "
        f"got {param.default}"
    )
