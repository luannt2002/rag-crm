"""Verify ``mmr_filter`` can drop the heavyweight ``embedding`` field from
its output once cosine diversity has consumed it.

MMR is the last node in the retrieval chain that needs the chunk vector;
grade / generate / persist read content text only. Leaving 1024-float
arrays attached to every selected chunk bloats the LangGraph state
checkpoint serialised at every node and inflates the persisted query
trail. The opt-in ``strip_embedding=True`` lets the caller release that
memory immediately, without touching the Port surface.
"""

from __future__ import annotations

from ragbot.orchestration import query_graph as qg_module
from ragbot.shared.mmr import mmr_filter


def _chunk(text: str, score: float, *, embedding: list[float] | None) -> dict:
    out: dict = {"content": text, "score": score, "id": text[:6]}
    if embedding is not None:
        out["embedding"] = embedding
    return out


def test_mmr_filter_strip_embedding_default_preserves_field() -> None:
    chunks = [
        _chunk("alpha aaa", 0.9, embedding=[1.0, 0.0]),
        _chunk("beta bbb", 0.8, embedding=[0.0, 1.0]),
    ]
    out = mmr_filter(chunks, lambda_param=0.7, similarity_threshold=0.99)
    assert all("embedding" in c for c in out), (
        "default behaviour must preserve embedding so callers without an "
        "explicit opt-in stay backwards compatible"
    )


def test_mmr_filter_strip_embedding_drops_field_on_full_path() -> None:
    chunks = [
        _chunk("alpha aaa", 0.9, embedding=[1.0, 0.0]),
        _chunk("beta bbb", 0.8, embedding=[0.0, 1.0]),
        _chunk("gamma ccc", 0.7, embedding=[0.7, 0.7]),
    ]
    out = mmr_filter(
        chunks,
        lambda_param=0.7,
        similarity_threshold=0.99,
        strip_embedding=True,
    )
    assert len(out) >= 2, "MMR should retain diverse picks before strip"
    assert all("embedding" not in c for c in out), (
        f"strip_embedding=True must remove the embedding field; got {out}"
    )


def test_mmr_filter_strip_embedding_drops_field_on_short_circuit() -> None:
    chunks = [_chunk("only one", 0.9, embedding=[1.0, 0.0])]
    out = mmr_filter(chunks, strip_embedding=True)
    assert len(out) == 1
    assert "embedding" not in out[0], (
        "the n<=1 short-circuit must also honour strip_embedding"
    )


def test_mmr_filter_strip_embedding_handles_chunks_without_embedding() -> None:
    chunks = [
        _chunk("alpha aaa", 0.9, embedding=None),
        _chunk("beta bbb", 0.8, embedding=None),
    ]
    out = mmr_filter(chunks, strip_embedding=True)
    # No embedding to strip — filter must still succeed.
    assert all("embedding" not in c for c in out)


def test_mmr_dedup_node_passes_strip_embedding_true() -> None:
    """The pipeline call site must opt into strip_embedding so the
    chunk vector is released before grade / generate / persist
    serialise the state checkpoint."""
    import inspect
    import re

    # The mmr_filter call lives in the extracted mmr_dedup node module
    # (query_graph.build_graph binds it via functools.partial).
    from ragbot.orchestration.nodes.mmr_dedup import mmr_dedup as _mmr_dedup_node
    src = inspect.getsource(_mmr_dedup_node)
    # Look for the mmr_filter invocation and confirm strip_embedding=True
    # is in its kwargs.
    pat = re.compile(r"mmr_filter\(([^)]*)\)", re.DOTALL)
    matches = pat.findall(src)
    assert matches, "mmr_dedup node must invoke mmr_filter"
    assert any("strip_embedding=True" in m for m in matches), (
        f"mmr_dedup must pass strip_embedding=True; saw kwargs: {matches}"
    )
