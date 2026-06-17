#!/usr/bin/env python3
"""run_kich_ban_test_run.py — Phase-3-only runner cho KICH_BAN_TEST_RAGBOT_v1.

Chạy 43 câu hỏi từ ``golden_set/kich_ban_questions_v1.json`` qua endpoint
``/api/ragbot/test/chat`` với corpus HIỆN CÓ (không nuke DB), tổng hợp
metrics REAL theo category, ghi report JSON + Markdown vào ``reports/``.

3-key identity (tenant_id, bot_id, channel_type) đọc từ ENV:
    RAGBOT_TEST_TENANT_ID, RAGBOT_TEST_BOT_ID, RAGBOT_TEST_CHANNEL_TYPE
Token: tự mint qua /api/ragbot/test/tokens/self (yêu cầu
RAGBOT_DEV_TOKEN_ENABLED=true + loopback).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = REPO_ROOT / "golden_set" / "kich_ban_questions_v1.json"
REPORTS_DIR = REPO_ROOT / "reports"

# Refuse-pattern heuristic — fallback khi answer_type không trả "no_context"/"refused"
REFUSE_PATTERNS = (
    "chưa có thông tin",
    "không có thông tin",
    "không tìm thấy",
    "không thể trả lời",
    "vui lòng liên hệ",
    "liên hệ hotline",
    "xin lỗi",
)


def _classify(answer_type: str | None, answer: str) -> str:
    """Quy 3 trạng thái: answered / refused / unknown."""
    at = (answer_type or "").lower()
    if at in ("no_context", "refused", "refuse"):
        return "refused"
    low = (answer or "").lower()
    if any(p in low for p in REFUSE_PATTERNS):
        return "refused"
    if at == "answered" or answer:
        return "answered"
    return "unknown"


def _mint_token(base_url: str) -> str:
    with httpx.Client(timeout=10) as c:
        r = c.get(f"{base_url}/api/ragbot/test/tokens/self")
        r.raise_for_status()
        return r.json()["token"]


def _load_identity() -> tuple[int, str, str, str]:
    tenant_id = os.getenv("RAGBOT_TEST_TENANT_ID") or os.getenv("RAGBOT_LOAD_TENANT_ID") or "32"
    bot_id = os.getenv("RAGBOT_TEST_BOT_ID") or os.getenv("RAGBOT_LOAD_BOT_ID") or "thula-test-bot-v1"
    channel = os.getenv("RAGBOT_TEST_CHANNEL_TYPE", "web")
    base_url = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
    return int(tenant_id), bot_id, channel, base_url


async def run() -> int:
    tenant_id, bot_id, channel, base_url = _load_identity()
    token = os.getenv("RAGBOT_TEST_API_TOKEN") or _mint_token(base_url)

    qdata = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    questions: list[dict[str, Any]] = qdata["questions"]
    print(f"[KICHBAN] base={base_url} tenant_id={tenant_id} bot_id={bot_id} ch={channel}")
    print(f"[KICHBAN] questions={len(questions)} from {QUESTIONS_PATH.relative_to(REPO_ROOT)}")

    results: list[dict[str, Any]] = []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    t_start_all = time.perf_counter()

    async def _request_with_retry(payload: dict, attempts: int = 3) -> tuple[int, dict | str, float]:
        nonlocal token, headers
        last_status = 0
        last_body: dict | str = ""
        last_dt = 0.0
        for i in range(attempts):
            t0 = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=90) as cli:
                    r = await cli.post(
                        f"{base_url}/api/ragbot/test/chat",
                        headers=headers,
                        json=payload,
                    )
                    if r.status_code == 401:
                        new_token = _mint_token(base_url)
                        if new_token:
                            token = new_token
                            headers["Authorization"] = f"Bearer {token}"
                        r = await cli.post(
                            f"{base_url}/api/ragbot/test/chat",
                            headers=headers,
                            json=payload,
                        )
                    dt = (time.perf_counter() - t0) * 1000
                    if r.status_code == 200:
                        return 200, r.json(), dt
                    last_status, last_body, last_dt = r.status_code, r.text[:300], dt
                    if r.status_code in (502, 503, 504, 500):
                        await asyncio.sleep(3.0)
                        continue
                    return last_status, last_body, last_dt
            except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError) as exc:
                last_status, last_body, last_dt = -1, repr(exc), (time.perf_counter() - t0) * 1000
                # Server may be restarting — wait & retry
                await asyncio.sleep(5.0 + i * 3.0)
                # If self-token endpoint reachable again, refresh token
                try:
                    new_token = _mint_token(base_url)
                    if new_token:
                        token = new_token
                        headers["Authorization"] = f"Bearer {token}"
                except Exception:  # noqa: BLE001 — token renewal best-effort (continue with stale token)
                    pass
                continue
        return last_status, last_body, last_dt

    for q in questions:
        payload = {
            "tenant_id": tenant_id,
            "bot_id": bot_id,
            "channel_type": channel,
            "question": q["question"],
            "debug": "full",
        }
        status, body, dt = await _request_with_retry(payload)
        if status != 200 or not isinstance(body, dict):
            err_body = body if isinstance(body, str) else json.dumps(body)[:300]
            print(f"  [HTTP {status}] turn {q['turn_id']:>3} {q['category']:<10}  {err_body[:200]}")
            results.append({
                "turn_id": q["turn_id"],
                "category": q["category"],
                "question": q["question"],
                "expected_type": q["expected_type"],
                "answer": "",
                "answer_type": f"http_{status}",
                "actual_class": "error",
                "chunks_used": 0,
                "top_score": 0.0,
                "tokens": {},
                "cost_usd": 0.0,
                "duration_ms": dt,
                "verdict": "FAIL",
            })
            continue

        if True:
            data = body
            answer = data.get("answer") or ""
            atype = data.get("answer_type")
            actual_class = _classify(atype, answer)
            verdict = "PASS" if actual_class == q["expected_type"] else "FAIL"

            results.append({
                "turn_id": q["turn_id"],
                "category": q["category"],
                "question": q["question"],
                "expected_type": q["expected_type"],
                "answer": answer[:600],
                "answer_type": atype,
                "answer_reason": data.get("answer_reason"),
                "actual_class": actual_class,
                "chunks_used": data.get("chunks_used", 0) or 0,
                "top_score": float(data.get("top_score") or 0.0),
                "tokens": data.get("tokens") or {},
                "cost_usd": float(data.get("cost_usd") or 0.0),
                "duration_ms": dt,
                "verdict": verdict,
            })
            print(
                f"  [{q['turn_id']:>3}] {q['category']:<10} "
                f"exp={q['expected_type']:<8} got={actual_class:<8} "
                f"chunks={data.get('chunks_used',0)} "
                f"top={float(data.get('top_score') or 0.0):.3f} "
                f"{dt:>6.0f}ms  {q['question'][:55]}",
            )

    elapsed_total = time.perf_counter() - t_start_all
    print(f"\n[KICHBAN] Total elapsed: {elapsed_total:.1f}s")

    # Aggregate per category
    by_cat: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)

    summary: dict[str, dict[str, Any]] = {}
    for cat, rs in by_cat.items():
        n = len(rs)
        answered = sum(1 for r in rs if r["actual_class"] == "answered")
        refused = sum(1 for r in rs if r["actual_class"] == "refused")
        errors = sum(1 for r in rs if r["actual_class"] == "error")
        passes = sum(1 for r in rs if r["verdict"] == "PASS")
        summary[cat] = {
            "count": n,
            "answered": answered,
            "refused": refused,
            "errors": errors,
            "verdict_pass": passes,
            "pass_rate": round(passes / n, 4) if n else 0.0,
            "avg_latency_ms": round(sum(r["duration_ms"] for r in rs) / n, 1) if n else 0.0,
            "avg_top_score": round(sum(r["top_score"] for r in rs) / n, 4) if n else 0.0,
            "avg_chunks": round(sum(r["chunks_used"] for r in rs) / n, 2) if n else 0.0,
            "total_cost_usd": round(sum(r["cost_usd"] for r in rs), 6),
        }

    # Overall
    on_topic = [r for r in results if r["category"] in ("PRICE", "SERVICE", "INFO")]
    off_topic = [r for r in results if r["category"] in ("OFF_CORPUS", "NOISE")]
    overall = {
        "questions": len(results),
        "on_topic_count": len(on_topic),
        "off_topic_count": len(off_topic),
        "on_topic_answered": sum(1 for r in on_topic if r["actual_class"] == "answered"),
        "on_topic_refused": sum(1 for r in on_topic if r["actual_class"] == "refused"),
        "off_topic_refused": sum(1 for r in off_topic if r["actual_class"] == "refused"),
        "off_topic_hallucinated": sum(1 for r in off_topic if r["actual_class"] == "answered"),
        "verdict_pass_total": sum(1 for r in results if r["verdict"] == "PASS"),
        "answered_rate_on_topic": round(
            sum(1 for r in on_topic if r["actual_class"] == "answered") / len(on_topic), 4,
        ) if on_topic else 0.0,
        "refuse_correct_rate_off_topic": round(
            sum(1 for r in off_topic if r["actual_class"] == "refused") / len(off_topic), 4,
        ) if off_topic else 0.0,
        "avg_latency_ms": round(sum(r["duration_ms"] for r in results) / len(results), 1) if results else 0,
        "avg_top_score_on_topic": round(
            sum(r["top_score"] for r in on_topic) / len(on_topic), 4,
        ) if on_topic else 0.0,
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 6),
        "elapsed_s": round(elapsed_total, 1),
    }

    timestamp = time.strftime("%Y%m%d_%H%M")
    REPORTS_DIR.mkdir(exist_ok=True)
    json_path = REPORTS_DIR / f"kich_ban_test_v1_{timestamp}.json"
    json_path.write_text(
        json.dumps(
            {
                "metadata": {
                    "tenant_id": tenant_id,
                    "bot_id": bot_id,
                    "channel_type": channel,
                    "base_url": base_url,
                    "questions_file": str(QUESTIONS_PATH.relative_to(REPO_ROOT)),
                    "timestamp": timestamp,
                    "elapsed_s": overall["elapsed_s"],
                    "corpus_state": "3 docs, 13 chunks (Phase B current — NOT nuked)",
                },
                "overall": overall,
                "per_category": summary,
                "results": results,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[KICHBAN] JSON: {json_path}")

    md = []
    md.append(f"# Kịch bản test v1 — {timestamp}\n")
    md.append(
        f"- **Bot**: tenant_id={tenant_id} bot_id=`{bot_id}` channel=`{channel}`\n"
        f"- **Questions**: {len(results)} from `{QUESTIONS_PATH.relative_to(REPO_ROOT)}`\n"
        f"- **Corpus state**: 3 docs / 13 chunks (Phase B current — KHÔNG nuke DB)\n"
        f"- **Elapsed**: {overall['elapsed_s']}s\n",
    )
    md.append("\n## Overall\n\n")
    md.append("| Metric | Value |\n|---|---|\n")
    md.append(f"| Questions | {overall['questions']} |\n")
    md.append(f"| Verdict PASS | {overall['verdict_pass_total']} / {overall['questions']} ({overall['verdict_pass_total']*100//overall['questions']}%) |\n")
    md.append(f"| On-topic answered rate (PRICE+SERVICE+INFO) | {overall['answered_rate_on_topic']*100:.1f}% ({overall['on_topic_answered']}/{overall['on_topic_count']}) |\n")
    md.append(f"| Off-topic correct refuse rate | {overall['refuse_correct_rate_off_topic']*100:.1f}% ({overall['off_topic_refused']}/{overall['off_topic_count']}) |\n")
    md.append(f"| Off-topic hallucinated | {overall['off_topic_hallucinated']} |\n")
    md.append(f"| Avg latency | {overall['avg_latency_ms']} ms |\n")
    md.append(f"| Avg top_score (on-topic) | {overall['avg_top_score_on_topic']} |\n")
    md.append(f"| Total cost | ${overall['total_cost_usd']:.4f} |\n")

    md.append("\n## Per-category summary\n\n")
    md.append("| Category | N | Answered | Refused | Pass | Pass% | Avg lat ms | Avg top | Avg chunks | Cost $ |\n")
    md.append("|---|---|---|---|---|---|---|---|---|---|\n")
    for cat in ("PRICE", "SERVICE", "INFO", "OFF_CORPUS", "NOISE"):
        s = summary.get(cat)
        if not s:
            continue
        md.append(
            f"| {cat} | {s['count']} | {s['answered']} | {s['refused']} | {s['verdict_pass']} | "
            f"{s['pass_rate']*100:.1f}% | {s['avg_latency_ms']} | {s['avg_top_score']} | "
            f"{s['avg_chunks']} | {s['total_cost_usd']:.4f} |\n",
        )

    md.append("\n## Per-question results\n\n")
    md.append("| ID | Cat | Exp | Got | V | Chunks | Top | ms | Cost | Question |\n")
    md.append("|---|---|---|---|---|---|---|---|---|---|\n")
    for r in results:
        md.append(
            f"| {r['turn_id']} | {r['category']} | {r['expected_type']} | {r['actual_class']} | "
            f"{r['verdict']} | {r['chunks_used']} | {r['top_score']:.3f} | "
            f"{r['duration_ms']:.0f} | {r['cost_usd']:.4f} | {r['question'][:60]} |\n",
        )

    md.append("\n## Sample answers (first 8)\n\n")
    for r in results[:8]:
        md.append(
            f"**Q{r['turn_id']} [{r['category']}]** {r['question']}\n\n"
            f"- expected: `{r['expected_type']}` got: `{r['actual_class']}` "
            f"(answer_type={r['answer_type']}, reason={r.get('answer_reason')})\n"
            f"- chunks={r['chunks_used']} top={r['top_score']:.4f} cost=${r['cost_usd']:.4f}\n"
            f"- answer: {r['answer'][:240]}\n\n",
        )

    md_path = REPORTS_DIR / f"kich_ban_test_v1_{timestamp}.md"
    md_path.write_text("".join(md), encoding="utf-8")
    print(f"[KICHBAN] MD  : {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
