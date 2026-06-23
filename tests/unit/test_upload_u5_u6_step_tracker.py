"""U5 CR enrich ∥ U6 VN segment — step_tracker observability preservation.

[T2-CostPerf] After the U5 ∥ U6 refactor, BOTH ingest_enrich (U5) and
ingest_vn_segment (U6) step rows MUST still be emitted to request_steps
in the canonical order (U5 before U6), with the correct metadata keys.

This test proves the parallelisation of config reads and per-chunk ops
did NOT remove or reorder the observability wraps.

Real behavioural assertions:
- ingest_enrich row appears in tracker.steps.
- ingest_vn_segment row appears AFTER ingest_enrich.
- u5_u6_concurrent metadata key is present on ingest_vn_segment row.
- Both rows carry step_kind = "ingest".
- No row appears multiple times (no double-emit).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from ragbot.application.services.document_service import (
    INGEST_STEP_KIND,
    INGEST_STEP_NAMES,
    DocumentService,
    _phase_d_step,
)


# ── Recording fakes (mirror test_phase_d_ingest_observability pattern) ── #

class _RecordingStepCtx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict[str, Any] = {}

    def set_metadata(self, **kwargs: Any) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs: Any) -> None:
        return None

    def record_llm(self, **_kwargs: Any) -> None:
        return None


class _RecordingStepTracker:
    def __init__(self, *, kind: str = "ingest") -> None:
        self.steps: list[_RecordingStepCtx] = []
        self._kind = kind

    @property
    def kind(self) -> str:
        return self._kind

    @asynccontextmanager
    async def step(self, name: str, **kwargs: Any):
        ctx = _RecordingStepCtx(name)
        seed = kwargs.get("metadata") or {}
        ctx.metadata.update(seed)
        self.steps.append(ctx)
        yield ctx

    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def by_name(self, name: str) -> list[_RecordingStepCtx]:
        return [s for s in self.steps if s.name == name]


class _FakeResult:
    rowcount: int = 0

    def fetchone(self) -> None:
        return None

    def first(self) -> None:
        return None

    def fetchall(self) -> list:
        return []

    def scalar(self) -> None:
        # documents upsert RETURNING id — None keeps the caller's generated id.
        return None


class _FakeAsyncSession:
    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        return _FakeResult()

    async def commit(self) -> None:
        return None


@asynccontextmanager
async def _fake_session_with_tenant(_sf: Any, *, record_tenant_id: Any) -> Any:
    yield _FakeAsyncSession()


def _fake_session_factory():
    @asynccontextmanager
    async def _ctx() -> Any:
        yield _FakeAsyncSession()

    return _ctx


def _build_doc_service() -> DocumentService:
    sf = MagicMock()

    settings = MagicMock()
    settings.rag.default_chunk_size = 1024
    settings.rag.default_chunk_overlap = 100
    settings.enrichment.enabled = False
    settings.enrichment.model_name = ""
    settings.enrichment.temperature = 0.0
    settings.enrichment.max_tokens = 100
    settings.enrichment.timeout_s = 5
    settings.enrichment.doc_preview_chars = 500
    settings.enrichment.chunk_preview_chars = 500
    settings.enrichment.max_prefix_chars = 500
    settings.embedding.model_version = "v1"

    async def _embed_batch(texts: list, **_kw: Any) -> list:
        return [[0.1] * 8 for _ in texts]

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(side_effect=_embed_batch)

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=False)
    cfg.get_int = AsyncMock(return_value=1024)
    cfg.get_float = AsyncMock(return_value=0.0)
    cfg.get_bool = AsyncMock(return_value=False)

    svc = DocumentService(
        session_factory=_fake_session_factory(),
        embedder=embedder,
        settings=settings,
        config_service=cfg,
    )
    spec = MagicMock()
    spec.model_name = "text-embedding-3-small"
    spec.dimension = 8
    spec.max_batch = 64
    svc._embedding_spec = AsyncMock(return_value=spec)
    return svc


# ── Tests ──────────────────────────────────────────────────────────────── #

def test_u5_and_u6_step_rows_both_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """After U5 ∥ U6 refactor: BOTH ingest_enrich and ingest_vn_segment
    step rows MUST still appear in the tracker. Parallelisation of config
    reads / per-chunk ops must NOT remove either step wrap.
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None))

    svc = _build_doc_service()
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="u5-u6-tracker-test",
            content="Nội dung kiểm chứng U5 và U6 vẫn ghi step_tracker row " * 4,
            source_url="",
            source_type="unit_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    names = tracker.names()
    assert "ingest_enrich" in names, (
        f"ingest_enrich (U5) missing from tracker: {names}"
    )
    assert "ingest_vn_segment" in names, (
        f"ingest_vn_segment (U6) missing from tracker: {names}"
    )


def test_u5_step_row_appears_before_u6(monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest_enrich (U5) MUST appear at a lower index than ingest_vn_segment
    (U6) — canonical pipeline order is preserved by the refactor.
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None))

    svc = _build_doc_service()
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="order-test",
            content="Kiểm chứng thứ tự U5 trước U6 " * 6,
            source_url="",
            source_type="unit_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    names = tracker.names()
    idx_u5 = names.index("ingest_enrich")
    idx_u6 = names.index("ingest_vn_segment")
    assert idx_u5 < idx_u6, (
        f"U5 must appear before U6; got U5 at {idx_u5}, U6 at {idx_u6}: {names}"
    )


def test_u5_step_row_carries_cr_active_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest_enrich row MUST carry 'cr_active' key so analytics dashboards
    can split CR-path vs legacy-enrich ingest jobs.
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None))

    svc = _build_doc_service()
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="cr-metadata-test",
            content="CR metadata key assertion test " * 6,
            source_url="",
            source_type="unit_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    u5_rows = tracker.by_name("ingest_enrich")
    assert len(u5_rows) >= 1, "ingest_enrich must appear at least once"
    md = u5_rows[0].metadata
    assert "cr_active" in md, f"cr_active key missing from U5 row: {md}"
    assert "n_chunks_in" in md, f"n_chunks_in key missing from U5 row: {md}"
    assert "n_chunks_out" in md, f"n_chunks_out key missing from U5 row: {md}"


def test_u6_step_row_carries_u5_u6_concurrent_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest_vn_segment row MUST carry 'u5_u6_concurrent' boolean key —
    added by the refactor to signal whether U6 results were pre-computed
    alongside U5 (CR-active path) or computed separately (non-CR path).
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None))

    svc = _build_doc_service()
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="u6-concurrent-metadata-test",
            content="U6 concurrent metadata assertion " * 6,
            source_url="",
            source_type="unit_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    u6_rows = tracker.by_name("ingest_vn_segment")
    assert len(u6_rows) >= 1, "ingest_vn_segment must appear at least once"
    md = u6_rows[0].metadata
    assert "u5_u6_concurrent" in md, (
        f"u5_u6_concurrent key missing from U6 row: {md} — "
        "refactor must add this observability key"
    )
    assert isinstance(md["u5_u6_concurrent"], bool), (
        f"u5_u6_concurrent must be bool, got {type(md['u5_u6_concurrent'])}"
    )
    assert "vi_seg_enabled" in md, f"vi_seg_enabled missing from U6: {md}"
    assert "n_chunks_total" in md, f"n_chunks_total missing from U6: {md}"


def test_both_step_rows_carry_ingest_step_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both U5 and U6 rows MUST carry step_kind='ingest' for analytics.
    This ensures the _phase_d_step wrapper still seeds step_kind on both
    rows after the parallelism refactor.
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None))

    svc = _build_doc_service()
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="step-kind-test",
            content="step_kind assertion for U5 and U6 rows " * 6,
            source_url="",
            source_type="unit_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    for step_name in ("ingest_enrich", "ingest_vn_segment"):
        rows = tracker.by_name(step_name)
        assert rows, f"{step_name} row missing"
        md = rows[0].metadata
        assert md.get("step_kind") == INGEST_STEP_KIND, (
            f"{step_name} has wrong step_kind: {md.get('step_kind')!r}"
        )
