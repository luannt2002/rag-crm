"""Block-feed S1 plumbing — blocks thread through ingest (ADR-W3-D1 S1).

S1 is pure plumbing: ``ingest(blocks=…)`` accepts the parser's structure-
aware Block stream and logs its type histogram, proving the stream survives
to ingest instead of being flattened at document_worker.py:298. It does NOT
yet change how chunks are produced — that flip (S2/S3) is A/B-gated. So the
contract under test is: (1) the kwarg exists and defaults None, (2) the
worker threads parsed.blocks, (3) presence is observable, (4) str-only
callers are unaffected.
"""

from __future__ import annotations

import inspect


def test_ingest_accepts_blocks_kwarg_defaulting_none() -> None:
    from ragbot.application.services.document_service import DocumentService

    sig = inspect.signature(DocumentService.ingest)
    assert "blocks" in sig.parameters, "ingest() must accept a blocks= kwarg"
    assert sig.parameters["blocks"].default is None, (
        "blocks must default None so str-only / direct-text callers are "
        "byte-unchanged (backward compat)"
    )


def test_worker_threads_parsed_blocks_into_ingest() -> None:
    import ragbot.interfaces.workers.document_worker as worker

    src = inspect.getsource(worker)
    # Worker captures the OCR Block stream and forwards it.
    assert "parsed_blocks = list(parsed.blocks)" in src, (
        "worker must keep the parser Block stream alongside the flat text"
    )
    assert "blocks=parsed_blocks," in src, (
        "worker must thread blocks= into doc_service.ingest()"
    )


def test_ingest_logs_block_histogram_when_present() -> None:
    """Presence of blocks emits the observability event WITHOUT altering the
    chunk pipeline (S1 is behaviour-neutral)."""
    from ragbot.application.services.document_service import DocumentService

    src = inspect.getsource(DocumentService.ingest)
    assert "ingest_block_stream_received" in src
    assert "block_types" in src and "block_count" in src
    # The block-native chunking flip must NOT be in S1 — guard against an
    # accidental early flip (S2/S3 land behind the A/B gate).
    assert "smart_chunk_atomic(" not in src, (
        "S1 is plumbing only — smart_chunk_atomic wiring is S2/S3 (A/B-gated)"
    )
