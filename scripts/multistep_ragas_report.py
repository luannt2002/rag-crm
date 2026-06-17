"""Per-bot multi-step evidence report + RAGAS-style LLM-judge scoring.

For ONE bot (sequential — avoids the parallel-burst 503), for each question prints
a full evidence row so the result can be analysed by hand:
  - câu hỏi (question)
  - câu RAG trả (bot answer)
  - đáp án đúng (ground-truth: must_contain literals + the correct corpus chunk text)
  - chunk đúng (the source chunk in DB the answer should come from)
  - chunk bot dùng (the chunks retrieval actually fed the LLM — source previews)

Then it scores each turn with two RAGAS-style metrics, judged by gpt-4.1-mini:
  - faithfulness    = fraction of the answer's claims supported by the USED chunks
  - answer_correctness = how correctly+completely the answer covers the ground-truth

RAGAS scoring explained (what the tool computes):
  * faithfulness ∈ [0,1]: decompose answer into claims; a claim counts if the
    retrieved context entails it. Low = hallucination (claim not in context).
  * answer_correctness ∈ [0,1]: semantic + factual overlap of answer vs the
    reference answer. Low = wrong/incomplete even if faithful to a wrong chunk.
  (Real RAGAS also has context_recall/precision; here we judge the two that
   matter for "did the bot answer the hard question correctly".)

Usage: PYTHONPATH=. python scripts/multistep_ragas_report.py <bot_id>
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

import httpx
import litellm
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

BASE = "http://localhost:3004/api/ragbot/test"
QFILE = Path(__file__).parent / "multistep_questions.json"
JUDGE_MODEL = "gpt-4.1-mini"
# Per-fact claim-level coverage: a required fact counts as covered when the
# semantic judge scores >= this cutoff. Claim-level (one yes/no per fact) is far
# less noisy than the holistic 0..1 answer_correctness judge — Coverage is the
# CLAUDE.md-mandated metric (answer_correct_when_corpus_has_answer / total).
COVERAGE_COVERED_THRESHOLD = 0.5


async def _token(c: httpx.AsyncClient) -> str:
    r = await c.get(f"{BASE}/tokens/self", timeout=10)
    return r.json()["token"]


async def _ask(c: httpx.AsyncClient, bot: str, q: str) -> dict:
    for attempt in range(4):
        tok = await _token(c)
        r = await c.post(
            f"{BASE}/chat",
            json={"bot_id": bot, "channel_type": "web", "question": q, "bypass_cache": True},
            headers={"Authorization": f"Bearer {tok}"}, timeout=120,
        )
        if r.status_code == 503:
            await asyncio.sleep(4 * (attempt + 1)); continue
        if r.status_code != 200:
            return {"_error": f"HTTP {r.status_code}: {r.text[:120]}"}
        d = r.json()
        return d.get("data") if isinstance(d, dict) and "data" in d else d
    return {"_error": "503 after retries"}


async def _judge(prompt: str) -> float:
    """Return a 0..1 score from the LLM judge (robust parse)."""
    try:
        resp = await litellm.acompletion(
            model=JUDGE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0, max_tokens=10,
            api_key=os.environ.get("OPENAI_API_KEY"),
        )
        txt = (resp.choices[0].message.content or "").strip()
        import re
        m = re.search(r"[01](?:\.\d+)?", txt)
        return min(1.0, max(0.0, float(m.group(0)))) if m else 0.0
    except Exception as exc:  # noqa: BLE001 — judge failure → -1 sentinel
        return -1.0


def _faith_prompt(answer: str, used: str) -> str:
    return (
        "You are a RAGAS faithfulness judge. Given CONTEXT (retrieved chunks) and "
        "ANSWER, output ONLY a number 0..1 = fraction of the answer's factual "
        "claims that are supported by the context. 1=all grounded, 0=none.\n\n"
        f"CONTEXT:\n{used[:3000]}\n\nANSWER:\n{answer[:1500]}\n\nScore (0..1):"
    )


def _correct_prompt(answer: str, gt_facts: str, gt_chunk: str, question: str = "") -> str:
    # Score against the REQUIRED FACTS, not a corpus chunk: computed/derived
    # values (e.g. "R23 = 2 Ω", "P = 16 W") are correct even when no chunk
    # states them verbatim, and paraphrases/equivalent formulas count. The
    # chunk is supplementary context only.
    #
    # PREMISE-FACT carve-out: many questions state data in their own preamble
    # ("Sông Mê Kông dài 4.880 km ... phân tích rủi ro") and then ask for
    # analysis. Such numbers leak into must_contain (~45% of all facts). An
    # answer that correctly analyses what is ASKED must NOT be penalised for not
    # parroting back a premise it was handed. The judge sees the QUESTION and
    # treats facts already present in it as given context, not deliverables —
    # applied uniformly to every question (no per-item cherry-picking).
    return (
        "You are a RAGAS answer-correctness judge. Output ONLY a number 0..1 = how "
        "correctly AND completely the ANSWER answers the QUESTION, using REQUIRED "
        "FACTS as the ground-truth checklist.\n"
        "Rules:\n"
        "- A fact counts as covered if the answer states it OR an equivalent form "
        "(paraphrase, equivalent formula, computed/derived value, same number in a "
        "different format). Computed answers are valid even if not verbatim in any "
        "document.\n"
        "- PREMISE CARVE-OUT: if a required fact is already stated in the QUESTION "
        "itself (a premise the asker handed over), do NOT penalise the answer for "
        "not restating it. Judge instead whether the answer correctly addresses "
        "what the question ASKS (the analysis / comparison / total / conclusion).\n"
        "- Penalise ONLY: a fact the answer was supposed to PRODUCE that is missing, "
        "or a wrong/fabricated value that contradicts a required fact.\n\n"
        f"QUESTION:\n{question[:800]}\n\n"
        f"REQUIRED FACTS (ground-truth checklist): {gt_facts}\n"
        f"(optional supporting context, do not require verbatim match):\n{gt_chunk[:1200]}\n\n"
        f"ANSWER:\n{answer[:1500]}\n\nScore (0..1):"
    )


def _fact_covered_prompt(answer: str, fact: str, question: str = "") -> str:
    """Claim-level: does the ANSWER deliver this ONE required fact (or equivalent)?

    Returns a 0/1 judge prompt. Equivalent forms count (paraphrase, computed /
    derived value, same number in a different format). Premise facts already in
    the QUESTION are excluded by the caller, not here.
    """
    return (
        "You are a claim-coverage judge. Output ONLY 1 or 0.\n"
        "1 = the ANSWER states the REQUIRED FACT or an equivalent form "
        "(paraphrase, equivalent formula, computed/derived value, the same number "
        "in a different format).\n"
        "0 = the ANSWER omits it or states a value that contradicts it.\n\n"
        f"QUESTION:\n{question[:600]}\n\n"
        f"REQUIRED FACT:\n{fact[:300]}\n\n"
        f"ANSWER:\n{answer[:1500]}\n\nOutput (1 or 0):"
    )


async def main() -> None:
    bot = sys.argv[1]
    qs = [q for q in json.loads(QFILE.read_text(encoding="utf-8")) if q["bot_id"] == bot]
    eng = create_async_engine(os.environ["DATABASE_URL"])
    out: list[str] = [f"# Multi-step RAGAS report — {bot}\n"]
    rows = []
    async with httpx.AsyncClient() as c:
        for i, q in enumerate(qs, 1):
            resp = await _ask(c, bot, q["question"])
            if resp.get("_error"):
                out.append(f"## Q{i} [{q['type']}] — ERROR {resp['_error']}\n")
                continue
            answer = resp.get("answer", "") or ""
            sources = resp.get("sources") or []
            used = "\n---\n".join(
                f"[{s.get('document_name','')}#{s.get('chunk_index')} score={s.get('score')}]\n{s.get('preview','')}"
                for s in sources
            )
            # correct chunk (DB): find the corpus chunk(s) containing the most
            # ground-truth facts — more reliable than agent-guessed chunk ids.
            gt_chunk = ""
            musts = [m for m in (q.get("must_contain") or []) if len(m) >= 2]
            if musts:
                async with eng.connect() as conn:
                    rows_db = await conn.execute(text("""
                        SELECT dc.content FROM document_chunks dc
                        JOIN documents d ON d.id = dc.record_document_id
                        JOIN bots b ON b.id = d.record_bot_id
                        WHERE b.bot_id = :bot
                    """).bindparams(bot=bot))
                    scored = []
                    for (content,) in rows_db:
                        ct = content or ""
                        ctl = ct.lower()
                        hits = sum(1 for m in musts if m.lower() in ctl)
                        if hits:
                            scored.append((hits, ct))
                    scored.sort(key=lambda x: -x[0])
                    gt_chunk = "\n---\n".join(c[:1000] for _, c in scored[:2])
            musts_all = q.get("must_contain", []) or []
            gt_facts = ", ".join(musts_all)
            faith = await _judge(_faith_prompt(answer, used))
            corr = await _judge(_correct_prompt(answer, gt_facts, gt_chunk, q["question"]))

            # PER-FACT FORENSIC (soi tận cùng): for each required fact, locate it
            # — in the answer / in a retrieved chunk / in the corpus / nowhere —
            # which pinpoints the exact failing layer for that specific fact.
            def _norm(s: str) -> str:
                s = s.lower()
                s = re.sub(r"(?<=\d)[.,\s](?=\d)", "", s)
                return re.sub(r"\s+", " ", s)
            _ans_n, _used_n, _gt_n = _norm(answer), _norm(used), _norm(gt_chunk)
            # full-corpus membership for each fact (one query, reused)
            async with eng.connect() as conn2:
                _corp = await conn2.execute(text("""
                    SELECT string_agg(dc.content, ' ') FROM document_chunks dc
                    JOIN documents d ON d.id=dc.record_document_id
                    JOIN bots b ON b.id=d.record_bot_id WHERE b.bot_id=:bot
                """).bindparams(bot=bot))
                _corpus_all = _norm(_corp.scalar() or "")
            _q_n = _norm(q.get("question", ""))
            fact_lines = []
            n_ret_miss = n_gen_drop = n_not_corpus = n_in_ans = 0
            n_deliverable = n_covered = 0  # claim-level Coverage basis
            for fct in musts_all:
                fn = _norm(fct)
                in_ans = fn in _ans_n
                in_used = fn in _used_n
                in_corp = fn in _corpus_all
                is_premise = bool(fn) and fn in _q_n
                if in_ans:
                    tag = "✅ trong câu trả lời"; n_in_ans += 1
                elif in_used:
                    tag = "🟡 CÓ trong chunk bot dùng nhưng LLM BỎ → generation"; n_gen_drop += 1
                elif in_corp:
                    tag = "🔴 CÓ trong corpus nhưng KHÔNG retrieve → retrieval miss"; n_ret_miss += 1
                else:
                    tag = "⚪ KHÔNG có trong corpus (computed/paraphrase) — judge ngữ nghĩa"; n_not_corpus += 1
                # Claim-level coverage: premise facts (already in the question) are
                # given context, excluded from the denominator. A deliverable fact
                # is covered on exact match OR a positive per-fact semantic judge
                # (handles paraphrase / computed / different-format).
                if is_premise:
                    tag += " · (premise — excluded)"
                else:
                    n_deliverable += 1
                    covered = in_ans
                    if not covered:
                        covered = (
                            await _judge(_fact_covered_prompt(answer, fct, q.get("question", "")))
                            >= COVERAGE_COVERED_THRESHOLD
                        )
                        if covered:
                            tag += " · ✅ covered (semantic)"
                    if covered:
                        n_covered += 1
                fact_lines.append(f"   - `{fct}` → {tag}")
            facts_block = "\n".join(fact_lines)
            coverage = (n_covered / n_deliverable) if n_deliverable else None
            rows.append((faith, corr, coverage))

            # Decision tree (sai ở đâu): chunk đúng có trong corpus? → trong top-K? → LLM đúng?
            # retrieval hit = a distinctive slice of the correct chunk appears in the retrieved set
            _gt_probe = _gt_n[:120]
            chunk_in_corpus = bool(gt_chunk.strip())
            chunk_in_topk = bool(_gt_probe) and _gt_probe in _used_n
            llm_correct_given_chunk = "N/A"
            if chunk_in_topk or n_gen_drop or n_in_ans:
                llm_correct_given_chunk = "YES" if corr >= 0.7 else "NO"
            # layer verdict
            if corr >= 0.7:
                root = "✅ CHUẨN — chunk đúng retrieve + LLM trả đúng"
                verdict = "✅ CHUẨN"
            elif n_ret_miss > 0 and not chunk_in_topk:
                root = ("🔴 RETRIEVAL — chunk chứa đáp án CÓ trong corpus nhưng KHÔNG vào top-K. "
                        "Sai tầng: CONFIG/LUỒNG (top_k / embedding / rerank / metadata-filter)")
                verdict = "🔴 RETRIEVAL"
            elif faith < 0.5:
                root = ("🟠 HALLU/FAITHFULNESS — câu trả lời có claim KHÔNG grounded trong chunk "
                        "(bịa số / parametric). Sai tầng: MODEL + PROMPT (anti-fabricate)")
                verdict = "🟠 HALLU"
            elif n_gen_drop > 0 or chunk_in_topk:
                root = ("🟡 GENERATION — chunk đúng ĐÃ retrieve nhưng LLM trả SAI/THIẾU "
                        "(tính sai / bỏ fact / tóm tắt). Sai tầng: MODEL + PROMPT, KHÔNG phải retrieval")
                verdict = "🟡 GENERATION"
            elif n_not_corpus == len(musts_all):
                root = ("⚪ DATA/COMPUTED — đáp án không có verbatim trong corpus (giá trị tính toán "
                        "hoặc corpus thiếu). Kiểm tra: LLM math (computed) vs DATA gap (corpus thiếu)")
                verdict = "⚪ DATA/COMPUTED"
            else:
                root = "🟡 MIXED — xem soi-từng-fact"
                verdict = "🟡 MIXED"
            out.append(
                f"## Q{i} [{q['type']}]  {verdict}  coverage={('%.2f' % coverage) if coverage is not None else 'n/a'}  faithfulness={faith:.2f}  answer_correctness={corr:.2f}  chunks_used={resp.get('chunks_used')}  intent={(resp.get('debug') or {}).get('intent')}  decomposed={(resp.get('debug') or {}).get('query_decomposed')}\n"
                f"**Câu hỏi:** {q['question']}\n\n"
                f"**RAG trả lời (full):** {answer}\n\n"
                f"**Đáp án đúng (facts bắt buộc):** {gt_facts}\n\n"
                f"**DECISION TREE (sai ở đâu):**\n"
                f"   1. Chunk đúng CÓ trong corpus? **{'CÓ' if chunk_in_corpus else 'KHÔNG (data gap)'}**\n"
                f"   2. Chunk đúng vào top-K (retrieved)? **{'CÓ' if chunk_in_topk else 'KHÔNG'}**\n"
                f"   3. LLM trả đúng KHI có chunk? **{llm_correct_given_chunk}**\n"
                f"   → **ROOT CAUSE: {root}**\n\n"
                f"**Soi từng fact (tận cùng):**\n{facts_block}\n\n"
                f"**Chunk ĐÚNG (corpus chứa đáp án):**\n{gt_chunk[:1200] or '(không tìm thấy chunk chứa facts)'}\n\n"
                f"**Chunk bot DÙNG (retrieve):**\n{(used[:1200]) or '(none retrieved)'}\n"
            )
            print(f"  Q{i} [{q['type']}] {verdict} cov={('%.2f' % coverage) if coverage is not None else 'n/a'} faith={faith:.2f} correct={corr:.2f} chunks={resp.get('chunks_used')}")
    await eng.dispose()
    # aggregate
    valid = [(f, c, cov) for f, c, cov in rows if f >= 0 and c >= 0]
    if valid:
        af = sum(f for f, _, _ in valid) / len(valid)
        ac = sum(c for _, c, _ in valid) / len(valid)
        # Coverage = claim-level headline (CLAUDE.md): mean over questions that
        # have at least one deliverable fact (cov is None when all facts are
        # premise / the question has no must_contain).
        cov_vals = [cov for _, _, cov in valid if cov is not None]
        cov_agg = (sum(cov_vals) / len(cov_vals)) if cov_vals else None
        cov_str = f"{cov_agg:.2f}" if cov_agg is not None else "n/a"
        out.insert(1, f"\n**AGG ({bot}, n={len(valid)}):** COVERAGE={cov_str}  faithfulness={af:.2f}  answer_correctness={ac:.2f}\n")
        print(f"\n=== {bot}: COVERAGE={cov_str}  faithfulness={af:.2f}  answer_correctness={ac:.2f}  (n={len(valid)}, cov_n={len(cov_vals)}) ===")
    rep = Path(__file__).parent.parent / "reports" / f"MULTISTEP_RAGAS_{bot}.md"
    rep.write_text("\n".join(out), encoding="utf-8")
    print(f"💾 {rep}")


if __name__ == "__main__":
    asyncio.run(main())
