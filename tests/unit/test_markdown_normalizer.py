"""Phase C — format→markdown normalizer.

Converts raw CSV regions into markdown pipe tables (preserving the
header↔column association) and promotes VN Chương/Mục/Điều markers to ATX
headings, so downstream chunking + embedding sees clean structured markdown
instead of comma-soup. Non-table prose passes through untouched.
"""
from __future__ import annotations

from ragbot.shared.markdown_normalizer import normalize_to_markdown


class TestNormalizeToMarkdown:
    def test_csv_region_becomes_pipe_table(self):
        csv = (
            "Dịch vụ,Giá\n"
            "Chăm sóc da,350000\n"
            "Trẻ hóa,1200000\n"
            "Triệt lông,500000\n"
        )
        out = normalize_to_markdown(csv)
        # Pipe-table header + separator row present.
        assert "| Dịch vụ | Giá |" in out
        assert "|---|" in out.replace(" ", "")  # separator row (spacing-agnostic)
        # Every cell value preserved.
        for cell in ("Chăm sóc da", "350000", "Trẻ hóa", "Triệt lông"):
            assert cell in out

    def test_prose_passthrough_no_spurious_pipes(self):
        prose = (
            "Trí tuệ nhân tạo đang thay đổi cách làm việc. "
            "Nhiều doanh nghiệp ứng dụng RAG để tự động hoá."
        )
        out = normalize_to_markdown(prose)
        assert "|" not in out
        assert out.strip() == prose.strip()

    def test_vn_legal_markers_promoted_to_atx(self):
        legal = (
            "Chương I\nQUY ĐỊNH CHUNG\n\n"
            "Điều 1. Phạm vi\nNội dung điều 1.\n\n"
            "Điều 2. Đối tượng\nNội dung điều 2.\n\n"
            "Điều 3. Giải thích\nNội dung.\n"
        )
        out = normalize_to_markdown(legal)
        assert "# Chương 1" in out
        assert "### Điều 1. Phạm vi" in out

    def test_idempotent(self):
        csv = "A,B\n1,2\n3,4\n5,6\n"
        once = normalize_to_markdown(csv)
        twice = normalize_to_markdown(once)
        assert once == twice

    def test_empty_input(self):
        assert normalize_to_markdown("") == ""
        assert normalize_to_markdown("   ") == "   "
