#!/usr/bin/env python3
"""Sinh bộ golden test set cho bot từ document chunks trong DB.

Đọc ALL document_chunks của bot, dùng LLM sinh câu hỏi test
theo 10 categories (easy, medium, hard, trick, casual, nonsense,
followup, typo, multi, edge).

Usage:
    python scripts/generate_golden_set.py --bot-id <test-bot-id> --channel-type web
    python scripts/generate_golden_set.py --bot-id <test-bot-id> --channel-type web --count 80
    # Or use env default: export RAGBOT_TEST_BOT_ID=test-bot-v1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# Thêm project root vào sys.path
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(Path(_PROJECT_ROOT) / ".env")

# ── Category distribution mặc định (tổng 54) ────────────────────────────────
DEFAULT_DISTRIBUTION: dict[str, int] = {
    "easy": 10,
    "medium": 10,
    "hard": 5,
    "trick": 5,
    "casual": 5,
    "nonsense": 3,
    "followup": 5,
    "typo": 5,
    "multi": 3,
    "edge": 3,
}
DEFAULT_TOTAL = sum(DEFAULT_DISTRIBUTION.values())  # 54

# Difficulty mapping per category
CATEGORY_DIFFICULTY: dict[str, str] = {
    "easy": "easy",
    "medium": "medium",
    "hard": "hard",
    "trick": "hard",
    "casual": "easy",
    "nonsense": "easy",
    "followup": "medium",
    "typo": "medium",
    "multi": "hard",
    "edge": "hard",
}

# ── LLM defaults ─────────────────────────────────────────────────────────────
FALLBACK_MODEL = "gpt-4.1-mini"


async def _get_db_url() -> str:
    """Lấy DATABASE_URL từ env, chuyển sang asyncpg format nếu cần."""
    url = os.getenv("DATABASE_URL", "")
    if not url:
        print("[ERROR] DATABASE_URL not set in .env", file=sys.stderr)
        sys.exit(1)
    # Chuyển sqlalchemy async format -> raw asyncpg DSN
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("postgresql+psycopg2://", "postgresql://")
    return url


async def _get_llm_model(conn) -> str:
    """Đọc model name từ system_config, fallback nếu không có."""
    try:
        row = await conn.fetchrow(
            "SELECT value FROM system_config WHERE key = 'llm_default_model'"
        )
        if row:
            val = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
            if isinstance(val, str):
                return val
    except Exception:  # noqa: BLE001 — fallback to default model name (safe default)
        pass
    return FALLBACK_MODEL


async def _load_chunks(conn, bot_id: str, channel_type: str) -> tuple[list[dict], str]:
    """Load ALL document_chunks cho bot, join documents để lấy document_name.

    Returns: (chunks_list, bot_internal_uuid)
    """
    # Tìm bot UUID từ bot_id + channel_type
    bot_row = await conn.fetchrow(
        """
        SELECT id FROM bots
        WHERE bot_id = $1 AND channel_type = $2 AND is_deleted = false
        LIMIT 1
        """,
        bot_id,
        channel_type,
    )
    if not bot_row:
        print(f"[ERROR] Bot not found: bot_id={bot_id}, channel_type={channel_type}", file=sys.stderr)
        sys.exit(1)

    bot_uuid = bot_row["id"]

    rows = await conn.fetch(
        """
        SELECT dc.chunk_index, dc.content, dc.metadata_json,
               d.document_name, d.id AS doc_id
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.bot_id = $1
        ORDER BY d.document_name, dc.chunk_index
        """,
        bot_uuid,
    )

    if not rows:
        print(f"[ERROR] No document chunks found for bot {bot_id}", file=sys.stderr)
        sys.exit(1)

    chunks = []
    for r in rows:
        chunks.append({
            "content": r["content"],
            "document_name": r["document_name"],
            "chunk_index": r["chunk_index"],
            "doc_id": str(r["doc_id"]),
        })

    return chunks, str(bot_uuid)


def _build_context(chunks: list[dict]) -> tuple[str, list[str]]:
    """Ghép tất cả chunks thành context string, trả về (context, doc_names)."""
    doc_sections: dict[str, list[str]] = {}
    for c in chunks:
        name = c["document_name"]
        if name not in doc_sections:
            doc_sections[name] = []
        doc_sections[name].append(c["content"])

    parts = []
    doc_names = list(doc_sections.keys())
    for name, contents in doc_sections.items():
        parts.append(f"=== Tài liệu: {name} ===")
        parts.append("\n".join(contents))
        parts.append("")

    return "\n".join(parts), doc_names


def _scale_distribution(total: int) -> dict[str, int]:
    """Scale distribution theo total count, giữ tỉ lệ."""
    if total == DEFAULT_TOTAL:
        return dict(DEFAULT_DISTRIBUTION)

    ratio = total / DEFAULT_TOTAL
    scaled = {}
    assigned = 0
    cats = list(DEFAULT_DISTRIBUTION.keys())
    for cat in cats[:-1]:
        n = max(1, round(DEFAULT_DISTRIBUTION[cat] * ratio))
        scaled[cat] = n
        assigned += n
    # Category cuối lấy phần còn lại
    scaled[cats[-1]] = max(1, total - assigned)
    return scaled


def _build_prompt(context: str, doc_names: list[str], distribution: dict[str, int]) -> tuple[str, str]:
    """Build system + user prompt cho LLM."""
    total = sum(distribution.values())
    dist_text = ", ".join(f"{cat}({n})" for cat, n in distribution.items())

    system_prompt = """Bạn là chuyên gia tạo bộ test cho chatbot RAG tiếng Việt.
Dựa trên tài liệu dưới đây, sinh danh sách câu hỏi test đa dạng.
Trả về ĐÚNG JSON array, không kèm markdown fence hay text khác.

Quy tắc từng category:
- easy: câu hỏi trực tiếp — tên, địa chỉ, giá dịch vụ cụ thể, hotline
- medium: so sánh, liệt kê, tổng hợp thông tin từ nhiều phần tài liệu
- hard: tính toán (chênh lệch giá), suy luận, tổng hợp nhiều tài liệu
- trick: hỏi thông tin KHÔNG có trong tài liệu — bot phải trả lời "không biết" hoặc tương đương
- casual: câu chat xã giao ("trời nóng quá", "bạn khỏe không") — bot nên redirect về dịch vụ
- nonsense: topic hoàn toàn khác (bitcoin, bóng đá, thời tiết) — bot nên nói không liên quan
- followup: chuỗi 2-3 câu hỏi liên quan (question chứa ngữ cảnh câu trước)
- typo: câu hỏi có lỗi chính tả, viết tắt ("goi dau gia sao", "cs da", "triet long nach")
- multi: kết hợp giá + thời gian + khuyến mãi trong 1 câu hỏi
- edge: emoji only, câu hỏi lặp lại, câu hỏi rất dài

Với category "trick" và "nonsense", ground_truth phải mô tả bot nên trả lời thế nào.
Với category "casual", ground_truth mô tả cách bot redirect về dịch vụ.
Với category "followup", question nên bao gồm context (ví dụ: "thế còn loại đắt nhất?").
expected_sources là list tên tài liệu liên quan (rỗng nếu không áp dụng)."""

    user_prompt = f"""Tài liệu:
{context}

Danh sách tài liệu: {json.dumps(doc_names, ensure_ascii=False)}

Sinh ĐÚNG {total} câu hỏi theo phân bổ: {dist_text}

Format JSON array:
[
  {{
    "question": "câu hỏi tiếng Việt",
    "ground_truth": "câu trả lời chính xác hoặc mô tả expected behavior",
    "difficulty": "easy|medium|hard",
    "category": "tên category",
    "expected_sources": ["tên tài liệu"]
  }}
]

LƯU Ý:
- ground_truth phải chính xác theo nội dung tài liệu (trích dẫn số liệu, giá cụ thể)
- Mỗi câu hỏi phải có id format "gs-XXX" (XXX = số thứ tự 001, 002, ...)
- Trả về ĐÚNG JSON array, KHÔNG kèm ```json``` hay text nào khác"""

    return system_prompt, user_prompt


def _extract_json(text: str) -> list[dict]:
    """Trích xuất JSON array từ LLM response, xử lý markdown fences."""
    # Bỏ markdown code fences nếu có
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()

    # Tìm JSON array
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if match:
        return json.loads(match.group())

    # Fallback: thử parse toàn bộ
    return json.loads(cleaned)


async def _call_llm(model: str, system_prompt: str, user_prompt: str, retry: bool = True) -> list[dict]:
    """Gọi LLM qua litellm, parse JSON response. Retry 1 lần nếu parse lỗi."""
    import litellm

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    for attempt in range(2 if retry else 1):
        print(f"  [LLM] Calling {model} (attempt {attempt + 1})...", flush=True)
        response = await litellm.acompletion(
            model=model,
            messages=messages,
            temperature=0.7,
            max_tokens=16000,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or ""
        token_usage = {
            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(response.usage, "completion_tokens", 0),
        }
        print(f"  [LLM] Tokens: {token_usage['prompt_tokens']} prompt + {token_usage['completion_tokens']} completion")

        try:
            parsed = _extract_json(raw)
            if not isinstance(parsed, list):
                raise ValueError("Response is not a JSON array")
            if len(parsed) == 0:
                raise ValueError("Empty array returned")
            return parsed
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"  [WARN] JSON parse error (attempt {attempt + 1}): {exc}")
            if attempt == 0 and retry:
                # Retry với prompt rõ ràng hơn
                messages.append({"role": "assistant", "content": raw[:200]})
                messages.append({
                    "role": "user",
                    "content": "Response không parse được JSON. Trả lại ĐÚNG JSON array, không kèm text.",
                })
                continue
            print(f"[ERROR] Cannot parse LLM response after retries", file=sys.stderr)
            print(f"  Raw response (first 500 chars): {raw[:500]}", file=sys.stderr)
            sys.exit(1)

    return []  # unreachable


def _postprocess(
    questions: list[dict], distribution: dict[str, int], bot_id: str
) -> list[dict]:
    """Chuẩn hoá output: thêm id, fix difficulty, đảm bảo schema."""
    processed = []
    for i, q in enumerate(questions, 1):
        cat = q.get("category", "easy")
        difficulty = CATEGORY_DIFFICULTY.get(cat, q.get("difficulty", "medium"))
        item = {
            "id": f"gs-{i:03d}",
            "question": q.get("question", ""),
            "ground_truth": q.get("ground_truth", ""),
            "difficulty": difficulty,
            "category": cat,
            "expected_sources": q.get("expected_sources", []),
        }
        processed.append(item)
    return processed


def _save_output(
    questions: list[dict],
    bot_id: str,
    channel_type: str,
    doc_names: list[str],
    total_chunks: int,
) -> str:
    """Lưu golden set ra file JSON, trả về path."""
    output_dir = Path(_PROJECT_ROOT) / "golden_set"
    output_dir.mkdir(parents=True, exist_ok=True)

    safe_bot_id = re.sub(r"[^a-zA-Z0-9_-]", "_", bot_id)
    filename = f"{safe_bot_id}_{channel_type}.json"
    output_path = output_dir / filename

    dataset = {
        "dataset_version": "1.0",
        "domain": bot_id,
        "description": f"Auto-generated golden set for bot '{bot_id}' ({channel_type})",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_documents": doc_names,
        "total_chunks": total_chunks,
        "questions": questions,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    return str(output_path)


def _print_summary(questions: list[dict], output_path: str) -> None:
    """In summary kết quả."""
    total = len(questions)
    by_cat: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    for q in questions:
        cat = q.get("category", "unknown")
        diff = q.get("difficulty", "unknown")
        by_cat[cat] = by_cat.get(cat, 0) + 1
        by_diff[diff] = by_diff.get(diff, 0) + 1

    print(f"\n{'='*60}")
    print("  GOLDEN SET GENERATION SUMMARY")
    print(f"{'='*60}")
    print(f"  Total questions generated: {total}")
    print(f"  Output: {output_path}")

    print(f"\n  {'Category':<16} {'Count':>5}")
    print(f"  {'-'*24}")
    for cat in sorted(by_cat.keys()):
        print(f"  {cat:<16} {by_cat[cat]:>5}")

    print(f"\n  {'Difficulty':<16} {'Count':>5}")
    print(f"  {'-'*24}")
    for diff in sorted(by_diff.keys()):
        print(f"  {diff:<16} {by_diff[diff]:>5}")

    print(f"\n{'='*60}\n")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate golden test set for a Ragbot bot using LLM",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bot-id",
        default=os.getenv("RAGBOT_TEST_BOT_ID"),
        required=not os.getenv("RAGBOT_TEST_BOT_ID"),
        help="Bot ID (default from RAGBOT_TEST_BOT_ID env var)",
    )
    parser.add_argument(
        "--channel-type", default="web",
        help="Channel type (default: web)",
    )
    parser.add_argument(
        "--count", type=int, default=DEFAULT_TOTAL,
        help=f"Total number of questions to generate (default: {DEFAULT_TOTAL})",
    )
    parser.add_argument(
        "--model", default=None,
        help="LLM model override (default: read from system_config or gpt-4.1-mini)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Output directory (default: golden_set/)",
    )
    args = parser.parse_args()

    import asyncpg

    # ── 1. Connect to DB ──────────────────────────────────────────────────
    db_url = await _get_db_url()
    print(f"[1/5] Connecting to database...")
    conn = await asyncpg.connect(db_url)

    try:
        # ── 2. Get LLM model ─────────────────────────────────────────────
        model = args.model or await _get_llm_model(conn)
        print(f"  Using model: {model}")

        # ── 3. Load chunks ───────────────────────────────────────────────
        print(f"[2/5] Loading document chunks for bot '{args.bot_id}' ({args.channel_type})...")
        chunks, bot_uuid = await _load_chunks(conn, args.bot_id, args.channel_type)
        print(f"  Found {len(chunks)} chunks")

        # Build context
        context, doc_names = _build_context(chunks)
        print(f"  Documents: {len(doc_names)}")
        for name in doc_names:
            chunk_count = sum(1 for c in chunks if c["document_name"] == name)
            print(f"    - {name} ({chunk_count} chunks)")
        print(f"  Total context length: {len(context):,} chars")

    finally:
        await conn.close()

    # ── 4. Call LLM ──────────────────────────────────────────────────────
    distribution = _scale_distribution(args.count)
    total_requested = sum(distribution.values())
    print(f"\n[3/5] Generating {total_requested} questions via LLM...")
    print(f"  Distribution: {distribution}")

    system_prompt, user_prompt = _build_prompt(context, doc_names, distribution)
    raw_questions = await _call_llm(model, system_prompt, user_prompt)
    print(f"  LLM returned {len(raw_questions)} questions")

    # ── 5. Post-process + save ───────────────────────────────────────────
    print(f"[4/5] Post-processing...")
    questions = _postprocess(raw_questions, distribution, args.bot_id)

    print(f"[5/5] Saving golden set...")
    output_path = _save_output(
        questions=questions,
        bot_id=args.bot_id,
        channel_type=args.channel_type,
        doc_names=doc_names,
        total_chunks=len(chunks),
    )

    _print_summary(questions, output_path)


if __name__ == "__main__":
    asyncio.run(main())
