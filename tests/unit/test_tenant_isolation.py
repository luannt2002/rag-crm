"""Tests for tenant isolation in document chunks."""
import pytest


class TestChunkTenantId:
    def test_ingest_signature_accepts_record_tenant_id(self):
        """document_service.ingest() must accept record_tenant_id parameter."""
        import inspect
        from ragbot.application.services.document_service import DocumentService
        sig = inspect.signature(DocumentService.ingest)
        params = list(sig.parameters.keys())
        assert "record_tenant_id" in params, (
            "CRITICAL: DocumentService.ingest() missing record_tenant_id parameter"
        )

    def test_document_insert_sql_includes_record_tenant_id(self):
        """INSERT INTO documents must include record_tenant_id."""
        import inspect
        from ragbot.application.services.document_service import DocumentService
        source = inspect.getsource(DocumentService.ingest)
        assert 'record_tenant_id' in source, (
            "INSERT INTO documents missing record_tenant_id"
        )


class TestTenantIdInSQL:
    def test_document_insert_has_record_tenant_id(self):
        """Verify record_tenant_id column appears in INSERT INTO documents SQL."""
        import inspect
        import re

        from ragbot.application.services.document_service import DocumentService

        source = inspect.getsource(DocumentService.ingest)

        # Match INSERT INTO documents statement
        inserts = re.findall(
            r'INSERT INTO documents\s*\([^)]+\)',
            source,
            re.DOTALL,
        )
        assert len(inserts) >= 1, f"Expected at least 1 INSERT INTO documents statement, found {len(inserts)}"
        for i, insert in enumerate(inserts):
            assert 'record_tenant_id' in insert, (
                f"INSERT #{i+1} missing record_tenant_id column:\n{insert[:200]}"
            )

    def test_chunk_inserts_no_longer_have_tenant_id(self):
        """Verify tenant_id is removed from INSERT INTO document_chunks SQL (tenant via document FK).

        After U7-1 bulk-INSERT refactor, the SQL is in _bulk_insert_chunks helper,
        not inlined in DocumentService.ingest. Check both locations.
        """
        import inspect
        import re

        import ragbot.application.services.document_service as _mod

        # Check the bulk insert helper (U7-1) — this is where the SQL now lives
        helper_source = inspect.getsource(_mod._bulk_insert_chunks)
        helper_inserts = re.findall(
            r'INSERT INTO document_chunks\s*\([^)]+\)',
            helper_source,
            re.DOTALL,
        )
        assert len(helper_inserts) >= 1, (
            "Expected INSERT INTO document_chunks in _bulk_insert_chunks helper"
        )
        for i, insert in enumerate(helper_inserts):
            assert 'tenant_id' not in insert, (
                f"_bulk_insert_chunks INSERT #{i+1} should NOT have tenant_id:\n{insert[:200]}"
            )

        # Verify ingest() no longer has direct chunk INSERT SQL (U7-1 cleaned them)
        ingest_source = inspect.getsource(_mod.DocumentService.ingest)
        ingest_chunk_inserts = re.findall(
            r'INSERT INTO document_chunks\s*\([^)]+\)',
            ingest_source,
            re.DOTALL,
        )
        assert len(ingest_chunk_inserts) == 0, (
            f"ingest() should use _bulk_insert_chunks helper — found {len(ingest_chunk_inserts)} "
            "direct INSERT INTO document_chunks"
        )
