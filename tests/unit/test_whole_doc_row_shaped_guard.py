"""[Phase 2] Whole-doc single-chunk must NEVER collapse row-shaped parser output.

Live bug (xe bot, 2026-07-01): a 3077-char Google-Sheet markdown (below the
4000-char ``whole_doc_threshold`` DB override) took the whole-doc fast path,
collapsing the 63 one-row-per-chunk ``google_sheets`` chunks into ONE chunk.
The stats-index extractor then lost the header binding and every column fell
back to ``col_N``. ``_is_csv_format`` can't catch this — it inspects the parsed
pipe-markdown, which carries no commas. The authoritative signal is that the
parser already emitted row-shaped chunks (``google_sheets`` / ``excel_openpyxl``)
— whole-doc must yield to it.
"""
from __future__ import annotations

from ragbot.application.services.document_service.ingest_stages import (
    _should_store_whole_doc,
)


def test_small_row_shaped_sheet_not_whole_doc() -> None:
    """The exact live bug: small sheet markdown + row-shaped parser → NOT whole-doc."""
    md = (
        "| Mã | Mô tả | Ngày |\n"
        "| --- | --- | --- |\n"
        "| A1 | Sản phẩm mẫu | 28-11 |\n"
    )
    assert len(md) < 4000  # would otherwise fast-path to whole-doc
    assert _should_store_whole_doc(
        md,
        enabled=True,
        threshold_chars=4000,
        max_topic_signals=2,
        parser_is_row_shaped=True,
    ) is False


def test_small_prose_still_whole_doc() -> None:
    """Unchanged behaviour: small single-topic prose, no row-shaped parser → whole-doc."""
    prose = "Cửa hàng cung cấp dịch vụ chăm sóc cho khách hàng mỗi ngày. " * 8
    assert len(prose) < 4000
    assert _should_store_whole_doc(
        prose,
        enabled=True,
        threshold_chars=4000,
        max_topic_signals=2,
        parser_is_row_shaped=False,
    ) is True


def test_large_doc_never_whole_doc() -> None:
    big = "x" * 5000
    assert _should_store_whole_doc(
        big,
        enabled=True,
        threshold_chars=4000,
        max_topic_signals=2,
        parser_is_row_shaped=False,
    ) is False


def test_disabled_never_whole_doc() -> None:
    assert _should_store_whole_doc(
        "small",
        enabled=False,
        threshold_chars=4000,
        max_topic_signals=2,
        parser_is_row_shaped=False,
    ) is False
