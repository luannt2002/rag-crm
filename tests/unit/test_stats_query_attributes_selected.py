"""[T1-Smartness] Every stats query that feeds the synthetic-chunk renderer MUST
select ``attributes_json``.

The synthetic-chunk renderer (query_graph) loops ``entity["attributes_json"]`` to
surface the owner's generic labelled fields ("Giá Combo 10 buổi", "Tồn", "date1",
"RAM") to the LLM. A query method that omits the column from its SELECT returns
entities whose ``attributes_json`` is absent → the renderer emits only the
name + headline value → the LLM never sees the combo/stock/date and refuses or
extrapolates (the spa "triệt râu combo" HALLU). ``list_all_entities`` regressed this
way; this guard pins all renderer-feeding methods.
"""
from __future__ import annotations

import inspect

from ragbot.infrastructure.repositories.stats_index_repository import (
    StatsIndexRepository,
)

# Methods whose result dicts are rendered into the synthetic LLM chunk.
_RENDERER_FEEDING_METHODS = [
    "list_all_entities",
    "query_by_name_keyword",
    "query_by_price_range",
    "top_by_price",
]


def test_all_renderer_feeding_queries_select_attributes_json() -> None:
    for name in _RENDERER_FEEDING_METHODS:
        method = getattr(StatsIndexRepository, name)
        src = inspect.getsource(method)
        assert "attributes_json" in src, (
            f"{name}() does not reference attributes_json — the synthetic-chunk "
            f"renderer will emit only name:price and the LLM loses every generic "
            f"labelled field (combo price / stock / date). Add it to the SELECT "
            f"and the returned row dict."
        )
