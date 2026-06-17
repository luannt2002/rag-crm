"""Guard: local:// (and other non-http) sources are never (re)fetched.

A locally-uploaded file lives only in ``documents.raw_content``; its
``source_url`` is a ``local://`` pseudo-URL. The worker must reuse raw_content
and NEVER attempt an HTTP/OCR fetch of ``local://`` (which raised
"unsupported protocol 'local://'" and left documents stuck). Regression guard
for the 2026-06-12 real-case bug.
"""
from __future__ import annotations

import pytest

from ragbot.interfaces.workers.document_worker import _is_refetchable_url


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://docs.google.com/spreadsheets/d/abc", True),
        ("http://example.com/doc.pdf", True),
        ("HTTPS://Example.com", True),  # case-insensitive
        ("  https://x  ", True),         # trimmed
        ("local://thong-tu/49f09e06-96fd", False),
        ("local://bot/uuid", False),
        ("ftp://server/file", False),
        ("file:///etc/passwd", False),
        ("", False),
        (None, False),
    ],
)
def test_is_refetchable_url(url, expected):
    assert _is_refetchable_url(url) is expected
