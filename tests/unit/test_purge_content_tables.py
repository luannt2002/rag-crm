"""[Phase 0] Content-purge single-source-of-truth. Every delete/re-ingest path must
purge ALL content-state tables (document_chunks + document_service_index). The bug:
purge was duplicated inline → re-ingest + delete-all-bot forgot document_service_index
→ stale col_N stats-rows survived re-ingest. A centralized helper enumerates the
tables ONCE so no path forgets one. Metadata (documents) is soft-deleted by the caller;
audit/cost tables are NEVER touched.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock
from uuid import uuid4

from ragbot.application.services.document_service import _purge_content_tables


class _CaptureSession:
    def __init__(self) -> None:
        self.sql: list[str] = []

    async def execute(self, stmt: object, params: object = None) -> MagicMock:
        self.sql.append(str(stmt))
        r = MagicMock()
        r.rowcount = 1
        return r


def test_purge_covers_chunks_AND_service_index() -> None:
    s = _CaptureSession()
    asyncio.run(_purge_content_tables(s, [uuid4()]))
    joined = " ".join(s.sql)
    assert "DELETE FROM document_chunks" in joined, "must purge chunks"
    assert "DELETE FROM document_service_index" in joined, (
        "must purge stats-index too — the re-ingest bug that left stale col_N rows"
    )


def test_purge_never_touches_audit_or_cost() -> None:
    s = _CaptureSession()
    asyncio.run(_purge_content_tables(s, [uuid4()]))
    joined = " ".join(s.sql).lower()
    for forbidden in ("audit_log", "request_logs", "request_steps"):
        assert forbidden not in joined, (
            f"content purge must NEVER delete {forbidden} (append-only forensic)"
        )


def test_purge_noop_on_empty_doc_ids() -> None:
    s = _CaptureSession()
    asyncio.run(_purge_content_tables(s, []))
    assert s.sql == [], "no docs → no DELETE fired"
