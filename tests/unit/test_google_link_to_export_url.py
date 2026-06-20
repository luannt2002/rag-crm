"""to_export_url — Google Docs/Sheets viewer URL → direct export URL.

Regression guard for the xe-3 retry-storm: a Google Sheets ``.../edit?gid=N``
viewer link returned an HTML login page; the worker OCR'd it to empty text and
looped to DLQ. Rewriting to the ``export`` endpoint makes the fetch receive
structured txt/csv that the csv/sheets parser ingests directly.
"""

from __future__ import annotations

from ragbot.application.services.google_link_service import to_export_url


def test_sheets_edit_gid_query_to_csv_export() -> None:
    url = (
        "https://docs.google.com/spreadsheets/d/ABC123/edit"
        "?gid=1394860155#gid=1394860155"
    )
    assert to_export_url(url) == (
        "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=1394860155"
    )


def test_sheets_edit_gid_fragment_to_csv_export() -> None:
    assert to_export_url(
        "https://docs.google.com/spreadsheets/d/ABC123/edit#gid=0"
    ) == "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv&gid=0"


def test_sheets_no_gid_to_csv_export() -> None:
    assert to_export_url(
        "https://docs.google.com/spreadsheets/d/ABC123/edit"
    ) == "https://docs.google.com/spreadsheets/d/ABC123/export?format=csv"


def test_docs_viewer_to_docx_export() -> None:
    # docx (not txt) so the docx parser recovers heading styles
    assert to_export_url(
        "https://docs.google.com/document/d/XYZ789/edit"
    ) == "https://docs.google.com/document/d/XYZ789/export?format=docx"


def test_non_google_url_unchanged() -> None:
    url = "https://example.com/data/file.pdf"
    assert to_export_url(url) == url


def test_google_forms_link_unchanged() -> None:
    # docs.google.com but neither spreadsheets nor document → left untouched
    url = "https://docs.google.com/forms/d/FORMID/viewform"
    assert to_export_url(url) == url


def test_empty_url_safe() -> None:
    assert to_export_url("") == ""
