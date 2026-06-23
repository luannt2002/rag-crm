"""I-3: hybrid_search must gate the VN compound segmenter on language.

Ingest segments VN compounds only for VI_DOMAIN_LANGUAGES; the query side
ran ``segment_vi_compounds`` UNCONDITIONALLY, so an English query was run
through the Vietnamese underthesea segmenter (asymmetric with ingest +
wasted CPU). hybrid_search now takes a per-bot ``language`` and gates the
segmenter on it.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import ragbot.infrastructure.vector.pgvector_store as pgmod
from ragbot.infrastructure.vector.pgvector_store import PgVectorStore


class _FakeResult:
    def mappings(self):
        class _M:
            def all(self):
                return []

        return _M()


class _FakeSession:
    async def execute(self, *_a, **_kw):
        return _FakeResult()

    async def close(self):
        return None


def _session_factory():
    """Mimic an async_sessionmaker: callable returning a session object."""
    return _FakeSession()


@pytest.mark.asyncio
async def test_english_query_skips_vn_segmenter(monkeypatch) -> None:
    seen: list[str] = []

    def _spy(txt: str) -> str:
        seen.append(txt)
        return txt + "_SEGMENTED"

    monkeypatch.setattr(pgmod, "segment_vi_compounds", _spy)
    store = PgVectorStore(session_factory=_session_factory)

    await store.hybrid_search(
        query_text="what is the warranty period",
        query_embedding=[0.1] * 8,
        record_bot_id=uuid4(),
        record_tenant_id=uuid4(),
        language="en",
    )
    assert seen == [], (
        f"VN segmenter ran on an English query: {seen} — must be gated by language"
    )


@pytest.mark.asyncio
async def test_vietnamese_query_runs_vn_segmenter(monkeypatch) -> None:
    seen: list[str] = []

    def _spy(txt: str) -> str:
        seen.append(txt)
        return txt

    monkeypatch.setattr(pgmod, "segment_vi_compounds", _spy)
    store = PgVectorStore(session_factory=_session_factory)

    await store.hybrid_search(
        query_text="thời gian bảo hành là bao lâu",
        query_embedding=[0.1] * 8,
        record_bot_id=uuid4(),
        record_tenant_id=uuid4(),
        language="vi",
    )
    assert seen, "VN segmenter must run for a Vietnamese-language bot"
