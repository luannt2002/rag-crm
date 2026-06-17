#!/usr/bin/env python3
"""RAGAS-style evaluation script for Ragbot.

Loads a golden dataset, sends each question to the chat API,
compares answers against ground truth, and outputs metrics.

Hỗ trợ:
- Follow-up question chains (câu hỏi nối tiếp, không xoá history)
- Multi-room testing (xoá history giữa các category)
- Scoring nâng cao: answer_contains_key_info, response_quality

Usage:
    python scripts/evaluate_ragas.py --bot-id <test-bot-id> --dataset golden_set/<dataset>.json
    python scripts/evaluate_ragas.py --bot-id <test-bot-id> --dataset golden_set/<dataset>.json --base-url http://<server-host>:3004
    # Or use env default: export RAGBOT_TEST_BOT_ID=test-bot-v1

Outputs metrics: answer_keyword_overlap, source_hit_rate, avg_response_time
Results saved to golden_set/results_{timestamp}.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
from pathlib import Path

import requests

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
DEFAULT_CHANNEL = "web"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

# Vietnamese stop words to ignore in keyword overlap
STOP_WORDS = frozenset(
    "là và của có được cho với trong này đó để từ một các không"
    " cũng như đã sẽ khi tại đến hay hoặc nếu thì mà về bị vì"
    " trên dưới ngoài sau trước giữa theo bao nhiêu bao".split()
)

# Thresholds for response_quality classification
QUALITY_CORRECT_THRESHOLD = 0.45
QUALITY_PARTIAL_THRESHOLD = 0.20


# ── Helpers ─────────────────────────────────────────────────────────────────
def _tokenize(text: str) -> set[str]:
    """Tách text thành set các từ có nghĩa (lowercase, bỏ stop words, bỏ ký tự đặc biệt)."""
    words = re.findall(r"[\w\d]+", text.lower())
    return {w for w in words if w not in STOP_WORDS and len(w) > 1}


def _keyword_overlap(answer: str, ground_truth: str) -> float:
    """Tỉ lệ keyword overlap = |intersection| / |union|. Trả về 0.0-1.0."""
    a_tokens = _tokenize(answer)
    gt_tokens = _tokenize(ground_truth)
    if not gt_tokens:
        return 1.0 if not a_tokens else 0.0
    union = a_tokens | gt_tokens
    if not union:
        return 1.0
    return len(a_tokens & gt_tokens) / len(union)


def _extract_key_info(text: str) -> set[str]:
    """Trích xuất thông tin then chốt: số, giá tiền, tên riêng, thời gian.

    Tìm các pattern quan trọng trong ground_truth mà answer BẮT BUỘC phải chứa
    để được coi là đúng: giá tiền (100k, 200.000đ), số (30 phút), tên riêng (viết hoa).
    """
    key_items: set[str] = set()
    # Giá tiền: 100k, 200K, 100.000đ, 500,000 VND, etc.
    prices = re.findall(r"\d[\d.,]*\s*(?:k|K|đ|VND|vnđ|triệu|nghìn|ngàn)", text)
    for p in prices:
        # Normalize: bỏ dấu cách, lowercase
        key_items.add(re.sub(r"\s+", "", p.lower()))
    # Số thuần (>=2 chữ số) — thường là thời gian, số lượng
    numbers = re.findall(r"\b\d{2,}\b", text)
    key_items.update(numbers)
    # Phần trăm
    percents = re.findall(r"\d+\s*%", text)
    key_items.update(p.replace(" ", "") for p in percents)
    return key_items


def _answer_contains_key_info(answer: str, ground_truth: str) -> tuple[bool, float]:
    """Kiểm tra answer có chứa các thông tin then chốt từ ground_truth không.

    Returns:
        (contains_all, ratio): contains_all=True nếu tất cả key info đều có,
        ratio = số key info tìm thấy / tổng key info.
    """
    key_info = _extract_key_info(ground_truth)
    if not key_info:
        return True, 1.0  # Không có key info cần kiểm tra => pass

    answer_lower = re.sub(r"\s+", "", answer.lower())
    found = 0
    for info in key_info:
        info_norm = re.sub(r"\s+", "", info.lower())
        if info_norm in answer_lower:
            found += 1
    ratio = found / len(key_info)
    return found == len(key_info), ratio


def _classify_quality(
    overlap: float, key_info_ratio: float, has_answer: bool
) -> str:
    """Phân loại chất lượng câu trả lời.

    Returns: "correct" / "partial" / "wrong" / "no_answer"
    """
    if not has_answer:
        return "no_answer"
    # Kết hợp overlap và key_info_ratio (trọng số 60/40)
    combined = overlap * 0.6 + key_info_ratio * 0.4
    if combined >= QUALITY_CORRECT_THRESHOLD:
        return "correct"
    if combined >= QUALITY_PARTIAL_THRESHOLD:
        return "partial"
    return "wrong"


def _source_hit(expected_sources: list[str], actual_sources: list[dict]) -> bool:
    """Kiểm tra ít nhất 1 expected source xuất hiện trong actual sources (fuzzy substring)."""
    if not expected_sources:
        return True  # no expectation = auto pass
    actual_names = [s.get("document_name", "").lower() for s in actual_sources]
    for exp in expected_sources:
        exp_lower = exp.lower()
        for actual_name in actual_names:
            if exp_lower in actual_name or actual_name in exp_lower:
                return True
    return False


def _get_token(base_url: str) -> str:
    """Lấy self-service token từ API."""
    url = f"{base_url}{SELF_TOKEN_PATH}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Failed to get self-token: {data}")
    return data["token"]


def _chat(
    base_url: str, token: str, bot_id: str, channel_type: str, question: str
) -> dict:
    """Gửi 1 câu hỏi đến chat API, trả về full response dict."""
    url = f"{base_url}{CHAT_PATH}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"bot_id": bot_id, "channel_type": channel_type, "question": question}
    t0 = time.perf_counter()
    resp = requests.post(url, json=payload, headers=headers, timeout=120)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    try:
        data = resp.json()
    except Exception:  # noqa: BLE001 — best-effort HTTP response parse fallback
        data = {"ok": False, "error": resp.text[:500]}
    data["_client_duration_ms"] = elapsed_ms
    data["_status_code"] = resp.status_code
    return data


def _clear_history(base_url: str, token: str, bot_id: str, channel_type: str) -> None:
    """Xoá chat history trước khi eval để tránh context leak giữa các câu hỏi."""
    url = f"{base_url}/api/ragbot/test/chat/clear"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        requests.post(
            url,
            json={"bot_id": bot_id, "channel_type": channel_type},
            headers=headers,
            timeout=15,
        )
    except Exception:  # noqa: BLE001 — non-critical cleanup request (fire-and-forget)
        pass  # not critical


def _group_by_category(questions: list[dict]) -> OrderedDict[str, list[dict]]:
    """Nhóm câu hỏi theo category, giữ nguyên thứ tự xuất hiện."""
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for q in questions:
        cat = q.get("category", "unknown")
        if cat not in groups:
            groups[cat] = []
        groups[cat].append(q)
    return groups


def _is_followup_chain(question_obj: dict) -> bool:
    """Kiểm tra câu hỏi có phải follow-up chain hay không (chứa | trong question)."""
    return (
        question_obj.get("category") == "followup"
        and "|" in question_obj.get("question", "")
    )


# ── Main evaluation ────────────────────────────────────────────────────────
def run_evaluation(
    dataset_path: str,
    bot_id: str,
    base_url: str,
    channel_type: str,
    delay: float,
    filter_category: str | None = None,
    filter_difficulty: str | None = None,
) -> dict:
    """Chạy evaluation loop, trả về summary dict.

    Logic xoá history:
    - Xoá history giữa các CATEGORY (multi-room testing)
    - KHÔNG xoá history giữa các câu trong cùng category
    - Follow-up chain (question chứa |): gửi tuần tự, chỉ eval câu cuối
    - Câu đơn thường: xoá history trước mỗi câu (trừ khi cùng category với câu trước)
    """
    # Load dataset
    ds_path = Path(dataset_path)
    if not ds_path.is_absolute():
        ds_path = Path.cwd() / ds_path
    if not ds_path.exists():
        print(f"[ERROR] Dataset not found: {ds_path}", file=sys.stderr)
        sys.exit(1)

    with open(ds_path, encoding="utf-8") as f:
        dataset = json.load(f)

    questions = dataset.get("questions", [])
    if not questions:
        print("[ERROR] No questions in dataset", file=sys.stderr)
        sys.exit(1)

    # Apply filters
    if filter_category:
        questions = [q for q in questions if q.get("category") == filter_category]
    if filter_difficulty:
        questions = [q for q in questions if q.get("difficulty") == filter_difficulty]

    total = len(questions)
    print(f"\n{'='*60}")
    print(f"  RAGBOT EVALUATION — {dataset.get('domain', 'unknown')}")
    print(f"  Dataset: {ds_path.name} v{dataset.get('dataset_version', '?')}")
    print(f"  Questions: {total}  |  Bot: {bot_id}  |  Channel: {channel_type}")
    print(f"  Base URL: {base_url}")
    print(f"{'='*60}\n")

    # Get token
    print("[1/3] Fetching self-service token...")
    try:
        token = _get_token(base_url)
        print(f"  Token acquired (first 8 chars: {token[:8]}...)")
    except Exception as exc:
        print(f"[ERROR] Cannot get token: {exc}", file=sys.stderr)
        sys.exit(1)

    # Group by category for multi-room testing
    category_groups = _group_by_category(questions)

    # Run questions grouped by category
    print(f"[2/3] Running {total} questions across {len(category_groups)} categories...\n")
    results = []
    question_num = 0

    for cat_idx, (category, cat_questions) in enumerate(category_groups.items()):
        # Clear history between categories (multi-room)
        print(f"  --- Category: {category} ({len(cat_questions)} questions) ---")
        _clear_history(base_url, token, bot_id, channel_type)
        if cat_idx > 0:
            print(f"  [history cleared for new category]")

        for q_idx, q in enumerate(cat_questions):
            question_num += 1
            qid = q["id"]
            question = q["question"]
            ground_truth = q["ground_truth"]
            expected_sources = q.get("expected_sources", [])
            difficulty = q.get("difficulty", "?")
            q_category = q.get("category", "?")

            # --- Follow-up chain ---
            if _is_followup_chain(q):
                chain = [s.strip() for s in question.split("|")]
                print(
                    f"  [{question_num:>2}/{total}] {qid} ({difficulty}/{q_category}): "
                    f"CHAIN[{len(chain)}] {chain[0][:40]}...",
                    end=" ",
                    flush=True,
                )
                # Clear history before chain starts
                _clear_history(base_url, token, bot_id, channel_type)

                resp = None
                for ci, chain_q in enumerate(chain):
                    if delay > 0 and ci > 0:
                        time.sleep(delay)
                    resp = _chat(base_url, token, bot_id, channel_type, chain_q)
                    if not resp.get("ok"):
                        break  # Stop chain on error

                # Evaluate only the LAST answer
                if resp and resp.get("ok"):
                    answer = resp.get("answer", "")
                    sources = resp.get("sources", [])
                    duration_ms = resp.get("duration_ms", resp.get("_client_duration_ms", 0))
                    overlap = _keyword_overlap(answer, ground_truth)
                    src_hit = _source_hit(expected_sources, sources)
                    ki_all, ki_ratio = _answer_contains_key_info(answer, ground_truth)
                    quality = _classify_quality(overlap, ki_ratio, bool(answer.strip()))
                    status = "ok"
                    print(
                        f"overlap={overlap:.2f} key={ki_ratio:.0%} "
                        f"quality={quality} src={'HIT' if src_hit else 'MISS'} {duration_ms}ms"
                    )
                else:
                    answer = ""
                    sources = []
                    duration_ms = resp.get("_client_duration_ms", 0) if resp else 0
                    overlap = 0.0
                    src_hit = False
                    ki_all, ki_ratio = False, 0.0
                    quality = "no_answer"
                    status = "error"
                    error_detail = (
                        resp.get("detail", resp.get("error", "unknown")) if resp else "no response"
                    )
                    print(f"ERROR: {str(error_detail)[:60]}")

                results.append({
                    "id": qid,
                    "question": question,
                    "chain_questions": chain,
                    "ground_truth": ground_truth,
                    "answer": answer,
                    "difficulty": difficulty,
                    "category": q_category,
                    "expected_sources": expected_sources,
                    "actual_sources": [s.get("document_name", "") for s in sources],
                    "keyword_overlap": round(overlap, 4),
                    "key_info_ratio": round(ki_ratio, 4),
                    "key_info_complete": ki_all,
                    "response_quality": quality,
                    "source_hit": src_hit,
                    "duration_ms": duration_ms,
                    "status": status,
                    "chunks_used": resp.get("chunks_used", 0) if resp else 0,
                    "tokens": resp.get("tokens", {}) if resp else {},
                    "cost_usd": resp.get("cost_usd", 0) if resp else 0,
                })

            # --- Single question (not a chain) ---
            else:
                print(
                    f"  [{question_num:>2}/{total}] {qid} ({difficulty}/{q_category}): "
                    f"{question[:50]}...",
                    end=" ",
                    flush=True,
                )

                # Clear history between individual questions within same category
                # (only for non-followup categories)
                if q_idx > 0 and q_category != "followup":
                    _clear_history(base_url, token, bot_id, channel_type)

                resp = _chat(base_url, token, bot_id, channel_type, question)

                if resp.get("ok"):
                    answer = resp.get("answer", "")
                    sources = resp.get("sources", [])
                    duration_ms = resp.get("duration_ms", resp.get("_client_duration_ms", 0))
                    overlap = _keyword_overlap(answer, ground_truth)
                    src_hit = _source_hit(expected_sources, sources)
                    ki_all, ki_ratio = _answer_contains_key_info(answer, ground_truth)
                    quality = _classify_quality(overlap, ki_ratio, bool(answer.strip()))
                    status = "ok"
                    print(
                        f"overlap={overlap:.2f} key={ki_ratio:.0%} "
                        f"quality={quality} src={'HIT' if src_hit else 'MISS'} {duration_ms}ms"
                    )
                else:
                    answer = ""
                    sources = []
                    duration_ms = resp.get("_client_duration_ms", 0)
                    overlap = 0.0
                    src_hit = False
                    ki_all, ki_ratio = False, 0.0
                    quality = "no_answer"
                    status = "error"
                    error_detail = resp.get("detail", resp.get("error", "unknown"))
                    print(f"ERROR: {str(error_detail)[:60]}")

                results.append({
                    "id": qid,
                    "question": question,
                    "ground_truth": ground_truth,
                    "answer": answer,
                    "difficulty": difficulty,
                    "category": q_category,
                    "expected_sources": expected_sources,
                    "actual_sources": [s.get("document_name", "") for s in sources],
                    "keyword_overlap": round(overlap, 4),
                    "key_info_ratio": round(ki_ratio, 4),
                    "key_info_complete": ki_all,
                    "response_quality": quality,
                    "source_hit": src_hit,
                    "duration_ms": duration_ms,
                    "status": status,
                    "chunks_used": resp.get("chunks_used", 0),
                    "tokens": resp.get("tokens", {}),
                    "cost_usd": resp.get("cost_usd", 0),
                })

            # Delay between requests
            if delay > 0 and question_num < total:
                time.sleep(delay)

    # ── Compute summary ────────────────────────────────────────────────────
    ok_results = [r for r in results if r["status"] == "ok"]
    error_count = total - len(ok_results)

    avg_overlap = sum(r["keyword_overlap"] for r in ok_results) / len(ok_results) if ok_results else 0
    avg_key_info = sum(r["key_info_ratio"] for r in ok_results) / len(ok_results) if ok_results else 0
    source_hit_rate = sum(1 for r in ok_results if r["source_hit"]) / len(ok_results) if ok_results else 0
    avg_duration = sum(r["duration_ms"] for r in ok_results) / len(ok_results) if ok_results else 0
    total_cost = sum(r["cost_usd"] for r in results)

    # Quality counts
    quality_counts = defaultdict(int)
    for r in ok_results:
        quality_counts[r["response_quality"]] += 1

    # Category breakdown
    cat_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "overlap_sum": 0.0, "hit_sum": 0, "ki_sum": 0.0, "quality": defaultdict(int)}
    )
    for r in ok_results:
        cat = r["category"]
        cat_stats[cat]["count"] += 1
        cat_stats[cat]["overlap_sum"] += r["keyword_overlap"]
        cat_stats[cat]["hit_sum"] += 1 if r["source_hit"] else 0
        cat_stats[cat]["ki_sum"] += r["key_info_ratio"]
        cat_stats[cat]["quality"][r["response_quality"]] += 1

    # Difficulty breakdown
    diff_stats: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "overlap_sum": 0.0, "hit_sum": 0, "ki_sum": 0.0, "quality": defaultdict(int)}
    )
    for r in ok_results:
        d = r["difficulty"]
        diff_stats[d]["count"] += 1
        diff_stats[d]["overlap_sum"] += r["keyword_overlap"]
        diff_stats[d]["hit_sum"] += 1 if r["source_hit"] else 0
        diff_stats[d]["ki_sum"] += r["key_info_ratio"]
        diff_stats[d]["quality"][r["response_quality"]] += 1

    summary = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(ds_path.name),
        "dataset_version": dataset.get("dataset_version"),
        "bot_id": bot_id,
        "channel_type": channel_type,
        "base_url": base_url,
        "total_questions": total,
        "ok_count": len(ok_results),
        "error_count": error_count,
        "metrics": {
            "avg_keyword_overlap": round(avg_overlap, 4),
            "avg_key_info_ratio": round(avg_key_info, 4),
            "source_hit_rate": round(source_hit_rate, 4),
            "avg_duration_ms": round(avg_duration, 1),
            "total_cost_usd": round(total_cost, 6),
            "quality_breakdown": dict(quality_counts),
        },
        "by_category": {
            cat: {
                "count": s["count"],
                "avg_overlap": round(s["overlap_sum"] / s["count"], 4) if s["count"] else 0,
                "avg_key_info": round(s["ki_sum"] / s["count"], 4) if s["count"] else 0,
                "source_hit_rate": round(s["hit_sum"] / s["count"], 4) if s["count"] else 0,
                "quality": dict(s["quality"]),
                "correct_count": s["quality"].get("correct", 0),
                "correct_pct": round(s["quality"].get("correct", 0) / s["count"] * 100, 1) if s["count"] else 0,
            }
            for cat, s in sorted(cat_stats.items())
        },
        "by_difficulty": {
            d: {
                "count": s["count"],
                "avg_overlap": round(s["overlap_sum"] / s["count"], 4) if s["count"] else 0,
                "avg_key_info": round(s["ki_sum"] / s["count"], 4) if s["count"] else 0,
                "source_hit_rate": round(s["hit_sum"] / s["count"], 4) if s["count"] else 0,
                "quality": dict(s["quality"]),
                "correct_count": s["quality"].get("correct", 0),
                "correct_pct": round(s["quality"].get("correct", 0) / s["count"] * 100, 1) if s["count"] else 0,
            }
            for d, s in sorted(diff_stats.items())
        },
        "results": results,
    }

    # ── Print summary table ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  EVALUATION SUMMARY")
    print(f"{'='*60}")
    print(f"  Total questions:      {total}")
    print(f"  Successful:           {len(ok_results)}")
    print(f"  Errors:               {error_count}")
    print(f"  Avg keyword overlap:  {avg_overlap:.2%}")
    print(f"  Avg key info ratio:   {avg_key_info:.2%}")
    print(f"  Source hit rate:       {source_hit_rate:.2%}")
    print(f"  Avg response time:    {avg_duration:.0f}ms")
    print(f"  Total cost:           ${total_cost:.4f}")

    # Quality breakdown
    correct = quality_counts.get("correct", 0)
    partial = quality_counts.get("partial", 0)
    wrong = quality_counts.get("wrong", 0)
    no_ans = quality_counts.get("no_answer", 0)
    ok_total = len(ok_results) or 1
    print(f"\n  Quality: correct={correct} partial={partial} wrong={wrong} no_answer={no_ans}")

    # Per-category results table
    print(f"\n  {'='*56}")
    print(f"  === RESULTS BY CATEGORY ===")
    print(f"  {'Category':<16} {'Correct':>9} {'Total':>6} {'Pct':>7} {'Overlap':>9}")
    print(f"  {'-'*50}")
    for cat, s in sorted(cat_stats.items()):
        c_correct = s["quality"].get("correct", 0)
        co = s["overlap_sum"] / s["count"] if s["count"] else 0
        pct = c_correct / s["count"] * 100 if s["count"] else 0
        print(f"  {cat:<16} {c_correct:>7}/{s['count']:<3} {s['count']:>5} {pct:>6.1f}% {co:>8.2%}")

    overall_correct = quality_counts.get("correct", 0)
    overall_pct = overall_correct / ok_total * 100
    print(f"  {'-'*50}")
    print(f"  {'OVERALL':<16} {overall_correct:>7}/{ok_total:<3} {ok_total:>5} {overall_pct:>6.1f}% {avg_overlap:>8.2%}")

    # Per-difficulty results table
    print(f"\n  {'Difficulty':<16} {'Correct':>9} {'Total':>6} {'Pct':>7} {'Overlap':>9}")
    print(f"  {'-'*50}")
    for d, s in sorted(diff_stats.items()):
        d_correct = s["quality"].get("correct", 0)
        do = s["overlap_sum"] / s["count"] if s["count"] else 0
        pct = d_correct / s["count"] * 100 if s["count"] else 0
        print(f"  {d:<16} {d_correct:>7}/{s['count']:<3} {s['count']:>5} {pct:>6.1f}% {do:>8.2%}")

    # ── Low-scoring questions ──────────────────────────────────────────────
    low = [r for r in ok_results if r["response_quality"] in ("wrong", "no_answer")]
    if low:
        print(f"\n  WRONG / NO_ANSWER — {len(low)} questions:")
        for r in low:
            print(f"    {r['id']} ({r['difficulty']}/{r['category']}): "
                  f"quality={r['response_quality']} overlap={r['keyword_overlap']:.2%}")
            q_display = r["question"]
            if len(q_display) > 70:
                q_display = q_display[:70] + "..."
            print(f"      Q: {q_display}")
            print(f"      Expected: {r['ground_truth'][:70]}")
            print(f"      Got:      {r['answer'][:70]}")

    print(f"\n{'='*60}\n")

    return summary


def save_results(summary: dict, output_dir: str) -> str:
    """Lưu kết quả ra file JSON, trả về đường dẫn file."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"results_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"  Results saved to: {out_path}")
    return str(out_path)


# ── CLI ─────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="RAGAS-style evaluation for Ragbot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--bot-id",
        default=os.getenv("RAGBOT_TEST_BOT_ID"),
        required=not os.getenv("RAGBOT_TEST_BOT_ID"),
        help="Bot ID to evaluate (default from RAGBOT_TEST_BOT_ID env var)",
    )
    parser.add_argument(
        "--dataset", required=True,
        help="Path to golden dataset JSON (e.g. golden_set/spa_salon.json)",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Ragbot API base URL (default: {DEFAULT_BASE_URL}, env: RAGBOT_BASE_URL)",
    )
    parser.add_argument(
        "--channel", default=DEFAULT_CHANNEL,
        help=f"Channel type (default: {DEFAULT_CHANNEL})",
    )
    parser.add_argument(
        "--delay", type=float, default=1.0,
        help="Delay in seconds between requests (default: 1.0)",
    )
    parser.add_argument(
        "--output-dir", default=None,
        help="Directory for results (default: same dir as dataset)",
    )
    parser.add_argument(
        "--category", default=None,
        help="Filter by category (e.g. pricing, info, comparison, followup)",
    )
    parser.add_argument(
        "--difficulty", default=None, choices=["easy", "medium", "hard"],
        help="Filter by difficulty level",
    )
    args = parser.parse_args()

    summary = run_evaluation(
        dataset_path=args.dataset,
        bot_id=args.bot_id,
        base_url=args.base_url,
        channel_type=args.channel,
        delay=args.delay,
        filter_category=args.category,
        filter_difficulty=args.difficulty,
    )

    output_dir = args.output_dir or str(Path(args.dataset).parent)
    save_results(summary, output_dir)


if __name__ == "__main__":
    main()
