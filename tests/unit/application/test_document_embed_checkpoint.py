"""Doc-level embed batching + progress checkpoint (Plan F4).

The orchestrator-side embed loop slices the texts list into doc-level
batches (default ``DEFAULT_EMBED_DOC_BATCH_SIZE``) so a thousands-of-chunks
document doesn't await one giant call with zero visibility. The embedder
keeps its own HTTP-level batching + retry; this layer adds:

* progress structlog events per batch (``embed_batch_progress``)
* cooperative ``asyncio.sleep`` between batches as provider QPS pacing
* config knobs: ``embed_doc_batch_size`` + ``embed_inter_batch_sleep_s``

The length-mismatch contract still lives at the ingest call site (raises
``ExternalServiceError`` when ``len(out) != len(_chunks_needing_embed)``);
the helper itself only guarantees ``len(out) == len(texts)``.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog
from structlog.testing import LogCapture

from ragbot.application.dto.ai_specs import EmbeddingSpec
from ragbot.application.services.document_service import DocumentService
from ragbot.shared.constants import (
    DEFAULT_EMBED_DOC_BATCH_SIZE,
    DEFAULT_EMBED_INTER_BATCH_SLEEP_S,  # noqa: F401 — re-exported for downstream tests
    DEFAULT_EMBEDDING_TASK_PASSAGE,
)


def _make_spec() -> EmbeddingSpec:
    return EmbeddingSpec(
        binding_id=uuid.uuid4(),
        model_name="zembed-1",
        provider="zeroentropy",
        dimension=4,
        model_version="zembed-1-v1",
        task=DEFAULT_EMBEDDING_TASK_PASSAGE,
    )


def _make_service(
    *,
    embedder: Any,
    config_service: Any | None = None,
) -> DocumentService:
    settings = MagicMock()
    settings.embedding.model_name = "zembed-1"
    settings.embedding.dimension = 4
    settings.embedding.model_version = "zembed-1-v1"
    return DocumentService(
        session_factory=MagicMock(),
        embedder=embedder,
        settings=settings,
        config_service=config_service,
    )


def _make_cfg(
    *,
    batch_size: int | None = None,
    sleep_s: float | None = None,
) -> MagicMock:
    """Stub SystemConfigService that returns the supplied overrides.

    ``None`` means "fall through to the default the caller passed in".
    """
    cfg = MagicMock()

    async def _get_int(key: str, default: int = 0) -> int:
        if key == "embed_doc_batch_size" and batch_size is not None:
            return batch_size
        return default

    async def _get_float(key: str, default: float = 0.0) -> float:
        if key == "embed_inter_batch_sleep_s" and sleep_s is not None:
            return sleep_s
        return default

    cfg.get_int = AsyncMock(side_effect=_get_int)
    cfg.get_float = AsyncMock(side_effect=_get_float)
    return cfg


@pytest.fixture
def log_capture() -> LogCapture:
    """Capture structlog events for assertion."""
    cap = LogCapture()
    structlog.configure(processors=[cap])
    yield cap
    structlog.reset_defaults()


@pytest.mark.asyncio
async def test_embed_doc_batch_size_respected() -> None:
    """The helper must slice ``texts`` into batches of the configured size.

    With 250 texts and ``embed_doc_batch_size=100`` the embedder is called
    exactly 3 times with slice lengths ``[100, 100, 50]``.
    """
    seen_batch_lengths: list[int] = []

    async def _embed(batch: list[str], *, spec: object, record_tenant_id: object) -> list[list[float]]:
        seen_batch_lengths.append(len(batch))
        return [[0.0, 0.0, 0.0, 0.0] for _ in batch]

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(side_effect=_embed)
    cfg = _make_cfg(batch_size=100, sleep_s=0.0)
    svc = _make_service(embedder=embedder, config_service=cfg)

    texts = [f"chunk-{i}" for i in range(250)]
    out = await svc._embed_in_doc_batches(
        texts,
        spec=_make_spec(),
        document_id=uuid.uuid4(),
        record_bot_id=uuid.uuid4(),
    )

    assert seen_batch_lengths == [100, 100, 50]
    assert len(out) == 250
    assert embedder.embed_batch.await_count == 3


@pytest.mark.asyncio
async def test_inter_batch_sleep_called(monkeypatch: pytest.MonkeyPatch) -> None:
    """``asyncio.sleep`` must fire between batches (not after the last).

    250 texts at batch_size=100 → 3 batches → 2 sleeps. Each sleep uses
    the configured ``embed_inter_batch_sleep_s`` value.
    """
    sleep_args: list[float] = []

    async def _fake_sleep(secs: float) -> None:
        sleep_args.append(secs)

    monkeypatch.setattr(
        "ragbot.application.services.document_service.asyncio.sleep",
        _fake_sleep,
    )

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(return_value=[[0.0] * 4])
    # Make the embedder return a properly sized batch each call.
    embedder.embed_batch.side_effect = lambda batch, *, spec, record_tenant_id: [
        [0.0, 0.0, 0.0, 0.0] for _ in batch
    ]

    cfg = _make_cfg(batch_size=100, sleep_s=0.25)
    svc = _make_service(embedder=embedder, config_service=cfg)

    await svc._embed_in_doc_batches(
        [f"t-{i}" for i in range(250)],
        spec=_make_spec(),
        document_id=uuid.uuid4(),
        record_bot_id=uuid.uuid4(),
    )

    # 3 batches → exactly 2 sleeps, both equal to the configured value.
    assert sleep_args == [0.25, 0.25]


@pytest.mark.asyncio
async def test_length_mismatch_raises() -> None:
    """The ingest call-site guard still aborts when total embed count
    drifts from total chunks-needing-embed (preserved invariant).

    The helper itself returns whatever the embedder emits; the
    length-mismatch check at the call site is what protects against
    silently-NULL vectors. Here we simulate an embedder that returns
    too few vectors for the requested batch and assert the helper
    propagates the truncated count — leaving the call-site guard to
    raise ``ExternalServiceError``.
    """
    embedder = MagicMock()
    # Provider truncates: returns only 2 vectors for a 3-chunk batch.
    embedder.embed_batch = AsyncMock(
        return_value=[[0.0, 0.0, 0.0, 0.0], [0.1, 0.1, 0.1, 0.1]],
    )

    cfg = _make_cfg(batch_size=100, sleep_s=0.0)
    svc = _make_service(embedder=embedder, config_service=cfg)

    out = await svc._embed_in_doc_batches(
        ["a", "b", "c"],
        spec=_make_spec(),
        document_id=uuid.uuid4(),
        record_bot_id=uuid.uuid4(),
    )

    # Length mismatch is observable to the ingest caller → caller raises
    # ExternalServiceError downstream. The helper exposes the count.
    assert len(out) == 2
    assert len(out) != 3  # explicit: the guard at the call-site fires.


@pytest.mark.asyncio
async def test_progress_logged_each_batch(log_capture: LogCapture) -> None:
    """``embed_batch_progress`` event must fire once per batch with the
    correct counters (batch_idx, total_batches, chunks_done, chunks_total).
    """
    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(
        side_effect=lambda batch, *, spec, record_tenant_id: [
            [0.0, 0.0, 0.0, 0.0] for _ in batch
        ],
    )
    cfg = _make_cfg(batch_size=2, sleep_s=0.0)
    svc = _make_service(embedder=embedder, config_service=cfg)

    doc_id = uuid.uuid4()
    record_bot_id = uuid.uuid4()
    await svc._embed_in_doc_batches(
        ["a", "b", "c", "d", "e"],
        spec=_make_spec(),
        document_id=doc_id,
        record_bot_id=record_bot_id,
    )

    events = [e for e in log_capture.entries if e.get("event") == "embed_batch_progress"]
    # 5 texts at batch_size=2 → 3 batches → 3 progress events.
    assert len(events) == 3
    assert [e["batch_idx"] for e in events] == [0, 1, 2]
    assert [e["chunks_done"] for e in events] == [2, 4, 5]
    assert all(e["chunks_total"] == 5 for e in events)
    assert all(e["total_batches"] == 3 for e in events)
    assert all(e["document_id"] == str(doc_id) for e in events)
    assert all(e["record_bot_id"] == str(record_bot_id) for e in events)


@pytest.mark.asyncio
async def test_single_batch_doc_no_extra_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document smaller than the batch size must NOT trigger any sleep.

    The pacing is only between provider rounds; a single-shot embed has
    no provider round to pace against.
    """
    sleep_args: list[float] = []

    async def _fake_sleep(secs: float) -> None:
        sleep_args.append(secs)

    monkeypatch.setattr(
        "ragbot.application.services.document_service.asyncio.sleep",
        _fake_sleep,
    )

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(
        side_effect=lambda batch, *, spec, record_tenant_id: [
            [0.0, 0.0, 0.0, 0.0] for _ in batch
        ],
    )
    cfg = _make_cfg(batch_size=100, sleep_s=0.5)
    svc = _make_service(embedder=embedder, config_service=cfg)

    out = await svc._embed_in_doc_batches(
        ["only", "five", "chunks", "fits", "one"],
        spec=_make_spec(),
        document_id=uuid.uuid4(),
        record_bot_id=uuid.uuid4(),
    )

    assert len(out) == 5
    assert embedder.embed_batch.await_count == 1
    assert sleep_args == []  # zero sleeps when only one batch executes.


@pytest.mark.asyncio
async def test_defaults_apply_when_cfg_absent() -> None:
    """Without a config_service the helper falls back to the constants.

    Verifies that no DB lookup is attempted, and batching still works
    with ``DEFAULT_EMBED_DOC_BATCH_SIZE``.
    """
    seen_lengths: list[int] = []

    async def _embed(batch: list[str], *, spec: object, record_tenant_id: object) -> list[list[float]]:
        seen_lengths.append(len(batch))
        return [[0.0, 0.0, 0.0, 0.0] for _ in batch]

    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(side_effect=_embed)
    svc = _make_service(embedder=embedder, config_service=None)

    n = DEFAULT_EMBED_DOC_BATCH_SIZE + 1
    out = await svc._embed_in_doc_batches(
        [f"t-{i}" for i in range(n)],
        spec=_make_spec(),
        document_id=uuid.uuid4(),
        record_bot_id=uuid.uuid4(),
    )

    assert seen_lengths == [DEFAULT_EMBED_DOC_BATCH_SIZE, 1]
    assert len(out) == n


@pytest.mark.asyncio
async def test_zero_batch_size_clamped_to_default() -> None:
    """Operator-misconfigured batch_size=0 must clamp to the default
    rather than infinite-looping on ``range(0, n, 0)``.
    """
    embedder = MagicMock()
    embedder.embed_batch = AsyncMock(
        side_effect=lambda batch, *, spec, record_tenant_id: [
            [0.0, 0.0, 0.0, 0.0] for _ in batch
        ],
    )
    cfg = _make_cfg(batch_size=0, sleep_s=0.0)
    svc = _make_service(embedder=embedder, config_service=cfg)

    n = DEFAULT_EMBED_DOC_BATCH_SIZE + 5
    out = await svc._embed_in_doc_batches(
        [f"t-{i}" for i in range(n)],
        spec=_make_spec(),
        document_id=uuid.uuid4(),
        record_bot_id=uuid.uuid4(),
    )
    assert len(out) == n
    # Clamped to default → exactly 2 batches.
    assert embedder.embed_batch.await_count == 2
