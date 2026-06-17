"""Generate the big per-question deepdive report from a deepdive jsonl run.

Evidence-only (no guessing): every field comes from the deepdive jsonl
(question / bot answer / expected literal / cited chunk) or a live corpus DB
query (the chunk that actually holds the expected literal = ground truth).

Per question it emits:
  - câu hỏi
  - câu bot trả lời
  - đáp án đúng (expected literal from the question spec)
  - CHUNK ĐÁP ÁN ĐÚNG ở DB (corpus chunk holding the literal + its chunk_id)
  - CHUNK BOT DÙNG (the cited chunk_id + quote + score)
  - phân loại (đúng / thiếu / chưa-chuẩn / sai / bịa / refuse-ok)
  - nguyên nhân + tầng (retrieval / chunking / rerank / grounding / oos / test)

Usage: python scripts/gen_deepdive_report.py /tmp/deepdive_<ts>.jsonl [out.md]
"""
from __future__ import annotations

import json
import subprocess
import sys

_PG_DSN = subprocess.run(
    ["bash", "-lc",
     "grep '^DATABASE_URL_SYNC=' /var/www/html/ragbot/.env | cut -d= -f2- | sed 's/+psycopg2//'"],
    capture_output=True, text=True,
).stdout.strip()

_BOT_PK: dict[str, str] = {}
_ENV = {"PGCONNECT_TIMEOUT": "10", "PATH": "/usr/bin:/bin"}

_REFUSE_MARKERS = (
    "chưa có thông tin", "không có thông tin", "không đề cập", "không tìm thấy",
    "không nằm trong", "liên hệ trực tiếp", "vui lòng", "tài liệu không",
    "chưa thấy", "không thể trả lời", "ngoài phạm vi",
)


def _psql(sql: str) -> str:
    return subprocess.run(
        ["psql", _PG_DSN, "-tA", "-c", sql],
        capture_output=True, text=True, env=_ENV,
    ).stdout.strip()


def _bot_pk(bot_id: str) -> str:
    if bot_id not in _BOT_PK:
        _BOT_PK[bot_id] = _psql(f"SELECT id FROM bots WHERE bot_id='{bot_id}' LIMIT 1;")
    return _BOT_PK[bot_id]


def _correct_chunk(bot_id: str, literal: str) -> tuple[str, str]:
    """Return (chunk_id, content_excerpt) of the corpus chunk holding ``literal``."""
    pk = _bot_pk(bot_id)
    if not pk or not literal:
        return ("", "")
    safe = literal.replace("'", "''")
    row = _psql(
        f"SELECT id || '|||' || left(replace(content,E'\\n',' '),200) "
        f"FROM document_chunks WHERE record_bot_id='{pk}' "
        f"AND content ILIKE '%{safe}%' ORDER BY length(content) LIMIT 1;"
    )
    if "|||" in row:
        cid, _, txt = row.partition("|||")
        return (cid.strip(), txt.strip())
    return ("", "")


def _is_refuse(answer: str) -> bool:
    a = answer.lower()
    return any(m in a for m in _REFUSE_MARKERS)


def classify(rec: dict, correct_cid: str) -> tuple[str, str, str]:
    """Return (nhãn, nguyên-nhân, tầng) — evidence-driven, refuse-aware."""
    ans = rec.get("answer", "") or ""
    missing = rec.get("missing", [])
    chunks = rec.get("chunks_used", 0)
    cits = rec.get("citations", [])
    is_oos = rec.get("is_oos", False)
    must = rec.get("must_contain", [])
    cited_quotes = " ".join((c.get("quote", "") or "").lower() for c in cits)
    literal_in_cited = bool(must) and all(k.lower() in cited_quotes for k in must)

    if is_oos:
        if _is_refuse(ans) or chunks == 0:
            return ("✅ ĐÚNG (refuse OOS)", "OOS được từ chối đúng", "—")
        return ("🔴 OOS-LEAK", "câu ngoài phạm vi nhưng bot trả từ kiến thức ngoài corpus", "grounding/OOS")

    if not missing:  # literal present in answer
        if literal_in_cited:
            return ("✅ ĐÚNG (grounded)", "đáp án có trong chunk bot cite", "—")
        if chunks == 0 or not cits:
            return ("🔴 BỊA/PARAMETRIC", "đáp án ĐÚNG nhưng KHÔNG có chunk grounding → trả từ kiến thức LLM", "grounding")
        return ("🟡 ĐÚNG nhưng chunk yếu", "đáp án đúng, chunk cite không chứa literal rõ", "rerank/citation")

    # literal missing from answer
    if _is_refuse(ans):
        if correct_cid:
            return ("🟡 THIẾU (refuse oan)", "corpus CÓ đáp án nhưng retrieval miss → bot refuse", "retrieval")
        return ("✅ ĐÚNG (refuse, corpus gap)", "corpus không có đáp án → refuse hợp lý", "—")
    if correct_cid and not literal_in_cited:
        return ("🔴 SAI/LẪN", "đáp án ở chunk khác; bot trả nhầm chunk/dòng (conflate)", "chunking/rerank/disambiguation")
    if not correct_cid:
        return ("🟡 CHƯA CHUẨN (test/corpus)", "literal kỳ vọng không thấy trong corpus → có thể đồng nghĩa / test quá chặt", "test-design/corpus")
    return ("🟡 PARTIAL", "trả thiếu một phần literal", "generate")


def main() -> None:
    jsonl = sys.argv[1] if len(sys.argv) > 1 else None
    if not jsonl:
        print("usage: gen_deepdive_report.py <jsonl> [out.md]")
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) > 2 else jsonl.replace(".jsonl", "_REPORT.md")
    recs = [json.loads(l) for l in open(jsonl) if l.strip()]

    lines: list[str] = []
    counts: dict[str, int] = {}
    by_bot: dict[str, list] = {}
    for r in recs:
        by_bot.setdefault(r.get("bot_id", "?"), []).append(r)

    lines.append("# Deepdive verify report — per-question chunk evidence\n")
    lines.append(f"> Source: `{jsonl}` · {len(recs)} câu · bypass_cache=ON · DB ground-truth\n")
    lines.append("> Mỗi case: câu hỏi → bot trả → đáp án đúng → chunk-đúng(DB) → chunk-bot-dùng → nhãn + nguyên nhân + tầng. Evidence-only.\n")

    for bot, rs in by_bot.items():
        lines.append(f"\n---\n\n## Bot: `{bot}`\n")
        for r in rs:
            must = r.get("must_contain", [])
            correct_cid, correct_txt = ("", "")
            if must and r.get("missing") and not r.get("is_oos"):
                correct_cid, correct_txt = _correct_chunk(bot, must[0])
            elif must and not r.get("missing") and not r.get("is_oos"):
                correct_cid, correct_txt = _correct_chunk(bot, must[0])
            label, cause, layer = classify(r, correct_cid)
            counts[label] = counts.get(label, 0) + 1
            cits = r.get("citations", [])
            bot_chunk = cits[0] if cits else {}
            lines.append(f"### [{r['qid']}] {label}")
            lines.append("")
            lines.append(f"- **Câu hỏi**: {r['question']}")
            lines.append(f"- **Bot trả lời**: {r['answer']}")
            lines.append(f"- **Đáp án đúng (literal)**: `{must}`" + (" *(OOS — phải refuse)*" if r.get("is_oos") else ""))
            if correct_cid:
                lines.append(f"- **CHUNK ĐÁP ÁN ĐÚNG (DB)** `chunk_id={correct_cid[:8]}`: {correct_txt}")
            elif not r.get("is_oos"):
                lines.append(f"- **CHUNK ĐÁP ÁN ĐÚNG (DB)**: (không tìm thấy literal trong corpus)")
            if bot_chunk:
                lines.append(f"- **CHUNK BOT DÙNG** `chunk_id={(bot_chunk.get('chunk_id') or '')[:8]}` score={bot_chunk.get('score')}: {bot_chunk.get('quote','')}")
            else:
                lines.append(f"- **CHUNK BOT DÙNG**: (none) · chunks_used={r.get('chunks_used')} · top_score={r.get('top_score')}")
            lines.append(f"- **Nguyên nhân**: {cause}")
            lines.append(f"- **Bị ở tầng**: {layer}")
            lines.append(f"- **cache**: {r.get('cache_status')} · chunks_used={r.get('chunks_used')} · top_score={r.get('top_score')}")
            lines.append("")

    summary = ["\n---\n\n## TỔNG HỢP nhãn\n", "| Nhãn | Số câu |", "|---|---|"]
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        summary.append(f"| {k} | {v} |")
    # insert summary right after header
    header_end = 4
    full = lines[:header_end] + summary + lines[header_end:]

    with open(out, "w") as f:
        f.write("\n".join(full))
    print(f"✅ report written: {out} ({len(recs)} câu)")
    for k, v in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
