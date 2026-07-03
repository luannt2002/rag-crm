"""Served-chunks persistence (truth-audit): every assistant turn stores the
chunk list the LLM saw — auditable without debug mode. Pure-helper tests +
wiring pins on the transport that persists chat_histories."""
from __future__ import annotations

import inspect

from ragbot.shared.constants import (
    SERVED_CHUNKS_PERSIST_MAX_CHARS,
    SERVED_CHUNKS_PERSIST_MAX_ITEMS,
)
from ragbot.shared.served_chunks import build_served_chunks


def test_normalizes_fields_and_caps_content() -> None:
    out = build_served_chunks([
        {"chunk_id": "c1", "score": 0.91, "source": "stats_index",
         "document_name": "d", "content": "x" * 1000},
        {"id": "c2", "rerank_score": 0.5, "text": "abc"},
    ])
    assert out[0]["chunk_id"] == "c1" and out[0]["score"] == 0.91
    assert len(out[0]["content"]) == SERVED_CHUNKS_PERSIST_MAX_CHARS
    assert out[1]["chunk_id"] == "c2" and out[1]["score"] == 0.5
    assert out[1]["content"] == "abc"


def test_caps_item_count() -> None:
    out = build_served_chunks([{"chunk_id": str(i), "content": "x"} for i in range(50)])
    assert len(out) == SERVED_CHUNKS_PERSIST_MAX_ITEMS


def test_empty_and_none_safe() -> None:
    assert build_served_chunks(None) == []
    assert build_served_chunks([]) == []
    assert build_served_chunks([None, "junk"]) == []  # type: ignore[list-item]


def test_chat_routes_persist_and_return_served_chunks() -> None:
    """Pin: both chat_histories INSERT sites bind :sc for the assistant row and
    the history endpoint SELECTs + returns served_chunks."""
    import ragbot.interfaces.http.routes.test_chat.chat_routes as cr

    src = inspect.getsource(cr)
    assert src.count("served_chunks") >= 4
    assert "build_served_chunks" in src
    assert ":sc" in src
