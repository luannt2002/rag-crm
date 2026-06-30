"""Upload-link mime fix — add_document must label a Sheet's raw_content as
text/csv (it already fetched the CSV export via to_export_url), NOT a hardcoded
text/html. The wrong label routed every uploaded sheet to the HTML parser
instead of GoogleSheetsParser (row-as-chunk) → multi-row chunks → value
mis-bind / fabricated prices.
"""
from __future__ import annotations

import inspect

from ragbot.interfaces.http.routes.test_chat import document_routes


def test_sheet_upload_mime_is_csv_not_hardcoded_html() -> None:
    src = inspect.getsource(document_routes)
    # The INSERT must bind a derived mime, not the literal 'text/html'.
    assert "_upload_mime = \"text/csv\" if validation.doc_type == \"sheets\"" in src, (
        "sheet upload mime must be derived (text/csv for sheets), not hardcoded"
    )
    assert "VALUES (\n                    :id, :tenant_id, :workspace_id, :bot_id,\n                    :source_url, :document_name, :tool_name, :mime_type," in src or ":mime_type," in src, (
        "INSERT must bind :mime_type (not a literal)"
    )
