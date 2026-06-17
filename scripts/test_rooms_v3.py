#!/usr/bin/env python3
"""Demo bot conversation test — 20 rooms × 15 questions with repeat probes.

Per user brief (2026-04-23):
- 20 rooms, each room 15 questions
- Questions 12-14 include 2 exact repeats of earlier probes (0, 2) to
  test consistency within one conversation
- Additionally each room has 2 cold-start probes (fresh connect_id,
  no history) asking the same probe questions to measure
  answer degradation without context

Per-turn metrics captured via response.debug (P16 Wave 1 Phase 9):
  - latency, duration, chunks_used, top_score
  - prompt/completion/cached tokens (P16 Wave 2 Phase 4)
  - rewritten_query, query_decomposed, parents_expanded_count
  - guardrail_flags (input + output)

Results → JSON for the auditor.

Usage:
    python scripts/test_rooms_v3.py
    python scripts/test_rooms_v3.py --rooms 5 --questions 10  # smoke
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BOT_ID = os.getenv("RAGBOT_TEST_BOT_ID", "test-bot-v1")
CHANNEL = "web"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

# 20 rooms, each with 15 questions. Indices 12, 14 repeat indices 0, 2.
ROOMS: list[dict] = [
    {"id": f"r{n:02d}-{topic_id}", "topic": topic, "questions": qs}
    for n, topic_id, topic, qs in [
        (1, "gội-đầu", "Gội đầu", [
            "giá gội đầu thường bao nhiêu",                         # 0 probe A
            "gội đầu dưỡng sinh khác gội thường chỗ nào",
            "gội đầu dầu cặp giá bao nhiêu",                        # 2 probe B
            "thời gian gội đầu bao lâu",
            "có combo gội đầu không",
            "gội đầu có tặng massage không",
            "mua 10 buổi tặng mấy buổi",
            "em xưng gì với khách",
            "có chỗ gửi xe không",
            "mấy giờ mở cửa",
            "dưỡng sinh 30 phút có đủ không",
            "sau khi gội đầu có phải kiêng gì không",
            "giá gội đầu thường bao nhiêu",                         # 12 repeat of 0
            "combo 30 buổi được giảm bao nhiêu",
            "gội đầu dầu cặp giá bao nhiêu",                        # 14 repeat of 2
        ]),
        (2, "chăm-sóc-da", "Chăm sóc da chuyên sâu", [
            "chăm sóc da mặt bên em có gói nào",
            "giá chăm sóc da bao nhiêu",
            "da em hay nổi mụn thì nên làm gì",
            "có trị mụn ẩn không",
            "hydra facial là gì",
            "quy trình chăm sóc da gồm mấy bước",
            "mất bao lâu mới thấy da đẹp lên",
            "có bảo hành kết quả không",
            "mỹ phẩm bên em có an toàn không",
            "da nhạy cảm có làm được không",
            "một liệu trình mấy buổi",
            "có tư vấn miễn phí không",
            "chăm sóc da mặt bên em có gói nào",                    # 12
            "sau khi trị mụn cần chăm sóc gì",
            "da em hay nổi mụn thì nên làm gì",                     # 14
        ]),
        (3, "triệt-lông", "Triệt lông", [
            "triệt lông nách giá bao nhiêu",
            "triệt lông vĩnh viễn có thật không",
            "triệt lông tay chân combo giá sao",
            "triệt bikini có đau không",
            "công nghệ triệt lông bên em dùng loại gì",
            "một combo triệt lông mấy buổi",
            "triệt lông nam có được không",
            "giữa các buổi cách mấy tuần",
            "sau triệt có phải kiêng tắm nắng không",
            "triệt lông có gây rối loạn hormone không",
            "bà bầu có triệt được không",
            "có khuyến mãi combo triệt không",
            "triệt lông nách giá bao nhiêu",                        # 12
            "10 buổi triệt nách tổng tiền bao nhiêu",
            "triệt lông tay chân combo giá sao",                    # 14
        ]),
        (4, "massage", "Massage", [
            "massage cổ vai gáy giá bao nhiêu",
            "massage body toàn thân bao nhiêu tiền",
            "thời gian massage bao lâu",
            "massage có giúp đỡ đau lưng không",
            "bên em có massage chân không",
            "massage có dùng tinh dầu không",
            "nhân viên nam hay nữ massage",
            "có phòng VIP không",
            "massage bầu có được không",
            "bên em có dịch vụ massage mặt không",
            "massage cổ vai gáy có khuyến mãi không",
            "em đặt lịch qua đâu",
            "massage cổ vai gáy giá bao nhiêu",                     # 12
            "1 combo 5 buổi giá bao nhiêu",
            "thời gian massage bao lâu",                            # 14
        ]),
        (5, "hỏi-chung-chung", "Hỏi chung chung & dò clarify", [
            "bên em có gì",
            "dịch vụ bên em",
            "em muốn tư vấn",
            "chị mới biết shop lần đầu",
            "có loại dịch vụ nào cho mặt",
            "có loại dịch vụ nào cho body",
            "loại nào rẻ nhất",
            "loại nào được ưa chuộng nhất",
            "shop ở đâu",
            "có mấy chi nhánh",
            "có fanpage không",
            "có app đặt lịch không",
            "bên em có gì",                                         # 12
            "chị thích mấy thứ như chăm sóc mặt",
            "dịch vụ bên em",                                       # 14
        ]),
        (6, "giá-quy-trình", "Hỏi giá + quy trình xen kẽ", [
            "giá triệt lông nách",
            "quy trình triệt lông nách gồm mấy bước",
            "giá chăm sóc da chuyên sâu",
            "quy trình chăm sóc da gồm gì",
            "giá gội đầu dưỡng sinh",
            "quy trình gội đầu dưỡng sinh",
            "giá massage cổ vai gáy",
            "quy trình massage cổ vai gáy",
            "em muốn đặt lịch triệt lông nách",
            "em muốn đặt lịch chăm sóc da",
            "giá và quy trình triệt bikini",
            "chi phí combo 10 buổi chăm sóc da",
            "giá triệt lông nách",                                  # 12
            "nhớ chị yêu cầu gì không",
            "giá chăm sóc da chuyên sâu",                           # 14
        ]),
        (7, "empathy-cá-nhân", "Khách kể vấn đề cá nhân → kiểm tra empathy", [
            "da em bị mụn",
            "em bị đau vai gáy lâu rồi",
            "lông nách em rậm quá",
            "tóc em gãy rụng",
            "em stress quá",
            "da em sạm",
            "em mới sinh xong",
            "chị 40t, da bắt đầu chảy",
            "em muốn trẻ hơn",
            "chị sợ đau",
            "chị đang cho con bú có làm được không",
            "da em dầu, lỗ chân lông to",
            "da em bị mụn",                                         # 12
            "chị nên chọn dịch vụ nào",
            "em bị đau vai gáy lâu rồi",                            # 14
        ]),
        (8, "out-of-scope", "Out-of-scope + chuyển hướng", [
            "thời tiết hôm nay thế nào",
            "bitcoin giờ bao nhiêu",
            "crypto có nên đầu tư",
            "bóng đá tối nay ai đá",
            "em tên gì",
            "em bao nhiêu tuổi",
            "cho em hỏi công thức chả giò",
            "chị có yêu em không",
            "em làm thơ giúp chị",
            "1 + 1 bằng mấy",
            "em biết gì về toán",
            "chị có thấy vui không",
            "thời tiết hôm nay thế nào",                            # 12
            "giờ mình nói chuyện dịch vụ được chưa",
            "bitcoin giờ bao nhiêu",                                # 14
        ]),
        (9, "context-đại-từ", "Kiểm tra nhớ ngữ cảnh & đại từ", [
            "giá chăm sóc da chuyên sâu",
            "cái đó bao lâu một buổi",
            "quy trình nó gồm gì",
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
            "cái đó bao lâu một buổi",                              # 14
        ]),
        (10, "negative-edge", "Bất lịch sự + chốt hạn chế", [
            "giá gội đầu",
            "sao đắt thế",
            "chỗ khác rẻ hơn",
            "có đảm bảo không, lỡ hỏng da ai chịu",
            "em trả lời nhanh đi",
            "thôi, đắt quá",
            "chị muốn refund",
            "hôm qua chị làm chưa hài lòng",
            "chị đưa giá 50k em làm không",
            "em bớt đi",
            "em gọi sếp em đi",
            "shop làm ăn kiểu gì",
            "giá gội đầu",                                          # 12
            "ok em giải thích lại giá chị nghe",
            "sao đắt thế",                                          # 14
        ]),
        (11, "follow-up", "Follow-up sau khi đặt", [
            "chị đặt 1 buổi triệt nách",
            "giờ nào còn lịch",
            "chiều mai được không",
            "3h hay 4h",
            "ok chị lấy 3h30",
            "địa chỉ chính xác ở đâu",
            "có gần bến xe Giáp Bát không",
            "có chỗ để xe máy không",
            "chị mang theo gì",
            "có cần đặt cọc không",
            "thanh toán khi nào",
            "chuyển khoản được không",
            "chị đặt 1 buổi triệt nách",                            # 12
            "cảm ơn em nhé",
            "giờ nào còn lịch",                                     # 14
        ]),
        (12, "so-sánh", "So sánh dịch vụ / gói", [
            "gói 199k và 350k khác gì",
            "gói combo 10 buổi với mua lẻ, cái nào lợi",
            "hydra facial với carbon peeling cái nào tốt",
            "triệt Diode Laser với IPL khác nhau chỗ nào",
            "gội đầu thường với dưỡng sinh chênh bao nhiêu phút",
            "massage VIP với massage thường chênh bao nhiêu",
            "combo 10 buổi bên em với spa khác giá bao nhiêu",
            "chăm sóc da lần đầu khác follow-up như thế nào",
            "gội đầu kích thích mọc tóc có khác gội thường",
            "triệt lông mặt với triệt lông tay cái nào đau hơn",
            "chị nên chọn gói nào cho da dầu nhạy cảm",
            "bên em có gói nào cho da 40t",
            "gói 199k và 350k khác gì",                             # 12
            "ok chị thử gói 199k",
            "gói combo 10 buổi với mua lẻ, cái nào lợi",            # 14
        ]),
        (13, "cam-kết-an-toàn", "Cam kết + an toàn", [
            "bên em có chứng chỉ gì không",
            "máy triệt có xuất xứ rõ không",
            "mỹ phẩm bên em có nguồn gốc không",
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
            "máy triệt có xuất xứ rõ không",                        # 14
        ]),
        (14, "combo-ưu-đãi", "Combo + ưu đãi dài hạn", [
            "combo 10 buổi bao nhiêu",
            "combo 30 buổi rẻ hơn bao nhiêu",
            "có flash sale không",
            "Tết có khuyến mãi gì",
            "mua gói cho mẹ có ưu đãi không",
            "giới thiệu bạn bè có hoa hồng không",
            "mua chung 2 người có giảm không",
            "khách vãng lai lần đầu có giảm không",
            "voucher 100k dùng được gì",
            "bên em bán voucher đi không",
            "thẻ thành viên có ích lợi gì",
            "vòng quay may mắn còn không",
            "combo 10 buổi bao nhiêu",                              # 12
            "combo 30 buổi tổng tiền",
            "combo 30 buổi rẻ hơn bao nhiêu",                       # 14
        ]),
        (15, "lịch-hẹn", "Lịch hẹn + đặt chỗ", [
            "mai 3h còn lịch không",
            "tuần này có slot nào đẹp không",
            "thứ 7 đông không",
            "chị muốn đi 2 người được không",
            "có thể đặt online không",
            "đặt rồi hủy có mất phí không",
            "đổi giờ sát nút được không",
            "em ưu tiên khách đặt trước",
            "giờ cao điểm mấy giờ",
            "tầm trưa có người không",
            "chị đến sớm 15p được không",
            "tới trễ 30p có phải đợi không",
            "mai 3h còn lịch không",                                # 12
            "8h tối mai được không",
            "tuần này có slot nào đẹp không",                       # 14
        ]),
        (16, "kỹ-thuật", "Câu hỏi kỹ thuật chuyên sâu", [
            "công nghệ Diode Laser là gì",
            "bước sóng bao nhiêu nm",
            "hydra facial có bao nhiêu bước",
            "dòng năng lượng RF là gì",
            "tẩy tế bào chết bằng acid nào",
            "collagen tiêm bao lâu hấp thu",
            "bên em có máy HIFU không",
            "IPL với Diode khác nhau chỗ nào",
            "làm đẹp bằng tế bào gốc có an toàn",
            "massage có dùng đèn hồng ngoại không",
            "cryotherapy là gì",
            "hydra ballet dùng huyết thanh gì",
            "công nghệ Diode Laser là gì",                          # 12
            "bước sóng bao nhiêu thì an toàn",
            "bước sóng bao nhiêu nm",                               # 14
        ]),
        (17, "kết-hợp", "Kết hợp dịch vụ", [
            "chị muốn làm gội đầu + chăm sóc mặt 1 ngày",
            "triệt lông + chăm sóc da nên làm cái nào trước",
            "có thể làm hydra facial + massage cùng ngày",
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
            "triệt lông + chăm sóc da nên làm cái nào trước",       # 14
        ]),
        (18, "tình-huống-khó", "Tình huống bất ngờ", [
            "chị đang có bầu triệt lông được không",
            "chị đang uống thuốc kháng sinh, chăm sóc da được không",
            "da chị vừa laser xong có làm nữa được",
            "chị vừa peel xong 2 hôm, gội đầu có sao không",
            "có khách 15 tuổi làm được chăm sóc da không",
            "người già 70t triệt lông được không",
            "chị sợ kim, chăm sóc nhưng không được dùng kim",
            "chị chỉ có 30p, làm được gì",
            "chị cần ngay tối nay, có làm được không",
            "chị bị tim mạch làm được massage không",
            "chị vừa mổ 1 tháng, đi chăm sóc da được không",
            "chị đang kinh nguyệt triệt được không",
            "chị đang có bầu triệt lông được không",                # 12
            "chị có ý định mang thai, triệt được không",
            "chị đang uống thuốc kháng sinh, chăm sóc da được không",  # 14
        ]),
        (19, "đánh-giá-chất-lượng", "Hỏi đánh giá + feedback", [
            "bên em có fanpage không",
            "có review của khách không",
            "có livestream không",
            "khách cũ quay lại bao nhiêu %",
            "điểm trung bình google map bao nhiêu",
            "có video before-after không",
            "có video kỹ thuật viên làm không",
            "có group Facebook khách hàng không",
            "review negative xử lý thế nào",
            "khách không ưng trả phí thế nào",
            "có khiếu nại gì không",
            "hồ sơ khách có bảo mật không",
            "bên em có fanpage không",                              # 12
            "link fanpage cho chị xin",
            "có review của khách không",                            # 14
        ]),
        (20, "đặt-câu-mơ-hồ", "Câu mơ hồ / chưa rõ nhu cầu", [
            "chị chưa biết muốn làm gì",
            "cho chị xem tất cả",
            "tư vấn giúp chị",
            "em xem chị hợp gì",
            "em gợi ý đi",
            "chị muốn đẹp hơn",
            "chị muốn khỏe mạnh",
            "cái gì đang hot ở spa",
            "mới nhất bên em có gì",
            "cái nào hiệu quả nhất",
            "cái nào nhanh nhất",
            "cái nào hợp với da dầu",
            "chị chưa biết muốn làm gì",                            # 12
            "cái gì phụ nữ 40 tuổi hay làm",
            "cho chị xem tất cả",                                   # 14
        ]),
    ]
]

REPEAT_PAIRS = [(0, 12), (2, 14)]


async def get_self_token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE_URL}{SELF_TOKEN_PATH}")
    r.raise_for_status()
    return r.json()["token"]


class TokenHolder:
    """Re-fetches self-token on 401. /tokens/self rotates every 5 min
    because it calls regenerate_token() after Redis cache expiry — so a long
    run will hit 401 mid-way. One retry per 401 is enough."""

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
    token_holder: "TokenHolder",
    *,
    bot_id: str,
    channel: str,
    connect_id: str,
    question: str,
    debug: str = "",
) -> dict[str, Any]:
    t0 = time.perf_counter()
    # HARN-3: when debug=="full", POST body includes `debug: "full"` so the
    # API returns `retrieved_chunks_content` (list of {chunk_id, content,
    # source, score}). The auditor reads this to feed REAL chunk text to the
    # LLM judge — otherwise the judge only sees doc NAMES and marks any
    # specific number as hallucinated by default.
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
            headers={"Authorization": f"Bearer {token_holder.value}", "Content-Type": "application/json"},
            json=payload,
            timeout=90.0,
        )
        if r.status_code == 401:
            await token_holder.refresh()
            r = await client.post(
                f"{BASE_URL}{CHAT_PATH}",
                headers={"Authorization": f"Bearer {token_holder.value}", "Content-Type": "application/json"},
                json=payload,
                timeout=90.0,
            )
        wall_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}", "_wall_ms": wall_ms, "body": r.text[:400]}
        body = r.json()
    except Exception as exc:
        return {"_error": str(exc)[:300], "_wall_ms": (time.perf_counter() - t0) * 1000}

    data = body.get("data") if isinstance(body.get("data"), dict) else body
    out = {
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
    # Propagate chunk content only when server sent it (debug=full path).
    # Safe to include unconditionally — when absent, the auditor falls back
    # to the legacy source-names payload.
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
        resp = await ask(client, token_holder, bot_id=bot_id, channel=channel,
                         connect_id=connect_id, question=q, debug=debug)
        resp["_idx"] = i
        resp["_question"] = q
        turns.append(resp)
        await asyncio.sleep(inter_turn_sleep_s)

    cold_probes: list[dict] = []
    for orig_idx, _ in REPEAT_PAIRS:
        if orig_idx >= len(questions):
            continue
        fresh = f"test-{room['id']}-cold-{orig_idx}-{int(time.time() * 1000)}"
        resp = await ask(client, token_holder, bot_id=bot_id, channel=channel,
                         connect_id=fresh, question=questions[orig_idx], debug=debug)
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


def _is_refuse(answer: str) -> bool:
    """Real refuse detection across both old and new OOS templates."""
    if not answer:
        return True
    a = answer.lower()
    return (
        a.startswith("xin loi, toi khong co thong tin")
        or "chưa có thông tin chính xác" in a
        or "khong hop le" in a  # blocked
    )


def summarize(results: dict) -> dict:
    rooms = results["rooms"]
    all_turns = [t for r in rooms for t in r["turns"]]
    real_answered = [t for t in all_turns if t.get("answer_type") == "answered" and not _is_refuse(t.get("answer") or "")]
    refuse_turns = [t for t in all_turns if _is_refuse(t.get("answer") or "")]
    blocked = [t for t in all_turns if t.get("answer_type") == "blocked"]
    oos = [t for t in all_turns if t.get("answer_type") == "out_of_scope"]
    errored = [t for t in all_turns if t.get("_error")]

    def _avg(key, src):
        vals = [t.get(key) or 0 for t in src if t.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    total_prompt = sum((t.get("tokens") or {}).get("prompt", 0) for t in all_turns)
    total_completion = sum((t.get("tokens") or {}).get("completion", 0) for t in all_turns)
    total_cached = sum((t.get("tokens") or {}).get("cached", 0) for t in all_turns)
    total_cost = sum(t.get("cost_usd") or 0 for t in all_turns)

    return {
        "total_turns": len(all_turns),
        "real_answered": len(real_answered),
        "refuse": len(refuse_turns),
        "blocked": len(blocked),
        "out_of_scope": len(oos),
        "errors": len(errored),
        "real_answer_rate": round(len(real_answered) / max(len(all_turns), 1), 3),
        "avg_duration_ms": _avg("duration_ms", all_turns),
        "avg_chunks_used": _avg("chunks_used", all_turns),
        "avg_top_score": _avg("top_score", all_turns),
        "total_prompt_tokens": total_prompt,
        "total_completion_tokens": total_completion,
        "total_cached_tokens": total_cached,
        "cache_hit_ratio": round(total_cached / total_prompt, 3) if total_prompt else 0,
        "total_cost_usd": round(total_cost, 6),
    }


async def main_async(args):
    rooms = ROOMS[:args.rooms]
    concurrency = max(1, int(args.concurrency))
    # httpx.Limits: scale pool to concurrency to avoid connection starvation.
    limits = httpx.Limits(
        max_connections=concurrency * 4,
        max_keepalive_connections=concurrency * 2,
    )
    async with httpx.AsyncClient(limits=limits, timeout=httpx.Timeout(120.0)) as client:
        token = await get_self_token(client)
        token_holder = TokenHolder(client, token)
        print(f"Running {len(rooms)} rooms × {args.questions} questions — concurrency={concurrency}")
        results = {"rooms": [], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}

        sem = asyncio.Semaphore(concurrency)
        t_start = time.time()

        async def _run_one(idx: int, room: dict) -> dict:
            async with sem:
                print(f"  [{idx}/{len(rooms)}] START {room['id']} — {room['topic']}")
                t0 = time.time()
                out = await run_room(
                    client, token_holder, bot_id=BOT_ID, channel=CHANNEL,
                    room=room, question_limit=args.questions,
                    inter_turn_sleep_s=args.sleep_ms / 1000.0,
                    debug=args.debug,
                )
                answered = sum(
                    1 for t in out.get("turns", [])
                    if t.get("answer_type") == "answered" and not _is_refuse(t.get("answer") or "")
                )
                print(
                    f"  [{idx}/{len(rooms)}] DONE  {room['id']} in {time.time() - t0:.1f}s "
                    f"— {answered}/{len(out.get('turns', []))} answered"
                )
                return out

        outs = await asyncio.gather(*[_run_one(i + 1, r) for i, r in enumerate(rooms)])
        # Preserve original room order in output (gather preserves order already).
        results["rooms"] = list(outs)
        print(f"\nAll rooms finished in {time.time() - t_start:.1f}s")

        results["summary"] = summarize(results)
        results["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        out_path = Path(args.output) if args.output else Path(
            f"reports/test_run_20rooms_{time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        s = results["summary"]
        print("\n" + "=" * 72)
        print(f"SUMMARY ({len(rooms)} rooms × {args.questions} questions = {s['total_turns']} turns)")
        print("=" * 72)
        print(f"  Real answered:    {s['real_answered']:>4}  ({s['real_answer_rate']:.1%})")
        print(f"  Refuse:           {s['refuse']:>4}")
        print(f"  Out of scope:     {s['out_of_scope']:>4}")
        print(f"  Blocked:          {s['blocked']:>4}")
        print(f"  Errors:           {s['errors']:>4}")
        print(f"  Avg duration:     {s['avg_duration_ms']:.0f} ms")
        print(f"  Avg chunks used:  {s['avg_chunks_used']:.2f}")
        print(f"  Avg top_score:    {s['avg_top_score']:.4f}")
        print(f"  Prompt tokens:    {s['total_prompt_tokens']:,}")
        print(f"  Cached tokens:    {s['total_cached_tokens']:,}  ({s['cache_hit_ratio']:.1%} of prompt)")
        print(f"  Completion toks:  {s['total_completion_tokens']:,}")
        print(f"  Total cost USD:   ${s['total_cost_usd']:.4f}")
        print(f"  Report: {out_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rooms", type=int, default=20)
    p.add_argument("--questions", type=int, default=15)
    p.add_argument("--output", default="")
    p.add_argument(
        "--sleep-ms",
        type=int,
        default=700,
        help="Inter-turn sleep (ms) within a single room. Default 700.",
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Rooms in parallel. Each room has its own connect_id so "
             "per-user rate-limit is per-room. Default 1 (sequential). "
             "Set to 20 to run all rooms simultaneously.",
    )
    # HARN-3: --debug=full opts into retrieved_chunks_content in response.
    p.add_argument("--debug", default="", choices=["", "full"])
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
