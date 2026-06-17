#!/usr/bin/env python3
"""Load test — 200 rooms × 15 questions = 3000 queries.

Extends test_rooms_v3.py to 200 rooms using 20 topic templates × 10 variations.
Each room has its own unique connect_id (tests per-user rate limit + conversation
isolation). Questions include repeat probes at indices 12 and 14.

Usage:
    python scripts/test_rooms_200.py
    python scripts/test_rooms_200.py --rooms 10 --concurrency 5   # smoke
    python scripts/test_rooms_200.py --rooms 200 --concurrency 10 --debug full \\
        --output reports/LOAD_TEST_200ROOM_raw.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BOT_ID = os.getenv("RAGBOT_TEST_BOT_ID", "thula-test-bot-v1")
CHANNEL = "web"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

# ---------------------------------------------------------------------------
# 20 topic templates — each with exactly 15 base questions.
# Indices 12 and 14 are exact repeats of 0 and 2 (consistency probes).
# ---------------------------------------------------------------------------
TOPIC_TEMPLATES: list[dict] = [
    {
        "id": "goi-dau",
        "topic": "Gội đầu",
        "questions": [
            "giá gội đầu thường bao nhiêu",                          # 0 probe A
            "gội đầu dưỡng sinh khác gội thường chỗ nào",
            "gội đầu dầu cặp giá bao nhiêu",                         # 2 probe B
            "thời gian gội đầu bao lâu",
            "có combo gội đầu không",
            "gội đầu có tặng massage không",
            "mua 10 buổi tặng mấy buổi",
            "em xưng gì với khách",
            "có chỗ gửi xe không",
            "mấy giờ mở cửa",
            "dưỡng sinh 30 phút có đủ không",
            "sau khi gội đầu có phải kiêng gì không",
            "giá gội đầu thường bao nhiêu",                          # 12 repeat of 0
            "combo 30 buổi được giảm bao nhiêu",
            "gội đầu dầu cặp giá bao nhiêu",                         # 14 repeat of 2
        ],
    },
    {
        "id": "cham-soc-da",
        "topic": "Chăm sóc da chuyên sâu",
        "questions": [
            "chăm sóc da mặt bên em có gói nào",                     # 0
            "giá chăm sóc da bao nhiêu",
            "da em hay nổi mụn thì nên làm gì",                      # 2
            "có trị mụn ẩn không",
            "hydra facial là gì",
            "quy trình chăm sóc da gồm mấy bước",
            "mất bao lâu mới thấy da đẹp lên",
            "có bảo hành kết quả không",
            "mỹ phẩm bên em có an toàn không",
            "da nhạy cảm có làm được không",
            "một liệu trình mấy buổi",
            "có tư vấn miễn phí không",
            "chăm sóc da mặt bên em có gói nào",                     # 12
            "sau khi trị mụn cần chăm sóc gì",
            "da em hay nổi mụn thì nên làm gì",                      # 14
        ],
    },
    {
        "id": "triet-long",
        "topic": "Triệt lông",
        "questions": [
            "triệt lông nách giá bao nhiêu",                         # 0
            "triệt lông vĩnh viễn có thật không",
            "triệt lông tay chân combo giá sao",                     # 2
            "triệt bikini có đau không",
            "công nghệ triệt lông bên em dùng loại gì",
            "một combo triệt lông mấy buổi",
            "triệt lông nam có được không",
            "giữa các buổi cách mấy tuần",
            "sau triệt có phải kiêng tắm nắng không",
            "triệt lông có gây rối loạn hormone không",
            "bà bầu có triệt được không",
            "có khuyến mãi combo triệt không",
            "triệt lông nách giá bao nhiêu",                         # 12
            "10 buổi triệt nách tổng tiền bao nhiêu",
            "triệt lông tay chân combo giá sao",                     # 14
        ],
    },
    {
        "id": "massage",
        "topic": "Massage",
        "questions": [
            "massage cổ vai gáy giá bao nhiêu",                      # 0
            "massage body toàn thân bao nhiêu tiền",
            "thời gian massage bao lâu",                             # 2
            "massage có giúp đỡ đau lưng không",
            "bên em có massage chân không",
            "massage có dùng tinh dầu không",
            "nhân viên nam hay nữ massage",
            "có phòng VIP không",
            "massage bầu có được không",
            "bên em có dịch vụ massage mặt không",
            "massage cổ vai gáy có khuyến mãi không",
            "em đặt lịch qua đâu",
            "massage cổ vai gáy giá bao nhiêu",                      # 12
            "1 combo 5 buổi giá bao nhiêu",
            "thời gian massage bao lâu",                             # 14
        ],
    },
    {
        "id": "hoi-chung",
        "topic": "Hỏi chung & dò clarify",
        "questions": [
            "bên em có gì",                                          # 0
            "dịch vụ bên em",
            "em muốn tư vấn",                                        # 2
            "chị mới biết shop lần đầu",
            "có loại dịch vụ nào cho mặt",
            "có loại dịch vụ nào cho body",
            "loại nào rẻ nhất",
            "loại nào được ưa chuộng nhất",
            "shop ở đâu",
            "có mấy chi nhánh",
            "có fanpage không",
            "có app đặt lịch không",
            "bên em có gì",                                          # 12
            "chị thích mấy thứ như chăm sóc mặt",
            "dịch vụ bên em",                                        # 14
        ],
    },
    {
        "id": "gia-quy-trinh",
        "topic": "Hỏi giá + quy trình xen kẽ",
        "questions": [
            "giá triệt lông nách",                                   # 0
            "quy trình triệt lông nách gồm mấy bước",
            "giá chăm sóc da chuyên sâu",                            # 2
            "quy trình chăm sóc da gồm gì",
            "giá gội đầu dưỡng sinh",
            "quy trình gội đầu dưỡng sinh",
            "giá massage cổ vai gáy",
            "quy trình massage cổ vai gáy",
            "em muốn đặt lịch triệt lông nách",
            "em muốn đặt lịch chăm sóc da",
            "giá và quy trình triệt bikini",
            "chi phí combo 10 buổi chăm sóc da",
            "giá triệt lông nách",                                   # 12
            "nhớ chị yêu cầu gì không",
            "giá chăm sóc da chuyên sâu",                            # 14
        ],
    },
    {
        "id": "empathy",
        "topic": "Empathy & vấn đề cá nhân",
        "questions": [
            "da em bị mụn",                                          # 0
            "em bị đau vai gáy lâu rồi",
            "lông nách em rậm quá",                                  # 2
            "tóc em gãy rụng",
            "em stress quá",
            "da em sạm",
            "em mới sinh xong",
            "chị 40t, da bắt đầu chảy",
            "em muốn trẻ hơn",
            "chị sợ đau",
            "chị đang cho con bú có làm được không",
            "da em dầu, lỗ chân lông to",
            "da em bị mụn",                                          # 12
            "chị nên chọn dịch vụ nào",
            "lông nách em rậm quá",                                  # 14
        ],
    },
    {
        "id": "out-of-scope",
        "topic": "Out-of-scope + chuyển hướng",
        "questions": [
            "thời tiết hôm nay thế nào",                             # 0
            "bitcoin giờ bao nhiêu",
            "crypto có nên đầu tư",                                  # 2
            "bóng đá tối nay ai đá",
            "em tên gì",
            "em bao nhiêu tuổi",
            "cho em hỏi công thức chả giò",
            "chị có yêu em không",
            "em làm thơ giúp chị",
            "1 + 1 bằng mấy",
            "em biết gì về toán",
            "chị có thấy vui không",
            "thời tiết hôm nay thế nào",                             # 12
            "giờ mình nói chuyện dịch vụ được chưa",
            "bitcoin giờ bao nhiêu",                                 # 14
        ],
    },
    {
        "id": "context-dai-tu",
        "topic": "Ngữ cảnh & đại từ",
        "questions": [
            "giá chăm sóc da chuyên sâu",                           # 0
            "cái đó bao lâu một buổi",
            "quy trình nó gồm gì",                                   # 2
            "có loại khác rẻ hơn không",
            "vậy loại rẻ nhất giá bao nhiêu",
            "combo 10 buổi rẻ hơn bao nhiêu",
            "chị lấy 5 buổi được không",
            "ok vậy chị lấy 10 buổi",
            "còn gói nào kết hợp với triệt lông không",
            "cái triệt lông nách đó giá bao nhiêu",
            "gộp cả hai lại tổng bao nhiêu",
            "combo 15 buổi được không",
            "giá chăm sóc da chuyên sâu",                           # 12
            "nhắc lại chị nghe tên 3 dịch vụ em vừa nói",
            "quy trình nó gồm gì",                                   # 14
        ],
    },
    {
        "id": "negative-edge",
        "topic": "Bất lịch sự + chốt hạn chế",
        "questions": [
            "giá gội đầu",                                           # 0
            "sao đắt thế",
            "chỗ khác rẻ hơn",                                       # 2
            "có đảm bảo không, lỡ hỏng da ai chịu",
            "em trả lời nhanh đi",
            "thôi, đắt quá",
            "chị muốn refund",
            "hôm qua chị làm chưa hài lòng",
            "chị đưa giá 50k em làm không",
            "em bớt đi",
            "em gọi sếp em đi",
            "shop làm ăn kiểu gì",
            "giá gội đầu",                                           # 12
            "ok em giải thích lại giá chị nghe",
            "chỗ khác rẻ hơn",                                       # 14
        ],
    },
    {
        "id": "follow-up",
        "topic": "Follow-up sau khi đặt",
        "questions": [
            "chị đặt 1 buổi triệt nách",                             # 0
            "giờ nào còn lịch",
            "chiều mai được không",                                  # 2
            "3h hay 4h",
            "ok chị lấy 3h30",
            "địa chỉ chính xác ở đâu",
            "có gần bến xe Giáp Bát không",
            "có chỗ để xe máy không",
            "chị mang theo gì",
            "có cần đặt cọc không",
            "thanh toán khi nào",
            "chuyển khoản được không",
            "chị đặt 1 buổi triệt nách",                             # 12
            "cảm ơn em nhé",
            "chiều mai được không",                                  # 14
        ],
    },
    {
        "id": "so-sanh",
        "topic": "So sánh dịch vụ / gói",
        "questions": [
            "gói 199k và 350k khác gì",                              # 0
            "gói combo 10 buổi với mua lẻ, cái nào lợi",
            "hydra facial với carbon peeling cái nào tốt",           # 2
            "triệt Diode Laser với IPL khác nhau chỗ nào",
            "gội đầu thường với dưỡng sinh chênh bao nhiêu phút",
            "massage VIP với massage thường chênh bao nhiêu",
            "combo 10 buổi bên em với spa khác giá bao nhiêu",
            "chăm sóc da lần đầu khác follow-up như thế nào",
            "gội đầu kích thích mọc tóc có khác gội thường",
            "triệt lông mặt với triệt lông tay cái nào đau hơn",
            "chị nên chọn gói nào cho da dầu nhạy cảm",
            "bên em có gói nào cho da 40t",
            "gói 199k và 350k khác gì",                              # 12
            "ok chị thử gói 199k",
            "gói combo 10 buổi với mua lẻ, cái nào lợi",            # 14
        ],
    },
    {
        "id": "an-toan",
        "topic": "Cam kết + an toàn",
        "questions": [
            "bên em có chứng chỉ gì không",                         # 0
            "máy triệt có xuất xứ rõ không",
            "mỹ phẩm bên em có nguồn gốc không",                    # 2
            "kỹ thuật viên có bằng không",
            "lỡ bị cháy da ai chịu",
            "có ký hợp đồng với khách không",
            "có xuất hóa đơn VAT không",
            "có cam kết không hiệu quả trả lại tiền không",
            "sau làm bị dị ứng thì sao",
            "có camera ở phòng không",
            "khách VIP có ưu đãi gì không",
            "sản phẩm bán bên em có bảo hành không",
            "bên em có chứng chỉ gì không",                         # 12
            "có giấy phép Sở Y tế không",
            "mỹ phẩm bên em có nguồn gốc không",                    # 14
        ],
    },
    {
        "id": "combo-uu-dai",
        "topic": "Combo + ưu đãi dài hạn",
        "questions": [
            "combo 10 buổi bao nhiêu",                               # 0
            "combo 30 buổi rẻ hơn bao nhiêu",
            "có flash sale không",                                   # 2
            "Tết có khuyến mãi gì",
            "mua gói cho mẹ có ưu đãi không",
            "giới thiệu bạn bè có hoa hồng không",
            "mua chung 2 người có giảm không",
            "khách vãng lai lần đầu có giảm không",
            "voucher 100k dùng được gì",
            "bên em bán voucher đi không",
            "thẻ thành viên có ích lợi gì",
            "vòng quay may mắn còn không",
            "combo 10 buổi bao nhiêu",                               # 12
            "combo 30 buổi tổng tiền",
            "có flash sale không",                                   # 14
        ],
    },
    {
        "id": "lich-hen",
        "topic": "Lịch hẹn + đặt chỗ",
        "questions": [
            "mai 3h còn lịch không",                                 # 0
            "tuần này có slot nào đẹp không",
            "thứ 7 đông không",                                      # 2
            "chị muốn đi 2 người được không",
            "có thể đặt online không",
            "đặt rồi hủy có mất phí không",
            "đổi giờ sát nút được không",
            "em ưu tiên khách đặt trước",
            "giờ cao điểm mấy giờ",
            "tầm trưa có người không",
            "chị đến sớm 15p được không",
            "tới trễ 30p có phải đợi không",
            "mai 3h còn lịch không",                                 # 12
            "8h tối mai được không",
            "tuần này có slot nào đẹp không",                        # 14
        ],
    },
    {
        "id": "ky-thuat",
        "topic": "Câu hỏi kỹ thuật chuyên sâu",
        "questions": [
            "công nghệ Diode Laser là gì",                           # 0
            "bước sóng bao nhiêu nm",
            "hydra facial có bao nhiêu bước",                        # 2
            "dòng năng lượng RF là gì",
            "tẩy tế bào chết bằng acid nào",
            "collagen tiêm bao lâu hấp thu",
            "bên em có máy HIFU không",
            "IPL với Diode khác nhau chỗ nào",
            "làm đẹp bằng tế bào gốc có an toàn",
            "massage có dùng đèn hồng ngoại không",
            "cryotherapy là gì",
            "hydra ballet dùng huyết thanh gì",
            "công nghệ Diode Laser là gì",                           # 12
            "bước sóng bao nhiêu thì an toàn",
            "hydra facial có bao nhiêu bước",                        # 14
        ],
    },
    {
        "id": "ket-hop",
        "topic": "Kết hợp dịch vụ",
        "questions": [
            "chị muốn làm gội đầu + chăm sóc mặt 1 ngày",           # 0
            "triệt lông + chăm sóc da nên làm cái nào trước",
            "có thể làm hydra facial + massage cùng ngày",           # 2
            "chị muốn combo trọn gói cả người",
            "gói combo đó giá bao nhiêu",
            "thời gian tổng 1 buổi",
            "em xếp lịch giúp chị",
            "nên cách bao lâu làm tiếp",
            "có gói dành cho cô dâu không",
            "gói tân binh chuẩn bị sự kiện có không",
            "em có gói restart sau sinh không",
            "bên em có gói detox toàn thân không",
            "chị muốn làm gội đầu + chăm sóc mặt 1 ngày",           # 12
            "ưu đãi cho khách làm 2 gói trở lên",
            "có thể làm hydra facial + massage cùng ngày",           # 14
        ],
    },
    {
        "id": "tinh-huong-kho",
        "topic": "Tình huống bất ngờ",
        "questions": [
            "chị đang có bầu triệt lông được không",                 # 0
            "chị đang uống thuốc kháng sinh, chăm sóc da được không",
            "da chị vừa laser xong có làm nữa được",                 # 2
            "chị vừa peel xong 2 hôm, gội đầu có sao không",
            "có khách 15 tuổi làm được chăm sóc da không",
            "người già 70t triệt lông được không",
            "chị sợ kim, chăm sóc nhưng không được dùng kim",
            "chị chỉ có 30p, làm được gì",
            "chị cần ngay tối nay, có làm được không",
            "chị bị tim mạch làm được massage không",
            "chị vừa mổ 1 tháng, đi chăm sóc da được không",
            "chị đang kinh nguyệt triệt được không",
            "chị đang có bầu triệt lông được không",                 # 12
            "chị có ý định mang thai, triệt được không",
            "da chị vừa laser xong có làm nữa được",                 # 14
        ],
    },
    {
        "id": "danh-gia",
        "topic": "Đánh giá + feedback",
        "questions": [
            "bên em có fanpage không",                               # 0
            "có review của khách không",
            "có livestream không",                                   # 2
            "khách cũ quay lại bao nhiêu %",
            "điểm trung bình google map bao nhiêu",
            "có video before-after không",
            "có video kỹ thuật viên làm không",
            "có group Facebook khách hàng không",
            "review negative xử lý thế nào",
            "khách không ưng trả phí thế nào",
            "có khiếu nại gì không",
            "hồ sơ khách có bảo mật không",
            "bên em có fanpage không",                               # 12
            "link fanpage cho chị xin",
            "có review của khách không",                             # 14
        ],
    },
    {
        "id": "mo-ho",
        "topic": "Câu mơ hồ / chưa rõ nhu cầu",
        "questions": [
            "chị chưa biết muốn làm gì",                             # 0
            "cho chị xem tất cả",
            "tư vấn giúp chị",                                       # 2
            "em xem chị hợp gì",
            "em gợi ý đi",
            "chị muốn đẹp hơn",
            "chị muốn khỏe mạnh",
            "cái gì đang hot ở spa",
            "mới nhất bên em có gì",
            "cái nào hiệu quả nhất",
            "cái nào nhanh nhất",
            "cái nào hợp với da dầu",
            "chị chưa biết muốn làm gì",                             # 12
            "cái gì phụ nữ 40 tuổi hay làm",
            "tư vấn giúp chị",                                       # 14
        ],
    },
]

assert len(TOPIC_TEMPLATES) == 20, f"Expected 20 topics, got {len(TOPIC_TEMPLATES)}"
for _t in TOPIC_TEMPLATES:
    assert len(_t["questions"]) == 15, f"Topic {_t['id']} has {len(_t['questions'])} questions, expected 15"

REPEAT_PAIRS = [(0, 12), (2, 14)]

# ---------------------------------------------------------------------------
# Paraphrase helpers — lightweight, no LLM needed.
# ---------------------------------------------------------------------------
_PREFIXES = ["anh ơi, ", "chị ơi, ", "cho em hỏi, ", "ạ, ", "dạ ", "bên em cho tôi hỏi "]
_SUFFIXES = [" ạ", " không ạ", " nhé", " được không", " vậy"]
_SWAPS = [
    ("bao nhiêu", "bao nhiêu tiền"),
    ("có ", "spa có "),
    ("giá ", "chi phí "),
    ("thế nào", "như thế nào"),
    ("không", "không ạ"),
]

_RNG = random.Random(20260430)  # reproducible


def _paraphrase(q: str) -> str:
    """Apply 1 lightweight transformation to add surface variation."""
    mode = _RNG.randint(0, 3)
    if mode == 0 and _RNG.random() < 0.5:
        return _RNG.choice(_PREFIXES) + q
    elif mode == 1 and _RNG.random() < 0.5:
        return q + _RNG.choice(_SUFFIXES)
    elif mode == 2:
        for src, dst in _SWAPS:
            if src in q:
                return q.replace(src, dst, 1)
    return q  # no change


def _gen_room(n: int, template: dict) -> dict:
    """Generate room n using the given topic template.

    Variation strategy:
    - 30% of questions (randomly chosen) receive a light paraphrase.
    - Indices 12 and 14 are always kept as exact repeats of 0 and 2
      (consistency probe invariant must hold).
    """
    qs = list(template["questions"])  # copy
    for i in range(15):
        if i in (12, 14):
            continue  # keep repeat probes exact
        if _RNG.random() < 0.30:
            qs[i] = _paraphrase(qs[i])
    return {
        "id": f"r{n:03d}-{template['id']}",
        "topic": template["topic"],
        "questions": qs,
    }


# Generate 200 rooms: rooms 1-200, cycling through 20 topics
ROOMS: list[dict] = [
    _gen_room(n, TOPIC_TEMPLATES[(n - 1) % 20])
    for n in range(1, 201)
]


# ---------------------------------------------------------------------------
# HTTP helpers (same pattern as test_rooms_v3.py)
# ---------------------------------------------------------------------------

async def get_self_token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE_URL}{SELF_TOKEN_PATH}")
    r.raise_for_status()
    return r.json()["token"]


class TokenHolder:
    """Re-fetches self-token on 401."""

    def __init__(self, client: httpx.AsyncClient, initial: str):
        self._client = client
        self._token = initial

    @property
    def value(self) -> str:
        return self._token

    async def refresh(self) -> str:
        self._token = await get_self_token(self._client)
        return self._token


async def ask(
    client: httpx.AsyncClient,
    token_holder: TokenHolder,
    *,
    bot_id: str,
    channel: str,
    connect_id: str,
    question: str,
    debug: str = "",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    payload: dict[str, Any] = {
        "tenant_id": int(os.getenv("RAGBOT_TEST_TENANT_ID", "32")),
        "bot_id": bot_id,
        "channel_type": channel,
        "connect_id": connect_id,
        "question": question,
        "user_id": connect_id,
    }
    if debug:
        payload["debug"] = debug
    try:
        r = await client.post(
            f"{BASE_URL}{CHAT_PATH}",
            headers={
                "Authorization": f"Bearer {token_holder.value}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90.0,
        )
        if r.status_code == 401:
            await token_holder.refresh()
            r = await client.post(
                f"{BASE_URL}{CHAT_PATH}",
                headers={
                    "Authorization": f"Bearer {token_holder.value}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90.0,
            )
        wall_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return {
                "_error": f"HTTP {r.status_code}",
                "_wall_ms": wall_ms,
                "body": r.text[:400],
            }
        body = r.json()
    except Exception as exc:  # noqa: BLE001 — top-level HTTP wrapper, log + continue
        return {"_error": str(exc)[:300], "_wall_ms": (time.perf_counter() - t0) * 1000}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    out: dict[str, Any] = {
        "answer": (data.get("answer") or "")[:2000],
        "answer_type": data.get("answer_type"),
        "answer_reason": data.get("answer_reason"),
        "chunks_used": data.get("chunks_used"),
        "top_score": data.get("top_score"),
        "duration_ms": data.get("duration_ms"),
        "tokens": data.get("tokens"),
        "cost_usd": data.get("cost_usd"),
        "sources": [s.get("document_name") for s in (data.get("sources") or [])],
        "debug": data.get("debug"),
        "_wall_ms": wall_ms,
    }
    if data.get("retrieved_chunks_content"):
        out["retrieved_chunks_content"] = data.get("retrieved_chunks_content")
    return out


async def run_room(
    client: httpx.AsyncClient,
    token_holder: TokenHolder,
    *,
    bot_id: str,
    channel: str,
    room: dict,
    question_limit: int,
    inter_turn_sleep_s: float,
    debug: str = "",
) -> dict:
    connect_id = f"test-{room['id']}-{int(time.time() * 1000)}"
    questions = room["questions"][:question_limit]
    turns: list[dict] = []
    for i, q in enumerate(questions):
        resp = await ask(
            client, token_holder,
            bot_id=bot_id, channel=channel,
            connect_id=connect_id, question=q, debug=debug,
        )
        resp["_idx"] = i
        resp["_question"] = q
        turns.append(resp)
        if i < len(questions) - 1:
            await asyncio.sleep(inter_turn_sleep_s)

    # Cold-start probes — fresh connect_id, no conversation history.
    cold_probes: list[dict] = []
    for orig_idx, _ in REPEAT_PAIRS:
        if orig_idx >= len(questions):
            continue
        fresh = f"test-{room['id']}-cold-{orig_idx}-{int(time.time() * 1000)}"
        resp = await ask(
            client, token_holder,
            bot_id=bot_id, channel=channel,
            connect_id=fresh, question=questions[orig_idx], debug=debug,
        )
        resp["_idx"] = orig_idx
        resp["_question"] = questions[orig_idx]
        resp["_cold_start"] = True
        cold_probes.append(resp)
        await asyncio.sleep(inter_turn_sleep_s)

    return {
        "room_id": room["id"],
        "topic": room["topic"],
        "n_turns": len(turns),
        "turns": turns,
        "cold_start_probes": cold_probes,
    }


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _is_refuse(answer: str) -> bool:
    """Real refuse detection across standard refusal templates."""
    if not answer:
        return True
    a = answer.lower()
    return (
        "chưa có thông tin chính xác" in a
        or "xin lỗi, tôi không có thông tin" in a
        or "không có thông tin" in a
        or "khong hop le" in a
    )


def _extract_debug_field(turn: dict, key: str):
    dbg = turn.get("debug") or {}
    return dbg.get(key)


def summarize(results: dict) -> dict:
    rooms = results["rooms"]
    all_turns = [t for r in rooms for t in r["turns"]]
    real_answered = [
        t for t in all_turns
        if t.get("answer_type") == "answered" and not _is_refuse(t.get("answer") or "")
    ]
    refuse_turns = [t for t in all_turns if _is_refuse(t.get("answer") or "")]
    blocked = [t for t in all_turns if t.get("answer_type") == "blocked"]
    oos = [t for t in all_turns if t.get("answer_type") == "out_of_scope"]
    errored = [t for t in all_turns if t.get("_error")]

    def _avg(key, src):
        vals = [t.get(key) for t in src if t.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    def _pct(sub, total):
        return round(len(sub) / max(len(total), 1), 3)

    total_prompt = sum((t.get("tokens") or {}).get("prompt", 0) for t in all_turns)
    total_completion = sum((t.get("tokens") or {}).get("completion", 0) for t in all_turns)
    total_cached = sum((t.get("tokens") or {}).get("cached", 0) for t in all_turns)
    total_cost = sum(t.get("cost_usd") or 0 for t in all_turns)

    # Per-topic stats
    per_topic: dict[str, dict] = {}
    for r in rooms:
        topic = r["topic"]
        if topic not in per_topic:
            per_topic[topic] = {"total": 0, "answered": 0, "refused": 0, "oos": 0, "errors": 0}
        for t in r["turns"]:
            per_topic[topic]["total"] += 1
            if _is_refuse(t.get("answer") or ""):
                per_topic[topic]["refused"] += 1
            elif t.get("answer_type") == "answered":
                per_topic[topic]["answered"] += 1
            elif t.get("answer_type") == "out_of_scope":
                per_topic[topic]["oos"] += 1
            if t.get("_error"):
                per_topic[topic]["errors"] += 1

    # Top-score histogram bins: [0-0.1), [0.1-0.3), [0.3-0.5), [0.5-1.0]
    bins = {"0.0-0.1": 0, "0.1-0.3": 0, "0.3-0.5": 0, "0.5-1.0": 0}
    for t in all_turns:
        s = t.get("top_score") or 0
        if s < 0.1:
            bins["0.0-0.1"] += 1
        elif s < 0.3:
            bins["0.1-0.3"] += 1
        elif s < 0.5:
            bins["0.3-0.5"] += 1
        else:
            bins["0.5-1.0"] += 1

    # Latency percentiles
    latencies = sorted(t.get("_wall_ms") or 0 for t in all_turns)
    n = len(latencies)

    def _pct_lat(p):
        if not latencies:
            return 0
        idx = min(int(n * p / 100), n - 1)
        return round(latencies[idx], 0)

    # Repeat consistency check (indices 12 vs 0, 14 vs 2)
    repeat_consistent = 0
    repeat_total = 0
    for r in rooms:
        turns = r["turns"]
        if len(turns) >= 15:
            for orig_i, rep_i in REPEAT_PAIRS:
                t_orig = turns[orig_i]
                t_rep = turns[rep_i]
                repeat_total += 1
                # Consistent if both same answer_type (answered/refused)
                orig_refused = _is_refuse(t_orig.get("answer") or "")
                rep_refused = _is_refuse(t_rep.get("answer") or "")
                if orig_refused == rep_refused:
                    repeat_consistent += 1

    return {
        "total_rooms": len(rooms),
        "total_turns": len(all_turns),
        "real_answered": len(real_answered),
        "refuse": len(refuse_turns),
        "blocked": len(blocked),
        "out_of_scope": len(oos),
        "errors": len(errored),
        "real_answer_rate": _pct(real_answered, all_turns),
        "refuse_rate": _pct(refuse_turns, all_turns),
        "avg_duration_ms": _avg("duration_ms", all_turns),
        "avg_wall_ms": _avg("_wall_ms", all_turns),
        "avg_chunks_used": _avg("chunks_used", all_turns),
        "avg_top_score": _avg("top_score", all_turns),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_cached_tokens": total_cached,
        "cache_hit_ratio": round(total_cached / total_prompt, 3) if total_prompt else 0,
        "total_cost_usd": round(total_cost, 6),
        "latency_p50_ms": _pct_lat(50),
        "latency_p95_ms": _pct_lat(95),
        "latency_p99_ms": _pct_lat(99),
        "top_score_histogram": bins,
        "per_topic": per_topic,
        "repeat_consistency": {
            "consistent": repeat_consistent,
            "total": repeat_total,
            "rate": round(repeat_consistent / max(repeat_total, 1), 3),
        },
    }


def worst_rooms(results: dict, top_n: int = 5) -> list[dict]:
    """Return top N rooms by refuse rate (worst first)."""
    scored = []
    for r in results["rooms"]:
        turns = r["turns"]
        if not turns:
            continue
        n_refuse = sum(1 for t in turns if _is_refuse(t.get("answer") or ""))
        avg_score = sum(t.get("top_score") or 0 for t in turns) / len(turns)
        scored.append({
            "room_id": r["room_id"],
            "topic": r["topic"],
            "n_turns": len(turns),
            "refuse_count": n_refuse,
            "refuse_rate": round(n_refuse / len(turns), 3),
            "avg_top_score": round(avg_score, 4),
        })
    return sorted(scored, key=lambda x: (-x["refuse_rate"], x["avg_top_score"]))[:top_n]


def export_csv(results: dict, csv_path: Path) -> None:
    """Export per-turn metrics as CSV for downstream analysis."""
    import csv

    rows = []
    for r in results["rooms"]:
        room_id = r["room_id"]
        topic = r["topic"]
        for t in r["turns"]:
            dbg = t.get("debug") or {}
            toks = t.get("tokens") or {}
            rows.append({
                "room_id": room_id,
                "topic": topic,
                "turn_idx": t.get("_idx", ""),
                "question": t.get("_question", ""),
                "answer_type": t.get("answer_type", ""),
                "refused": 1 if _is_refuse(t.get("answer") or "") else 0,
                "wall_ms": round(t.get("_wall_ms") or 0, 1),
                "duration_ms": t.get("duration_ms") or "",
                "top_score": t.get("top_score") or 0,
                "chunks_used": t.get("chunks_used") or 0,
                "cache_hit": 1 if dbg.get("cache_hit") else 0,
                "rerank_mode": dbg.get("rerank_mode", ""),
                "crag_state": dbg.get("crag_state", ""),
                "llm_model": dbg.get("llm_model", ""),
                "prompt_tokens": toks.get("prompt", ""),
                "completion_tokens": toks.get("completion", ""),
                "cached_tokens": toks.get("cached", ""),
                "cost_usd": t.get("cost_usd") or "",
                "answer_len": len(t.get("answer") or ""),
                "error": t.get("_error", ""),
            })

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main async driver
# ---------------------------------------------------------------------------

async def main_async(args) -> None:
    rooms = ROOMS[: args.rooms]
    concurrency = max(1, int(args.concurrency))
    limits = httpx.Limits(
        max_connections=concurrency * 4,
        max_keepalive_connections=concurrency * 2,
    )
    error_count = 0
    total_count = 0

    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(120.0)) as client:
        token = await get_self_token(client)
        token_holder = TokenHolder(client, token)
        print(
            f"[test_rooms_200] {len(rooms)} rooms × {args.questions} questions"
            f" — concurrency={concurrency}"
        )
        results: dict[str, Any] = {
            "rooms": [],
            "config": {
                "rooms": args.rooms,
                "questions": args.questions,
                "concurrency": concurrency,
                "sleep_ms": args.sleep_ms,
                "debug": args.debug,
                "bot_id": BOT_ID,
                "channel": CHANNEL,
            },
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        sem = asyncio.Semaphore(concurrency)
        t_start = time.time()
        completed: list[dict] = []

        async def _run_one(idx: int, room: dict) -> dict:
            nonlocal error_count, total_count
            async with sem:
                print(f"  [{idx:>3}/{len(rooms)}] START {room['id']} — {room['topic']}")
                t0 = time.time()
                out = await run_room(
                    client, token_holder,
                    bot_id=BOT_ID, channel=CHANNEL,
                    room=room,
                    question_limit=args.questions,
                    inter_turn_sleep_s=args.sleep_ms / 1000.0,
                    debug=args.debug,
                )
                room_errors = sum(1 for t in out.get("turns", []) if t.get("_error"))
                room_answered = sum(
                    1 for t in out.get("turns", [])
                    if t.get("answer_type") == "answered" and not _is_refuse(t.get("answer") or "")
                )
                error_count += room_errors
                total_count += len(out.get("turns", []))
                # Stop if error rate > 10% and at least 50 turns done
                if total_count >= 50 and error_count / total_count > 0.10:
                    print(
                        f"  [ABORT] Error rate {error_count}/{total_count}"
                        f" ({error_count/total_count:.1%}) > 10% — stopping"
                    )
                    raise RuntimeError("Error rate exceeded 10% threshold")
                print(
                    f"  [{idx:>3}/{len(rooms)}] DONE  {room['id']}"
                    f" in {time.time() - t0:.1f}s"
                    f" — {room_answered}/{len(out.get('turns', []))} answered"
                    f" — {room_errors} errors"
                )
                completed.append(out)
                return out

        try:
            outs = await asyncio.gather(*[_run_one(i + 1, r) for i, r in enumerate(rooms)])
            results["rooms"] = list(outs)
        except RuntimeError as exc:
            print(f"[WARN] Gather aborted: {exc}")
            results["rooms"] = completed
            results["aborted"] = True

        elapsed = time.time() - t_start
        print(f"\nAll rooms finished in {elapsed:.1f}s")

        results["summary"] = summarize(results)
        results["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        results["elapsed_s"] = round(elapsed, 1)

        # Write JSON
        ts = time.strftime("%Y%m%d_%H%M%S")
        if args.output:
            out_path = Path(args.output)
        else:
            out_path = Path(f"reports/LOAD_TEST_200ROOM_{ts}.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"[JSON] {out_path}")

        # Write CSV
        csv_path = out_path.with_suffix(".csv")
        if results["rooms"]:
            export_csv(results, csv_path)
            print(f"[CSV]  {csv_path}")

        s = results["summary"]
        w_rooms = worst_rooms(results, top_n=5)

        print("\n" + "=" * 72)
        print(f"SUMMARY ({s['total_rooms']} rooms × {args.questions} q = {s['total_turns']} turns)")
        print("=" * 72)
        print(f"  Real answered:    {s['real_answered']:>5}  ({s['real_answer_rate']:.1%})")
        print(f"  Refused:          {s['refuse']:>5}  ({s['refuse_rate']:.1%})")
        print(f"  Out of scope:     {s['out_of_scope']:>5}")
        print(f"  Blocked:          {s['blocked']:>5}")
        print(f"  Errors:           {s['errors']:>5}")
        print(f"  Avg wall_ms:      {s['avg_wall_ms']:.0f} ms")
        print(f"  Avg dur_ms:       {s['avg_duration_ms']:.0f} ms")
        print(f"  P50 latency:      {s['latency_p50_ms']:.0f} ms")
        print(f"  P95 latency:      {s['latency_p95_ms']:.0f} ms")
        print(f"  P99 latency:      {s['latency_p99_ms']:.0f} ms")
        print(f"  Avg top_score:    {s['avg_top_score']:.4f}")
        print(f"  Avg chunks_used:  {s['avg_chunks_used']:.2f}")
        print(f"  Prompt tokens:    {s['total_prompt_tokens']:,}")
        print(f"  Cached tokens:    {s['total_cached_tokens']:,}  ({s['cache_hit_ratio']:.1%})")
        print(f"  Completion toks:  {s['total_completion_tokens']:,}")
        print(f"  Total cost USD:   ${s['total_cost_usd']:.4f}")
        print(f"\n  Top-score histogram: {s['top_score_histogram']}")
        print(f"\n  Repeat consistency:  {s['repeat_consistency']}")
        print(f"\n  Worst 5 rooms (by refuse rate):")
        for wr in w_rooms:
            print(
                f"    {wr['room_id']:40s}  refuse={wr['refuse_rate']:.0%}"
                f"  top_score={wr['avg_top_score']:.4f}"
            )
        print(f"\n[elapsed] {elapsed:.1f}s total")

        return results


def main():
    p = argparse.ArgumentParser(description="200-room load test for RAGBot")
    p.add_argument("--rooms", type=int, default=200, help="Number of rooms to run (max 200)")
    p.add_argument("--questions", type=int, default=15, help="Questions per room")
    p.add_argument("--output", default="", help="Output JSON path (auto-generated if empty)")
    p.add_argument(
        "--sleep-ms",
        type=int,
        default=500,
        help="Inter-turn sleep ms within a room. Default 500.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=10,
        help="Rooms in parallel (default 10). Each room = unique connect_id.",
    )
    p.add_argument(
        "--debug",
        default="",
        choices=["", "full"],
        help="Debug level. 'full' returns retrieved chunk content.",
    )
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
