"""Wire test for `extract_structured_refs` in `DocumentService.ingest()`.

Before LEGAL-RETRIEVAL-FIX Phase 1 the extractor was imported but never
called, so `document_chunks.metadata_json` never carried `article_no` /
`chapter_no` / etc. `ArticleAwareFilter` (the query-side regex pre-filter)
therefore always degraded to a no-op.

This test pins the wiring contract:

1. Module-level import of `extract_structured_refs` still resolves.
2. The function is *actually called* from `DocumentService.ingest()`. The
   wire is a single-call `update()` so we can grep the source — the test
   doubles as a regression pin against an accidental delete of the call
   sites in any of the three chunk-metadata builder loops.
3. Smoke-call returns the expected key shape so a future refactor that
   renames `article_no` → `article_id` (or similar) breaks this test
   before it breaks the orchestrator metadata-filter contract.

Domain-neutral: the test uses Vietnamese legal corpus phrasings because
that's the immediate beneficiary — but the extractor itself only matches
literal Latin keywords ("Điều", "Chương", ...) so the same wire serves
any structured corpus that uses the same convention.
"""

from __future__ import annotations

from pathlib import Path

from ragbot.application.services import document_service
from ragbot.application.services.structured_ref_extractor import (
    extract_structured_refs,
)


_DOC_SERVICE_FILE = Path(document_service.__file__)


def test_extractor_import_resolves() -> None:
    """`extract_structured_refs` symbol must remain importable from the
    extractor module — the document service relies on it.
    """
    assert callable(extract_structured_refs)


def test_extractor_called_in_document_service() -> None:
    """Source-level pin: three call sites of `extract_structured_refs`
    (parent / child / single chunk loops) must exist in `document_service.py`
    and each must pass ``persisted_text`` — NOT ``chunk_text``.

    Why ``persisted_text`` and not ``chunk_text``: the contextual-retrieval
    enricher prepends a leading "[Chương X > Điều Y. <title>] ..." crumb
    to the chunk content for BM25 + rerank visibility. The raw
    ``chunk_text`` (pre-CR) often inlines the HDT structural-path
    "Điều X > Điều Y" — first-match-wins on that text yields the parent
    article (the SAI variant), while the same regex on ``persisted_text``
    matches the leaf article from the enrichment prefix (CORRECT).

    Why source-grep instead of behaviour mock: `DocumentService.ingest()`
    is a 1000+ line coroutine that walks the full chunk-embed-write
    pipeline. Mocking the entire dependency tree to assert a metadata
    field is fragile; the source-grep guarantees the wire stays put AND
    keeps using the post-enrichment input.
    """
    # The chunk-write loops (parent / child / single) now live in the
    # ``ingest_stages`` mixin (ingest() god-method split into stage methods);
    # scan the whole document_service package directory so the pin survives.
    _pkg_dir = _DOC_SERVICE_FILE.parent
    body = "".join(
        p.read_text(encoding="utf-8") for p in sorted(_pkg_dir.glob("*.py"))
    )
    n_correct = body.count("extract_structured_refs(persisted_text)")
    n_wrong = body.count("extract_structured_refs(chunk_text)")
    assert n_correct >= 3, (
        f"expected ≥3 call sites passing ``persisted_text`` (parent / "
        f"child / single chunk metadata builders), found {n_correct}. "
        f"Phase 1 wire may have regressed or reverted to ``chunk_text``."
    )
    assert n_wrong == 0, (
        f"found {n_wrong} call site(s) passing ``chunk_text`` (raw, "
        f"pre-CR). Use ``persisted_text`` (post-CR enriched) so the "
        f"structural-anchor metadata reflects the leaf article, not the "
        f"HDT parent path that first-match-wins would otherwise pick up."
    )


def test_extractor_returns_article_no_on_legal_chunk() -> None:
    """Smoke: realistic VN-legal chunk → metadata dict with `article_no`.

    Pins the key name so renaming `article_no` → anything else breaks
    the test before retrieval starts silently failing.
    """
    chunk = (
        "[Chương II > Mục 1 > Điều 11. Quản lý sử dụng thiết bị di động]\n"
        "1. Các thiết bị di động khi kết nối vào hệ thống mạng nội bộ ..."
    )
    out = extract_structured_refs(chunk)
    assert out["article_no"] == "11"
    assert out["chapter_no"] == "II"
    assert out["section_no"] == "1"


def test_extractor_empty_on_no_structural_anchor() -> None:
    """Chunk without structural keywords → empty dict (no metadata pollution)."""
    out = extract_structured_refs("This is just a plain paragraph of text.")
    assert out == {}


def test_extractor_first_match_wins_when_chunk_spans_multiple() -> None:
    """Chunk spanning Điều 9 → Điều 11 → Điều 12 → metadata keeps Điều 9.

    Matches the user-visible expectation: "the chunk *starting with* Điều X".
    Verifies the regex `\\bĐiều\\s+(\\d+)\\b` returns the first match.
    """
    chunk = (
        "Điều 9. Quản lý tài sản vật lý.\n"
        "Tài sản phải được quản lý theo Điều 11, Điều 12 Thông tư này.\n"
    )
    out = extract_structured_refs(chunk)
    assert out["article_no"] == "9", (
        "First-match-wins: chunk starts with Điều 9, so metadata.article_no "
        "must be '9' even though the body references Điều 11 + Điều 12."
    )


def test_persisted_vs_raw_chunk_distinguishes_leaf_vs_parent_article() -> None:
    """Regression case for the bug fixed 2026-05-21.

    HDT chunker leaves a structural-path crumb like ``[Chương I >
    Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng > Điều 2. Giải
    thích từ ngữ]`` at the start of each child chunk so the LLM citation
    path can rebuild the hierarchy. Running the extractor on this raw
    text picks up ``Điều 1`` (the PARENT path) — wrong.

    The CR enricher prepends a leading ``"Chương I, Điều 2: Giải thích
    từ ngữ. ..."`` summary so BM25 sees the leaf article first. Running
    the extractor on the enriched text correctly picks up ``Điều 2``.

    This test pins the divergence so a future refactor that "simplifies"
    the call site back to ``chunk_text`` re-introduces the silent
    metadata corruption documented under LEGAL-RETRIEVAL-FIX bug #6.
    """
    raw_chunk = (
        "[Chương I > Điều 1. Phạm vi điều chỉnh và đối tượng áp dụng "
        "> Điều 2. Giải thích từ ngữ]\n"
        "9. Dịch vụ điện toán đám mây là ...\n"
    )
    persisted_text = (
        "Chương I, Điều 2: Giải thích từ ngữ. Định nghĩa dịch vụ điện "
        "toán đám mây và tài khoản người sử dụng.\n\n"
    ) + raw_chunk

    raw_meta = extract_structured_refs(raw_chunk)
    persisted_meta = extract_structured_refs(persisted_text)

    assert raw_meta["article_no"] == "1", (
        "Raw HDT-path chunk first-matches Điều 1 (parent); this is the "
        "behaviour that made chunks 2-5 of TT 09/2020 carry article_no='1' "
        "before the fix."
    )
    assert persisted_meta["article_no"] == "2", (
        "Post-CR enriched chunk leads with the leaf article — extracting "
        "from ``persisted_text`` (the column Ragbot persists into "
        "``content``) yields the SEMANTICALLY CORRECT article_no='2'."
    )
