"""Pin test: semantic_cache stores + restores chunks snapshot.

Before 2026-05-27: cache hit returned answer+citations only. /chat API's
``_build_sources`` reads from ``graded_chunks`` → cache_hit responses
had ``sources=[]`` → external evaluator (RAGAS LLM judge) saw n_chunks=0
→ Faith=0 + CtxPrec=0 on every cached turn.

After fix: ``CachedResponse.chunks`` carries a compact snapshot of
graded_chunks (≤8 chunks × 2KB content) stored in ``semantic_cache.
metadata_json`` and restored on hit so sources match a fresh response.
"""
from __future__ import annotations

from dataclasses import asdict

from ragbot.application.ports.cache_port import CachedResponse


def test_cached_response_has_chunks_field() -> None:
    """CachedResponse dataclass must expose ``chunks`` for snapshot round-trip."""
    r = CachedResponse(
        answer="x",
        citations=[],
        model_name="m",
        cached_at_ts=0,
        chunks=({"document_name": "d", "content": "c", "score": 0.5},),
    )
    assert r.chunks == ({"document_name": "d", "content": "c", "score": 0.5},)


def test_cached_response_chunks_default_empty_tuple() -> None:
    """Existing callers passing 4 args must keep working — backward compat."""
    r = CachedResponse(answer="x", citations=[], model_name="m", cached_at_ts=0)
    assert r.chunks == ()
    # asdict roundtrip works
    d = asdict(r)
    assert d["chunks"] == ()


def test_cached_response_chunks_is_tuple_immutable() -> None:
    """chunks=tuple keeps the dataclass frozen-safe (no list mutation hazard)."""
    r = CachedResponse(answer="x", citations=[], model_name="m", cached_at_ts=0, chunks=())
    assert isinstance(r.chunks, tuple)
