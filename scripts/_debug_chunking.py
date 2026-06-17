"""Debug the chunking pipeline end-to-end on representative documents.

Shows, for each sample: analyze_document() profile → select_strategy()
decision → smart_chunk() output chunks (count, sizes, boundaries). The goal
is to eyeball whether each of the 4 strategies (hdt / semantic / recursive /
proposition) + the table_csv path produce "chuẩn" chunks or fragment.

Run: .venv/bin/python scripts/_debug_chunking.py
"""
from __future__ import annotations

import sys

from ragbot.shared.chunking import (
    analyze_document,
    select_strategy,
    smart_chunk,
)

# ----------------------------------------------------------------------------
# Sample documents — one per realistic upload shape
# ----------------------------------------------------------------------------

SPA_PRICE_TABLE = """# Bảng giá dịch vụ spa

| Dịch vụ | Thời gian | Giá |
|---|---|---|
| Chăm sóc da cơ bản | 60 phút | 350.000đ |
| Trẻ hóa da công nghệ cao | 90 phút | 1.200.000đ |
| Triệt lông nách | 30 phút | 500.000đ |
| Massage body thư giãn | 75 phút | 450.000đ |
| Tắm trắng phi thuyền | 120 phút | 1.500.000đ |
"""

SPA_PRICE_CSV = """Dịch vụ,Thời gian,Giá
Chăm sóc da cơ bản,60 phút,350.000đ
Trẻ hóa da công nghệ cao,90 phút,1.200.000đ
Triệt lông nách,30 phút,500.000đ
Massage body thư giãn,75 phút,450.000đ
Tắm trắng phi thuyền,120 phút,1.500.000đ
"""

LEGAL_TEXT = """Chương I
QUY ĐỊNH CHUNG

Điều 1. Phạm vi điều chỉnh
Thông tư này quy định về tỷ lệ an toàn vốn đối với ngân hàng, chi nhánh
ngân hàng nước ngoài.

Điều 2. Đối tượng áp dụng
1. Ngân hàng thương mại.
2. Chi nhánh ngân hàng nước ngoài.

Điều 3. Giải thích từ ngữ
Trong Thông tư này, các từ ngữ dưới đây được hiểu như sau:
1. Tỷ lệ an toàn vốn là tỷ lệ giữa vốn tự có và tổng tài sản.
2. Vốn tự có gồm vốn cấp 1 và vốn cấp 2.

Chương II
QUY ĐỊNH CỤ THỂ

Điều 4. Tỷ lệ an toàn vốn tối thiểu
Ngân hàng phải duy trì tỷ lệ an toàn vốn tối thiểu 8%.
"""

PROSE = """Trí tuệ nhân tạo đang thay đổi cách chúng ta làm việc. Nhiều doanh nghiệp
đã ứng dụng các mô hình ngôn ngữ lớn để tự động hóa quy trình chăm sóc khách
hàng. Tuy nhiên, việc triển khai không hề đơn giản.

Thách thức lớn nhất là đảm bảo độ chính xác của câu trả lời. Một hệ thống RAG
tốt cần kết hợp giữa truy hồi thông tin và sinh văn bản. Khi tài liệu nguồn được
chia nhỏ không hợp lý, chất lượng câu trả lời sẽ giảm sút đáng kể.

Để giải quyết vấn đề này, các kỹ sư thường thử nghiệm nhiều chiến lược chia nhỏ
khác nhau. Mỗi loại tài liệu phù hợp với một chiến lược riêng. Bảng biểu cần giữ
nguyên cấu trúc hàng, trong khi văn bản tường thuật nên cắt theo ngữ nghĩa.
"""

SAMPLES = [
    ("SPA price (markdown table)", SPA_PRICE_TABLE),
    ("SPA price (raw CSV)", SPA_PRICE_CSV),
    ("Legal (Chương/Điều hierarchy)", LEGAL_TEXT),
    ("Prose (narrative)", PROSE),
]


def _preview(s: str, n: int = 90) -> str:
    s = s.replace("\n", "⏎")
    return s if len(s) <= n else s[:n] + "…"


def main() -> int:
    for title, text in SAMPLES:
        print("=" * 78)
        print(f"SAMPLE: {title}   (input {len(text)} chars)")
        print("-" * 78)

        profile = analyze_document(text)
        strategy, confidence = select_strategy(profile, text=text)
        # Trim profile to the keys that drive the decision for readability.
        keys = (
            "total_headings", "table_count", "is_csv_format", "heading_ratio",
            "vn_hierarchical_markers", "mixed_content_score", "avg_text_length",
            "total_words",
        )
        prof_view = {k: profile[k] for k in keys if k in profile}
        print(f"profile   : {prof_view}")
        print(f"STRATEGY  : {strategy}   (confidence={confidence:.2f})")

        chunks = smart_chunk(text)
        sizes = [len(c) for c in chunks]
        print(f"CHUNKS    : n={len(chunks)}  sizes={sizes}")
        for i, c in enumerate(chunks):
            print(f"  [{i}] ({len(c):>4}c) {_preview(c)}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
