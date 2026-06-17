"""Multi-turn dialogue integration test framework — catches HALLU drift across turns.

Replaces load test pattern (`run_*_phase5_verify.py` family) that uses
``connect_id = hash(question)`` — different connect_id per query → no
history accumulation → CANNOT detect multi-turn HALLU (service conflate,
price flip-flop, carry-over wrong context, etc.).

This test uses STICKY connect_id across entire dialogue (matches real UI),
so ``conversation_history`` accumulates and exposes the drift patterns the
load test misses.

Each ``DialogueFlow`` carries hand-crafted ``ground_truth`` per turn
(facts MUST appear in answer + facts that would constitute HALLU).
Verdict is computed automatically against the contract — no LLM judge
involved, removing the tolerant-judge false-positive that masked
2026-05-29 evening multi-turn HALLU on test-spa-id.

Run manually (not invoked by `pytest` by default — requires live API):

    python tests/integration/test_multiturn_dialogue.py

Or with a single bot filter:

    python tests/integration/test_multiturn_dialogue.py spa_booking_flow
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path("/var/www/html/ragbot")
sys.path.insert(0, str(ROOT / "src"))

ENV = ROOT / ".env"
for line in ENV.read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip().strip('"')

BASE = "http://localhost:3004/api/ragbot/test"


# ----------------------------------------------------------------------- #
# Data model                                                                #
# ----------------------------------------------------------------------- #
@dataclass(frozen=True)
class GroundTruth:
    """Per-turn contract — what MUST and MUST NOT appear in answer."""

    turn: int
    # Lower-cased substrings that MUST appear in answer (fact present)
    must_contain: tuple[str, ...] = ()
    # Lower-cased substrings whose presence = HALLU (fact fabricated)
    must_not_contain: tuple[str, ...] = ()
    # Free-text describing what this turn checks
    description: str = ""


@dataclass(frozen=True)
class DialogueFlow:
    """One end-to-end multi-turn conversation with sticky connect_id."""

    name: str
    bot_id: str
    channel_type: str
    turns: tuple[str, ...]
    ground_truth: tuple[GroundTruth, ...]
    description: str = ""


@dataclass
class TurnResult:
    turn: int
    question: str
    answer: str
    answer_type: str
    chunks_used: int
    top_score: float
    latency_s: float
    must_contain_passed: list[str]
    must_contain_missing: list[str]
    must_not_contain_passed: list[str]  # not present, OK
    must_not_contain_violations: list[str]  # HALLU detected
    verdict: str  # "pass" | "partial" | "hallu" | "error"
    sources: list[dict] = field(default_factory=list)
    raw_response: dict = field(default_factory=dict)


# ----------------------------------------------------------------------- #
# HTTP client                                                               #
# ----------------------------------------------------------------------- #
def _fresh_token() -> str:
    with urllib.request.urlopen(f"{BASE}/tokens/self", timeout=10) as r:
        return json.loads(r.read())["token"]


def _chat(bot_id: str, channel_type: str, question: str, connect_id: str) -> dict:
    """One turn — uses sticky connect_id so history accumulates."""
    token = _fresh_token()
    body = {
        "bot_id": bot_id,
        "channel_type": channel_type,
        "question": question,
        "connect_id": connect_id,
        # bypass_cache=false → cache có thể hit cross-turn (giống UI thật)
    }
    req = urllib.request.Request(
        f"{BASE}/chat",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        t = time.time()
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
            data["_latency_s"] = time.time() - t
            return data
    except Exception as e:
        return {"ok": False, "error": str(e)[:200], "_latency_s": 0}


# ----------------------------------------------------------------------- #
# Dialogue runner                                                           #
# ----------------------------------------------------------------------- #
def run_flow(flow: DialogueFlow) -> list[TurnResult]:
    """Run one dialogue. STICKY connect_id across all turns."""
    connect_id = f"multiturn-{flow.name}-{int(time.time())}"
    results: list[TurnResult] = []

    for i, q in enumerate(flow.turns, 1):
        r = _chat(flow.bot_id, flow.channel_type, q, connect_id)
        gt = flow.ground_truth[i - 1] if i - 1 < len(flow.ground_truth) else GroundTruth(turn=i)

        if not r.get("ok"):
            results.append(TurnResult(
                turn=i, question=q, answer="", answer_type="error",
                chunks_used=0, top_score=0.0,
                latency_s=r.get("_latency_s", 0),
                must_contain_passed=[], must_contain_missing=list(gt.must_contain),
                must_not_contain_passed=[], must_not_contain_violations=[],
                verdict="error", raw_response=r,
            ))
            continue

        ans = (r.get("answer", "") or "").lower()
        must_contain_passed = [s for s in gt.must_contain if s.lower() in ans]
        must_contain_missing = [s for s in gt.must_contain if s.lower() not in ans]
        must_not_contain_violations = [s for s in gt.must_not_contain if s.lower() in ans]
        must_not_contain_passed = [s for s in gt.must_not_contain if s.lower() not in ans]

        # Verdict logic:
        # - hallu: bot stated something it MUST NOT (fabrication)
        # - pass: all must_contain present + 0 violations
        # - partial: some must_contain missing but 0 violations
        if must_not_contain_violations:
            verdict = "hallu"
        elif not must_contain_missing:
            verdict = "pass"
        else:
            verdict = "partial"

        results.append(TurnResult(
            turn=i, question=q,
            answer=r.get("answer", "") or "",
            answer_type=r.get("answer_type", "?"),
            chunks_used=r.get("chunks_used", 0),
            top_score=r.get("top_score", 0.0),
            latency_s=r.get("_latency_s", 0),
            must_contain_passed=must_contain_passed,
            must_contain_missing=must_contain_missing,
            must_not_contain_passed=must_not_contain_passed,
            must_not_contain_violations=must_not_contain_violations,
            verdict=verdict,
            sources=[
                {
                    "score": s.get("score", 0),
                    "preview": (s.get("preview") or "")[:200],
                    "chunk_id": s.get("chunk_id", ""),
                }
                for s in (r.get("sources", []) or [])[:5]
            ],
            raw_response=r,
        ))

        # Delay so DB commits history row + ZE rate-limit breathes
        # (CB trip at burst >10 req/s on free tier; 2s = safe)
        time.sleep(2.0)

    return results


# ----------------------------------------------------------------------- #
# Hand-crafted dialogue flows                                               #
# ----------------------------------------------------------------------- #
# All flows below are derived from corpus chunks. The ground_truth
# contracts encode WHAT facts the bot SHOULD say and what facts would
# constitute HALLU. Updating corpus → may require updating these flows.

SPA_BOOKING_FLOW = DialogueFlow(
    name="spa_booking_drift",
    bot_id="test-spa-id",
    channel_type="web",
    description="Reproduces 10-turn HALLU pattern verified 2026-05-29",
    turns=(
        "tôi cần tư vấn",
        "tư vấn về da",
        "da thải độc",
        "tôi muốn đặt lịch",
        "luan 0353988280, sáng thứ 7",
        "quy trình cho dịch vụ này như nào",
        "giá dịch vụ sao",
        "có ưu đãi hay khuyến mãi cho dịch vụ này không",
        "địa chỉ bên mình ở đâu ạ",
        "em đặt lịch thế nào",
    ),
    ground_truth=(
        GroundTruth(turn=1, must_contain=("tư vấn",), description="bot acks clarify intent"),
        GroundTruth(turn=2, must_contain=("da",), description="bot acks da-focus"),
        # Turn 3: user said "da thải độc" → bot should retrieve "Thải độc da 800K"
        # BP-3 violation: bot must NOT say "PAYOT" or "Gym Beauté 42 bước" for Thải độc da
        # (those are features of Detox Ballet, different service)
        GroundTruth(
            turn=3,
            must_contain=("800",),
            must_not_contain=("payot", "gym beauté"),
            description="BP-3 check: feature must bind to chunk source",
        ),
        # Turn 4: user said "tôi muốn đặt lịch" — should clarify which service
        # BP-1 violation: bot must NOT fabricate "chăm sóc da chuyên sâu thải độc"
        # (no such service in corpus)
        GroundTruth(
            turn=4,
            must_not_contain=("chăm sóc da chuyên sâu thải độc",),
            description="BP-1 check: no fabricated service name",
        ),
        # Turn 5: slots provided — bot should ack name+phone+time
        GroundTruth(
            turn=5,
            must_contain=("luân", "0353988280"),
            description="BP-4 check: inline slot ack",
        ),
        # Turn 6: "quy trình" — should reference exactly the service user chose
        # If service was "thải độc da" (800K), quy trình quote from Thải độc da chunk
        # NOT from "chăm sóc da chuyên sâu" (199K) chunk
        GroundTruth(
            turn=6,
            must_contain=("bước",),
            description="quy trình mentions steps",
        ),
        # Turn 7: "giá dịch vụ sao" — must match service locked from earlier turn
        # If turn 3 quoted 800K for Thải độc da, turn 7 must also say 800K
        # BP-2 violation: flip-flop 800K↔199K = price inconsistency
        GroundTruth(
            turn=7,
            must_contain=("800",),
            must_not_contain=("199",),
            description="BP-2 check: price consistency (locked at turn 3)",
        ),
        # Turn 8: "ưu đãi" — must be the promo of locked service, not other service
        # Thải độc da KHÔNG có promo 199K (that's chăm sóc da chuyên sâu)
        GroundTruth(
            turn=8,
            must_not_contain=("199.000 đồng/buổi", "199k/buổi"),
            description="BP-2 + BP-6 check: no cross-service promo borrow",
        ),
        # Turn 9: địa chỉ — should quote literal from corpus, not refuse
        # BP-5 fix verify
        GroundTruth(
            turn=9,
            must_contain=("102 vũ trọng phụng", "thanh xuân"),
            description="BP-5 check: address allowed_facts whitelist",
        ),
        GroundTruth(
            turn=10,
            must_contain=("đặt lịch", "thông tin"),
            description="booking flow ack",
        ),
    ),
)


SPA_CROSS_COMPARE_FLOW = DialogueFlow(
    name="spa_cross_compare",
    bot_id="test-spa-id",
    channel_type="web",
    description="Cross-compare with verdict (rule 16 inheritance)",
    turns=(
        "tôi muốn biết về dịch vụ triệt lông",
        "giá triệt lông nách bao nhiêu",
        "so sánh với triệt cả chân thì sao",
    ),
    ground_truth=(
        GroundTruth(turn=1, must_contain=("triệt lông",)),
        GroundTruth(
            turn=2,
            must_contain=("199",),
            must_not_contain=("massage", "gội đầu"),
        ),
        # Turn 3: cross-compare — rule 16 + rule 21 require verdict + chunk binding
        # If retrieve returns both chunks (nách 199K + cả chân 699K), bot must verdict
        # If only 1 chunk returns, bot must apply rule 10 partial
        GroundTruth(
            turn=3,
            must_contain=("199",),
            must_not_contain=("toàn thân",),  # don't conflate cả chân with toàn thân (CSV row 7 vs 11)
            description="BP-3 + BP-1 check: cross-row anti-conflate",
        ),
    ),
)


LUAT_CROSS_VERDICT_FLOW = DialogueFlow(
    name="luat_cross_verdict",
    bot_id="luat-giao-thong",
    channel_type="web",
    description="Cross-bot rule 16 inheritance from platform tier",
    turns=(
        "tôi muốn hỏi về phạt giao thông",
        "vượt đèn đỏ xe máy phạt bao nhiêu",
        "vi phạm tốc độ với vượt đèn đỏ, cái nào nặng hơn",
    ),
    ground_truth=(
        GroundTruth(turn=1, must_contain=("phạt", "giao thông")),
        GroundTruth(turn=2, must_contain=("800",)),  # 800K-1M xe máy
        # Turn 3: cross-compare must have verdict
        # If platform rule 16 inherited correctly → bot gives explicit conclusion
        GroundTruth(
            turn=3,
            description="rule 16 verdict structure expected; lenient on words",
        ),
    ),
)


HOA_HOC_FLOW = DialogueFlow(
    name="hoa_hoc_basic",
    bot_id="hoa-hoc-10",
    channel_type="web",
    description="Basic factoid + cross-bot domain leak check",
    turns=(
        "phản ứng giữa naoh và hcl tạo gì",
        "ngoài ra còn phản ứng nào tạo nacl",
    ),
    ground_truth=(
        GroundTruth(
            turn=1,
            must_contain=("nacl", "h2o"),
            must_not_contain=("triệt lông", "gội đầu", "massage", "medispa"),  # cross-bot leak check
            description="Domain-neutral platform rule must NOT leak spa text",
        ),
        GroundTruth(
            turn=2,
            must_not_contain=("triệt lông", "gội đầu", "massage", "medispa"),
        ),
    ),
)


OOS_TRAP_FLOW = DialogueFlow(
    name="oos_trap",
    bot_id="test-spa-id",
    channel_type="web",
    description="OOS refuse should NOT leak to next-turn answer",
    turns=(
        "thời tiết hà nội hôm nay thế nào",
        "tôi muốn hỏi về dịch vụ trị mụn",
    ),
    ground_truth=(
        GroundTruth(
            turn=1,
            must_not_contain=("medispa", "dịch vụ"),  # OOS refuse should be generic
        ),
        GroundTruth(turn=2, must_contain=("mụn",)),
    ),
)


ALL_FLOWS = (
    SPA_BOOKING_FLOW,
    SPA_CROSS_COMPARE_FLOW,
    LUAT_CROSS_VERDICT_FLOW,
    HOA_HOC_FLOW,
    OOS_TRAP_FLOW,
)


# ----------------------------------------------------------------------- #
# Reporter                                                                  #
# ----------------------------------------------------------------------- #
def _emoji(verdict: str) -> str:
    return {"pass": "✅", "partial": "🟡", "hallu": "🔴", "error": "❌"}.get(verdict, "?")


def print_flow_report(flow: DialogueFlow, results: list[TurnResult]) -> dict:
    """Print human-readable report; return summary stats."""
    print(f"\n{'='*100}")
    print(f"FLOW: {flow.name}  ({flow.bot_id})")
    print(f"  {flow.description}")
    print(f"{'='*100}")

    n_pass = 0
    n_partial = 0
    n_hallu = 0
    n_error = 0

    for r in results:
        em = _emoji(r.verdict)
        print(f"\n{em} Turn {r.turn}: {r.question}")
        print(f"   verdict={r.verdict} chunks={r.chunks_used} top={r.top_score:.3f} lat={r.latency_s:.1f}s")
        print(f"   ANSWER: {r.answer[:250]}")

        if r.must_contain_missing:
            print(f"   ⚠️  must_contain MISSING: {r.must_contain_missing}")
        if r.must_not_contain_violations:
            print(f"   🔴 HALLU detected: {r.must_not_contain_violations}")
        if r.must_contain_passed:
            print(f"   ✓ must_contain present: {r.must_contain_passed}")

        if r.verdict == "pass":
            n_pass += 1
        elif r.verdict == "partial":
            n_partial += 1
        elif r.verdict == "hallu":
            n_hallu += 1
        else:
            n_error += 1

    total = len(results)
    summary = {
        "flow": flow.name,
        "total": total,
        "pass": n_pass,
        "partial": n_partial,
        "hallu": n_hallu,
        "error": n_error,
        "hallu_rate": n_hallu / total if total else 0.0,
    }
    print(f"\n  → {n_pass}/{total} pass, {n_partial} partial, {n_hallu} HALLU, {n_error} error")
    print(f"  → HALLU rate: {summary['hallu_rate']*100:.1f}%")
    return summary


def run_all(filter_name: str | None = None) -> dict:
    """Run all flows (or filter by name). Return aggregate report."""
    flows = [f for f in ALL_FLOWS if filter_name is None or f.name == filter_name]
    if not flows:
        print(f"No flow matches '{filter_name}'. Available: {[f.name for f in ALL_FLOWS]}")
        return {}

    all_summaries = []
    all_results = {}
    for flow in flows:
        print(f"\n🚀 Running flow: {flow.name} ({len(flow.turns)} turns)...")
        results = run_flow(flow)
        summary = print_flow_report(flow, results)
        all_summaries.append(summary)
        all_results[flow.name] = [
            {
                "turn": r.turn,
                "question": r.question,
                "answer": r.answer,
                "verdict": r.verdict,
                "must_contain_missing": r.must_contain_missing,
                "must_not_contain_violations": r.must_not_contain_violations,
                "chunks_used": r.chunks_used,
                "top_score": r.top_score,
                "latency_s": r.latency_s,
            }
            for r in results
        ]

    # Aggregate
    total_turns = sum(s["total"] for s in all_summaries)
    total_hallu = sum(s["hallu"] for s in all_summaries)
    total_pass = sum(s["pass"] for s in all_summaries)
    total_partial = sum(s["partial"] for s in all_summaries)
    total_error = sum(s["error"] for s in all_summaries)

    print(f"\n{'='*100}")
    print(f"AGGREGATE — {len(flows)} flow(s), {total_turns} turns total")
    print(f"{'='*100}")
    print(f"  ✅ pass:     {total_pass}/{total_turns} ({total_pass/total_turns*100:.1f}%)")
    print(f"  🟡 partial:  {total_partial}/{total_turns} ({total_partial/total_turns*100:.1f}%)")
    print(f"  🔴 HALLU:    {total_hallu}/{total_turns} ({total_hallu/total_turns*100:.1f}%)")
    print(f"  ❌ error:    {total_error}/{total_turns}")

    out_path = f"/tmp/multiturn_aggregate_{int(time.time())}.json"
    with open(out_path, "w") as f:
        json.dump({"summaries": all_summaries, "results": all_results}, f, ensure_ascii=False, indent=2)
    print(f"\n💾 Saved → {out_path}")

    return {
        "total_turns": total_turns,
        "total_pass": total_pass,
        "total_partial": total_partial,
        "total_hallu": total_hallu,
        "total_error": total_error,
        "summaries": all_summaries,
        "out_path": out_path,
    }


if __name__ == "__main__":
    filter_name = sys.argv[1] if len(sys.argv) > 1 else None
    run_all(filter_name)
