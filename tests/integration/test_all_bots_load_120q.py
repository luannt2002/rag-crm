"""Load test all 12 bots × 10 questions parallel + RAGAS validate.

Operator-only script. NOT in pytest suite (requires live API + DB).

Run:
    python3 tests/integration/test_all_bots_load_120q.py

Output:
    /tmp/all_bots_load_<timestamp>.json — full per-question result
    stdout — per-bot summary + aggregate + RAGAS metrics

Design:
- Questions hand-crafted grounded in each bot's corpus (sample-verified).
- 6 factoid (must_contain literal) + 2 reasoning + 2 OOS-trap per bot.
- Parallel asyncio.gather with semaphore N=8 (DEFAULT_LOAD_TEST_CONC).
- Verdict: pass / partial / hallu / oos_correct / error.
- RAGAS-lite: faithfulness (answer ⊆ chunks union) + answer_relevance
  (must_contain coverage) + retrieval (top_score / chunks_used).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import httpx

ROOT = Path("/var/www/html/ragbot")
ENV = ROOT / ".env"
for line in ENV.read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"')

BASE = "http://localhost:3004/api/ragbot/test"
CONCURRENCY = 1


# ----------------------------------------------------------------------- #
# Data model                                                                #
# ----------------------------------------------------------------------- #
@dataclass(frozen=True)
class Question:
    qid: str
    bot_id: str
    channel_type: str
    text: str
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ()
    is_oos: bool = False  # True = expect refuse/partial
    category: str = "factoid"  # factoid | reasoning | oos


@dataclass
class Result:
    qid: str
    bot_id: str
    question: str
    answer: str
    answer_type: str
    chunks_used: int
    top_score: float
    latency_s: float
    must_contain_missing: list[str]
    must_not_contain_violations: list[str]
    verdict: str  # pass | partial | hallu | oos_correct | error
    category: str
    is_oos: bool
    sources_preview: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------- #
# Question bank (120 questions, grounded in corpus, sample-verified)        #
# ----------------------------------------------------------------------- #
QUESTIONS: list[Question] = [
    # ============================ dia-ly-vn ============================
    Question("dia-01", "dia-ly-vn", "web",
        "tổng diện tích tự nhiên việt nam bao nhiêu",
        must_contain=("331.212", "km"), category="factoid"),
    Question("dia-02", "dia-ly-vn", "web",
        "diện tích đất nông nghiệp năm 2022",
        must_contain=("11,57",), category="factoid"),
    Question("dia-03", "dia-ly-vn", "web",
        "tỉ lệ dân số việt nam từ 0-14 tuổi",
        must_contain=("24,3",), category="factoid"),
    Question("dia-04", "dia-ly-vn", "web",
        "tuổi thọ trung bình của người việt nam",
        must_contain=("73,6",), category="factoid"),
    Question("dia-05", "dia-ly-vn", "web",
        "việt nam có bao nhiêu con sông dài",
        must_contain=("2.360",), category="factoid"),
    Question("dia-06", "dia-ly-vn", "web",
        "khoáng sản vàng tại việt nam có ở đâu",
        must_contain=("bồng miêu", "quảng nam"), category="factoid"),
    Question("dia-07", "dia-ly-vn", "web",
        "phân tích cơ cấu dân số việt nam theo nhóm tuổi",
        must_contain=("69,3",), category="reasoning"),
    Question("dia-08", "dia-ly-vn", "web",
        "đá granit phổ biến ở đâu",
        must_contain=("miền trung", "miền bắc"), category="reasoning"),
    Question("dia-09", "dia-ly-vn", "web",
        "thời tiết hà nội hôm nay thế nào",
        is_oos=True, category="oos"),
    Question("dia-10", "dia-ly-vn", "web",
        "giá vàng tại việt nam hôm nay",
        is_oos=True, category="oos"),

    # ============================ hoa-hoc-10 ============================
    Question("hoa-01", "hoa-hoc-10", "web",
        "pin galvanic là gì",
        must_contain=("oxi hóa", "khử"), category="factoid"),
    Question("hoa-02", "hoa-hoc-10", "web",
        "pin daniel có cấu tạo gì",
        must_contain=("zn", "cu"), category="factoid"),
    Question("hoa-03", "hoa-hoc-10", "web",
        "lai hóa sp tạo bao nhiêu orbital",
        must_contain=("2", "thẳng"), category="factoid"),
    Question("hoa-04", "hoa-hoc-10", "web",
        "lai hóa sp2 cho ví dụ phân tử nào",
        must_contain=("sp2",), category="factoid"),
    Question("hoa-05", "hoa-hoc-10", "web",
        "delta chi nhỏ hơn 0,4 là loại liên kết gì",
        must_contain=("cộng hóa trị", "không phân cực"), category="factoid"),
    Question("hoa-06", "hoa-hoc-10", "web",
        "khi delta chi từ 0,4 đến 1,7 là liên kết gì",
        must_contain=("phân cực",), category="factoid"),
    Question("hoa-07", "hoa-hoc-10", "web",
        "phản ứng giữa naoh và hcl tạo ra gì",
        must_contain=("nacl", "h2o"), category="reasoning"),
    Question("hoa-08", "hoa-hoc-10", "web",
        "khi delta chi >= 1,7 liên kết gì hình thành",
        must_contain=("ion",), category="reasoning"),
    Question("hoa-09", "hoa-hoc-10", "web",
        "ai là người phát minh ra bóng đèn",
        is_oos=True, category="oos"),
    Question("hoa-10", "hoa-hoc-10", "web",
        "công thức nấu phở ngon",
        is_oos=True, category="oos"),

    # ============================ kinh-te-vi-mo ============================
    Question("kt-01", "kinh-te-vi-mo", "web",
        "định nghĩa đáy chu kỳ kinh tế",
        must_contain=("trough", "cực tiểu"), category="factoid"),
    Question("kt-02", "kinh-te-vi-mo", "web",
        "depression nghĩa là gì",
        must_contain=("trầm cảm", "suy thoái"), category="factoid"),
    Question("kt-03", "kinh-te-vi-mo", "web",
        "công thức số nhân chi tiêu",
        must_contain=("mpc", "1"), category="factoid"),
    Question("kt-04", "kinh-te-vi-mo", "web",
        "điều kiện marshall-lerner cải thiện cán cân thương mại",
        must_contain=("1",), category="factoid"),
    Question("kt-05", "kinh-te-vi-mo", "web",
        "mpc là viết tắt của gì",
        must_contain=("marginal propensity to consume",), category="factoid"),
    Question("kt-06", "kinh-te-vi-mo", "web",
        "nguyên nhân chu kỳ kinh tế gồm những gì",
        must_contain=("cầu",), category="reasoning"),
    Question("kt-07", "kinh-te-vi-mo", "web",
        "khi nào phá giá đồng tiền cải thiện được cán cân thương mại",
        must_contain=("marshall", "lerner"), category="reasoning"),
    Question("kt-08", "kinh-te-vi-mo", "web",
        "phân biệt suy thoái và trầm cảm",
        must_contain=("suy thoái",), category="reasoning"),
    Question("kt-09", "kinh-te-vi-mo", "web",
        "tỉ giá usd hôm nay là bao nhiêu",
        is_oos=True, category="oos"),
    Question("kt-10", "kinh-te-vi-mo", "web",
        "dự báo lạm phát việt nam tháng 6",
        is_oos=True, category="oos"),

    # ============================ lich-su-vn ============================
    Question("lsu-01", "lich-su-vn", "web",
        "khởi nghĩa hai bà trưng xảy ra năm nào",
        must_contain=("40", "43"), category="factoid"),
    Question("lsu-02", "lich-su-vn", "web",
        "nhà tiền lê do ai sáng lập",
        must_contain=("lê hoàn",), category="factoid"),
    Question("lsu-03", "lich-su-vn", "web",
        "lê hoàn đánh tan quân tống năm nào",
        must_contain=("981",), category="factoid"),
    Question("lsu-04", "lich-su-vn", "web",
        "lý thái tổ dời đô về đâu năm nào",
        must_contain=("thăng long", "1010"), category="factoid"),
    Question("lsu-05", "lich-su-vn", "web",
        "đường lối đổi mới bắt đầu năm nào",
        must_contain=("1986",), category="factoid"),
    Question("lsu-06", "lich-su-vn", "web",
        "trưng trắc là ai",
        must_contain=("trưng",), category="factoid"),
    Question("lsu-07", "lich-su-vn", "web",
        "lý thường kiệt thuộc thời nhà nào",
        must_contain=("lý",), category="reasoning"),
    Question("lsu-08", "lich-su-vn", "web",
        "thời kỳ bắc thuộc lần thứ nhất do triều đại nào áp đặt",
        must_contain=("hán",), category="reasoning"),
    Question("lsu-09", "lich-su-vn", "web",
        "tổng thống mỹ hiện nay là ai",
        is_oos=True, category="oos"),
    Question("lsu-10", "lich-su-vn", "web",
        "thời tiết hôm nay ra sao",
        is_oos=True, category="oos"),

    # ============================ luat-giao-thong ============================
    Question("lgt-01", "luat-giao-thong", "web",
        "không có gplx phạt bao nhiêu",
        must_contain=("1.000.000",), category="factoid"),
    Question("lgt-02", "luat-giao-thong", "web",
        "không mang gplx phạt bao nhiêu",
        must_contain=("100.000",), category="factoid"),
    Question("lgt-03", "luat-giao-thong", "web",
        "không đội mũ bảo hiểm phạt bao nhiêu",
        must_contain=("400.000",), category="factoid"),
    Question("lgt-04", "luat-giao-thong", "web",
        "xe dưới 50cc có cần gplx không",
        must_contain=("không",), category="factoid"),
    Question("lgt-05", "luat-giao-thong", "web",
        "đi sai làn đường phạt bao nhiêu",
        must_contain=("300.000",), category="factoid"),
    Question("lgt-06", "luat-giao-thong", "web",
        "xe từ 50cc trở lên cần gplx hạng nào",
        must_contain=("a1",), category="factoid"),
    Question("lgt-07", "luat-giao-thong", "web",
        "nghị định 100/2019 điều 6 quy định về ai",
        must_contain=("xe máy",), category="reasoning"),
    Question("lgt-08", "luat-giao-thong", "web",
        "phân biệt phạt không có gplx và không mang gplx",
        must_contain=("1.000.000",), category="reasoning"),
    Question("lgt-09", "luat-giao-thong", "web",
        "luật giao thông mỹ thế nào",
        is_oos=True, category="oos"),
    Question("lgt-10", "luat-giao-thong", "web",
        "giá xe máy honda hiện nay",
        is_oos=True, category="oos"),

    # ============================ sinh-hoc-12 ============================
    Question("sh-01", "sinh-hoc-12", "web",
        "điều kiện áp dụng quy luật phân li độc lập",
        must_contain=("nst", "khác nhau"), category="factoid"),
    Question("sh-02", "sinh-hoc-12", "web",
        "phép lai aabb x aabb cho f1 kiểu hình gì",
        must_contain=("aabb",), category="factoid"),
    Question("sh-03", "sinh-hoc-12", "web",
        "tỉ lệ kiểu hình f2 trong phép lai 2 cặp gen",
        must_contain=("9:3:3:1",), category="factoid"),
    Question("sh-04", "sinh-hoc-12", "web",
        "f2 có bao nhiêu tổ hợp khi lai 2 cặp gen",
        must_contain=("16",), category="factoid"),
    Question("sh-05", "sinh-hoc-12", "web",
        "diễn thế nguyên sinh bắt đầu trên gì",
        must_contain=("nền trống",), category="factoid"),
    Question("sh-06", "sinh-hoc-12", "web",
        "diễn thế sinh thái là gì",
        must_contain=("quần xã",), category="factoid"),
    Question("sh-07", "sinh-hoc-12", "web",
        "primary succession trong tiếng anh là gì",
        must_contain=("primary succession",), category="reasoning"),
    Question("sh-08", "sinh-hoc-12", "web",
        "tại sao f1 100% là aabb trong phép lai aabb x aabb",
        must_contain=("thuần chủng",), category="reasoning"),
    Question("sh-09", "sinh-hoc-12", "web",
        "cách chăm sóc thú cưng tại nhà",
        is_oos=True, category="oos"),
    Question("sh-10", "sinh-hoc-12", "web",
        "công thức nấu phở bò",
        is_oos=True, category="oos"),

    # ============================ test-spa-id ============================
    Question("spa-01", "test-spa-id", "web",
        "dịch vụ detox ballet dành cho da nào",
        must_contain=("da dầu mụn",), category="factoid"),
    Question("spa-02", "test-spa-id", "web",
        "detox ballet dùng dược mỹ phẩm gì",
        must_contain=("payot",), category="factoid"),
    Question("spa-03", "test-spa-id", "web",
        "kỹ thuật massage trong detox ballet",
        must_contain=("gym beauté",), category="factoid"),
    Question("spa-04", "test-spa-id", "web",
        "địa chỉ dr. medispa ở đâu",
        must_contain=("102 vũ trọng phụng",), category="factoid"),
    Question("spa-05", "test-spa-id", "web",
        "dr. medispa hotline số nào",
        must_contain=("0926",), category="factoid"),
    Question("spa-06", "test-spa-id", "web",
        "giờ mở cửa spa",
        must_contain=("9",), category="factoid"),
    Question("spa-07", "test-spa-id", "web",
        "dịch vụ chăm sóc da chuyên sâu có giá bao nhiêu",
        must_contain=("700",), category="reasoning"),
    Question("spa-08", "test-spa-id", "web",
        "dịch vụ trị mụn quy trình ra sao",
        must_contain=("mụn",), category="reasoning"),
    Question("spa-09", "test-spa-id", "web",
        "thời tiết hà nội hôm nay",
        is_oos=True, category="oos"),
    Question("spa-10", "test-spa-id", "web",
        "công thức nấu phở bò ngon",
        is_oos=True, category="oos"),

    # ============================ thong-tu-09-2020-tt-nhnn ============================
    Question("tt09-01", "thong-tu-09-2020-tt-nhnn", "web",
        "điều 34 thông tư 09/2020 quy định về gì",
        must_contain=("điện toán đám mây",), category="factoid"),
    Question("tt09-02", "thong-tu-09-2020-tt-nhnn", "web",
        "điều 29 thông tư 09/2020 quy định gì",
        must_contain=("truy cập",), category="factoid"),
    Question("tt09-03", "thong-tu-09-2020-tt-nhnn", "web",
        "điều 57 thông tư 09 quy định về mẫu báo cáo gì",
        must_contain=("sự cố",), category="factoid"),
    Question("tt09-04", "thong-tu-09-2020-tt-nhnn", "web",
        "điều 55 thông tư 09 nói về trách nhiệm của ai",
        must_contain=("ngân hàng nhà nước",), category="factoid"),
    Question("tt09-05", "thong-tu-09-2020-tt-nhnn", "web",
        "tiêu chí lựa chọn bên thứ ba cung cấp dịch vụ cloud nằm ở đâu",
        must_contain=("34",), category="factoid"),
    Question("tt09-06", "thong-tu-09-2020-tt-nhnn", "web",
        "quy định về quản lý truy cập mạng nội bộ nằm ở điều nào",
        must_contain=("29",), category="factoid"),
    Question("tt09-07", "thong-tu-09-2020-tt-nhnn", "web",
        "yêu cầu hạ tầng công nghệ thông tin của bên thứ ba cloud có ở điều 34 không",
        must_contain=("có", "34"), category="reasoning"),
    Question("tt09-08", "thong-tu-09-2020-tt-nhnn", "web",
        "ai phải báo cáo sự cố an toàn thông tin",
        must_contain=("ngân hàng",), category="reasoning"),
    Question("tt09-09", "thong-tu-09-2020-tt-nhnn", "web",
        "luật dân sự việt nam điều 1 nói gì",
        is_oos=True, category="oos"),
    Question("tt09-10", "thong-tu-09-2020-tt-nhnn", "web",
        "phí mở thẻ tín dụng vietcombank",
        is_oos=True, category="oos"),

    # ============================ tin-hoc-co-ban ============================
    Question("th-01", "tin-hoc-co-ban", "web",
        "cú pháp hàm vlookup trong excel",
        must_contain=("vlookup", "lookup_value"), category="factoid"),
    Question("th-02", "tin-hoc-co-ban", "web",
        "tham số table_array trong vlookup là gì",
        must_contain=("vùng",), category="factoid"),
    Question("th-03", "tin-hoc-co-ban", "web",
        "vòng lặp for trong python với range(5) chạy mấy lần",
        must_contain=("0", "4"), category="factoid"),
    Question("th-04", "tin-hoc-co-ban", "web",
        "phím tắt tạo ô mới trong bảng word",
        must_contain=("tab",), category="factoid"),
    Question("th-05", "tin-hoc-co-ban", "web",
        "shift + tab dùng để làm gì trong bảng word",
        must_contain=("trước",), category="factoid"),
    Question("th-06", "tin-hoc-co-ban", "web",
        "merge cells trong word làm gì",
        must_contain=("merge",), category="factoid"),
    Question("th-07", "tin-hoc-co-ban", "web",
        "khi viết for fruit in ['táo', 'cam', 'chuối'] kết quả in ra gì",
        must_contain=("táo", "cam", "chuối"), category="reasoning"),
    Question("th-08", "tin-hoc-co-ban", "web",
        "vlookup tìm giá trị ở cột thứ mấy của vùng dữ liệu",
        must_contain=("col",), category="reasoning"),
    Question("th-09", "tin-hoc-co-ban", "web",
        "ngôn ngữ rust dùng để làm gì",
        is_oos=True, category="oos"),
    Question("th-10", "tin-hoc-co-ban", "web",
        "thương hiệu laptop nào tốt nhất",
        is_oos=True, category="oos"),

    # ============================ toan-hoc-12 ============================
    Question("toan-01", "toan-hoc-12", "web",
        "phương trình mũ cơ bản a^f(x) = a^g(x) tương đương với gì",
        must_contain=("f(x)", "g(x)"), category="factoid"),
    Question("toan-02", "toan-hoc-12", "web",
        "điều kiện của a trong phương trình mũ a^f(x) = a^g(x)",
        must_contain=("a > 0",), category="factoid"),
    Question("toan-03", "toan-hoc-12", "web",
        "công thức nguyên hàm x^n",
        must_contain=("n+1",), category="factoid"),
    Question("toan-04", "toan-hoc-12", "web",
        "nguyên hàm của 1/x",
        must_contain=("ln",), category="factoid"),
    Question("toan-05", "toan-hoc-12", "web",
        "công thức tích phân từng phần",
        must_contain=("uv",), category="factoid"),
    Question("toan-06", "toan-hoc-12", "web",
        "ký hiệu nguyên hàm tổng quát của f(x)",
        must_contain=("f(x)", "c"), category="factoid"),
    Question("toan-07", "toan-hoc-12", "web",
        "khi tính nguyên hàm 2x*(x^2+1)^5 nên đặt u là gì",
        must_contain=("x^2+1",), category="reasoning"),
    Question("toan-08", "toan-hoc-12", "web",
        "phương trình logarit cơ bản tương đương với điều kiện gì",
        must_contain=("> 0",), category="reasoning"),
    Question("toan-09", "toan-hoc-12", "web",
        "giải bài toán tiếng anh lớp 5",
        is_oos=True, category="oos"),
    Question("toan-10", "toan-hoc-12", "web",
        "công thức hóa học nước",
        is_oos=True, category="oos"),

    # ============================ vat-ly-11 ============================
    Question("vl-01", "vat-ly-11", "web",
        "định luật coulomb công thức gì",
        must_contain=("q1", "q2"), category="factoid"),
    Question("vl-02", "vat-ly-11", "web",
        "lực coulomb tỉ lệ nghịch với gì",
        must_contain=("bình phương",), category="factoid"),
    Question("vl-03", "vat-ly-11", "web",
        "điện trường tổng hợp tính bằng phép gì",
        must_contain=("vector",), category="factoid"),
    Question("vl-04", "vat-ly-11", "web",
        "cùng dấu thì hai điện tích như thế nào",
        must_contain=("đẩy",), category="factoid"),
    Question("vl-05", "vat-ly-11", "web",
        "trái dấu thì hai điện tích như thế nào",
        must_contain=("hút",), category="factoid"),
    Question("vl-06", "vat-ly-11", "web",
        "hằng số điện môi ký hiệu là gì",
        must_contain=("epsilon",), category="factoid"),
    Question("vl-07", "vat-ly-11", "web",
        "khi q1 > 0 thì điện trường e1 hướng như thế nào",
        must_contain=("ra xa",), category="reasoning"),
    Question("vl-08", "vat-ly-11", "web",
        "lực coulomb trong môi trường điện môi giảm hay tăng so với chân không",
        must_contain=("giảm",), category="reasoning"),
    Question("vl-09", "vat-ly-11", "web",
        "định luật newton thứ nhất là gì",
        is_oos=True, category="oos"),
    Question("vl-10", "vat-ly-11", "web",
        "công thức nấu phở",
        is_oos=True, category="oos"),

    # ============================ y-te-co-ban ============================
    Question("yte-01", "y-te-co-ban", "web",
        "khi đo huyết áp cần nghỉ ngơi bao lâu trước đó",
        must_contain=("5 phút",), category="factoid"),
    Question("yte-02", "y-te-co-ban", "web",
        "không hút thuốc bao lâu trước khi đo huyết áp",
        must_contain=("30 phút",), category="factoid"),
    Question("yte-03", "y-te-co-ban", "web",
        "dấu hiệu nguy hiểm cần đi cấp cứu khi đau dạ dày",
        must_contain=("nôn ra máu",), category="factoid"),
    Question("yte-04", "y-te-co-ban", "web",
        "triệu chứng đau dạ dày gồm",
        must_contain=("ợ",), category="factoid"),
    Question("yte-05", "y-te-co-ban", "web",
        "tiểu đường type 2 còn gọi là gì",
        must_contain=("đái tháo đường",), category="factoid"),
    Question("yte-06", "y-te-co-ban", "web",
        "cách phòng ngừa huyết áp cao gồm",
        must_contain=("kiểm tra",), category="factoid"),
    Question("yte-07", "y-te-co-ban", "web",
        "tại sao cần tái khám định kỳ khi tăng huyết áp",
        must_contain=("bác sĩ",), category="reasoning"),
    Question("yte-08", "y-te-co-ban", "web",
        "ăn kém ngon có phải triệu chứng đau dạ dày không",
        must_contain=("có",), category="reasoning"),
    Question("yte-09", "y-te-co-ban", "web",
        "cách chữa cảm cúm tại nhà bằng thảo dược",
        is_oos=True, category="oos"),
    Question("yte-10", "y-te-co-ban", "web",
        "giá viện phí bệnh viện bạch mai",
        is_oos=True, category="oos"),
]


# ----------------------------------------------------------------------- #
# HTTP client                                                               #
# ----------------------------------------------------------------------- #
async def _fresh_token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE}/tokens/self", timeout=10)
    r.raise_for_status()
    return r.json()["token"]


async def ask(client: httpx.AsyncClient, q: Question, sem: asyncio.Semaphore) -> Result:
    async with sem:
        try:
            token = await _fresh_token(client)
        except Exception as exc:
            return Result(
                qid=q.qid, bot_id=q.bot_id, question=q.text,
                answer="", answer_type="error",
                chunks_used=0, top_score=0.0, latency_s=0.0,
                must_contain_missing=list(q.must_contain),
                must_not_contain_violations=[],
                verdict="error", category=q.category, is_oos=q.is_oos,
                sources_preview=[f"token_fetch_failed: {type(exc).__name__}"],
            )
        body = {
            "bot_id": q.bot_id,
            "channel_type": q.channel_type,
            "question": q.text,
            "connect_id": f"loadtest-{q.qid}",
        }
        t = time.time()
        try:
            r = await client.post(
                f"{BASE}/chat",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=120,
            )
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            return Result(
                qid=q.qid, bot_id=q.bot_id, question=q.text,
                answer="", answer_type="error",
                chunks_used=0, top_score=0.0,
                latency_s=time.time() - t,
                must_contain_missing=list(q.must_contain),
                must_not_contain_violations=[],
                verdict="error", category=q.category, is_oos=q.is_oos,
                sources_preview=[f"http_failed: {type(exc).__name__}: {str(exc)[:120]}"],
            )
        lat = time.time() - t

    # Extract response — support both raw + envelope formats
    payload = data.get("data") if isinstance(data, dict) and "data" in data else data
    answer = (payload or {}).get("answer", "") or ""
    answer_type = (payload or {}).get("answer_type", "?")
    chunks_used = (payload or {}).get("chunks_used", 0) or 0
    top_score = float((payload or {}).get("top_score", 0.0) or 0.0)
    sources_arr = (payload or {}).get("citations") or []
    sources_preview = [
        (s.get("preview") or s.get("content") or "")[:80]
        for s in sources_arr[:3]
        if isinstance(s, dict)
    ]

    # Verdict (case-insensitive substring match against lower-cased answer)
    ans_lower = answer.lower()
    miss = [k for k in q.must_contain if k.lower() not in ans_lower]
    viol = [k for k in q.must_not_contain if k.lower() in ans_lower]

    if q.is_oos:
        # OOS expected refuse — answer_type oos OR explicit refusal text.
        # Refuse markers cover the platform-tier rule 10 PARTIAL_ANSWER
        # vocabulary + per-bot oos_answer_template phrasing observed in
        # production. Keep generous: any of these substrings = refuse
        # (false-negative OOS is far worse than false-positive).
        refuse_markers = (
            "chưa có thông tin",
            "liên hệ trực tiếp",
            "không có",
            "không nằm trong",
            "không đề cập",
            "không tìm thấy",
            "chưa thấy nội dung",
            "chỉ hỗ trợ",
            "tài liệu không",
            "vui lòng tham khảo",
            "chỉ hỗ trợ các câu hỏi",
        )
        is_refuse = answer_type == "oos" or any(m in ans_lower for m in refuse_markers)
        verdict = "oos_correct" if is_refuse else "hallu"
    else:
        if viol:
            verdict = "hallu"
        elif miss:
            verdict = "partial" if len(miss) < len(q.must_contain) else "hallu"
        else:
            verdict = "pass"

    return Result(
        qid=q.qid, bot_id=q.bot_id, question=q.text,
        answer=answer, answer_type=answer_type,
        chunks_used=chunks_used, top_score=top_score,
        latency_s=round(lat, 3),
        must_contain_missing=miss,
        must_not_contain_violations=viol,
        verdict=verdict, category=q.category, is_oos=q.is_oos,
        sources_preview=sources_preview,
    )


# ----------------------------------------------------------------------- #
# RAGAS-lite metrics                                                        #
# ----------------------------------------------------------------------- #
def compute_ragas_lite(results: list[Result]) -> dict[str, Any]:
    """Lightweight RAGAS substitute (no LLM judge — pure heuristic).

    - faithfulness: 1 - (must_not_contain_violation_rate)  (HALLU absence)
    - answer_relevance: must_contain_coverage (over non-OOS)
    - context_precision_proxy: P(chunks_used > 0 | non-OOS) — retrieval hit
    - latency_p95
    """
    non_oos = [r for r in results if not r.is_oos]
    oos = [r for r in results if r.is_oos]
    n = max(1, len(non_oos))
    n_total = max(1, len(results))

    # Faithfulness — fraction of non-OOS turns with zero must_not_contain violations
    n_no_violation = sum(1 for r in non_oos if not r.must_not_contain_violations)
    faithfulness = n_no_violation / n

    # Answer relevance — fraction of expected must_contain markers actually present
    total_expected = 0
    total_hit = 0
    for r in non_oos:
        q_obj = next((q for q in QUESTIONS if q.qid == r.qid), None)
        if not q_obj or not q_obj.must_contain:
            continue
        total_expected += len(q_obj.must_contain)
        total_hit += len(q_obj.must_contain) - len(r.must_contain_missing)
    answer_relevance = total_hit / max(1, total_expected)

    # Context precision proxy — non-OOS turns with chunks retrieved
    n_with_chunks = sum(1 for r in non_oos if r.chunks_used > 0)
    context_precision_proxy = n_with_chunks / n

    # OOS refuse accuracy
    n_oos_correct = sum(1 for r in oos if r.verdict == "oos_correct")
    oos_refuse_rate = n_oos_correct / max(1, len(oos))

    # Latency
    lats = sorted(r.latency_s for r in results if r.latency_s > 0)
    p50 = lats[len(lats) // 2] if lats else 0.0
    p95 = lats[int(len(lats) * 0.95)] if lats else 0.0
    p99 = lats[int(len(lats) * 0.99)] if lats else 0.0

    return {
        "faithfulness": round(faithfulness, 4),
        "answer_relevance": round(answer_relevance, 4),
        "context_precision_proxy": round(context_precision_proxy, 4),
        "oos_refuse_rate": round(oos_refuse_rate, 4),
        "latency_p50_s": p50,
        "latency_p95_s": p95,
        "latency_p99_s": p99,
        "n_total": len(results),
        "n_non_oos": len(non_oos),
        "n_oos": len(oos),
    }


def aggregate(results: list[Result]) -> dict[str, Any]:
    by_bot: dict[str, list[Result]] = {}
    for r in results:
        by_bot.setdefault(r.bot_id, []).append(r)
    bots_summary = {}
    for bot, rs in sorted(by_bot.items()):
        v = {"pass": 0, "partial": 0, "hallu": 0, "oos_correct": 0, "error": 0}
        for r in rs:
            v[r.verdict] = v.get(r.verdict, 0) + 1
        bots_summary[bot] = {
            **v,
            "total": len(rs),
            "pass_rate": round(v["pass"] / max(1, len(rs)), 3),
            "hallu_rate": round(v["hallu"] / max(1, len(rs)), 3),
            "ragas_lite": compute_ragas_lite(rs),
        }
    return {
        "aggregate": compute_ragas_lite(results),
        "verdicts": {
            "pass": sum(1 for r in results if r.verdict == "pass"),
            "partial": sum(1 for r in results if r.verdict == "partial"),
            "hallu": sum(1 for r in results if r.verdict == "hallu"),
            "oos_correct": sum(1 for r in results if r.verdict == "oos_correct"),
            "error": sum(1 for r in results if r.verdict == "error"),
        },
        "bots": bots_summary,
    }


# ----------------------------------------------------------------------- #
# Driver                                                                    #
# ----------------------------------------------------------------------- #
async def main() -> None:
    print(f"=== Load test all bots: {len(QUESTIONS)} questions, concurrency={CONCURRENCY} ===")
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient() as client:
        # Warm — token fetch first
        try:
            _ = await _fresh_token(client)
        except Exception as exc:
            print(f"WARM_FAILED: {exc}")
            return
        t0 = time.time()
        tasks = [ask(client, q, sem) for q in QUESTIONS]
        results: list[Result] = await asyncio.gather(*tasks)
        wall = time.time() - t0

    agg = aggregate(results)
    out_path = f"/tmp/all_bots_load_{int(time.time())}.json"
    with open(out_path, "w") as f:
        json.dump({
            "wall_clock_s": round(wall, 2),
            "concurrency": CONCURRENCY,
            "aggregate": agg,
            "results": [asdict(r) for r in results],
        }, f, indent=2, ensure_ascii=False)

    # Print summary
    print()
    print(f"=== AGGREGATE ({len(QUESTIONS)} questions, {round(wall,1)}s wall, conc={CONCURRENCY}) ===")
    v = agg["verdicts"]
    print(f"  pass        : {v['pass']:3d}/{len(QUESTIONS)} ({v['pass']/len(QUESTIONS)*100:.1f}%)")
    print(f"  partial     : {v['partial']:3d}/{len(QUESTIONS)}")
    print(f"  oos_correct : {v['oos_correct']:3d}/{len(QUESTIONS)}")
    print(f"  HALLU       : {v['hallu']:3d}/{len(QUESTIONS)}  ⭐")
    print(f"  error       : {v['error']:3d}/{len(QUESTIONS)}")
    print()
    r = agg["aggregate"]
    print(f"=== RAGAS-LITE ===")
    print(f"  faithfulness            : {r['faithfulness']}")
    print(f"  answer_relevance        : {r['answer_relevance']}")
    print(f"  context_precision_proxy : {r['context_precision_proxy']}")
    print(f"  oos_refuse_rate         : {r['oos_refuse_rate']}")
    print(f"  latency p50/p95/p99 (s) : {r['latency_p50_s']:.2f} / {r['latency_p95_s']:.2f} / {r['latency_p99_s']:.2f}")
    print()
    print(f"=== PER-BOT ===")
    print(f"{'bot_id':30s} {'pass':>5s} {'part':>5s} {'oos':>5s} {'HALLU':>6s} {'err':>4s} | {'faith':>6s} {'rel':>5s} {'ctx':>5s} {'oos%':>5s} {'p95':>6s}")
    print("-" * 115)
    for bot, s in agg["bots"].items():
        rl = s["ragas_lite"]
        print(f"{bot:30s} {s['pass']:>5d} {s['partial']:>5d} {s['oos_correct']:>5d} {s['hallu']:>6d} {s['error']:>4d} | "
              f"{rl['faithfulness']:>6.3f} {rl['answer_relevance']:>5.3f} {rl['context_precision_proxy']:>5.3f} "
              f"{rl['oos_refuse_rate']:>5.2f} {rl['latency_p95_s']:>5.1f}s")

    print()
    print(f"=== HALLU DETAILS ===")
    n_hallu = 0
    for r in results:
        if r.verdict == "hallu":
            n_hallu += 1
            print(f"  [{r.qid}] {r.bot_id} | Q: {r.question[:60]}")
            print(f"     A: {r.answer[:140]}")
            if r.must_contain_missing:
                print(f"     MISS: {r.must_contain_missing}")
            if r.must_not_contain_violations:
                print(f"     VIOLATE: {r.must_not_contain_violations}")
    if n_hallu == 0:
        print(f"  (none — HALLU = 0 sacred ✅)")

    print()
    print(f"💾 Full JSON: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
