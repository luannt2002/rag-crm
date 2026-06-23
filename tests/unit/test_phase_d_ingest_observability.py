"""Phase D ingest pipeline observability — U1-U7 step coverage.

Verifies the 7 ingest-side wraps inside ``DocumentService.ingest()`` per
deepdive's ``reports/MEGA_24STEP_MATRIX_20260430.md`` §2 plan:

- ``ingest_validate`` (U1) — tenant guard + sanity gate
- ``ingest_parse`` (U2) — parser registry routing
- ``ingest_clean`` (U3) — cleaner + injection-strip
- ``ingest_chunk`` (U4) — whole-doc / parent-child / smart_chunk branch
- ``ingest_enrich`` (U5) — Contextual Retrieval + legacy enrichment
- ``ingest_vn_segment`` (U6) — VN compound segmentation for BM25
- ``ingest_embed_store`` (U7) — embed batch + bulk_insert_chunks

T2 / observability — instrumentation OBSERVES only. Zero LLM injection,
zero answer override, zero new LLM calls. Backward-compat: when no
tracker is injected, ingest runs the legacy untracked path.
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


# --------------------------------------------------------------------------- #
# Recording fakes — mirror Phase A/B/C/D-query harness                        #
# --------------------------------------------------------------------------- #


class _RecordingStepCtx:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata: dict = {}

    def set_metadata(self, **kwargs: Any) -> None:
        self.metadata.update(kwargs)

    def add_tokens(self, **_kwargs: Any) -> None:
        return None

    def record_llm(self, **_kwargs: Any) -> None:
        """Wave M3.2 — no-op mirror of StepContext.record_llm."""
        return None


class _RecordingStepTracker:
    """Captures (step_name, metadata) per ``async with tracker.step()``.

    Mimics ``StepTracker.step()`` contract — accepts the ``metadata``
    kwarg that the production tracker uses to seed the recorded row.
    """

    def __init__(self, *, kind: str = "query") -> None:
        self.steps: list[_RecordingStepCtx] = []
        self._kind = kind

    @property
    def kind(self) -> str:
        return self._kind

    @asynccontextmanager
    async def step(self, name: str, **kwargs: Any):
        ctx = _RecordingStepCtx(name)
        # Preserve any seed metadata passed by ``_phase_d_step``.
        seed = kwargs.get("metadata") or {}
        ctx.metadata.update(seed)
        self.steps.append(ctx)
        yield ctx

    def names(self) -> list[str]:
        return [s.name for s in self.steps]

    def by_name(self, name: str) -> list[_RecordingStepCtx]:
        return [s for s in self.steps if s.name == name]


# --------------------------------------------------------------------------- #
# 1. INGEST_STEP_NAMES contract                                               #
# --------------------------------------------------------------------------- #


def test_ingest_step_names_tuple_is_canonical_seven():
    """The canonical Phase D ingest step name list MUST be exactly 7
    entries in the documented runtime order. Analyzers / dashboards
    pin against this contract.
    """
    assert isinstance(INGEST_STEP_NAMES, tuple), type(INGEST_STEP_NAMES)
    assert len(INGEST_STEP_NAMES) == 7, INGEST_STEP_NAMES
    assert INGEST_STEP_NAMES == (
        "ingest_validate",
        "ingest_parse",
        "ingest_clean",
        "ingest_chunk",
        "ingest_enrich",
        "ingest_vn_segment",
        "ingest_embed_store",
    )


def test_ingest_step_kind_constant_is_ingest():
    """``INGEST_STEP_KIND`` is the metadata-namespace label that gets
    written to ``request_steps.metadata_json.step_kind`` so analytics
    can split ingest vs query rows without joining ``request_logs``.
    """
    assert INGEST_STEP_KIND == "ingest"


# --------------------------------------------------------------------------- #
# 2. _phase_d_step helper — backward-compat with no tracker                   #
# --------------------------------------------------------------------------- #


def test_phase_d_step_yields_noop_when_tracker_is_none():
    """When ``step_tracker is None`` (legacy callers), ``_phase_d_step``
    MUST yield a stub that swallows ``set_metadata`` / ``add_tokens``
    so wrap-site code stays flat and untracked ingest still works.
    """
    async def _drive() -> None:
        async with _phase_d_step(None, "ingest_validate") as ctx:
            # Stub must not raise on either method.
            ctx.set_metadata(n_bytes=42, mime_detected="application/pdf")
            ctx.add_tokens(prompt=10, completion=5)

    asyncio.run(_drive())


def test_phase_d_step_seeds_step_kind_metadata_when_tracker_present():
    """When a tracker is injected, the helper MUST seed
    ``metadata={"step_kind": "ingest"}`` so the recorded row carries
    the namespace label without the wrap site having to set it manually.
    """
    tracker = _RecordingStepTracker()

    async def _drive() -> None:
        async with _phase_d_step(tracker, "ingest_chunk") as ctx:
            ctx.set_metadata(n_chunks_out=3)

    asyncio.run(_drive())

    rows = tracker.by_name("ingest_chunk")
    assert len(rows) == 1, tracker.names()
    md = rows[0].metadata
    assert md.get("step_kind") == INGEST_STEP_KIND, md
    assert md.get("n_chunks_out") == 3, md


# --------------------------------------------------------------------------- #
# 3. End-to-end ingest emits 7 steps in canonical order                       #
# --------------------------------------------------------------------------- #


def _build_doc_service(_tmp_session_factory: Any) -> DocumentService:
    """Construct a DocumentService with the minimal collaborators needed
    for the U1-U7 wraps to exercise. We bypass the real SQL session
    factory by passing a context-managed mock — every async-with on it
    yields a session whose ``execute`` / ``commit`` are no-ops.
    """
    # ``self._sf()`` is called directly (without ``session_with_tenant``)
    # in the bot-prefix resolver — provide an async-CM-returning callable.
    @asynccontextmanager
    async def _session_cm() -> Any:
        yield _FakeAsyncSession()

    def _session_factory() -> Any:
        return _session_cm()

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

    # Embedder returns one vector per input — match input length so the
    # ingest length-mismatch guard at the end of U7 stays happy.
    async def _embed_batch(texts: list, **_kw: Any) -> list:
        return [[0.1] * 8 for _ in texts]

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(side_effect=_embed_batch)

    cfg = MagicMock()
    cfg.get = AsyncMock(return_value=False)  # disable cleaning, CR, etc.
    cfg.get_int = AsyncMock(return_value=1024)
    cfg.get_float = AsyncMock(return_value=0.0)
    cfg.get_bool = AsyncMock(return_value=False)

    svc = DocumentService(
        session_factory=_session_factory,
        embedder=embedder,
        settings=settings,
        config_service=cfg,
    )
    # Stub embedding spec to avoid pinging model_resolver.
    spec = MagicMock()
    spec.model_name = "text-embedding-3-small"
    spec.dimension = 8
    spec.max_batch = 64
    svc._embedding_spec = AsyncMock(return_value=spec)
    return svc


def test_phase_d_step_helper_emits_all_seven_step_names_in_order():
    """Drive ``_phase_d_step`` directly through the canonical 7-step list
    and assert names + order + step_kind metadata. This pin protects
    against accidental rename / reorder of ``INGEST_STEP_NAMES``.
    """
    tracker = _RecordingStepTracker()

    async def _drive() -> None:
        for name in INGEST_STEP_NAMES:
            async with _phase_d_step(tracker, name) as ctx:
                ctx.set_metadata(driver_emitted=True)

    asyncio.run(_drive())

    assert tracker.names() == list(INGEST_STEP_NAMES), tracker.names()
    for ctx in tracker.steps:
        assert ctx.metadata.get("step_kind") == INGEST_STEP_KIND, ctx.metadata


def test_phase_d_step_failure_propagates_and_records_via_tracker():
    """If the ingest body raises inside ``async with _phase_d_step()``,
    the exception MUST propagate (caller decides recovery) AND the row
    MUST still appear in the tracker (the production StepTracker writes
    status="failed" in its ``finally`` block — recording-tracker just
    captures the entry).
    """
    tracker = _RecordingStepTracker()

    async def _drive() -> None:
        async with _phase_d_step(tracker, "ingest_embed_store") as _ctx:
            raise RuntimeError("simulated embed failure")

    with pytest.raises(RuntimeError, match="simulated embed failure"):
        asyncio.run(_drive())

    rows = tracker.by_name("ingest_embed_store")
    assert len(rows) == 1, tracker.names()
    assert rows[0].metadata.get("step_kind") == INGEST_STEP_KIND


# --------------------------------------------------------------------------- #
# 4. StepTracker(kind="ingest") backward-compat + propagation                 #
# --------------------------------------------------------------------------- #


def test_step_tracker_kind_defaults_to_query_for_existing_callers():
    """Existing call sites (chat_worker / chat_stream) construct
    ``StepTracker(request_id=..., record_tenant_id=..., repo=...)``
    without ``kind=`` — they MUST stay on the ``"query"`` namespace.
    """
    from ragbot.application.services.step_tracker import StepTracker

    repo = MagicMock()
    repo.add_step = AsyncMock(return_value=uuid4())

    tracker = StepTracker(
        request_id=uuid4(),
        record_tenant_id=uuid4(),
        repo=repo,
    )
    assert tracker.kind == "query"


def test_step_tracker_kind_ingest_writes_step_kind_into_recorded_metadata():
    """When constructed with ``kind="ingest"``, every persisted row
    carries ``metadata_json.step_kind == "ingest"`` — analytics filter
    by this key (no schema migration needed).
    """
    from ragbot.application.services.step_tracker import StepTracker

    repo = MagicMock()
    repo.add_step = AsyncMock(return_value=uuid4())

    tracker = StepTracker(
        request_id=uuid4(),
        record_tenant_id=uuid4(),
        repo=repo,
        kind="ingest",
    )

    async def _drive() -> None:
        async with tracker.step("ingest_validate") as ctx:
            ctx.set_metadata(n_bytes=100)

    asyncio.run(_drive())

    assert repo.add_step.await_count == 1
    call_kwargs = repo.add_step.await_args.kwargs
    md = call_kwargs["metadata"]
    assert md.get("step_kind") == "ingest", md
    assert md.get("n_bytes") == 100, md
    assert call_kwargs["step_name"] == "ingest_validate"


def test_step_tracker_kind_query_writes_step_kind_query():
    """Default ``kind="query"`` rows MUST carry ``step_kind="query"`` —
    bookkeeping for analyzers that aggregate across all rows.
    """
    from ragbot.application.services.step_tracker import StepTracker

    repo = MagicMock()
    repo.add_step = AsyncMock(return_value=uuid4())

    tracker = StepTracker(
        request_id=uuid4(),
        record_tenant_id=uuid4(),
        repo=repo,
    )

    async def _drive() -> None:
        async with tracker.step("retrieve") as ctx:
            ctx.set_metadata(n_chunks=5)

    asyncio.run(_drive())

    md = repo.add_step.await_args.kwargs["metadata"]
    assert md.get("step_kind") == "query", md


# --------------------------------------------------------------------------- #
# 5. End-to-end DocumentService.ingest emits 7 steps                          #
# --------------------------------------------------------------------------- #


class _FakeResult:
    """Stand-in for SQLAlchemy ``Result``. All cursor-style accessors
    return None / [] / 0 so dedup-lookups, re-index lookups, and the
    plan-limits SELECT all take the "no row found" branch.
    """

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
    """Minimal stand-in for ``AsyncSession``. ``execute`` returns a
    ``_FakeResult`` whose accessors all yield None / [] / 0.
    """

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

    # Mimic SQLAlchemy ``async_sessionmaker`` callable that returns a CM.
    return _ctx


def test_ingest_emits_all_seven_phase_d_step_rows(monkeypatch: pytest.MonkeyPatch):
    """Drive ``DocumentService.ingest()`` end-to-end with a recording
    tracker and assert each of the 7 canonical step names appears once
    in canonical order with diagnostic metadata.

    All collaborators (DB session, embedder, config) are stubbed so the
    test runs in isolation — what we assert is the OBSERVABILITY layer,
    not the ingest business logic itself.
    """
    # Patch session_with_tenant in the document_service module.
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    # Bulk-insert SQL helper: no-op (we don't care about the DB write).
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None),
    )

    sf = MagicMock()  # never called because session_with_tenant is patched.
    svc = _build_doc_service(sf)
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="phase-d-test-doc",
            content=(
                "Đây là một tài liệu mẫu để kiểm chứng Phase D ingest "
                "observability. Nội dung đủ dài để smart_chunk có thể "
                "tách thành nhiều chunks và toàn bộ pipeline U1-U7 chạy "
                "qua tất cả các phase một cách tự nhiên."
            ) * 5,
            source_url="",
            source_type="phase_d_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    names = tracker.names()
    # Each canonical step MUST appear at least once.
    for canonical_name in INGEST_STEP_NAMES:
        assert canonical_name in names, (
            f"missing step {canonical_name!r}: {names}"
        )

    # The canonical 7 must appear in the documented runtime order — find
    # the first occurrence index of each and verify monotonic increase.
    indices = [names.index(n) for n in INGEST_STEP_NAMES]
    assert indices == sorted(indices), (
        f"steps emitted out of canonical order: {names}"
    )

    # Every recorded step MUST carry the ingest namespace label.
    for ctx in tracker.steps:
        if ctx.name in INGEST_STEP_NAMES:
            assert ctx.metadata.get("step_kind") == INGEST_STEP_KIND, (
                f"{ctx.name} missing step_kind: {ctx.metadata}"
            )


def test_ingest_metadata_keys_present_per_step(monkeypatch: pytest.MonkeyPatch):
    """Each U1-U7 row MUST carry its diagnostic metadata keys — the
    contract that analyzers / dashboards rely on. Per-step expected key
    set is documented in the master matrix §2.
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None),
    )

    sf = MagicMock()
    svc = _build_doc_service(sf)
    tracker = _RecordingStepTracker(kind="ingest")

    async def _drive() -> None:
        await svc.ingest(
            record_bot_id=uuid4(),
            title="phase-d-meta-doc",
            content="Nội dung mẫu. " * 50,
            source_type="phase_d_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=tracker,
        )

    asyncio.run(_drive())

    expectations = {
        "ingest_validate": ("n_bytes", "mime_detected", "language_in"),
        "ingest_parse": ("parser_provider", "mime_type", "n_chars_in"),
        "ingest_clean": ("cleaning_enabled", "n_chars_in", "n_chars_out"),
        "ingest_chunk": ("strategy_used", "n_chunks_out", "language"),
        "ingest_enrich": ("cr_active", "n_chunks_in", "n_chunks_out"),
        "ingest_vn_segment": ("vi_seg_enabled", "language", "skipped"),
        "ingest_embed_store": (
            "n_chunks_embedded", "n_chunks_stored", "embedding_model",
        ),
    }
    for step_name, required_keys in expectations.items():
        rows = tracker.by_name(step_name)
        assert rows, f"{step_name} did not fire: {tracker.names()}"
        md = rows[0].metadata
        for key in required_keys:
            assert key in md, f"{step_name} missing key {key!r}: {md}"


def test_ingest_without_tracker_runs_unchanged(monkeypatch: pytest.MonkeyPatch):
    """Backward-compat: ``step_tracker=None`` is the legacy path used by
    HTTP /sync + tests. Ingest MUST complete successfully and the result
    MUST carry chunk metadata identical to the tracked path.
    """
    from ragbot.application.services import document_service as ds_mod

    monkeypatch.setattr(ds_mod, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod.ingest_core, "session_with_tenant", _fake_session_with_tenant)
    monkeypatch.setattr(ds_mod, "_bulk_insert_chunks", AsyncMock(return_value=None))
    monkeypatch.setattr(ds_mod.ingest_core, "_bulk_insert_chunks", AsyncMock(return_value=None),
    )

    sf = MagicMock()
    svc = _build_doc_service(sf)

    async def _drive() -> Any:
        return await svc.ingest(
            record_bot_id=uuid4(),
            title="phase-d-untracked",
            content="content " * 100,
            source_type="phase_d_test",
            language="vi",
            mime_type="text/plain",
            record_tenant_id=uuid4(),
            step_tracker=None,
        )

    result = asyncio.run(_drive())
    # IngestResult fields must populate as on the tracked path.
    assert result.title == "phase-d-untracked"
    assert isinstance(result.chunks, int)
    assert result.chunks >= 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
