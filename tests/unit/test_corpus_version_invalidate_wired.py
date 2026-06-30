"""Wire check — ``CorpusVersionService.invalidate()`` callsites (ADR-W1-D4 §2e).

``invalidate()`` was defined but had ZERO callsites — every corpus
mutation relied on the 300s TTL lag. These tests pin the three wired
callsites in ``DocumentService``:

1. ingest terminal state flip (post-commit),
2. the doc-delete family (``delete_document`` / ``delete_all_for_bot``
   / ``replace_documents_for_bot``),
3. (purge S3 is covered in ``test_bot_lifecycle_purge.py``).

``corpus_version_service=None`` must stay a no-op so existing
construction sites (e.g. ``test_chat.py``) keep working unchanged.
"""

from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from ragbot.application.services import document_service as ds_module
from ragbot.application.services.document_service import DocumentService

# ── Helpers ─────────────────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchone(self) -> Any:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[Any]:
        return list(self._rows)


class _FakeSession:
    def __init__(self, results: list[_FakeResult] | None = None) -> None:
        self._results = list(results or [])
        self.executes: list[tuple[str, Any]] = []
        self.commits = 0

    async def execute(self, stmt: Any, params: Any = None) -> _FakeResult:
        self.executes.append((str(stmt), params))
        if self._results:
            return self._results.pop(0)
        return _FakeResult()

    async def commit(self) -> None:
        self.commits += 1

    async def close(self) -> None:
        return None


def _make_service(
    corpus_svc: Any,
) -> DocumentService:
    settings = MagicMock()
    settings.embedding.model_name = "stub"
    settings.embedding.dimension = 8
    settings.embedding.model_version = "stub"
    return DocumentService(
        session_factory=MagicMock(),
        embedder=MagicMock(),
        settings=settings,
        corpus_version_service=corpus_svc,
    )


def _patch_swt(monkeypatch: pytest.MonkeyPatch, session: _FakeSession) -> None:
    @asynccontextmanager
    async def _fake_swt(_factory: Any, *, record_tenant_id: Any = None):  # noqa: ARG001
        yield session

    monkeypatch.setattr(ds_module, "session_with_tenant", _fake_swt)


def _corpus_mock() -> MagicMock:
    corpus = MagicMock()
    corpus.invalidate = AsyncMock()
    return corpus


# ── delete family ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_document_invalidates_corpus_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """delete_document resolves the owning bot row first — the same
    SELECT must surface the tenant so the invalidate key is exact."""
    bot_uuid = uuid4()
    tenant_uuid = uuid4()
    session = _FakeSession([
        _FakeResult([(bot_uuid, tenant_uuid)]),  # owning-bot SELECT
    ])
    corpus = _corpus_mock()
    svc = _make_service(corpus)
    _patch_swt(monkeypatch, session)

    ok = await svc.delete_document(uuid4(), record_tenant_id=tenant_uuid)

    assert ok is True
    corpus.invalidate.assert_awaited_once_with(tenant_uuid, bot_uuid)


@pytest.mark.asyncio
async def test_delete_all_for_bot_invalidates_corpus_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_uuid = uuid4()
    tenant_uuid = uuid4()
    session = _FakeSession()
    corpus = _corpus_mock()
    svc = _make_service(corpus)
    _patch_swt(monkeypatch, session)

    await svc.delete_all_for_bot(bot_uuid, record_tenant_id=tenant_uuid)

    corpus.invalidate.assert_awaited_once_with(tenant_uuid, bot_uuid)


@pytest.mark.asyncio
async def test_replace_documents_for_bot_invalidates_corpus_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot_uuid = uuid4()
    tenant_uuid = uuid4()
    session = _FakeSession()
    corpus = _corpus_mock()
    svc = _make_service(corpus)
    _patch_swt(monkeypatch, session)

    await svc.replace_documents_for_bot(
        bot_uuid,
        source_urls=["https://example.test/a"],
        record_tenant_id=tenant_uuid,
    )

    corpus.invalidate.assert_awaited_once_with(tenant_uuid, bot_uuid)


@pytest.mark.asyncio
async def test_replace_documents_for_bot_purges_chunks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-sync MUST hard-delete the replaced docs' chunks.

    The re-ingest re-uses the SAME document_id (``uq_doc_tool`` ON CONFLICT
    resurrects the soft-deleted row) and the ingest dedup SELECT's
    ``deleted_at IS NULL`` filter makes ``is_reindex=False`` → the store stage
    does a pure INSERT. Without an explicit chunk purge here the OLD chunks
    survive alongside the NEW ones (duplication — observed xe 97→319). There
    is NO FK cascade on ``document_chunks`` deletion.
    """
    bot_uuid = uuid4()
    tenant_uuid = uuid4()
    session = _FakeSession()
    svc = _make_service(_corpus_mock())
    _patch_swt(monkeypatch, session)

    await svc.replace_documents_for_bot(
        bot_uuid,
        source_urls=["https://example.test/a"],
        record_tenant_id=tenant_uuid,
    )

    sqls = " ".join(s.lower() for s, _ in session.executes)
    assert "delete from document_chunks" in sqls, (
        "replace_documents_for_bot must purge chunks of the replaced docs "
        "(anti-duplication); executed SQL: " + sqls
    )


@pytest.mark.asyncio
async def test_none_corpus_service_is_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """corpus_version_service=None → no raise on any delete path
    (backward-compat for construction sites that do not wire it)."""
    bot_uuid = uuid4()
    tenant_uuid = uuid4()
    session = _FakeSession([
        _FakeResult([(bot_uuid, tenant_uuid)]),
    ])
    svc = _make_service(None)
    _patch_swt(monkeypatch, session)

    assert await svc.delete_document(uuid4(), record_tenant_id=tenant_uuid) is True
    session2 = _FakeSession()
    _patch_swt(monkeypatch, session2)
    await svc.delete_all_for_bot(bot_uuid, record_tenant_id=tenant_uuid)
    session3 = _FakeSession()
    _patch_swt(monkeypatch, session3)
    await svc.replace_documents_for_bot(
        bot_uuid, source_urls=["https://example.test/a"],
        record_tenant_id=tenant_uuid,
    )


# ── ingest terminal flip (source pin — full ingest is integration-tier) ────


def test_ingest_flip_calls_invalidate_helper() -> None:
    """The terminal state-flip block in ``ingest`` must bust the corpus
    version AFTER its commit. Full ingest execution is integration-tier;
    here we pin the callsite in source so a refactor cannot drop it."""
    # The terminal state-flip + corpus-bust now live in the ``_stage_finalize``
    # stage method (ingest() god-method was split into stage methods); pin the
    # callsite there.
    src = inspect.getsource(DocumentService._stage_finalize)
    flip_idx = src.index("ingest_state_flip_failed")
    after_flip = src[flip_idx:]
    assert "_invalidate_corpus_version(" in after_flip


@pytest.mark.asyncio
async def test_invalidate_helper_behaviour() -> None:
    """Helper: delegates when wired, silent no-op when None/ids missing."""
    corpus = _corpus_mock()
    svc = _make_service(corpus)
    tenant_uuid, bot_uuid = uuid4(), uuid4()

    await svc._invalidate_corpus_version(tenant_uuid, bot_uuid)
    corpus.invalidate.assert_awaited_once_with(tenant_uuid, bot_uuid)

    # None tenant → skip (TTL backstop covers it) — never a raise.
    corpus.invalidate.reset_mock()
    await svc._invalidate_corpus_version(None, bot_uuid)
    corpus.invalidate.assert_not_awaited()

    # Unwired service → no-op.
    svc_none = _make_service(None)
    await svc_none._invalidate_corpus_version(tenant_uuid, bot_uuid)


def test_construction_sites_pass_corpus_version_service() -> None:
    """document_worker + both sync.py sites must wire the DI singleton.
    Source pin (cheap, no app boot): every ``DocumentService(`` call in
    those two modules carries ``corpus_version_service=``."""
    import re  # noqa: PLC0415 — source-pin helper, test-local
    from pathlib import Path  # noqa: PLC0415

    src_root = Path(__file__).resolve().parents[2] / "src" / "ragbot"
    for rel in (
        "interfaces/workers/document_worker.py",
        "interfaces/http/routes/sync.py",
    ):
        text = (src_root / rel).read_text(encoding="utf-8")
        calls = re.findall(r"DocumentService\((?:[^()]|\([^()]*\))*\)", text)
        assert calls, f"no DocumentService( construction found in {rel}"
        for call in calls:
            assert "corpus_version_service=" in call, (
                f"{rel}: DocumentService construction missing "
                f"corpus_version_service wiring: {call[:120]}"
            )
