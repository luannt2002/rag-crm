"""Bot analytics routes (audit / question-gen / quality-dashboard) for test_chat.

Carved verbatim from the original ``test_chat.py`` (behavior-preserving). Split
out of ``bot_admin_routes`` so each module stays under the size cap; same
``@router.get("/bots/...")`` path strings, same handler bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime as _dt, timezone as _tz
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import text

from ragbot.shared.rbac import require_min_level
from ragbot.shared.constants import (
    AUDIT_MAX_TEMP_TABLES,
    DEFAULT_ADMIN_LEVEL,
)

from ._shared import (
    _find_bot_uuid,
    _page_limit,
    _require_owner,
    _sf,
    _sys_config,
    logger,
)

router = APIRouter(tags=["test"])

_AUDIT_PAGE_SIZE = 50  # fallback, overridden by system_config


def _audit_snap_name(bot_uuid: uuid.UUID, idx: int) -> str:
    """Tạo tên temp table xác định theo bot UUID + slot index.
    @param bot_uuid, idx: UUID bot và chỉ số slot
    @return: tên temp table
    """
    short = str(bot_uuid).replace("-", "")[:12]
    return f"_audit_{short}_{idx}"


@router.get("/bots/{bot_id}/{channel_type}/audit")
async def bot_audit_stats(
    bot_id: str, channel_type: str, request: Request,
    date_from: str | None = None, date_to: str | None = None,
    cursor: str | None = None,  # keyset cursor: ISO started_at of last item
    page_size: int = _AUDIT_PAGE_SIZE,
    workspace_id: str | None = None,  # 4-key: per-bot workspace
) -> dict:
    """Thống kê audit với temp table snapshot + keyset pagination.
    @param date_from, date_to, cursor, page_size: bộ lọc và phân trang
    @return: {ok, stats, extremes, invocations, requests, next_cursor}
    """
    require_min_level(request, DEFAULT_ADMIN_LEVEL)  # admin-only cross-user reads
    bot_uuid = await _find_bot_uuid(request, bot_id, channel_type, workspace_id=workspace_id)
    sf = _sf(request)
    cfg_svc = _sys_config(request)
    page_size = _page_limit(page_size)
    max_temp_tables = await cfg_svc.get_int("audit_max_temp_tables", AUDIT_MAX_TEMP_TABLES)

    # Parse date range
    params: dict[str, Any] = {"bid": bot_uuid}
    date_filter = ""
    if date_from:
        try:
            params["df"] = _dt.fromisoformat(date_from)
            date_filter += " AND started_at >= :df"
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_from format")
    if date_to:
        try:
            dt_parsed = _dt.fromisoformat(date_to)
            # End-of-day inclusive: 00:00:00 → 23:59:59.999999.
            if dt_parsed.hour == 0 and dt_parsed.minute == 0 and dt_parsed.second == 0:
                dt_parsed = dt_parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
            params["dt"] = dt_parsed
            date_filter += " AND started_at <= :dt"
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date_to format")

    async with sf() as session:
        # --- Anti-spam: drop oldest temp tables for this bot if >= limit ---
        snap_name = _audit_snap_name(bot_uuid, 0)
        for i in range(max_temp_tables):
            old_name = _audit_snap_name(bot_uuid, i)
            await session.execute(text(f"DROP TABLE IF EXISTS {old_name}"))

        # --- Create temp table snapshot with index ---
        await session.execute(text(f"""
            CREATE TEMP TABLE {snap_name} AS
            SELECT request_id, message_id, status, duration_ms,
                prompt_tokens, completion_tokens, total_tokens, cost_usd,
                model_name, started_at, finished_at, error_message
            FROM request_logs
            WHERE record_bot_id = :bid {date_filter}
        """), params)

        # Index for keyset pagination on started_at
        await session.execute(text(
            f"CREATE INDEX ON {snap_name} (started_at DESC)"
        ))

        # --- Aggregate stats (single scan) ---
        stats = (await session.execute(text(f"""
            SELECT count(*), count(*) FILTER (WHERE status = 'success'),
                count(*) FILTER (WHERE status = 'failed'),
                coalesce(avg(duration_ms), 0), coalesce(min(duration_ms) FILTER (WHERE duration_ms > 0), 0),
                coalesce(max(duration_ms), 0), coalesce(avg(prompt_tokens), 0),
                coalesce(avg(completion_tokens), 0), coalesce(avg(total_tokens), 0),
                coalesce(sum(total_tokens), 0),
                coalesce(min(total_tokens) FILTER (WHERE total_tokens > 0), 0),
                coalesce(max(total_tokens), 0), coalesce(sum(cost_usd), 0),
                coalesce(avg(cost_usd), 0), min(started_at), max(started_at)
            FROM {snap_name}
        """))).fetchone()

        # --- Extremes (index-backed LIMIT 1) ---
        slowest = (await session.execute(text(
            f"SELECT message_id, duration_ms FROM {snap_name} WHERE duration_ms > 0 ORDER BY duration_ms DESC LIMIT 1"
        ))).fetchone()
        fastest = (await session.execute(text(
            f"SELECT message_id, duration_ms FROM {snap_name} WHERE duration_ms > 0 ORDER BY duration_ms ASC LIMIT 1"
        ))).fetchone()
        most_expensive = (await session.execute(text(
            f"SELECT message_id, cost_usd, total_tokens FROM {snap_name} WHERE cost_usd > 0 ORDER BY cost_usd DESC LIMIT 1"
        ))).fetchone()
        most_tokens = (await session.execute(text(
            f"SELECT message_id, total_tokens FROM {snap_name} WHERE total_tokens > 0 ORDER BY total_tokens DESC LIMIT 1"
        ))).fetchone()

        # --- Keyset paginated requests ---
        req_params: dict[str, Any] = {"lim": page_size}
        cursor_filter = ""
        if cursor:
            try:
                req_params["cursor"] = _dt.fromisoformat(cursor)
                cursor_filter = "WHERE started_at < :cursor"
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid cursor format")

        page_rows = (await session.execute(text(f"""
            SELECT request_id, message_id, status, duration_ms,
                prompt_tokens, completion_tokens, total_tokens, cost_usd,
                model_name, started_at, finished_at, error_message
            FROM {snap_name} {cursor_filter}
            ORDER BY started_at DESC
            LIMIT :lim
        """), req_params)).fetchall()

        # --- Model invocations aggregate ---
        # Bug fix column was `request_id` post-migration-0034
        # rename → `record_request_id`. The DELETE handler at line ~1903
        # already uses the correct name; this SELECT was stale.
        inv = (await session.execute(text(f"""
            SELECT count(*), coalesce(avg(duration_ms), 0),
                coalesce(sum(prompt_tokens), 0), coalesce(sum(completion_tokens), 0),
                coalesce(sum(cost_usd), 0)
            FROM model_invocations
            WHERE record_request_id IN (SELECT request_id FROM {snap_name})
        """))).fetchone()

        # Drop temp table
        await session.execute(text(f"DROP TABLE IF EXISTS {snap_name}"))

    # Build next_cursor from last item's started_at
    next_cursor = None
    if page_rows and len(page_rows) == page_size:
        last_started = page_rows[-1][9]
        if last_started:
            next_cursor = last_started.isoformat()

    return {
        "ok": True,
        "date_from": date_from, "date_to": date_to,
        "page_size": page_size,
        "next_cursor": next_cursor,
        "stats": {
            "total_requests": stats[0], "success_count": stats[1], "failed_count": stats[2],
            "duration": {"avg_ms": float(stats[3]), "min_ms": float(stats[4]), "max_ms": float(stats[5])},
            "tokens": {
                "avg_prompt": float(stats[6]), "avg_completion": float(stats[7]),
                "avg_total": float(stats[8]), "sum_total": int(stats[9]),
                "min_total": float(stats[10]), "max_total": float(stats[11]),
            },
            "cost": {"total_usd": float(stats[12]), "avg_usd": float(stats[13])},
            "first_request": stats[14].isoformat() if stats[14] else None,
            "last_request": stats[15].isoformat() if stats[15] else None,
        },
        "extremes": {
            "slowest": {"message_id": slowest[0], "duration_ms": slowest[1]} if slowest else None,
            "fastest": {"message_id": fastest[0], "duration_ms": fastest[1]} if fastest else None,
            "most_expensive": {"message_id": most_expensive[0], "cost_usd": float(most_expensive[1]), "total_tokens": int(most_expensive[2])} if most_expensive else None,
            "most_tokens": {"message_id": most_tokens[0], "total_tokens": int(most_tokens[1])} if most_tokens else None,
        },
        "invocations": {
            "total": inv[0], "avg_duration_ms": float(inv[1]),
            "total_prompt_tokens": int(inv[2]), "total_completion_tokens": int(inv[3]),
            "total_cost_usd": float(inv[4]),
        },
        "requests": [
            {"request_id": str(r[0]), "message_id": r[1], "status": r[2], "duration_ms": r[3],
             "prompt_tokens": r[4], "completion_tokens": r[5], "total_tokens": r[6],
             "cost_usd": float(r[7]) if r[7] else 0, "model_name": r[8],
             "started_at": r[9].isoformat() if r[9] else None,
             "finished_at": r[10].isoformat() if r[10] else None, "error_message": r[11]}
            for r in page_rows
        ],
    }


@router.get("/bots/{bot_id}/{channel_type}/generate-test-questions")
async def generate_test_questions(bot_id: str, channel_type: str, request: Request, workspace_id: str | None = None) -> dict:
    """Sinh bộ câu hỏi golden dataset từ toàn bộ document_chunks của bot.

    Dùng LLM để phân tích nội dung tài liệu và tạo câu hỏi test theo 10 categories:
    easy, medium, hard, trick, casual, nonsense, followup, typo, multi, edge.
    Kết quả tương thích với evaluate_ragas.py và scripts/generate_golden_set.py.

    @param bot_id, channel_type: định danh bot
    @return: {ok, bot_id, channel_type, questions, total, by_category, by_difficulty, model_used, chunks_loaded, output_file}
    """
    import json as _json  # noqa: PLC0415
    import re as _re  # noqa: PLC0415

    _require_owner(request)
    bot_uuid = await _find_bot_uuid(request, bot_id, channel_type, workspace_id=workspace_id)
    cfg_svc = _sys_config(request)
    sf = _sf(request)

    # ── Category distribution (tổng mặc định 54) ────────────────────────
    _DEFAULT_DISTRIBUTION: dict[str, int] = {
        "easy": 10, "medium": 10, "hard": 5, "trick": 5, "casual": 5,
        "nonsense": 3, "followup": 5, "typo": 5, "multi": 3, "edge": 3,
    }
    _DEFAULT_TOTAL = sum(_DEFAULT_DISTRIBUTION.values())  # 54

    _CATEGORY_DIFFICULTY: dict[str, str] = {
        "easy": "easy", "medium": "medium", "hard": "hard", "trick": "hard",
        "casual": "easy", "nonsense": "easy", "followup": "medium",
        "typo": "medium", "multi": "hard", "edge": "hard",
    }

    # ── 1. Load ALL document_chunks kèm document_name ───────────────────
    async with sf() as session:
        rows = (await session.execute(
            text("""
                SELECT dc.content, d.document_name
                FROM document_chunks dc
                JOIN documents d ON dc.record_document_id = d.id
                WHERE d.record_bot_id = :bid AND d.deleted_at IS NULL
                ORDER BY d.document_name, dc.chunk_index
            """),
            {"bid": bot_uuid},
        )).fetchall()

    if not rows:
        raise HTTPException(status_code=404, detail="Bot chưa có document chunks nào")

    # ── 2. Build context (group by document) ─────────────────────────────
    doc_sections_map: dict[str, list[str]] = {}
    for content, doc_name in rows:
        if doc_name not in doc_sections_map:
            doc_sections_map[doc_name] = []
        doc_sections_map[doc_name].append(content or "")

    doc_names = list(doc_sections_map.keys())
    context_parts: list[str] = []
    for name, contents in doc_sections_map.items():
        context_parts.append(f"=== Tài liệu: {name} ===")
        context_parts.append("\n".join(contents))
        context_parts.append("")
    full_content = "\n".join(context_parts)

    # Truncate nếu quá dài (tránh vượt context window)
    max_chars = await cfg_svc.get_int("golden_dataset_max_content_chars", 50000)
    if len(full_content) > max_chars:
        full_content = full_content[:max_chars] + "\n\n[... nội dung bị cắt bớt do quá dài ...]"

    # ── 3. Scale distribution theo num_questions ─────────────────────────
    num_questions = await cfg_svc.get_int("golden_dataset_num_questions", 54)

    def _scale_dist(total: int) -> dict[str, int]:
        if total == _DEFAULT_TOTAL:
            return dict(_DEFAULT_DISTRIBUTION)
        ratio = total / _DEFAULT_TOTAL
        scaled: dict[str, int] = {}
        assigned = 0
        cats = list(_DEFAULT_DISTRIBUTION.keys())
        for cat in cats[:-1]:
            n = max(1, round(_DEFAULT_DISTRIBUTION[cat] * ratio))
            scaled[cat] = n
            assigned += n
        scaled[cats[-1]] = max(1, total - assigned)
        return scaled

    distribution = _scale_dist(num_questions)
    total_requested = sum(distribution.values())
    dist_text = ", ".join(f"{cat}({n})" for cat, n in distribution.items())

    # ── 4. Build LLM prompts (same as generate_golden_set.py) ────────────
    system_prompt = (
        "Bạn là chuyên gia tạo bộ test cho chatbot RAG tiếng Việt.\n"
        "Dựa trên tài liệu dưới đây, sinh danh sách câu hỏi test đa dạng.\n"
        "Trả về ĐÚNG JSON object có key \"questions\" chứa array, không kèm markdown fence hay text khác.\n"
        "\n"
        "Quy tắc từng category:\n"
        "- easy: câu hỏi trực tiếp — tên, địa chỉ, giá dịch vụ cụ thể, hotline\n"
        "- medium: so sánh, liệt kê, tổng hợp thông tin từ nhiều phần tài liệu\n"
        "- hard: tính toán (chênh lệch giá), suy luận, tổng hợp nhiều tài liệu\n"
        "- trick: hỏi thông tin KHÔNG có trong tài liệu — bot phải trả lời \"không biết\" hoặc tương đương\n"
        "- casual: câu chat xã giao (\"trời nóng quá\", \"bạn khỏe không\") — bot nên redirect về dịch vụ\n"
        "- nonsense: topic hoàn toàn khác (bitcoin, bóng đá, thời tiết) — bot nên nói không liên quan\n"
        "- followup: chuỗi 2-3 câu hỏi liên quan (question chứa ngữ cảnh câu trước)\n"
        "- typo: câu hỏi có lỗi chính tả, viết tắt (\"goi dau gia sao\", \"cs da\", \"triet long nach\")\n"
        "- multi: kết hợp giá + thời gian + khuyến mãi trong 1 câu hỏi\n"
        "- edge: emoji only, câu hỏi lặp lại, câu hỏi rất dài\n"
        "\n"
        "Với category \"trick\" và \"nonsense\", ground_truth phải mô tả bot nên trả lời thế nào.\n"
        "Với category \"casual\", ground_truth mô tả cách bot redirect về dịch vụ.\n"
        "Với category \"followup\", question nên bao gồm context (ví dụ: \"thế còn loại đắt nhất?\").\n"
        "expected_sources là list tên tài liệu liên quan (rỗng nếu không áp dụng)."
    )

    user_prompt = (
        f"Tài liệu:\n{full_content}\n\n"
        f"Danh sách tài liệu: {_json.dumps(doc_names, ensure_ascii=False)}\n\n"
        f"Sinh ĐÚNG {total_requested} câu hỏi theo phân bổ: {dist_text}\n\n"
        "Format JSON object:\n"
        "{\n"
        "  \"questions\": [\n"
        "    {\n"
        "      \"id\": \"gs-001\",\n"
        "      \"question\": \"câu hỏi tiếng Việt\",\n"
        "      \"ground_truth\": \"câu trả lời chính xác hoặc mô tả expected behavior\",\n"
        "      \"difficulty\": \"easy|medium|hard\",\n"
        "      \"category\": \"tên category\",\n"
        "      \"expected_sources\": [\"tên tài liệu\"]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "LƯU Ý:\n"
        "- ground_truth phải chính xác theo nội dung tài liệu (trích dẫn số liệu, giá cụ thể)\n"
        "- Mỗi câu hỏi phải có id format \"gs-XXX\" (XXX = số thứ tự 001, 002, ...)\n"
        "- Trả về ĐÚNG JSON object, KHÔNG kèm ```json``` hay text nào khác"
    )

    # ── 5. Resolve model + config ────────────────────────────────────────
    model_name = await cfg_svc.get("golden_dataset_model", None)
    if not model_name:
        model_name = await cfg_svc.get("llm_default_model", None)
    if not model_name:
        raise HTTPException(
            status_code=500,
            detail="Chưa cấu hình golden_dataset_model hoặc llm_default_model trong system_config",
        )

    max_tokens = await cfg_svc.get_int("golden_dataset_max_tokens", 16000)

    # ── 6. Call LLM ──────────────────────────────────────────────────────
    try:
        import litellm as _litellm  # noqa: PLC0415

        resp = await _litellm.acompletion(
            model=model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=max_tokens,
            timeout=120,
            response_format={"type": "json_object"},
        )
        raw_answer = resp.choices[0].message.content or ""
    except ImportError:
        raise HTTPException(status_code=500, detail="litellm chưa được cài đặt")
    except Exception as exc:  # noqa: BLE001 — LLM provider exception classes vary across litellm/httpx/openai. Map all to 5xx, preserve stack.
        logger.error(
            "generate_test_questions_llm_failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"LLM call failed: {type(exc).__name__}: {exc}") from exc

    # ── 7. Parse JSON response ───────────────────────────────────────────
    error_note = None
    try:
        cleaned = _re.sub(r"```(?:json)?\s*", "", raw_answer).strip()
        parsed = _json.loads(cleaned)
        # Support both {"questions": [...]} and bare [...]
        if isinstance(parsed, dict):
            questions = parsed.get("questions", [])
        elif isinstance(parsed, list):
            questions = parsed
        else:
            questions = []
    except (_json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        logger.warning("generate_test_questions_parse_failed", error=str(exc), raw=raw_answer[:500])
        questions = []
        error_note = f"LLM trả về JSON không hợp lệ: {type(exc).__name__}. Raw response đã được log."

    # ── 8. Postprocess: normalize id, difficulty, schema ─────────────────
    processed: list[dict] = []
    for i, q in enumerate(questions, 1):
        cat = q.get("category", "easy")
        difficulty = _CATEGORY_DIFFICULTY.get(cat, q.get("difficulty", "medium"))
        processed.append({
            "id": f"gs-{i:03d}",
            "question": q.get("question", ""),
            "ground_truth": q.get("ground_truth", ""),
            "difficulty": difficulty,
            "category": cat,
            "expected_sources": q.get("expected_sources", []),
        })
    questions = processed

    # ── 9. Count by_category + by_difficulty ─────────────────────────────
    by_category: dict[str, int] = {}
    by_difficulty: dict[str, int] = {}
    for q in questions:
        cat = q.get("category", "unknown")
        diff = q.get("difficulty", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1
        by_difficulty[diff] = by_difficulty.get(diff, 0) + 1

    # ── 10. Save output file ─────────────────────────────────────────────
    # Path traversal validation before file construction
    if not _re.match(r"^[a-zA-Z0-9_-]+$", bot_id):
        raise HTTPException(status_code=400, detail="Invalid bot_id format")
    if not _re.match(r"^[a-zA-Z0-9_-]+$", channel_type):
        raise HTTPException(status_code=400, detail="Invalid channel_type format")

    output_file = None
    try:
        output_dir = Path(__file__).resolve().parent.parent.parent.parent.parent.parent / "golden_set"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_bot_id = _re.sub(r"[^a-zA-Z0-9_-]", "_", bot_id)
        filename = f"{safe_bot_id}_{channel_type}.json"
        output_path = output_dir / filename

        dataset = {
            "dataset_version": "1.0",
            "domain": bot_id,
            "description": f"Auto-generated golden set for bot '{bot_id}' ({channel_type})",
            "generated_at": _dt.now(tz=_tz.utc).isoformat(),
            "source_documents": doc_names,
            "total_chunks": len(rows),
            "questions": questions,
        }
        with open(output_path, "w", encoding="utf-8") as f:
            _json.dump(dataset, f, ensure_ascii=False, indent=2)
        output_file = f"golden_set/{filename}"
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(
            "generate_test_questions_save_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # ── 11. Build response ───────────────────────────────────────────────
    result: dict[str, Any] = {
        "ok": True,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "questions": questions,
        "total": len(questions),
        "by_category": by_category,
        "by_difficulty": by_difficulty,
        "model_used": model_name,
        "chunks_loaded": len(rows),
        "output_file": output_file,
    }
    if error_note:
        result["ok"] = False
        result["error_note"] = error_note
        result["raw_response"] = raw_answer[:2000]
    return result


def _parse_result_timestamp(filename: str) -> str:
    """Trích xuất timestamp string từ tên file results_*.json.
    @param filename: e.g. "results_20260418_153000.json"
    @return: timestamp string e.g. "20260418_153000", hoặc "" nếu không match
    """
    import re as _re_mod
    m = _re_mod.search(r"results_(\d{8}_\d{6})\.json$", filename)
    return m.group(1) if m else ""


def _compute_dashboard(data: dict, weak_threshold: float) -> dict:
    """Tính toán dashboard summary từ 1 result file.
    @param data: parsed JSON từ results_*.json
    @param weak_threshold: ngưỡng correct_pct để coi là weak point
    @return: dict chứa metrics, by_category, by_difficulty, score, weak_points
    """
    ok_count = data.get("ok_count", 0)
    metrics = data.get("metrics", {})
    by_category = data.get("by_category", {})
    by_difficulty = data.get("by_difficulty", {})
    quality = metrics.get("quality_breakdown", {})
    correct_count = quality.get("correct", 0)

    # Score: (correct / ok_count) * 10, scale 0-10
    score = round((correct_count / ok_count) * 10, 2) if ok_count > 0 else 0.0

    # Weak points: categories or difficulties with correct_pct < threshold
    weak_points: list[dict[str, Any]] = []
    for cat_name, cat_data in by_category.items():
        pct = cat_data.get("correct_pct", 0.0)
        if pct < weak_threshold:
            weak_points.append({
                "type": "category",
                "name": cat_name,
                "correct_pct": pct,
            })
    for diff_name, diff_data in by_difficulty.items():
        pct = diff_data.get("correct_pct", 0.0)
        if pct < weak_threshold:
            weak_points.append({
                "type": "difficulty",
                "name": diff_name,
                "correct_pct": pct,
            })

    # Sort weak points by correct_pct ascending (worst first)
    weak_points.sort(key=lambda wp: wp["correct_pct"])

    return {
        "run_at": data.get("run_at", ""),
        "dataset": data.get("dataset", ""),
        "total_questions": data.get("total_questions", 0),
        "ok_count": ok_count,
        "error_count": data.get("error_count", 0),
        "metrics": {
            "avg_keyword_overlap": metrics.get("avg_keyword_overlap", 0.0),
            "avg_key_info_ratio": metrics.get("avg_key_info_ratio", 0.0),
            "source_hit_rate": metrics.get("source_hit_rate", 0.0),
            "avg_duration_ms": metrics.get("avg_duration_ms", 0.0),
            "total_cost_usd": metrics.get("total_cost_usd", 0.0),
            "quality_breakdown": quality,
        },
        "by_category": by_category,
        "by_difficulty": by_difficulty,
        "score": score,
        "weak_points": weak_points,
    }


@router.get("/bots/{bot_id}/{channel_type}/quality-dashboard")
async def quality_dashboard(bot_id: str, channel_type: str, request: Request) -> dict:
    """Quality Dashboard — đọc kết quả evaluation và trả summary cho dashboard.

    Scan golden_set/ directory cho result files, load file mới nhất,
    tính score + weak points, trả trend từ N evaluation gần nhất.

    @param bot_id: ID của bot
    @param channel_type: loại channel (web, zalo, messenger...)
    @return: dashboard summary JSON
    """
    import json as _json_mod
    import re as _re_local  # noqa: PLC0415

    _require_owner(request)

    # Path traversal validation before file path construction
    if not _re_local.match(r"^[a-zA-Z0-9_-]+$", bot_id):
        raise HTTPException(status_code=400, detail="Invalid bot_id format")
    if not _re_local.match(r"^[a-zA-Z0-9_-]+$", channel_type):
        raise HTTPException(status_code=400, detail="Invalid channel_type format")

    # Load config thresholds from system_config (zero hardcode)
    cfg_svc = _sys_config(request)
    trend_limit = await cfg_svc.get_int("quality_dashboard_trend_limit", 10)
    weak_threshold = await cfg_svc.get_int("quality_dashboard_weak_threshold", 60)

    # Scan golden_set/ directory for result files
    golden_dir = Path(__file__).resolve().parents[5] / "golden_set"
    if not golden_dir.is_dir():
        return {
            "ok": True,
            "bot_id": bot_id,
            "channel_type": channel_type,
            "latest_eval": None,
            "trend": [],
            "score": 0.0,
            "weak_points": [],
            "message": "No golden_set directory found",
        }

    # Collect all result files (general results_*.json)
    result_files: list[Path] = sorted(
        [f for f in golden_dir.glob("results_*.json") if f.is_file()],
        key=lambda p: _parse_result_timestamp(p.name),
        reverse=True,
    )

    # Also look for bot-specific golden set: {bot_id}_{channel_type}.json
    bot_specific = golden_dir / f"{bot_id}_{channel_type}.json"

    if not result_files:
        return {
            "ok": True,
            "bot_id": bot_id,
            "channel_type": channel_type,
            "latest_eval": None,
            "trend": [],
            "score": 0.0,
            "weak_points": [],
            "bot_specific_dataset": bot_specific.name if bot_specific.is_file() else None,
            "message": "No evaluation results found",
        }

    # Filter result files that match this bot (by bot_id in JSON content)
    matched_files: list[tuple[Path, dict]] = []
    for rf in result_files:
        try:
            raw = rf.read_text(encoding="utf-8")
            data = _json_mod.loads(raw)
        except (OSError, ValueError):
            logger.warning("quality_dashboard_skip_file", file=str(rf))
            continue
        # Match by bot_id and channel_type in result data
        file_bot = data.get("bot_id", "")
        file_channel = data.get("channel_type", "")
        if file_bot == bot_id and file_channel == channel_type:
            matched_files.append((rf, data))
        if len(matched_files) >= trend_limit:
            break

    if not matched_files:
        return {
            "ok": True,
            "bot_id": bot_id,
            "channel_type": channel_type,
            "latest_eval": None,
            "trend": [],
            "score": 0.0,
            "weak_points": [],
            "total_result_files": len(result_files),
            "bot_specific_dataset": bot_specific.name if bot_specific.is_file() else None,
            "message": f"No evaluation results found for bot {bot_id}:{channel_type}",
        }

    # Latest evaluation (first in sorted list = most recent)
    _latest_path, latest_data = matched_files[0]
    dashboard = _compute_dashboard(latest_data, float(weak_threshold))

    # Build trend from matched files (most recent first)
    trend: list[dict[str, Any]] = []
    for _tp, td in matched_files:
        quality = td.get("metrics", {}).get("quality_breakdown", {})
        td_ok = td.get("ok_count", 0)
        td_correct = quality.get("correct", 0)
        td_score = round((td_correct / td_ok) * 10, 2) if td_ok > 0 else 0.0
        trend.append({
            "run_at": td.get("run_at", ""),
            "dataset": td.get("dataset", ""),
            "total_questions": td.get("total_questions", 0),
            "ok_count": td_ok,
            "error_count": td.get("error_count", 0),
            "score": td_score,
            "avg_keyword_overlap": td.get("metrics", {}).get("avg_keyword_overlap", 0.0),
            "source_hit_rate": td.get("metrics", {}).get("source_hit_rate", 0.0),
            "quality_breakdown": quality,
        })

    return {
        "ok": True,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "latest_eval": {
            "run_at": dashboard["run_at"],
            "dataset": dashboard["dataset"],
            "total_questions": dashboard["total_questions"],
            "ok_count": dashboard["ok_count"],
            "error_count": dashboard["error_count"],
            "metrics": dashboard["metrics"],
            "by_category": dashboard["by_category"],
            "by_difficulty": dashboard["by_difficulty"],
        },
        "trend": trend,
        "score": dashboard["score"],
        "weak_points": dashboard["weak_points"],
        "bot_specific_dataset": bot_specific.name if bot_specific.is_file() else None,
    }


__all__ = [
    "router",
    "bot_audit_stats",
    "generate_test_questions",
    "quality_dashboard",
]
