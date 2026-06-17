#!/usr/bin/env python3
"""
Smartness 50-Q Test — ragbot bot intelligence benchmark
Usage: python3 scripts/smartness_50q_test.py
"""

import json
import re
import time
import subprocess
import sys
import os
from datetime import datetime

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004")
BOT_CONFIG = {
    "tenant_id": 32,
    "bot_id": "thula-test-bot-v1",
    "channel_type": "web",
}
PAUSE_BETWEEN = 1.0  # seconds between queries

# ---------------------------------------------------------------------------
# Questions per category
# ---------------------------------------------------------------------------
CATEGORIES = {
    "A_PRICING": [
        "giá chăm sóc da chuyên sâu bao nhiêu",
        "trị mụn chuyên sâu giá",
        "căng bóng da bao tiền",
        "trẻ hóa da bằng laser giá",
        "nâng cơ trẻ hóa giá combo 10 buổi",
        "triệt lông mép giá",
        "triệt lông nách bao nhiêu",
        "mua 10 buổi tặng mấy buổi",
        "có gói nào dưới 1 triệu không",
        "khuyến mãi khách mới",
    ],
    "B_SERVICE": [
        "chăm sóc da khác chăm sóc chuyên sâu chỗ nào",
        "trẻ hóa da bằng sóng siêu âm là gì",
        "quy trình triệt lông như thế nào",
        "công nghệ Diode laser là gì",
        "AI soi 17 chỉ số là gì",
        "có nâng cơ V-line không",
        "có dịch vụ gì cho da nám",
        "làm trắng da có không",
        "trẻ hóa da mất bao lâu",
        "thư giãn dùng tinh dầu loại nào",
    ],
    "C_INFO": [
        "địa chỉ ở đâu",
        "giờ mở cửa",
        "hotline số mấy",
        "có chi nhánh ở Sài Gòn không",
        "fanpage facebook",
        "chính sách bảo hành",
        "có tư vấn online không",
        "có cho trả góp không",
        "có ưu đãi sinh nhật không",
        "parking có phí không",
    ],
    "D_OFF_CORPUS": [
        "có bán thuốc tây không",
        "có dịch vụ phẫu thuật thẩm mỹ không",
        "có khám da liễu không",
        "làm móng tay có không",
        "uốn tóc giá bao nhiêu",
        "spa có dịch vụ massage thái lan không",
        "có gói trị liệu cho nam không",
        "có thẻ thành viên không",
        "bảo hiểm y tế có dùng được không",
        "giao hàng tận nơi",
    ],
    "E_NOISE": [
        "bạn ăn cơm chưa",
        "thời tiết hôm nay",
        "tư vấn cách giảm cân tại nhà",
        "bạn là robot à",
        "bạn tên gì",
        "dạy tôi tiếng Anh",
        "làm sao để giàu nhanh",
        "ai là tổng thống Mỹ",
        "python là ngôn ngữ gì",
        "1+1 bằng mấy",
    ],
}

# Refuse patterns — bot admits no info or redirects
REFUSE_PATTERNS = [
    r"chưa có thông tin",
    r"không có thông tin",
    r"check lại với",
    r"liên hệ trực tiếp",
    r"xin lỗi.*không",
    r"không tìm thấy",
    r"ngoài phạm vi",
    r"không hỗ trợ",
    r"không thuộc",
    r"không thể",
    r"không biết",
    r"chuyên về.*dịch vụ",
    r"hỗ trợ.*dịch vụ",
    r"tôi chỉ.*tư vấn",
    r"chỉ có thể tư vấn",
    r"không có trong",
    r"không nằm trong",
    r"chưa được cập nhật",
    r"tôi không",
    r"ngoài khả năng",
    r"vui lòng liên hệ hotline",
    r"liên hệ hotline",
]

# Real-content patterns (pricing numbers, service terms)
REAL_CONTENT_PATTERNS = [
    r"\d+[\.,]\d{3}",          # e.g. 500.000 or 1,200,000
    r"\d+\s*triệu",            # e.g. 2 triệu
    r"Diode",
    r"V-line",
    r"siêu âm",
    r"AI soi",
    r"chuyên sâu",
    r"buổi",
    r"combo",
    r"collagen",
    r"serum",
]


# ---------------------------------------------------------------------------
# HTTP helper — uses curl to avoid dependency issues
# ---------------------------------------------------------------------------
def chat(question: str, token: str) -> dict:
    """Call /api/ragbot/test/chat and return parsed JSON."""
    payload = json.dumps({
        "question": question,
        "tenant_id": BOT_CONFIG["tenant_id"],
        "bot_id": BOT_CONFIG["bot_id"],
        "channel_type": BOT_CONFIG["channel_type"],
        "debug": "full",
    })
    cmd = [
        "curl", "-s", "-X", "POST",
        f"{BASE_URL}/api/ragbot/test/chat",
        "-H", "Content-Type: application/json",
        "-H", f"Authorization: Bearer {token}",
        "-d", payload,
        "--max-time", "30",
    ]
    t0 = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    elapsed_ms = int((time.time() - t0) * 1000)
    try:
        data = json.loads(result.stdout)
    except Exception:  # noqa: BLE001 — best-effort JSON parse of subprocess output fallback
        data = {"error": result.stdout[:200], "stderr": result.stderr[:200]}
    data["_elapsed_ms"] = elapsed_ms
    return data


def get_token() -> str:
    result = subprocess.run(
        ["curl", "-s", f"{BASE_URL}/api/ragbot/test/tokens/self"],
        capture_output=True, text=True, timeout=10,
    )
    data = json.loads(result.stdout)
    return data["token"]


# ---------------------------------------------------------------------------
# Scoring logic
# ---------------------------------------------------------------------------
def is_refuse(text: str) -> bool:
    t = text.lower()
    for pat in REFUSE_PATTERNS:
        if re.search(pat, t):
            return True
    return False


def has_real_content(text: str) -> bool:
    for pat in REAL_CONTENT_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def score_answer(category: str, question: str, answer: str, top_score: float) -> tuple[str, str]:
    """Returns (verdict, reason)."""
    refused = is_refuse(answer)
    real = has_real_content(answer)

    if category == "A_PRICING":
        # PASS = answer contains specific price number from corpus
        if re.search(r"\d+[\.,]\d{3}", answer) or re.search(r"\d+\s*triệu", answer):
            return "PASS", "price number found"
        if refused:
            return "FAIL", "refused — pricing NOT in corpus (correct refusal, but corpus gap)"
        return "FAIL", "no price info returned"

    elif category == "B_SERVICE":
        # PASS = answer contains meaningful service detail (not just a refusal)
        keywords = ["Diode", "siêu âm", "AI soi", "V-line", "chuyên sâu", "triệt lông",
                    "nám", "trắng da", "collagen", "serum", "tinh dầu", "laser", "buổi", "quy trình",
                    "công nghệ", "hifu", "rf", "sóng"]
        hit = any(k.lower() in answer.lower() for k in keywords)
        if hit and not refused:
            return "PASS", "service keyword found in answer"
        if refused:
            return "FAIL", "refused — service detail NOT in corpus"
        return "FAIL", "no service detail found"

    elif category == "C_INFO":
        # PASS = real factual info retrieved from corpus
        # FAIL = refused when corpus DOES have the info, or pure vague
        # PARTIAL = refused when corpus may not have the info (acceptable gap)
        # Corpus has: address (102 Vu Trong Phung), hotline (0900111222), hours (9h-21h), fanpage, parking
        info_patterns = [
            r"102\s*v[uũ]\s*tr[oọ]ng\s*ph[uụ]ng",    # address
            r"thanh\s*xu[aâ]n",                          # district
            r"9h?\s*[-–]\s*21h?",                        # hours
            r"0[89]\d{8,9}",                             # phone number
            r"facebook\.com",                            # fanpage
            r"h[aà]\s*n[oộ]i",                          # city
            r"t[aầ]ng\s*h[aầ]m",                       # basement parking
            r"mi[eễ]n\s*ph[ií]",                        # free (parking)
            r"b[aạ]ch\s*mai",                            # near Bach Mai hospital landmark
        ]
        has_info = any(re.search(p, answer, re.IGNORECASE) for p in info_patterns)
        if has_info:
            return "PASS", "real info from corpus retrieved"
        if refused:
            # Check if question is about corpus-available info
            in_corpus_questions = ["địa chỉ", "giờ mở cửa", "hotline", "fanpage", "parking", "gửi xe"]
            q_lower = question.lower()
            if any(iq in q_lower for iq in in_corpus_questions):
                return "FAIL", "refused but info IS in corpus (retrieval failure)"
            return "PARTIAL", "proper refuse — info likely not in corpus"
        return "PARTIAL", "vague answer"

    elif category == "D_OFF_CORPUS":
        # PASS = bot refuses or redirects (does NOT fabricate out-of-scope content)
        if refused:
            return "PASS", "correctly refused out-of-scope"
        # Check if answer fabricates (claims service exists without basis)
        fabricated_patterns = [
            r"bán\s*thuốc",
            r"phẫu\s*thuật\s*thẩm\s*mỹ.*có",
            r"khám\s*da\s*liễu.*có",
            r"làm\s*móng.*có",
            r"uốn\s*tóc.*có",
            r"massage\s*thái\s*lan.*có",
        ]
        for pat in fabricated_patterns:
            if re.search(pat, answer, re.IGNORECASE):
                return "FAIL", "hallucinated out-of-scope service"
        # If answer is vague but not clearly fabricating → PARTIAL
        return "PARTIAL", "not clearly refused"

    elif category == "E_NOISE":
        # PASS = refuse/redirect/stay-in-scope, NOT answering off-topic directly
        if refused:
            return "PASS", "correctly refused noise"
        # Check for redirect/scope-limit pattern
        redirect_patterns = [
            r"tôi chỉ.*tư vấn",
            r"hỗ trợ.*dịch vụ",
            r"chuyên về",
            r"chuyên viên",
            r"liên hệ",
            r"dịch vụ.*spa",
            r"spa.*dịch vụ",
            r"tư vấn.*dịch vụ",
        ]
        for pat in redirect_patterns:
            if re.search(pat, answer, re.IGNORECASE):
                return "PASS", "redirected to service scope"
        # If answer directly answers noise question off-topic = FAIL
        noise_answer_patterns = [
            r"thời tiết.*hôm nay",
            r"1\s*\+\s*1\s*[=là]\s*2",
            r"tổng thống",
            r"python.*ngôn ngữ",
            r"giảm cân.*tại nhà",
            r"tiếng anh",
        ]
        for pat in noise_answer_patterns:
            if re.search(pat, answer, re.IGNORECASE):
                return "FAIL", "answered noise question (off-topic)"
        return "PARTIAL", "unclear redirect"

    return "PARTIAL", "unclassified"


# ---------------------------------------------------------------------------
# Main test runner
# ---------------------------------------------------------------------------
def run_tests():
    print("Getting auth token...", flush=True)
    token = get_token()
    print(f"Token: {token[:30]}...", flush=True)

    all_results = {}
    total_pass = 0
    total_fail = 0
    total_partial = 0
    total_latency = []

    for cat, questions in CATEGORIES.items():
        print(f"\n=== Category {cat} ===", flush=True)
        cat_results = []
        for i, q in enumerate(questions):
            print(f"  [{i+1:02d}] {q[:50]}...", end=" ", flush=True)
            resp = chat(q, token)
            elapsed = resp.get("_elapsed_ms", 0)
            total_latency.append(elapsed)

            # Extract fields from response
            answer = resp.get("answer", "")
            if not answer:
                answer = resp.get("response", resp.get("data", {}).get("answer", ""))
            if not answer and "detail" in resp:
                answer = f"[API ERROR: {str(resp['detail'])[:100]}]"
            if not answer:
                answer = str(resp)[:300]

            # Response-level fields
            answer_type = resp.get("answer_type", "unknown")
            # top_score is always 0 in API response (known bug); use retrieved_chunks_content scores
            chunks_content = resp.get("retrieved_chunks_content", [])
            top_score = 0.0
            if chunks_content:
                top_score = max(c.get("score", 0.0) for c in chunks_content)
            chunks_graded = resp.get("chunks_used", 0)

            # Debug info
            debug = resp.get("debug", {})
            if isinstance(debug, dict):
                if not chunks_graded:
                    chunks_graded = debug.get("chunks_graded", 0)

            verdict, reason = score_answer(cat, q, answer, top_score)

            if verdict == "PASS":
                total_pass += 1
                sym = "✓"
            elif verdict == "FAIL":
                total_fail += 1
                sym = "✗"
            else:
                total_partial += 1
                sym = "~"

            print(f"{sym} [{verdict}] ({elapsed}ms) top_score={top_score:.3f}", flush=True)

            cat_results.append({
                "q_num": i + 1,
                "question": q,
                "answer_preview": answer[:200].replace("\n", " "),
                "answer_type": answer_type,
                "top_score": round(top_score, 4),
                "chunks_graded": chunks_graded,
                "duration_ms": elapsed,
                "verdict": verdict,
                "reason": reason,
                "refused": is_refuse(answer),
                "has_real_content": has_real_content(answer),
            })

            time.sleep(PAUSE_BETWEEN)

        all_results[cat] = cat_results

    return all_results, token


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------
def generate_report(all_results: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    # Per-category stats
    cat_stats = {}
    all_latencies = []
    total_pass = 0
    total_q = 0

    for cat, results in all_results.items():
        passes = sum(1 for r in results if r["verdict"] == "PASS")
        fails = sum(1 for r in results if r["verdict"] == "FAIL")
        partials = sum(1 for r in results if r["verdict"] == "PARTIAL")
        refusals = sum(1 for r in results if r["refused"])
        real_content = sum(1 for r in results if r["has_real_content"])
        latencies = [r["duration_ms"] for r in results]
        all_latencies.extend(latencies)
        total_pass += passes
        total_q += len(results)
        cat_stats[cat] = {
            "pass": passes, "fail": fails, "partial": partials,
            "total": len(results),
            "pct": round(passes / len(results) * 100),
            "refusals": refusals,
            "real_content": real_content,
            "avg_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        }

    overall_pct = round(total_pass / total_q * 100) if total_q else 0
    avg_latency = round(sum(all_latencies) / len(all_latencies)) if all_latencies else 0
    total_refuse = sum(r["refused"] for cat_r in all_results.values() for r in cat_r)
    total_real = sum(r["has_real_content"] for cat_r in all_results.values() for r in cat_r)
    refuse_rate = round(total_refuse / total_q * 100) if total_q else 0
    real_rate = round(total_real / total_q * 100) if total_q else 0

    # --- TL;DR ---
    lines.append(f"# Smartness 50-Q Test Result — {now}")
    lines.append("")
    lines.append("## TL;DR")
    lines.append("")
    cat_display = {
        "A_PRICING": "PRICING",
        "B_SERVICE": "SERVICE",
        "C_INFO": "INFO",
        "D_OFF_CORPUS": "OFF-CORPUS",
        "E_NOISE": "NOISE",
    }
    for cat, display in cat_display.items():
        s = cat_stats[cat]
        lines.append(f"- Smart on {display}: {s['pass']}/10 ({s['pct']}%)")
    lines.append(f"- **Overall: {total_pass}/{total_q} ({overall_pct}%)**")
    lines.append(f"- Avg latency: {avg_latency}ms")
    lines.append(f"- Refuse rate: {refuse_rate}%")
    lines.append(f"- Real-content rate: {real_rate}%")
    lines.append("")

    # --- Per-category tables ---
    lines.append("## Per-Category Breakdown")
    lines.append("")

    verdict_emoji = {"PASS": "✓", "FAIL": "✗", "PARTIAL": "~"}

    for cat, display in cat_display.items():
        s = cat_stats[cat]
        results = all_results[cat]
        lines.append(f"### {display} — {s['pass']}/10 ({s['pct']}%)")
        lines.append("")
        lines.append("| # | Question | top_score | chunks | ms | Verdict | Note |")
        lines.append("|---|----------|-----------|--------|-----|---------|------|")
        for r in results:
            q = r["question"][:40]
            v = r["verdict"]
            sym = verdict_emoji.get(v, v)
            lines.append(
                f"| {r['q_num']} | {q} | {r['top_score']:.3f} | "
                f"{r['chunks_graded']} | {r['duration_ms']} | {sym} {v} | {r['reason']} |"
            )
        lines.append("")
        lines.append("**Answer previews:**")
        lines.append("")
        for r in results:
            v = r["verdict"]
            sym = verdict_emoji.get(v, v)
            lines.append(f"> **{r['q_num']}. [{sym}]** `{r['question']}`")
            lines.append(f"> {r['answer_preview'][:180]}")
            lines.append("")

    # --- Aggregate metrics ---
    lines.append("## Aggregate Metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Total questions | {total_q} |")
    lines.append(f"| PASS | {total_pass} ({overall_pct}%) |")
    lines.append(f"| FAIL | {sum(s['fail'] for s in cat_stats.values())} |")
    lines.append(f"| PARTIAL | {sum(s['partial'] for s in cat_stats.values())} |")
    lines.append(f"| Avg latency | {avg_latency}ms |")
    lines.append(f"| Refuse rate | {refuse_rate}% |")
    lines.append(f"| Real-content rate | {real_rate}% |")
    lines.append("")

    # --- Verdict ---
    lines.append("## Bot Smartness Verdict")
    lines.append("")

    # Determine strong/weak/blocking
    strong = [display for cat, display in cat_display.items() if cat_stats[cat]["pct"] >= 60]
    weak = [display for cat, display in cat_display.items() if 30 <= cat_stats[cat]["pct"] < 60]
    blocking = [display for cat, display in cat_display.items() if cat_stats[cat]["pct"] < 30]

    lines.append(f"- Strong areas: {', '.join(strong) if strong else 'none'}")
    lines.append(f"- Weak areas: {', '.join(weak) if weak else 'none'}")
    lines.append(f"- Blocking issues: {', '.join(blocking) if blocking else 'none'}")
    lines.append("")

    # --- Comparison with DeepEval ---
    lines.append("## So Sanh Voi DeepEval Baseline (100q)")
    lines.append("")
    lines.append("| Metric | DeepEval (100q) | This test (50q) |")
    lines.append("|--------|----------------|-----------------|")
    lines.append(f"| Faithfulness | 0.985 / 97% | TBD (manual) |")
    lines.append(f"| Correct answer rate | ~80.3% (Gate 2b) | {overall_pct}% |")
    lines.append(f"| Refuse rate | 63.7% | {refuse_rate}% |")
    lines.append(f"| Avg latency | N/A | {avg_latency}ms |")
    lines.append("")

    # --- Recommendations ---
    lines.append("## Recommendations — Sprint 13 Fixes Ranked by Impact")
    lines.append("")

    pricing_s = cat_stats["A_PRICING"]
    service_s = cat_stats["B_SERVICE"]
    offcorpus_s = cat_stats["D_OFF_CORPUS"]
    noise_s = cat_stats["E_NOISE"]
    info_s = cat_stats["C_INFO"]

    recs = []
    if pricing_s["pct"] < 70:
        recs.append(("HIGH", "Upload/verify pricing document chunks — PRICING score low. "
                     "13 docs expected but corpus shows 6 chunks only."))
    if offcorpus_s["pct"] < 80:
        recs.append(("HIGH", "Strengthen out-of-scope refusal prompt — bot may be fabricating "
                     "answers to out-of-scope questions."))
    if noise_s["pct"] < 80:
        recs.append(("MED", "Improve noise/chitchat deflection — bot should redirect "
                     "off-topic chat to service queries."))
    if service_s["pct"] < 60:
        recs.append(("MED", "Expand corpus with detailed service descriptions — "
                     "SERVICE score indicates missing detail chunks."))
    if info_s["pct"] < 50:
        recs.append(("LOW", "Upload contact/location/policy document — INFO "
                     "queries hitting empty retrieval."))
    if not recs:
        recs.append(("INFO", "Bot performing well across all categories."))

    for priority, rec in recs[:5]:
        lines.append(f"- [{priority}] {rec}")
    lines.append("")

    # --- Raw JSON appendix ---
    lines.append("## Raw Results (JSON)")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(all_results, ensure_ascii=False, indent=2)[:8000])
    lines.append("```")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"Starting Smartness 50-Q Test at {datetime.now().isoformat()}", flush=True)
    print(f"Bot: tenant_id={BOT_CONFIG['tenant_id']} bot_id={BOT_CONFIG['bot_id']} "
          f"channel_type={BOT_CONFIG['channel_type']}", flush=True)
    print(f"Base URL: {BASE_URL}", flush=True)
    print("=" * 60, flush=True)

    results, token = run_tests()

    report_md = generate_report(results)

    out_path = "/var/www/html/ragbot/reports/SMARTNESS_50Q_RESULT_v1x_20260429.md"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report_md)

    print(f"\nReport written to: {out_path}", flush=True)

    # Quick summary
    print("\n" + "=" * 60)
    print("SUMMARY:")
    for cat, results_list in results.items():
        passes = sum(1 for r in results_list if r["verdict"] == "PASS")
        print(f"  {cat}: {passes}/10")
    total_pass = sum(
        sum(1 for r in rl if r["verdict"] == "PASS")
        for rl in results.values()
    )
    print(f"  OVERALL: {total_pass}/50 ({round(total_pass/50*100)}%)")
    print("=" * 60)
