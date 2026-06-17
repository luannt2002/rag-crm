"""Build a RAGAS load-test report in the `qa_4docs` JSON structure.

Output mirrors the diagnostic-QA-set shape (documents[] -> questions[] with
id / category / question / answer / reference), but each "document" is a live
bot and each question carries the RAG's ACTUAL answer plus the ground-truth +
the RAGAS verdict + per-step fail label.

Field mapping vs the qa_4docs input file:
  - documents[].id          = bot_id
  - questions[].category    = câu shape (factual / comparison / aggregation ...)
  - questions[].question    = the question asked
  - questions[].answer      = the RAG's actual response (bot trả lời)
  - questions[].reference   = the ground-truth required facts (đáp án đúng)
  + questions[].verdict / answer_correctness / faithfulness / fail_step (report extras)

Source: reports/MULTISTEP_MASTER_DASHBOARD.md (§4 forensic blocks).
Usage: python scripts/build_qa_format_report.py <date>  → reports/QA_FORMAT_REPORT_<date>.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

DASH = Path("reports/MULTISTEP_MASTER_DASHBOARD.md")

_Q_HEAD = re.compile(
    r"^## Q(\d+) \[([^\]]+)\]\s+(\S+(?:\s\S+)?)\s+(?:coverage=(?P<cov>\S+)\s+)?"
    r"faithfulness=([0-9.]+)\s+"
    r"answer_correctness=([0-9.]+)\s+chunks_used=(\S+)\s+intent=(\S+)\s+decomposed=(\S+)"
)


def _clip(s: str, n: int = 1200) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> None:
    date = sys.argv[1] if len(sys.argv) > 1 else "unknown"
    text = DASH.read_text(encoding="utf-8")
    m_tot = re.search(r"faithfulness \*\*([0-9.]+)\*\* · answer_correctness \*\*([0-9.]+)\*\*", text)
    m_cov = re.search(r"COVERAGE \*\*([0-9.]+|n/a)\*\*", text)
    m_chuan = re.search(r"CHUẨN (\d+) \((\d+)%\)", text)
    sec4 = text.split("## §4")[1] if "## §4" in text else text
    bot_blocks = re.split(r"\n# 🤖 ", sec4)
    documents = []
    for bb in bot_blocks[1:]:
        bot_id = bb.split("\n", 1)[0].strip()
        q_blocks = re.split(r"\n## Q", bb)
        questions = []
        for qb in q_blocks[1:]:
            mh = _Q_HEAD.match("## Q" + qb.split("\n", 1)[0])
            if not mh:
                continue
            # cov is an optional named group (4); positional faith/corr/... shift
            # to 5..9 when present, so read everything by explicit number/name.
            idx, cat, verdict = mh.group(1), mh.group(2), mh.group(3)
            cov = mh.group("cov")
            faith, corr, n_chunks, intent, decomp = (
                mh.group(5), mh.group(6), mh.group(7), mh.group(8), mh.group(9),
            )
            body = qb

            def _grab(label: str) -> str:
                m = re.search(rf"\*\*{re.escape(label)}\*\* (.+?)(?:\n\n|\n\*\*|\Z)", body, re.S)
                return _clip(m.group(1)) if m else ""

            def _grab_block(label: str) -> str:
                """Grab a multi-line block (with blank lines) until the next bold
                label / question header — for raw chunk text under a label."""
                m = re.search(
                    rf"\*\*{re.escape(label)}\*\*\n(.+?)(?=\n\*\*|\n## Q|\Z)", body, re.S
                )
                return _clip(m.group(1).strip(), 1500) if m else ""

            def _grab_chunks(label: str, max_n: int = 5) -> list:
                """Parse the retrieved-chunk block under a label into structured
                entries [doc#idx score=X] + chunk_context + preview so a reviewer
                can SEE which sources the answer was based on (verify correctness)."""
                m = re.search(rf"\*\*{re.escape(label)}\*\*\n(.+?)(?:\n\*\*|\Z)", body, re.S)
                if not m:
                    return []
                parts = re.split(r"\n?\[([^\]]+?)#(\d+) score=([0-9.]+)\]", m.group(1))
                out, i = [], 1
                while i + 3 <= len(parts) and len(out) < max_n:
                    doc, idx, score, btext = parts[i], parts[i + 1], parts[i + 2], parts[i + 3]
                    mc = re.search(r"<chunk_context>(.+?)</chunk_context>", btext, re.S)
                    ctx = mc.group(1).strip() if mc else ""
                    prev = re.sub(r"<chunk_context>.+?</chunk_context>", "", btext, flags=re.S).strip()
                    out.append({
                        "doc": doc.strip(), "chunk_index": int(idx), "score": float(score),
                        "context": _clip(ctx, 200), "preview": _clip(prev, 600),
                    })
                    i += 4
                return out

            mroot = re.search(r"ROOT CAUSE:\s*(.+?)\*\*", body, re.S)
            fail_step = _clip(mroot.group(1).strip(), 140) if mroot else ""
            is_ok = "CHUẨN" in verdict
            questions.append({
                "id": f"{bot_id}_q{int(idx):02d}",
                "category": cat,
                "question": _grab("Câu hỏi:"),
                "answer": _grab("RAG trả lời (full):"),        # câu BOT thực sự trả lời
                "reference": _grab("Đáp án đúng (facts bắt buộc):"),  # đáp án đúng (ground-truth)
                "verdict": verdict.strip(),
                "coverage": (float(cov) if cov and cov != "n/a" else None),
                "answer_correctness": float(corr),
                "faithfulness": float(faith),
                "intent": intent,
                "decomposed": decomp == "True",
                "fail_step": "" if is_ok else fail_step,
                "n_chunks_used": n_chunks,
                # Top chunks the bot RETRIEVED + used to answer (so reviewer can
                # verify correctness from the source, not just the short reference).
                "top_chunks_retrieved": _grab_chunks("Chunk bot DÙNG (retrieve):"),
                # The corpus chunk text that actually contains the answer
                # (ground-truth source; raw block, not [doc#idx] format).
                "answer_source_chunk": _grab_block("Chunk ĐÚNG (corpus chứa đáp án):"),
            })
        af = round(sum(q["faithfulness"] for q in questions) / len(questions), 3) if questions else 0.0
        ac = round(sum(q["answer_correctness"] for q in questions) / len(questions), 3) if questions else 0.0
        _cv = [q["coverage"] for q in questions if q.get("coverage") is not None]
        cov = round(sum(_cv) / len(_cv), 3) if _cv else None
        documents.append({
            "id": bot_id,
            "doc_type": "live_bot",
            "model": "gpt-4.1-mini",
            "coverage": cov,
            "faithfulness": af,
            "answer_correctness": ac,
            "n_questions": len(questions),
            "questions": questions,
        })
    report = {
        "_description": (
            "RAGAS load-test report (qa_4docs format) — live bots on gpt-4.1-mini, "
            "per-question RAG answer vs ground-truth + retrieved chunks + verdict. "
            "bypass_cache, fixed judge."
        ),
        "_field_guide": {
            "question": "Câu hỏi gửi tới bot.",
            "answer": "Câu BOT THỰC SỰ trả lời (output của RAG).",
            "reference": "Đáp án ĐÚNG / câu trả lời mẫu chuẩn (ground-truth) — dùng để chấm. KHÔNG phải đoạn raw trong tài liệu; là đáp án kỳ vọng.",
            "top_chunks_retrieved": "Các CHUNK bot lấy về (retrieve) + đưa cho LLM để trả lời — gồm doc, chunk_index, score, context, preview. Đây là NGUỒN câu trả lời dựa vào → soi để biết bot trả đúng/sai từ nguồn nào.",
            "answer_source_chunk": "Chunk trong corpus THỰC SỰ chứa đáp án (ground-truth source) — so với top_chunks_retrieved để biết bot có lấy đúng chunk không.",
            "verdict": "✅CHUẨN / 🟡GENERATION (chunk đúng nhưng LLM trả sai/thiếu) / 🔴RETRIEVAL (chunk đáp án không vào top-K) / 🟠HALLU (bịa).",
            "coverage": "% required-fact xuất hiện đúng trong answer (claim-level).",
            "faithfulness": "% claim của answer được grounded trong chunk (0=bịa).",
            "answer_correctness": "Điểm holistic LLM-judge answer vs reference.",
            "fail_step": "Tầng lỗi (retrieval/generation/...) nếu không CHUẨN.",
        },
        "run": {
            "date": date,
            "model": "gpt-4.1-mini",
            "coverage": (float(m_cov.group(1)) if m_cov and m_cov.group(1) != "n/a" else None),
            "faithfulness": float(m_tot.group(1)) if m_tot else None,
            "answer_correctness": float(m_tot.group(2)) if m_tot else None,
            "chuan_count": int(m_chuan.group(1)) if m_chuan else None,
            "chuan_pct": int(m_chuan.group(2)) if m_chuan else None,
            "total_questions": sum(d["n_questions"] for d in documents),
            "n_bots": len(documents),
        },
        "verdict_legend": {
            "✅ CHUẨN": "chunk retrieved + LLM answered correctly",
            "🟡 GENERATION": "chunk retrieved but LLM dropped/miscomputed facts",
            "🔴 RETRIEVAL": "answer-bearing chunk in corpus but not in top-K",
            "🟠 HALLU": "ungrounded claim (auto-label; verify manually)",
            "⚪ DATA": "answer computed / not verbatim in corpus",
        },
        "documents": sorted(
            documents,
            key=lambda d: d["coverage"] if d.get("coverage") is not None else d["answer_correctness"],
        ),
    }
    out = Path(f"reports/QA_FORMAT_REPORT_{date}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out}  ({report['run']['total_questions']} câu / {len(documents)} bot)")


if __name__ == "__main__":
    main()
