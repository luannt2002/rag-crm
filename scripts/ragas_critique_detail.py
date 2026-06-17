"""Per-question content critique: ĐÚNG / SAI / THIẾU / LẠC ĐỀ for every Q of every bot.

Reads the stored answers in reports/MULTISTEP_RAGAS_<bot>.md (no bot re-query),
and for each question asks an LLM judge to produce a structured Vietnamese
critique comparing the bot answer against the ground-truth facts + correct chunk:
  - ĐÚNG  : cái bot trả đúng (cụ thể)
  - SAI   : giá trị/khẳng định SAI (chỉ rõ con số/điều sai)
  - THIẾU : fact bắt buộc còn thiếu
  - LẠC ĐỀ: nội dung thừa / ngoài câu hỏi / sai chủ đề

Output: reports/MULTISTEP_CRITIQUE_DETAIL.md
Usage:  PYTHONPATH=. python scripts/ragas_critique_detail.py
"""
from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import litellm

REPORTS = Path(__file__).parent.parent / "reports"
OUT = REPORTS / "MULTISTEP_CRITIQUE_DETAIL.md"
JUDGE = "gpt-4.1-mini"
SEM = asyncio.Semaphore(6)

_HEAD = re.compile(r"## Q(\d+) \[([a-z_]+)\]\s+(\S+ \S+)\s+faithfulness=([0-9.]+)\s+answer_correctness=([0-9.]+)")


def _field(block: str, label: str, nxt: str) -> str:
    m = re.search(rf"\*\*{re.escape(label)}\*\* (.+?)(?=\n\*\*{re.escape(nxt)}|\Z)", block, re.S)
    return m.group(1).strip() if m else ""


async def _critique(q: str, ans: str, facts: str, chunk: str) -> str:
    prompt = (
        "Bạn là giám khảo RAG khắt khe. So câu trả lời của bot với đáp án đúng "
        "(facts bắt buộc + chunk gốc). Viết phân tích NGẮN GỌN, CỤ THỂ bằng tiếng "
        "Việt theo đúng 4 mục (mỗi mục 1-2 dòng, nếu không có thì ghi 'không'):\n"
        "ĐÚNG: <cái bot trả đúng>\n"
        "SAI: <giá trị/khẳng định sai — chỉ rõ con số/điều sai vs đúng>\n"
        "THIẾU: <fact bắt buộc còn thiếu>\n"
        "LẠC ĐỀ: <nội dung thừa/ngoài câu hỏi/sai chủ đề>\n\n"
        f"CÂU HỎI: {q}\n\nFACTS BẮT BUỘC: {facts}\n\nCHUNK GỐC:\n{chunk[:1500]}\n\n"
        f"BOT TRẢ LỜI:\n{ans[:1500]}\n"
    )
    async with SEM:
        try:
            r = await litellm.acompletion(
                model=JUDGE, messages=[{"role": "user", "content": prompt}],
                temperature=0.0, max_tokens=300, api_key=os.environ.get("OPENAI_API_KEY"),
            )
            return (r.choices[0].message.content or "").strip()
        except Exception as exc:  # noqa: BLE001
            return f"(critique error: {type(exc).__name__})"


async def main() -> None:
    tasks = []
    meta = []
    for f in sorted(REPORTS.glob("MULTISTEP_RAGAS_*.md")):
        bot = f.stem.replace("MULTISTEP_RAGAS_", "")
        text = f.read_text(encoding="utf-8")
        for block in re.split(r"(?=^## Q\d+ \[)", text, flags=re.M):
            h = _HEAD.search(block)
            if not h:
                continue
            q = _field(block, "Câu hỏi:", "RAG")
            ans = _field(block, "RAG trả lời (full):", "Đáp án")
            facts = _field(block, "Đáp án đúng (facts bắt buộc):", "DECISION")
            chunk = _field(block, "Chunk ĐÚNG (corpus chứa đáp án):", "Chunk bot")
            meta.append((bot, int(h.group(1)), h.group(2), h.group(3), float(h.group(5)), q, facts))
            tasks.append(_critique(q, ans, facts, chunk))
    crits = await asyncio.gather(*tasks)

    by_bot: dict[str, list] = {}
    for (bot, qn, qtype, verdict, corr, q, facts), crit in zip(meta, crits):
        by_bot.setdefault(bot, []).append((qn, qtype, verdict, corr, q, facts, crit))

    L = ["# CRITIQUE CHI TIẾT — ĐÚNG / SAI / THIẾU / LẠC ĐỀ từng câu từng bot\n"]
    for bot in sorted(by_bot):
        L.append(f"\n## 🤖 {bot}\n")
        for qn, qtype, verdict, corr, q, facts, crit in sorted(by_bot[bot]):
            L.append(f"### Q{qn} [{qtype}] — {verdict} (correct={corr:.2f})")
            L.append(f"**Câu hỏi:** {q}\n")
            L.append(f"**Đáp án đúng:** {facts}\n")
            L.append(crit + "\n")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print(f"💾 {OUT}  ({len(meta)} câu)")


if __name__ == "__main__":
    asyncio.run(main())
