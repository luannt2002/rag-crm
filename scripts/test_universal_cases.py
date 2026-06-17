#!/usr/bin/env python3
"""Universal 96-case load test harness — parses UNIVERSAL_CASE_STUDIES_20260430.md.

Domain-neutral. Bot owner provides:
  --bot-id, --tenant-id, --channel-type
  --corpus-mapping-file (optional JSON dict to substitute <placeholder> tokens)

Auto-classifier uses STRUCTURAL behavior rules (refuse pattern match, intent
label, length threshold, redirect-pattern match) — NO brand/tenant literal.

Usage:
  python3 scripts/test_universal_cases.py \\
      --bot-id <bot> --tenant-id <tid> --channel-type <ch> \\
      --corpus-mapping-file /tmp/<bot>_mapping.json \\
      --output /tmp/universal_$(date +%Y%m%d_%H%M).json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Single source of truth for refuse-pattern fragments — DRY across the 3
# load-test harnesses. See `shared.constants.DEFAULT_LOADTEST_REFUSE_PATTERNS`.
_REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))
from ragbot.shared.constants import DEFAULT_LOADTEST_REFUSE_PATTERNS  # noqa: E402

# === Module constants — NO inline magic numbers below ===
DEFAULT_BASE_URL = "http://localhost:3004"
DEFAULT_CASES_FILE = "reports/UNIVERSAL_CASE_STUDIES_20260430.md"

SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

REQUEST_TIMEOUT_S = 90.0
RATE_LIMIT_RETRY_SLEEP_S = 60.0
RATE_LIMIT_HTTP_CODE = 429
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
CONNECT_RETRY_SLEEP_S = 5.0
MAX_CONNECT_RETRIES = 6

DEFAULT_INTER_ROOM_SLEEP_S = 5.0
DEFAULT_INTER_QUESTION_SLEEP_S = 0.5

# Classification thresholds — keep at top, no inline literals.
MIN_PASS_ANSWER_LEN = 30
GREETING_MIN_LEN = 20
ANSWER_TRUNCATE_LEN = 2000

# Behaviour-pattern hints (Vietnamese language, structural — NOT brand).
# Bot persona language: refuse / clarify / redirect / booking-capture cues.
# REFUSE patterns sourced from shared.constants (single source of truth) —
# regex form (each fragment may contain `.*` wildcard) compiled once at
# import time. Used via re.search instead of substring `in` check.
REFUSE_REGEX = re.compile(
    "(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")",
    re.IGNORECASE,
)
CLARIFY_PATTERNS = (
    "anh/chị quan tâm",
    "anh/chị muốn",
    "anh/chị cần",
    "tư vấn",
    "cần em",
    "anh chị muốn",
    "muốn tìm hiểu",
    "nhu cầu",
)
BOOKING_PATTERNS = (
    "số điện thoại",
    "phone",
    "sđt",
    "tên anh",
    "tên chị",
    "thời gian",
    "mấy giờ",
    "ngày nào",
    "khung giờ",
)
REDIRECT_PATTERNS = (
    "tư vấn",
    "dịch vụ",
    "scope",
    "hỗ trợ về",
    "em chỉ hỗ trợ",
    "em chuyên",
    "lĩnh vực",
)
GREETING_PATTERNS = (
    "chào",
    "xin chào",
    "hello",
    "hi anh",
    "hi chị",
    "rất vui",
    "em là",
)
PLACEHOLDER_LEAK_TOKENS = ("{", "}", "<documents>", "<service>", "<product>", "<topic>")

# Markdown parsing: section heading "### N. `category` ..." then table rows.
SECTION_HEADING_RE = re.compile(
    r"^###\s+\d+\.\s+`([a-z_]+)`",
    re.IGNORECASE,
)
# Table rows look like:  | 1.1 | giá <service> bao nhiêu | trích ... | 0.5 |
TABLE_ROW_RE = re.compile(
    r"^\|\s*(\d+\.\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*([^|]+?)\s*\|\s*$"
)


@dataclass
class CaseResult:
    """One test case: question + answer + classification."""

    case_id: str
    category: str
    raw_query: str
    query: str  # after placeholder substitution
    expected_behavior: str
    classification: str = ""
    answer: str = ""
    answer_type: str | None = None
    answer_reason: str | None = None
    chunks_used: int = 0
    top_score: float = 0.0
    intent: str = ""
    history_msgs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    wall_ms: float = 0.0
    citations: list[Any] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    chunks: list[Any] = field(default_factory=list)
    request_id: str | None = None
    error: str | None = None
    placeholder_leak: bool = False


# ---------- Markdown parser ----------


def parse_cases(md_path: Path) -> list[dict[str, str]]:
    """Parse UNIVERSAL_CASE_STUDIES_*.md into a flat list of cases.

    Each entry: {case_id, category, query, expected_behavior}.
    """
    if not md_path.exists():
        raise FileNotFoundError(f"Cases file not found: {md_path}")

    cases: list[dict[str, str]] = []
    current_cat: str | None = None
    in_table = False
    skipped_header = False

    for line in md_path.read_text(encoding="utf-8").splitlines():
        m_sec = SECTION_HEADING_RE.match(line)
        if m_sec:
            current_cat = m_sec.group(1)
            in_table = False
            skipped_header = False
            continue
        # Detect start of a markdown table — header row "| # | Query (VN) | ..."
        if current_cat and line.lstrip().startswith("|") and ("Query" in line or "query" in line):
            in_table = True
            skipped_header = False
            continue
        if in_table and line.lstrip().startswith("|---"):
            skipped_header = True
            continue
        if in_table and skipped_header and line.lstrip().startswith("|"):
            m_row = TABLE_ROW_RE.match(line)
            if m_row:
                cases.append(
                    {
                        "case_id": m_row.group(1).strip(),
                        "category": current_cat or "",
                        "query": m_row.group(2).strip(),
                        "expected_behavior": m_row.group(3).strip(),
                    }
                )
            else:
                # Table ended (or row malformed) — turn off until next section.
                in_table = False
                skipped_header = False
        elif in_table and not line.lstrip().startswith("|"):
            in_table = False
            skipped_header = False
    return cases


def substitute(query: str, mapping: dict[str, str]) -> str:
    """Replace all <token> placeholders in query with mapped real values.

    Order matters: longer keys first so <service_A> matches before <service>.
    """
    if not mapping:
        return query
    out = query
    for token in sorted(mapping.keys(), key=len, reverse=True):
        out = out.replace(token, mapping[token])
    return out


# ---------- Classifier ----------


def _has_any(text: str, patterns: tuple[str, ...]) -> bool:
    low = text.lower()
    return any(p in low for p in patterns)


def _has_placeholder_leak(answer: str) -> bool:
    return any(tok in answer for tok in PLACEHOLDER_LEAK_TOKENS)


def classify_case(  # noqa: C901, PLR0911, PLR0912 — 12 categories, deliberate per-cat branching
    *,
    category: str,
    answer: str,
    chunks_used: int,
    intent: str,
    error: str | None,
    placeholder_leak: bool,
) -> str:
    """Auto-classify per category. Returns PASS / FAIL / REFUSE / ERROR.

    Rules are STRUCTURAL (length, refuse-cue, redirect-cue, intent label) — no
    brand/tenant literal in this function. Bot owner controls actual phrasing
    via system_prompt; harness only checks behavior bucket.
    """
    if error:
        return "ERROR"
    ans = (answer or "").strip()
    if not ans:
        return "FAIL"
    if placeholder_leak:
        return "FAIL"

    is_refuse = bool(REFUSE_REGEX.search(ans))
    is_clarify = _has_any(ans, CLARIFY_PATTERNS)
    is_redirect = _has_any(ans, REDIRECT_PATTERNS)
    is_booking = _has_any(ans, BOOKING_PATTERNS)
    is_greeting = _has_any(ans, GREETING_PATTERNS)
    long_enough = len(ans) >= MIN_PASS_ANSWER_LEN

    if category == "factoid_in_corpus":
        # PASS: chunks retrieved + a substantive answer that is NOT pure refusal.
        if chunks_used > 0 and long_enough and not is_refuse:
            return "PASS"
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    if category == "comparison_in_corpus":
        # PASS: chunks > 0 and answer is long (synthesis usually verbose).
        if chunks_used > 0 and long_enough and not is_refuse:
            return "PASS"
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    if category == "aggregation_in_corpus":
        if chunks_used > 0 and long_enough and not is_refuse:
            return "PASS"
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    if category == "factoid_no_corpus":
        # PASS = correctly refuses (info NOT in docs).
        if is_refuse:
            return "PASS"
        return "FAIL"

    if category == "greeting":
        # PASS = greets back (greeting cue OR intent=greeting OR enough length and no refuse).
        if intent == "greeting":
            return "PASS"
        if is_greeting and len(ans) >= GREETING_MIN_LEN and not is_refuse:
            return "PASS"
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    if category == "chitchat":
        # PASS = redirect or clarify (NOT plain refuse-loop).
        if is_clarify or is_redirect:
            return "PASS"
        if is_refuse and not is_clarify and not is_redirect:
            return "REFUSE"
        return "FAIL"

    if category == "vu_vo":
        # PASS = clarify or redirect (no enough context to act → ask).
        if is_clarify or is_redirect:
            return "PASS"
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    if category == "off_topic":
        # PASS = redirect (politely refuse + redirect to scope).
        if is_redirect:
            return "PASS"
        if is_refuse:
            return "REFUSE"  # over-refuse without redirect = mediocre
        return "FAIL"

    if category == "booking":
        # PASS = booking-capture cues (ask phone / ask time / ask name).
        if is_booking:
            return "PASS"
        if is_clarify:
            return "PASS"  # clarify is acceptable too
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    if category == "hallucination_trap":
        # PASS = refuse the false claim (do NOT confirm).
        if is_refuse:
            return "PASS"
        return "FAIL"

    if category == "numeric_compute":
        # PASS = refuse to auto-compute OR redirect to staff.
        if is_refuse or is_booking:
            return "PASS"
        return "FAIL"

    if category == "multi_intent":
        # PASS = answer is long enough to cover multiple parts and uses chunks.
        if chunks_used > 0 and long_enough and not is_refuse:
            return "PASS"
        if is_refuse:
            return "REFUSE"
        return "FAIL"

    # Unknown category — fall back to length check.
    return "PASS" if long_enough else "FAIL"


# ---------- HTTP plumbing ----------


async def get_self_token(base_url: str) -> str:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
        r = await client.get(f"{base_url}{SELF_TOKEN_PATH}")
        r.raise_for_status()
        return r.json()["token"]


async def ask_once(
    *,
    base_url: str,
    token: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], float]:
    """Send one POST using a fresh AsyncClient — avoids stale-connection-pool
    failures that were observed when reusing one client across 90+ calls."""
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            r = await client.post(
                f"{base_url}{CHAT_PATH}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except (httpx.HTTPError, asyncio.TimeoutError, OSError) as exc:
        return -1, {"_exc": f"{type(exc).__name__}: {exc!s}"[:400]}, (time.perf_counter() - t0) * 1000
    wall_ms = (time.perf_counter() - t0) * 1000
    if r.status_code != HTTP_OK:
        return r.status_code, {"_body": r.text[:400]}, wall_ms
    try:
        return r.status_code, r.json(), wall_ms
    except (ValueError, json.JSONDecodeError) as exc:
        return r.status_code, {"_exc": f"json_parse: {exc!s}"[:200]}, wall_ms


async def run_case(
    *,
    base_url: str,
    token_box: dict[str, str],
    bot_id: str,
    tenant_id: int,
    channel: str,
    connect_id: str,
    case: dict[str, str],
    mapping: dict[str, str],
    bypass_cache: bool,
    debug: str,
) -> CaseResult:
    raw = case["query"]
    query = substitute(raw, mapping)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "channel_type": channel,
        "connect_id": connect_id,
        "question": query,
    }
    if bypass_cache:
        payload["bypass_cache"] = True
    if debug:
        payload["debug"] = debug

    # Send with retries: 401 → re-fetch token; ConnectError → small backoff.
    status, body, wall_ms = -1, {}, 0.0
    for attempt in range(MAX_CONNECT_RETRIES):
        status, body, wall_ms = await ask_once(
            base_url=base_url, token=token_box["token"], payload=payload
        )
        if status == HTTP_OK:
            break
        if status == HTTP_UNAUTHORIZED:
            # Re-fetch token (server may have regenerated on restart).
            try:
                token_box["token"] = await get_self_token(base_url)
            except (httpx.HTTPError, OSError):
                await asyncio.sleep(CONNECT_RETRY_SLEEP_S)
            continue
        if status == RATE_LIMIT_HTTP_CODE:
            await asyncio.sleep(RATE_LIMIT_RETRY_SLEEP_S)
            continue
        if status == -1:
            # Connection error — wait and retry.
            await asyncio.sleep(CONNECT_RETRY_SLEEP_S)
            continue
        break  # other error — give up

    err: str | None = None
    if status != HTTP_OK:
        err = f"HTTP {status} {body.get('_exc') or body.get('_body') or ''}"[:400]

    answer = ""
    answer_type = answer_reason = None
    chunks_used = history_msgs = tokens_in = tokens_out = 0
    top_score = 0.0
    cost_usd = 0.0
    duration_ms = 0
    intent = ""
    citations: list[Any] = []
    sources: list[Any] = []
    chunks: list[Any] = []
    request_id = None

    if status == HTTP_OK and isinstance(body, dict):
        answer = (body.get("answer") or "")[:ANSWER_TRUNCATE_LEN]
        answer_type = body.get("answer_type")
        answer_reason = body.get("answer_reason")
        chunks_used = int(body.get("chunks_used") or 0)
        top_score = float(body.get("top_score") or 0.0)
        cost_usd = float(body.get("cost_usd") or 0.0)
        duration_ms = int(body.get("duration_ms") or 0)
        citations = body.get("citations") or []
        sources = body.get("sources") or []
        chunks = body.get("retrieved_chunks_content") or []
        request_id = body.get("request_id")
        toks = body.get("tokens") or {}
        if isinstance(toks, dict):
            tokens_in = int(toks.get("prompt") or 0)
            tokens_out = int(toks.get("completion") or 0)
        dbg = body.get("debug") or {}
        if isinstance(dbg, dict):
            history_msgs = int(dbg.get("history_messages") or 0)
            intent = str(dbg.get("intent") or "")

    placeholder_leak = _has_placeholder_leak(answer)
    classification = classify_case(
        category=case["category"],
        answer=answer,
        chunks_used=chunks_used,
        intent=intent,
        error=err,
        placeholder_leak=placeholder_leak,
    )
    return CaseResult(
        case_id=case["case_id"],
        category=case["category"],
        raw_query=raw,
        query=query,
        expected_behavior=case["expected_behavior"],
        classification=classification,
        answer=answer,
        answer_type=answer_type,
        answer_reason=answer_reason,
        chunks_used=chunks_used,
        top_score=top_score,
        intent=intent,
        history_msgs=history_msgs,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        wall_ms=wall_ms,
        citations=citations,
        sources=sources,
        chunks=chunks,
        request_id=request_id,
        error=err,
        placeholder_leak=placeholder_leak,
    )


# ---------- Aggregation ----------


def aggregate(results: list[CaseResult]) -> dict[str, Any]:
    """Per-category counts + overall summary."""
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    overall: dict[str, int] = defaultdict(int)
    for r in results:
        by_cat[r.category][r.classification] += 1
        by_cat[r.category]["TOTAL"] += 1
        overall[r.classification] += 1
        overall["TOTAL"] += 1
    # Compute per-category PASS rate.
    cat_summary: dict[str, dict[str, Any]] = {}
    for cat, counts in by_cat.items():
        total = counts.get("TOTAL", 0) or 1
        cat_summary[cat] = {
            "total": counts.get("TOTAL", 0),
            "pass": counts.get("PASS", 0),
            "fail": counts.get("FAIL", 0),
            "refuse": counts.get("REFUSE", 0),
            "error": counts.get("ERROR", 0),
            "pass_pct": round(100.0 * counts.get("PASS", 0) / total, 1),
        }
    total_n = overall.get("TOTAL", 0) or 1
    return {
        "overall": {
            "total": overall.get("TOTAL", 0),
            "pass": overall.get("PASS", 0),
            "fail": overall.get("FAIL", 0),
            "refuse": overall.get("REFUSE", 0),
            "error": overall.get("ERROR", 0),
            "pass_pct": round(100.0 * overall.get("PASS", 0) / total_n, 1),
        },
        "by_category": cat_summary,
    }


def print_summary_table(summary: dict[str, Any]) -> None:
    print("\n=== Per-category PASS/FAIL/REFUSE ===", flush=True)
    print(f"{'Category':<26} {'Total':>5} {'Pass':>5} {'Fail':>5} {'Refuse':>6} {'Err':>4} {'Pass%':>6}")
    print("-" * 64)
    for cat in sorted(summary["by_category"].keys()):
        s = summary["by_category"][cat]
        print(
            f"{cat:<26} {s['total']:>5} {s['pass']:>5} {s['fail']:>5} "
            f"{s['refuse']:>6} {s['error']:>4} {s['pass_pct']:>5}%"
        )
    print("-" * 64)
    o = summary["overall"]
    print(
        f"{'OVERALL':<26} {o['total']:>5} {o['pass']:>5} {o['fail']:>5} "
        f"{o['refuse']:>6} {o['error']:>4} {o['pass_pct']:>5}%"
    )


# ---------- Driver ----------


async def main_async(args: argparse.Namespace) -> int:
    md_path = Path(args.cases_file)
    cases = parse_cases(md_path)
    if not cases:
        print(f"ERROR: no cases parsed from {md_path}", file=sys.stderr)
        return 2

    # Filter by --rooms (categories).
    if args.rooms and args.rooms.strip().lower() != "all":
        wanted = {c.strip() for c in args.rooms.split(",") if c.strip()}
        cases = [c for c in cases if c["category"] in wanted]
        if not cases:
            print(f"ERROR: no cases match categories={sorted(wanted)}", file=sys.stderr)
            return 2

    mapping: dict[str, str] = {}
    if args.corpus_mapping_file:
        mp = Path(args.corpus_mapping_file)
        if mp.exists():
            mapping = json.loads(mp.read_text(encoding="utf-8"))
        else:
            print(f"WARN: mapping file {mp} not found — running with raw placeholders", flush=True)

    cats_in_run = sorted({c["category"] for c in cases})
    print(
        f"Bot: {args.bot_id} tenant={args.tenant_id} channel={args.channel_type} | "
        f"cases={len(cases)} categories={len(cats_in_run)} | "
        f"mapping_keys={len(mapping)} bypass_cache={args.bypass_cache}",
        flush=True,
    )

    # Group cases by category for SERIAL execution per category.
    by_cat: dict[str, list[dict[str, str]]] = defaultdict(list)
    for c in cases:
        by_cat[c["category"]].append(c)

    all_results: list[CaseResult] = []
    token_box: dict[str, str] = {"token": await get_self_token(args.base_url)}
    print(f"Token acquired. Running {len(cats_in_run)} category groups SERIAL.", flush=True)

    for cidx, cat in enumerate(cats_in_run):
        cat_cases = by_cat[cat]
        ts = int(time.time())
        connect_id = f"univ_{cat}_{ts}"  # fresh history per category
        print(f"\n=== Category {cat} — {len(cat_cases)} cases ===", flush=True)
        for i, case in enumerate(cat_cases):
            tr = await run_case(
                base_url=args.base_url,
                token_box=token_box,
                bot_id=args.bot_id,
                tenant_id=args.tenant_id,
                channel=args.channel_type,
                connect_id=connect_id,
                case=case,
                mapping=mapping,
                bypass_cache=args.bypass_cache,
                debug=args.debug,
            )
            all_results.append(tr)
            preview = tr.query[:55].replace("\n", " ")
            print(
                f"  [{cat[:18]:<18}] {tr.case_id:>4} {tr.classification:<8} "
                f"chunks={tr.chunks_used:>2} top={tr.top_score:.3f} "
                f"intent={tr.intent[:10]:<10} dur={tr.duration_ms}ms  {preview}",
                flush=True,
            )
            if tr.error:
                print(f"      ! err={tr.error[:200]}", flush=True)
            if args.inter_question_sleep > 0:
                await asyncio.sleep(args.inter_question_sleep)
        if cidx < len(cats_in_run) - 1 and args.inter_room_sleep > 0:
            print(f"  ...sleep {args.inter_room_sleep}s before next category", flush=True)
            await asyncio.sleep(args.inter_room_sleep)

    summary = aggregate(all_results)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": {
            "bot_id": args.bot_id,
            "tenant_id": args.tenant_id,
            "channel_type": args.channel_type,
            "categories": cats_in_run,
            "bypass_cache": args.bypass_cache,
            "debug": args.debug,
            "cases_file": str(md_path),
            "corpus_mapping_file": args.corpus_mapping_file,
            "mapping_keys": len(mapping),
        },
        "summary": summary,
        "results": [asdict(r) for r in all_results],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print_summary_table(summary)
    print(f"\nWrote {out_path}")
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Universal 96-case study harness — 12 categories.")
    p.add_argument("--bot-id", required=True)
    p.add_argument("--tenant-id", type=int, required=True)
    p.add_argument("--channel-type", required=True)
    p.add_argument("--base-url", default=os.getenv("RAGBOT_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--cases-file", default=DEFAULT_CASES_FILE)
    p.add_argument(
        "--corpus-mapping-file",
        default=None,
        help="Optional JSON dict {<placeholder>: real_term, ...} for substitution",
    )
    p.add_argument(
        "--rooms",
        default="all",
        help="Comma-separated category names (e.g. greeting,booking) or 'all'",
    )
    p.add_argument("--bypass-cache", action="store_true", default=True)
    p.add_argument("--no-bypass-cache", dest="bypass_cache", action="store_false")
    p.add_argument("--debug", default="full", choices=["", "full"])
    p.add_argument("--inter-room-sleep", type=float, default=DEFAULT_INTER_ROOM_SLEEP_S)
    p.add_argument("--inter-question-sleep", type=float, default=DEFAULT_INTER_QUESTION_SLEEP_S)
    p.add_argument(
        "--output",
        default=f"/tmp/universal_{int(time.time())}.json",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(_parse_args())))
