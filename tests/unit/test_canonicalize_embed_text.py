"""Dual-field embed canonicalization (2026-06-13).

Embed a cleaned canonical form (URLs + redundant whitespace stripped) while the
raw chunk text is persisted to ``content`` for BM25. Cuts token waste + vector
dilution on URL-heavy / loosely-spaced spreadsheet rows without losing keyword
recall (raw unchanged).
"""
from __future__ import annotations

from ragbot.application.services.document_service import canonicalize_embed_text


def test_collapses_runs_of_whitespace() -> None:
    assert canonicalize_embed_text("nhà       tôi      có") == "nhà tôi có"


def test_strips_image_urls() -> None:
    raw = ",Kho,2-R13,Lop xe,26,,https://drive.google.com/drive/folders/abc,https://lh3.google.com/x"
    out = canonicalize_embed_text(raw)
    assert "http" not in out
    assert "2-R13" in out and "Lop xe" in out  # real data preserved


def test_never_returns_empty() -> None:
    # A URL-only chunk must not canonicalize to "" (would break the embedder).
    assert canonicalize_embed_text("https://drive.google.com/x") != ""


def test_idempotent_on_clean_text() -> None:
    clean = "Tên kho,Mã hàng,Tên hàng"
    assert canonicalize_embed_text(clean) == clean
