"""Enrich a qa_format report with FULL ground-truth references.

The QA-set only carries short `must_contain` facts (e.g. "800.000, 1.000.000"),
which is too terse to judge correctness (HuyPT feedback). This synthesizes a
COMPLETE correct-answer sentence per question — grounded in the corpus source
chunk (answer_source_chunk) + the required facts — so `reference` reads like the
qa_4docs standard (a full sample correct answer), not a keyword list.

Reference is built from the SOURCE CHUNK (ground-truth), NOT the bot answer, so
it stays an independent gold answer. Parallel (semaphore N=8) per the
feedback_ragas_parallel rule.

Usage: PYTHONPATH=. python scripts/enrich_references.py <date>
       → reports/QA_FORMAT_REPORT_<date>.json (reference rewritten long; short
         facts kept as reference_facts)
"""
from __future__ import annotations
import asyncio
import json
import os
import sys

import litellm

JUDGE = "gpt-4.1-mini"
SEM = asyncio.Semaphore(8)


def _prompt(question: str, facts: str, source: str) -> str:
    return (
        "Viết CÂU TRẢ LỜI MẪU ĐÚNG (ground-truth) cho câu hỏi, NGẮN GỌN nhưng ĐẦY ĐỦ "
        "(2-5 câu), CHỈ dựa trên ĐOẠN NGUỒN + CÁC FACTS BẮT BUỘC bên dưới. Viết tự nhiên, "
        "nêu rõ mọi con số/tên/điều khoản bắt buộc. KHÔNG thêm thông tin ngoài nguồn. "
        "KHÔNG mở đầu kiểu 'Dựa trên tài liệu...', trả lời thẳng.\n\n"
        f"CÂU HỎI:\n{question}\n\n"
        f"FACTS BẮT BUỘC (phải có đủ):\n{facts}\n\n"
        f"ĐOẠN NGUỒN (corpus):\n{source[:2500]}\n\n"
        "CÂU TRẢ LỜI MẪU ĐÚNG:"
    )


async def _gen(q: dict) -> None:
    facts = q.get("reference", "") or ""
    source = q.get("answer_source_chunk") or ""  # raw corpus block (string)
    q["reference_facts"] = facts  # keep the short keyword facts
    if not source.strip():
        # No corpus source (computed / data gap) → keep facts as reference.
        return
    async with SEM:
        try:
            r = await litellm.acompletion(
                model=JUDGE,
                messages=[{"role": "user", "content": _prompt(q["question"], facts, source)}],
                temperature=0.0, max_tokens=400,
                api_key=os.environ.get("OPENAI_API_KEY"),
            )
            full = (r.choices[0].message.content or "").strip()
            if len(full) > len(facts):
                q["reference"] = full
        except Exception as exc:  # noqa: BLE001 — keep facts on judge failure
            print(f"  ! {q['id']}: {type(exc).__name__}", file=sys.stderr)


async def main() -> None:
    date = sys.argv[1]
    path = f"reports/QA_FORMAT_REPORT_{date}.json"
    rep = json.load(open(path, encoding="utf-8"))
    qs = [q for d in rep["documents"] for q in d["questions"]]
    print(f"enriching {len(qs)} references (LLM from source chunks, parallel)...")
    await asyncio.gather(*[_gen(q) for q in qs])
    rep["_field_guide"]["reference"] = (
        "Câu trả lời MẪU ĐÚNG đầy đủ (ground-truth), tổng hợp từ đoạn nguồn corpus + "
        "facts bắt buộc — dùng để chấm. reference_facts = các keyword/số bắt buộc gốc."
    )
    rep["_field_guide"]["reference_facts"] = "Các fact/số keyword bắt buộc (must_contain gốc, ngắn)."
    json.dump(rep, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    n_long = sum(1 for q in qs if len(q.get("reference", "")) > len(q.get("reference_facts", "")))
    print(f"done: {n_long}/{len(qs)} references expanded to full answers → {path}")


if __name__ == "__main__":
    asyncio.run(main())
