"""Deep answer-quality verify — traces query→understand→retrieve→chunk→LLM→answer per
question, scores the LLM answer against a ground-truth keyword, and repeats N rounds
to check consistency. Answers the question "does the whole flow produce a CORRECT
answer (not just 'answered')?" with evidence at every step (rule #0).

    set -a && source .env && set +a
    python scripts/verify_answer_quality.py [rounds]
"""
from __future__ import annotations

import asyncio
import sys

import httpx

TEST = "http://localhost:3004/api/ragbot/test"

# (bot, question, ground_truth) — gt: a keyword that MUST appear / "REFUSE" / "LIST"
QUESTIONS = [
    ("test-spa-id", "Giá triệt lông vùng mép", "129"),
    ("test-spa-id", "Giá massage cổ vai gáy 90 phút", "500"),
    ("test-spa-id", "Dịch vụ nào rẻ nhất", "60"),
    ("test-spa-id", "Có làm giảm béo công nghệ Mỹ không", "REFUSE"),
    ("test-spa-id", "Cho mình xem toàn bộ danh sách dịch vụ kèm giá", "LIST"),
    ("chinh-sach-xe", "Lốp Rovelo nào rẻ nhất", "648"),
    ("chinh-sach-xe", "Giá lốp 195/65R15", "195"),
    ("chinh-sach-xe", "Chính sách bảo hành lốp thế nào", "bảo hành"),
    ("thong-tu-09-2020-tt-nhnn", "Điều 4 quy định về gì", "phân loại"),
    ("thong-tu-09-2020-tt-nhnn", "Điều 18 nói về gì", "trung tâm dữ liệu"),
    ("thong-tu-09-2020-tt-nhnn", "Thông tư có những chương nào", "3"),
]
_REFUSE = ("chưa thấy", "chưa tìm thấy", "chưa có thông tin", "liên hệ hotline")


def _score(answer: str, gt: str) -> bool:
    a = answer.lower()
    refused = any(x in a for x in _REFUSE)
    if gt == "REFUSE":
        return refused
    if gt == "LIST":
        return (answer.count("\n-") >= 3 or answer.count("\n•") >= 3) and not refused  # noqa: PLR2004
    return (gt.lower() in a) and not refused


async def one_round(c: httpx.AsyncClient, h: dict, rnd: int) -> int:
    correct = 0
    print(f"\n{'='*82}\nLƯỢT {rnd}\n{'='*82}")
    for i, (bot, q, gt) in enumerate(QUESTIONS):
        r = await c.post(f"{TEST}/chat", json={"bot_id": bot, "channel_type": "web",
                         "question": q, "connect_id": f"vaq-{rnd}-{i}"}, headers=h)
        d = r.json()
        dbg = d.get("debug", {}) or {}
        a = d.get("answer") or ""
        ok = _score(a, gt)
        correct += ok
        # trace: understand → retrieve → LLM answer
        print(f"{'✅' if ok else '🔴'} [{bot[:10]}] {q[:34]}")
        print(f"    Q1 intent={dbg.get('intent')} rewrite={dbg.get('rewritten_query')!r}")
        print(f"    Q3 top_k={dbg.get('top_k')} score_max={dbg.get('score_max')} (route={'stats' if (dbg.get('top_k') or 0)==1 else 'vector'})")
        print(f"    Q7 LLM[{dbg.get('model')}] type={d.get('answer_type')} | gt={gt}")
        print(f"    💬 {a[:120]}")
    print(f"\n→ LƯỢT {rnd}: ĐÚNG ground-truth {correct}/{len(QUESTIONS)}")
    return correct


async def main() -> None:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    async with httpx.AsyncClient(timeout=90.0) as c:
        tok = (await c.get(f"{TEST}/tokens/self")).json()["token"]
        h = {"Authorization": f"Bearer {tok}"}
        scores = [await one_round(c, h, r + 1) for r in range(rounds)]
    n = len(QUESTIONS)
    print(f"\n{'█'*82}")
    print(f"TỔNG {rounds} lượt × {n} câu: " + " · ".join(f"L{i+1}={s}/{n}" for i, s in enumerate(scores)))
    print(f"  → consistency: {'ỔN ĐỊNH' if len(set(scores))==1 else 'CÓ DAO ĐỘNG'} | trung bình {sum(scores)/rounds:.1f}/{n}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except httpx.HTTPError as e:
        print(f"HTTP error: {e}")
        sys.exit(1)
