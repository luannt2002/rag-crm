#!/usr/bin/env python3
"""Demo bot conversation test — 10 rooms x 20 questions with repeat probes.

Each room:
  - Q1..Q14: sequential topical questions
  - Q15..Q20: 6 more questions, of which 2 are EXACT repeats of Q1/Q3 (probe A)
    and Q5/Q7 (probe B). This tests two things:
      1. Does the bot answer consistently when asked the same thing twice in
         the same conversation (with full history visible)?
      2. Does caching / grounding / guardrail kick in on the repeat?

We also run the same probe pair against a FRESH connect_id to test the
"no history" scenario — does the bot still give a useful answer?

All per-question metrics captured: latency, tokens, cost, answer_type,
chunks_used, top_score, model, intent. Results → JSON file that an
auditor reads next.

Usage:
    python scripts/test_rooms_v2.py
    python scripts/test_rooms_v2.py --rooms 3 --questions 10   # smoke run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BOT_ID = os.getenv("RAGBOT_TEST_BOT_ID", "test-bot-v1")
CHANNEL = "web"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

# ── 10 rooms, each with 20 carefully ordered questions ────────────────────
# Indexes 0..13: topic probes. Indexes 14..19: include 2 repeats of Q0/Q2
# plus 4 continuations. Keep mix: pricing, services, FAQ, edge-case.

ROOMS: list[dict] = [
    {
        "id": "r01-gội-đầu",
        "topic": "Gội đầu",
        "questions": [
            "giá gội đầu thường bao nhiêu",                  # 0  (probe A)
            "gội đầu dưỡng sinh khác gội thường chỗ nào",
            "gội đầu dầu cặp giá bao nhiêu",                 # 2  (probe B)
            "thời gian gội đầu bao lâu",
            "có combo gội đầu không",
            "gội đầu có tặng massage không",
            "mua 10 buổi tặng mấy buổi",
            "bên em nhận thanh toán qua chuyển khoản không",
            "có chỗ gửi xe không",
            "mấy giờ mở cửa",
            "chị muốn gội đầu mai lúc 3h chiều được không",
            "em xưng gì với khách",
            "dưỡng sinh 30 phút có đủ không",
            "sau khi gội đầu có phải kiêng gì không",
            # Repeat probes + continuations
            "giá gội đầu thường bao nhiêu",                  # 14 = 0 (repeat)
            "vậy mua combo 10 buổi tổng tiền bao nhiêu",
            "gội đầu dầu cặp giá bao nhiêu",                 # 16 = 2 (repeat)
            "có voucher giảm giá không",
            "combo 30 buổi được giảm bao nhiêu",
            "em cảm ơn chị",
        ],
    },
    {
        "id": "r02-chăm-sóc-da",
        "topic": "Chăm sóc da chuyên sâu",
        "questions": [
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
            "sau khi làm da có kiêng ra nắng không",
            "một liệu trình mấy buổi",
            "có tư vấn miễn phí không",
            "em muốn đặt lịch thì liên hệ số nào",
            "chăm sóc da mặt bên em có gói nào",             # 14 (repeat of 0)
            "gói 199k là gì",
            "da em hay nổi mụn thì nên làm gì",              # 16 (repeat of 2)
            "sau khi trị mụn cần chăm sóc gì",
            "giá trị liệu mụn cả liệu trình bao nhiêu",
            "cảm ơn bạn",
        ],
    },
    {
        "id": "r03-triệt-lông",
        "topic": "Triệt lông",
        "questions": [
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
            "bao lâu thì lông biến mất hoàn toàn",
            "bà bầu có triệt được không",
            "có khuyến mãi combo triệt không",
            "triệt lông mặt có được không",
            "triệt lông nách giá bao nhiêu",                 # 14 (repeat)
            "nếu da em đang bị mụn có triệt được không",
            "triệt lông tay chân combo giá sao",             # 16 (repeat)
            "10 buổi triệt nách tổng tiền bao nhiêu",
            "chị đặt 5 buổi được không",
            "cảm ơn em",
        ],
    },
    {
        "id": "r04-massage",
        "topic": "Massage",
        "questions": [
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
            "đi một mình hay đi theo nhóm",
            "có ưu đãi cho khách mới không",
            "massage cổ vai gáy giá bao nhiêu",              # 14 (repeat)
            "1 combo 5 buổi giá bao nhiêu",
            "thời gian massage bao lâu",                     # 16 (repeat)
            "bên em có nhận thanh toán thẻ không",
            "đi combo lợi hơn lẻ bao nhiêu",
            "ok em cảm ơn",
        ],
    },
    {
        "id": "r05-hỏi-chung-chung",
        "topic": "Hỏi chung chung & dò khả năng clarify",
        "questions": [
            "bên em có gì",
            "dịch vụ bên em",
            "em muốn tư vấn",
            "chị mới biết shop lần đầu",
            "có loại dịch vụ nào cho mặt",
            "có loại dịch vụ nào cho body",
            "loại nào rẻ nhất",
            "loại nào được ưa chuộng nhất",
            "mới mở à",
            "shop ở đâu",
            "có mấy chi nhánh",
            "có app đặt lịch không",
            "có fanpage không",
            "em tư vấn giúp chị dịch vụ phù hợp nhé",
            "bên em có gì",                                   # 14 (repeat of 0)
            "chị thích mấy thứ như chăm sóc mặt",
            "dịch vụ bên em",                                 # 16 (repeat of 1)
            "chị muốn trẻ hóa da, nên làm gì",
            "chị quan tâm giảm stress",
            "ok cảm ơn em",
        ],
    },
    {
        "id": "r06-flow-giá-và-quy-trình",
        "topic": "Hỏi giá + quy trình xen kẽ",
        "questions": [
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
            "gội đầu bao lâu một buổi",
            "massage bao lâu một buổi",
            "giá triệt lông nách",                           # 14 (repeat)
            "nhớ chị yêu cầu gì không",
            "giá chăm sóc da chuyên sâu",                    # 16 (repeat)
            "chị muốn thanh toán sau dịch vụ",
            "ok chị đặt 2 gói thử",
            "hẹn gặp em",
        ],
    },
    {
        "id": "r07-dấu-hiệu-cá-nhân",
        "topic": "Khách kể vấn đề cá nhân → kiểm tra empathy",
        "questions": [
            "da em bị mụn",
            "em bị đau vai gáy lâu rồi",
            "lông nách em rậm quá",
            "tóc em gãy rụng",
            "em stress quá",
            "da em sạm",
            "em mới sinh xong",
            "chị 40t, da bắt đầu chảy",
            "em muốn trẻ hơn",
            "chị có khuyến mãi cho lần đầu không",
            "chị sợ đau",
            "chị đang cho con bú có làm được không",
            "chị sắp đi biển",
            "da em dầu, lỗ chân lông to",
            "da em bị mụn",                                  # 14 (repeat)
            "chị nên chọn dịch vụ nào",
            "em bị đau vai gáy lâu rồi",                     # 16 (repeat)
            "massage 1 buổi đỡ không",
            "giờ chị muốn thử, đặt luôn được không",
            "ok em",
        ],
    },
    {
        "id": "r08-out-of-scope",
        "topic": "Out-of-scope + chuyển hướng",
        "questions": [
            "thời tiết hôm nay thế nào",
            "bitcoin giờ bao nhiêu",
            "crypto có nên đầu tư",
            "bóng đá tối nay ai đá",
            "chị Kim ơi",
            "em tên gì",
            "em bao nhiêu tuổi",
            "cho em hỏi công thức chả giò",
            "chị có yêu em không",
            "em làm thơ giúp chị",
            "1 + 1 bằng mấy",
            "em biết gì về toán",
            "chị có thấy vui không",
            "em biết cách uống cafe không",
            "thời tiết hôm nay thế nào",                     # 14 (repeat)
            "giờ mình nói chuyện dịch vụ được chưa",
            "bitcoin giờ bao nhiêu",                         # 16 (repeat)
            "ok thôi không nói nữa, bên em có gì",
            "ok chị đặt dịch vụ chăm sóc da",
            "cảm ơn em",
        ],
    },
    {
        "id": "r09-context-đại-từ",
        "topic": "Kiểm tra nhớ ngữ cảnh & đại từ",
        "questions": [
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
            "có trả góp không",
            "nếu trả tiền luôn có giảm thêm không",
            "giá chăm sóc da chuyên sâu",                    # 14 (repeat)
            "nhắc lại chị nghe tên 3 dịch vụ em vừa nói",
            "cái đó bao lâu một buổi",                       # 16 (repeat)
            "ok chị book",
            "mấy giờ mai có lịch",
            "hẹn gặp em nhé",
        ],
    },
    {
        "id": "r10-negative-edge",
        "topic": "Bất lịch sự + chốt hạn chế",
        "questions": [
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
            "em mất lịch sự quá",
            "chị sẽ báo cáo",
            "giá gội đầu",                                   # 14 (repeat)
            "ok em giải thích lại giá chị nghe",
            "sao đắt thế",                                   # 16 (repeat)
            "ok chị sẽ cân nhắc",
            "thôi được chị đặt 1 buổi thử",
            "cảm ơn em đã kiên nhẫn",
        ],
    },
]

REPEAT_PAIRS = [(0, 14), (2, 16)]  # (original_idx, repeat_idx) per room

# ── HTTP helpers ───────────────────────────────────────────────────────────

async def get_self_token(client: httpx.AsyncClient) -> str:
    r = await client.get(f"{BASE_URL}{SELF_TOKEN_PATH}")
    r.raise_for_status()
    return r.json()["token"]


async def ask(
    client: httpx.AsyncClient,
    token: str,
    *,
    bot_id: str,
    channel: str,
    connect_id: str,
    question: str,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{BASE_URL}{CHAT_PATH}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={
                "bot_id": bot_id,
                "channel_type": channel,
                "connect_id": connect_id,
                "question": question,
            },
            timeout=60.0,
        )
        wall_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}", "_wall_ms": wall_ms, "body": r.text[:400]}
        body = r.json()
    except Exception as exc:
        return {"_error": str(exc)[:300], "_wall_ms": (time.perf_counter() - t0) * 1000}

    # API shape has either flat fields or {"data": {...}}. Normalize.
    data = body.get("data") if isinstance(body.get("data"), dict) else body
    return {
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


# ── Runner ─────────────────────────────────────────────────────────────────

async def run_room(
    client: httpx.AsyncClient,
    token: str,
    *,
    bot_id: str,
    channel: str,
    room: dict,
    question_limit: int,
) -> dict:
    # Unique connect_id per test run isolates each room's history from
    # previous runs (connect_id is the history key). No need to DELETE the
    # bot's chat table — each room is already its own conversation thread.
    connect_id = f"test-{room['id']}-{int(time.time())}"

    questions = room["questions"][:question_limit]
    turns: list[dict] = []
    for i, q in enumerate(questions):
        resp = await ask(client, token, bot_id=bot_id, channel=channel, connect_id=connect_id, question=q)
        resp["_idx"] = i
        resp["_question"] = q
        turns.append(resp)
        # Small jitter to avoid hammering
        await asyncio.sleep(0.2)

    # No-history repeat: same probe Q, but a DIFFERENT connect_id with no
    # prior chat. Checks cold-start answer quality.
    no_history_probes = []
    for orig_idx, _repeat_idx in REPEAT_PAIRS:
        if orig_idx >= len(questions):
            continue
        # Unique connect_id = fresh history (no clear needed)
        fresh_conn = f"test-{room['id']}-cold-{orig_idx}-{int(time.time())}"
        resp = await ask(
            client, token, bot_id=bot_id, channel=channel,
            connect_id=fresh_conn, question=questions[orig_idx],
        )
        resp["_idx"] = orig_idx
        resp["_question"] = questions[orig_idx]
        resp["_cold_start"] = True
        no_history_probes.append(resp)
        await asyncio.sleep(0.2)

    return {
        "room_id": room["id"],
        "topic": room["topic"],
        "n_turns": len(turns),
        "turns": turns,
        "cold_start_probes": no_history_probes,
    }


def summarize(results: dict) -> dict:
    rooms = results["rooms"]
    all_turns = [t for r in rooms for t in r["turns"]]
    answered = [t for t in all_turns if t.get("answer_type") == "answered"]
    blocked = [t for t in all_turns if t.get("answer_type") == "blocked"]
    errored = [t for t in all_turns if t.get("_error")]
    oos = [t for t in all_turns if t.get("answer_type") == "out_of_scope"]
    no_ctx = [t for t in all_turns if t.get("answer_type") == "no_context"]

    def _avg(key, src):
        vals = [t.get(key) or 0 for t in src if t.get(key) is not None]
        return round(sum(vals) / len(vals), 2) if vals else 0

    total_cost = sum(t.get("cost_usd") or 0 for t in all_turns)
    total_prompt_tokens = sum((t.get("tokens") or {}).get("prompt", 0) for t in all_turns)
    total_completion_tokens = sum((t.get("tokens") or {}).get("completion", 0) for t in all_turns)

    return {
        "total_turns": len(all_turns),
        "answered": len(answered),
        "blocked": len(blocked),
        "out_of_scope": len(oos),
        "no_context": len(no_ctx),
        "errors": len(errored),
        "answer_rate": round(len(answered) / max(len(all_turns), 1), 3),
        "avg_duration_ms": _avg("duration_ms", all_turns),
        "avg_chunks_used": _avg("chunks_used", all_turns),
        "avg_top_score": _avg("top_score", all_turns),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cost_usd": round(total_cost, 6),
    }


async def main_async(args):
    rooms = ROOMS[:args.rooms]
    async with httpx.AsyncClient() as client:
        token = await get_self_token(client)
        print(f"Token acquired, running {len(rooms)} rooms x {args.questions} questions each")
        results = {"rooms": [], "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        for i, room in enumerate(rooms, 1):
            print(f"\n[{i}/{len(rooms)}] {room['id']} — {room['topic']}")
            out = await run_room(
                client, token, bot_id=BOT_ID, channel=CHANNEL,
                room=room, question_limit=args.questions,
            )
            for turn in out["turns"]:
                marker = "✓" if turn.get("answer_type") == "answered" else "✗"
                ans_preview = (turn.get("answer") or turn.get("_error") or "")[:60]
                print(f"  {turn['_idx']:>2} {marker} [{turn.get('answer_type','err')}] {ans_preview}")
            results["rooms"].append(out)

        results["summary"] = summarize(results)
        results["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        out_path = Path(args.output) if args.output else Path(
            f"reports/test_run_{time.strftime('%Y%m%d_%H%M%S')}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)

        s = results["summary"]
        print("\n" + "=" * 70)
        print(f"SUMMARY ({len(rooms)} rooms x {args.questions} questions)")
        print("=" * 70)
        print(f"  Total turns:      {s['total_turns']}")
        print(f"  Answered:         {s['answered']}  ({s['answer_rate']:.1%})")
        print(f"  Blocked:          {s['blocked']}")
        print(f"  Out of scope:     {s['out_of_scope']}")
        print(f"  No context:       {s['no_context']}")
        print(f"  Errors:           {s['errors']}")
        print(f"  Avg duration:     {s['avg_duration_ms']:.0f} ms")
        print(f"  Avg chunks used:  {s['avg_chunks_used']:.2f}")
        print(f"  Avg top_score:    {s['avg_top_score']:.4f}")
        print(f"  Prompt tokens:    {s['total_prompt_tokens']:,}")
        print(f"  Completion toks:  {s['total_completion_tokens']:,}")
        print(f"  Total cost USD:   ${s['total_cost_usd']:.4f}")
        print(f"\nReport: {out_path}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--rooms", type=int, default=10)
    p.add_argument("--questions", type=int, default=20)
    p.add_argument("--output", default="")
    args = p.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
