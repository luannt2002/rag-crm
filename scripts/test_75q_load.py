#!/usr/bin/env python3
"""75-question load test harness — parses 5-room markdown and runs 15Q per room SERIAL.

Usage:
  python3 scripts/test_75q_load.py \\
    --bot-id <bot> --tenant-id <tid> --channel-type <ch> \\
    --rooms 1,2,3,4,5 --output /tmp/<bot>_75q_<ts>.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

# Single source of truth for refuse-pattern fragments + transport thresholds
# — DRY across the 3 load-test harnesses. See `shared/constants.py` for every
# threshold below; the harness owns ZERO inline magic numbers (CLAUDE.md
# zero-hardcode rule applies even to scripts/ tooling).
_REPO_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))
from ragbot.shared.constants import (  # noqa: E402
    DEFAULT_LOADTEST_ANSWER_TRUNCATE_CHARS,
    DEFAULT_LOADTEST_FACTUAL_LEN_THRESHOLD,
    DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S,
    DEFAULT_LOADTEST_INTER_ROOM_SLEEP_S,
    DEFAULT_LOADTEST_MAX_TOKEN_REFRESH_RETRIES,
    DEFAULT_LOADTEST_MIN_PASS_ANSWER_CHARS,
    DEFAULT_LOADTEST_RATE_LIMIT_RETRY_SLEEP_S,
    DEFAULT_LOADTEST_REFUSE_PATTERNS,
    DEFAULT_LOADTEST_REQUEST_TIMEOUT_S,
)

# Batch-mode constants live in `scripts/_loadtest_common.py` (test-tooling
# scope, NOT shared/constants.py). See the module docstring there for
# rationale.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # noqa: E402
from scripts._loadtest_common import (  # noqa: E402
    DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS,
    DEFAULT_LOADTEST_BATCH_SIZE,
    DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE,
)

# === Module constants — NO inline magic numbers below ===
DEFAULT_BASE_URL = "http://localhost:3004"
# Bot identity — read from env (.env) or required CLI args. No tenant literal in code.
DEFAULT_BOT_ID = os.getenv("LOADTEST_BOT_ID", "")
DEFAULT_TENANT_ID = int(os.getenv("LOADTEST_TENANT_ID", "0") or "0")
DEFAULT_CHANNEL = os.getenv("LOADTEST_CHANNEL_TYPE", "")
DEFAULT_QUESTIONS_FILE = "reports/LUANNT_LOAD_TEST_75Q.md"

SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
CHAT_PATH = "/api/ragbot/test/chat"

# Transport thresholds — all imported from shared.constants (zero-hardcode).
REQUEST_TIMEOUT_S = DEFAULT_LOADTEST_REQUEST_TIMEOUT_S
RATE_LIMIT_RETRY_SLEEP_S = DEFAULT_LOADTEST_RATE_LIMIT_RETRY_SLEEP_S
RATE_LIMIT_HTTP_CODE = 429
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
# Bound retries so a permanently-rejecting upstream cannot infinite-loop.
# 1st call + 1 refresh-and-retry on 401 = max 2 attempts.
MAX_TOKEN_REFRESH_RETRIES = DEFAULT_LOADTEST_MAX_TOKEN_REFRESH_RETRIES

DEFAULT_INTER_ROOM_SLEEP_S = DEFAULT_LOADTEST_INTER_ROOM_SLEEP_S
DEFAULT_INTER_QUESTION_SLEEP_S = DEFAULT_LOADTEST_INTER_QUESTION_SLEEP_S

# Classification thresholds — keep at top, no inline literals.
MIN_PASS_ANSWER_LEN = DEFAULT_LOADTEST_MIN_PASS_ANSWER_CHARS
ANSWER_TRUNCATE_LEN = DEFAULT_LOADTEST_ANSWER_TRUNCATE_CHARS
FACTUAL_LEN_THRESHOLD = DEFAULT_LOADTEST_FACTUAL_LEN_THRESHOLD

# Informativeness gate — Mode D fix per
# reports/MEGA_REFUSE_WITH_DOCS_DEEPDIVE_20260501.md §2. Pattern matches
# numeric facts grounded in service docs (price / duration / step count /
# month-year-hour) OR an explicit chunk citation marker. Any match means the
# answer carries fact and a trailing hedge does NOT make it a refusal — bot
# did its job + acknowledged a gap on a sub-aspect.
#
# R4 extension (per reports/MEGA_R3_VERDICT.md §6 lever #3): also accept
# address / hotline / maps patterns so fact-then-hedge hybrid answers
# (e.g. "Hotline 0926.559.268. Tuy nhiên, em chưa có thông tin cụ thể về
# ngã tư gần nhất") classify as PASS — same playbook as numeric facts.
_FACTUAL_CLAIM_RE = re.compile(
    r"\d{2,}\s*(đồng|VND|phút|buổi|bước|năm|tháng|giờ)"
    r"|\[chunk:"
    r"|hotline\s*\d|0\d{2,3}[.\-\s]?\d{3,4}[.\-\s]?\d{3,4}"
    r"|google\s*maps|maps\.google|goo\.gl/maps"
    r"|\bsố\s+\d+|\bđịa\s*chỉ\b.*\d|\d+\s+[\w]+\s+đường",
    re.IGNORECASE,
)


def _has_factual_claim(answer: str) -> bool:
    """True if the answer carries fact (numbers + units / chunk marker / long-form)."""
    if _FACTUAL_CLAIM_RE.search(answer):
        return True
    if len(answer) > FACTUAL_LEN_THRESHOLD:
        return True
    return False

# Refuse pattern — bot owner controls actual phrasing in system_prompt.
# Heuristic only; over-broad = false positives, but acceptable for triage.
# Fragments imported from shared.constants (single source of truth).
REFUSE_PATTERN = re.compile(
    "(" + "|".join(DEFAULT_LOADTEST_REFUSE_PATTERNS) + ")",
    re.IGNORECASE,
)

# Markdown parse: room heading "## Room N" then ordered list "<num>. <text>".
ROOM_HEADING_RE = re.compile(r"^##\s+Room\s+(\d+)\b", re.IGNORECASE)
NUMBERED_ITEM_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")


@dataclass
class TurnResult:
    """One question/answer turn with classification + diagnostics."""

    room: int
    idx: int
    question: str
    classification: str = ""
    answer: str = ""
    answer_type: str | None = None
    answer_reason: str | None = None
    chunks_used: int = 0
    top_score: float = 0.0
    top_score_min: float = 0.0
    history_msgs: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    # F1b — Anthropic prompt-cache hit measurement: count of input tokens
    # served from the provider's prompt cache on this turn (Anthropic
    # cache_read or OpenAI auto-cache). Persisted into the per-run JSON so
    # the offline analyser can compute prod hit-ratio without trawling DB.
    cached_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    wall_ms: float = 0.0
    citations: list[Any] = field(default_factory=list)
    sources: list[Any] = field(default_factory=list)
    chunks: list[Any] = field(default_factory=list)
    request_id: str | None = None
    error: str | None = None
    is_repeat_probe: bool = False


def parse_questions(md_path: Path) -> dict[int, list[str]]:
    """Parse markdown — return {room_num: [q1, q2, ...]}."""
    rooms: dict[int, list[str]] = {}
    current_room: int | None = None
    if not md_path.exists():
        raise FileNotFoundError(f"Questions file not found: {md_path}")

    in_questions_section = False
    for line in md_path.read_text(encoding="utf-8").splitlines():
        m_room = ROOM_HEADING_RE.match(line)
        if m_room:
            current_room = int(m_room.group(1))
            rooms[current_room] = []
            in_questions_section = True
            continue
        # Stop collecting once a horizontal rule or new top heading hits
        if line.strip().startswith("---") or line.startswith("# ") or line.startswith("## "):
            if not m_room:
                in_questions_section = False
                continue
        if not in_questions_section or current_room is None:
            continue
        m_item = NUMBERED_ITEM_RE.match(line)
        if m_item:
            text = m_item.group(2).strip()
            # Strip trailing emoji/markers like "🔁 (repeat probe)" but keep the question proper.
            text = re.sub(r"\s*🔁.*$", "", text).strip()
            if text:
                rooms[current_room].append(text)
    return rooms


async def get_self_token(client: httpx.AsyncClient, base_url: str) -> str:
    """Fetch self-issued JWT for /test/chat."""
    r = await client.get(f"{base_url}{SELF_TOKEN_PATH}")
    r.raise_for_status()
    return r.json()["token"]


async def ask_once(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], float]:
    """Single POST. Returns (status_code, body_or_error_dict, wall_ms)."""
    t0 = time.perf_counter()
    try:
        r = await client.post(
            f"{base_url}{CHAT_PATH}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_S,
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


async def ask_with_token_refresh(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token_box: dict[str, str],
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], float]:
    """POST with mid-flight JWT refresh on 401.

    Long-running load tests can outlive a single self-issued token (R4
    OLD-half: 41/75 turns failed with HTTP 401 after the JWT expired
    around room 3 idx 4). On 401 we re-fetch via `get_self_token`,
    mutate `token_box["token"]` so subsequent turns see the new value,
    and retry once. Two consecutive 401s → return last response so the
    caller can record the error normally.
    """
    status, body, wall_ms = -1, {}, 0.0
    for attempt in range(MAX_TOKEN_REFRESH_RETRIES):
        status, body, wall_ms = await ask_once(
            client,
            base_url=base_url,
            token=token_box["token"],
            payload=payload,
        )
        if status != HTTP_UNAUTHORIZED:
            return status, body, wall_ms
        # Last attempt — surface the 401 instead of silently looping.
        if attempt == MAX_TOKEN_REFRESH_RETRIES - 1:
            break
        try:
            token_box["token"] = await get_self_token(client, base_url)
        except (httpx.HTTPError, OSError):
            # Token endpoint failed — keep prior status/body for the caller.
            break
    return status, body, wall_ms


def classify(answer: str, chunks_used: int, error: str | None) -> str:
    """Classify a turn — see README/docstring of harness for rules."""
    if error:
        return "ERROR"
    ans = (answer or "").strip()
    if not ans:
        return "FAIL"
    # Informativeness-gated refuse: a hedge clause does not flip a fact-bearing
    # answer. Mode D fix per reports/MEGA_REFUSE_WITH_DOCS_DEEPDIVE_20260501.md.
    is_refuse = bool(REFUSE_PATTERN.search(ans)) and not _has_factual_claim(ans)
    if is_refuse and chunks_used == 0:
        return "REFUSE_NO_DOCS"
    if is_refuse and chunks_used > 0:
        return "REFUSE_WITH_DOCS"
    if len(ans) <= MIN_PASS_ANSWER_LEN:
        return "FAIL"
    return "PASS"


async def run_turn(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token_box: dict[str, str],
    bot_id: str,
    tenant_id: int,
    channel: str,
    connect_id: str,
    question: str,
    bypass_cache: bool,
    debug: str,
    room: int,
    idx: int,
    is_repeat: bool,
) -> TurnResult:
    """Send 1 question; retry once on 429; refresh JWT once on 401.

    Tenant identity is lifted from the JWT bearer (record_tenant_id
    UUID claim minted by /test/tokens/self). The body carries 2-key bot
    identity only; ``tenant_id`` is no longer accepted on the wire.
    ``--tenant-id`` CLI arg remains for ops back-compat when minting the
    dev token (RAGBOT_DEV_TOKEN_TENANT_ID env)."""
    _ = tenant_id  # kwarg kept on the caller signature; not sent on wire.
    payload: dict[str, Any] = {
        "bot_id": bot_id,
        "channel_type": channel,
        "connect_id": connect_id,
        "question": question,
    }
    if bypass_cache:
        payload["bypass_cache"] = True
    if debug:
        payload["debug"] = debug

    status, body, wall_ms = await ask_with_token_refresh(
        client, base_url=base_url, token_box=token_box, payload=payload
    )
    if status == RATE_LIMIT_HTTP_CODE:
        await asyncio.sleep(RATE_LIMIT_RETRY_SLEEP_S)
        status, body, wall_ms = await ask_with_token_refresh(
            client, base_url=base_url, token_box=token_box, payload=payload
        )

    err: str | None = None
    if status != HTTP_OK:
        err = f"HTTP {status}: {body.get('_body') or body.get('_exc') or ''}"[:400]

    answer = ""
    answer_type = answer_reason = None
    chunks_used = history_msgs = tokens_in = tokens_out = 0
    cached_tokens = 0
    top_score = top_score_min = 0.0
    cost_usd = 0.0
    duration_ms = 0
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
        # Full chunk content (debug=full only) — keeps RAGAS faithfulness
        # honest by giving the judge the entire retrieved text instead of
        # the 200c preview that lives in `sources`.
        chunks = body.get("retrieved_chunks_content") or []
        request_id = body.get("request_id")
        toks = body.get("tokens") or {}
        if isinstance(toks, dict):
            tokens_in = int(toks.get("prompt") or 0)
            tokens_out = int(toks.get("completion") or 0)
            # /test/chat surfaces tokens.cached (test_chat.py:1391) — the
            # provider-reported count of input tokens served from prompt
            # cache (Anthropic cache_read OR OpenAI auto-cache). Captured
            # here so the run JSON can report cache hit-ratio per round.
            cached_tokens = int(toks.get("cached") or 0)
        dbg = body.get("debug") or {}
        if isinstance(dbg, dict):
            history_msgs = int(dbg.get("history_messages") or 0)
            top_score_min = float(dbg.get("score_min") or 0.0)

    classification = classify(answer, chunks_used, err)
    return TurnResult(
        room=room,
        idx=idx,
        question=question,
        classification=classification,
        answer=answer,
        answer_type=answer_type,
        answer_reason=answer_reason,
        chunks_used=chunks_used,
        top_score=top_score,
        top_score_min=top_score_min,
        history_msgs=history_msgs,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
        duration_ms=duration_ms,
        wall_ms=wall_ms,
        citations=citations,
        sources=sources,
        chunks=chunks,
        request_id=request_id,
        error=err,
        is_repeat_probe=is_repeat,
    )


async def run_room(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    token_box: dict[str, str],
    bot_id: str,
    tenant_id: int,
    channel: str,
    room_num: int,
    questions: list[str],
    bypass_cache: bool,
    debug: str,
    inter_question_sleep: float,
) -> list[TurnResult]:
    """SERIAL execution — preserves history within room."""
    ts = int(time.time())
    connect_id = f"lt_75q_r{room_num:02d}_{ts}"
    n = len(questions)
    last_repeat_idx = n - 1  # final question is the repeat probe in source markdown
    turns: list[TurnResult] = []
    for i, q in enumerate(questions):
        is_repeat = i == last_repeat_idx
        tr = await run_turn(
            client,
            base_url=base_url,
            token_box=token_box,
            bot_id=bot_id,
            tenant_id=tenant_id,
            channel=channel,
            connect_id=connect_id,
            question=q,
            bypass_cache=bypass_cache,
            debug=debug,
            room=room_num,
            idx=i,
            is_repeat=is_repeat,
        )
        turns.append(tr)
        print(
            f"  [r{room_num:02d}] Q{i + 1:02d}/{n} {tr.classification:18s} "
            f"chunks={tr.chunks_used} top={tr.top_score:.3f} "
            f"dur={tr.duration_ms}ms cost=${tr.cost_usd:.5f}  "
            f"{q[:55]}",
            flush=True,
        )
        if inter_question_sleep > 0:
            await asyncio.sleep(inter_question_sleep)
    return turns


def summarize(turns: list[TurnResult]) -> dict[str, Any]:
    """Aggregate counts + cost + latency percentiles."""
    n = len(turns)
    counts: dict[str, int] = {}
    for t in turns:
        counts[t.classification] = counts.get(t.classification, 0) + 1
    total_cost = sum(t.cost_usd for t in turns)
    total_in = sum(t.tokens_in for t in turns)
    total_out = sum(t.tokens_out for t in turns)
    # F1b — provider prompt-cache hit-ratio surface. Aggregate across
    # turns so the run JSON exposes cache effectiveness end-to-end without
    # the offline analyser having to recompute.
    total_cached = sum(t.cached_tokens for t in turns)
    cache_hit_pct = (
        round(100.0 * total_cached / total_in, 2) if total_in else 0.0
    )
    durations = sorted([t.duration_ms for t in turns if t.duration_ms > 0])

    def pct(p: float) -> int:
        if not durations:
            return 0
        k = max(0, min(len(durations) - 1, int(round((p / 100.0) * (len(durations) - 1)))))
        return durations[k]

    return {
        "total_turns": n,
        "counts": counts,
        "rates_pct": {k: round(100.0 * v / n, 1) if n else 0.0 for k, v in counts.items()},
        "cost_usd_total": round(total_cost, 6),
        "cost_usd_per_turn_avg": round(total_cost / n, 6) if n else 0.0,
        "tokens_in_total": total_in,
        "tokens_out_total": total_out,
        "cached_tokens_total": total_cached,
        "cache_hit_pct_input": cache_hit_pct,
        "latency_ms_p50": pct(50),
        "latency_ms_p95": pct(95),
        "latency_ms_p99": pct(99),
        "latency_ms_max": durations[-1] if durations else 0,
        "duration_zero_count": sum(1 for t in turns if t.duration_ms == 0),
    }


# ---------------------------------------------------------------------------
# Batch-10 mode helpers (User explicit 2026-04-30): expose per-batch quality
# metrics + worst-failure samples so a 75-question round can be diagnosed
# at 10-question granularity instead of one final aggregate.
#
# Pure tooling — never injects text into the LLM, never overrides the answer.
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile on a pre-sorted ascending list."""
    if not sorted_values:
        return 0.0
    k = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[k])


def summarize_batch(
    turns: list[TurnResult],
    *,
    top_n_worst_refuse: int = DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE,
    preview_chars: int = DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS,
) -> dict[str, Any]:
    """Compute per-batch summary used by the batch checkpoint writer.

    Returned shape is JSON-serialisable so callers can dump it next to the
    intermediate turn list. Includes:
    - `counts` per classification bucket
    - `top_score_avg_pass` — mean retrieval score among PASS turns
    - `latency_ms_p50` / `latency_ms_p95` — duration percentiles (server-reported)
    - `cost_usd_total`
    - `worst_refuse_no_docs` — up to N preview-truncated questions
    """
    n = len(turns)
    counts: dict[str, int] = {}
    for t in turns:
        counts[t.classification] = counts.get(t.classification, 0) + 1

    pass_turns = [t for t in turns if t.classification == "PASS"]
    top_score_avg_pass = (
        round(sum(t.top_score for t in pass_turns) / len(pass_turns), 4)
        if pass_turns
        else 0.0
    )

    durations = sorted([float(t.duration_ms) for t in turns if t.duration_ms > 0])
    cost_total = round(sum(t.cost_usd for t in turns), 6)

    refuse_no_docs = [t for t in turns if t.classification == "REFUSE_NO_DOCS"]
    refuse_no_docs.sort(key=lambda t: (t.room, t.idx))
    worst_refuse = [
        {
            "room": t.room,
            "idx": t.idx,
            "question_preview": (t.question or "")[:preview_chars],
        }
        for t in refuse_no_docs[:top_n_worst_refuse]
    ]

    return {
        "total_turns": n,
        "counts": counts,
        "top_score_avg_pass": top_score_avg_pass,
        "latency_ms_p50": int(_percentile(durations, 50)),
        "latency_ms_p95": int(_percentile(durations, 95)),
        "cost_usd_total": cost_total,
        "worst_refuse_no_docs": worst_refuse,
    }


def format_batch_markdown(
    *,
    batch_idx: int,
    total_batches: int,
    turn_range: tuple[int, int],
    summary: dict[str, Any],
) -> str:
    """Render a per-batch summary as the markdown block appended to the log.

    Format is plain GFM with one buckets table + an optional worst-refuse
    list. Operator scans this with `tail -F` while a long round runs.
    """
    lo, hi = turn_range
    lines: list[str] = []
    lines.append(f"## Batch {batch_idx}/{total_batches} — turns {lo}-{hi}")
    lines.append("")
    lines.append("| Bucket | Count |")
    lines.append("| --- | ---: |")
    for bucket in ("PASS", "REFUSE_NO_DOCS", "REFUSE_WITH_DOCS", "FAIL", "ERROR"):
        lines.append(f"| {bucket} | {summary['counts'].get(bucket, 0)} |")
    lines.append("")
    lines.append(
        f"top_score_avg(PASS)={summary['top_score_avg_pass']:.4f}  "
        f"p50={summary['latency_ms_p50']}ms  p95={summary['latency_ms_p95']}ms  "
        f"cost=${summary['cost_usd_total']:.5f}"
    )
    worst = summary.get("worst_refuse_no_docs") or []
    if worst:
        lines.append("")
        lines.append("Top worst REFUSE_NO_DOCS:")
        for w in worst:
            lines.append(
                f"- r{w['room']:02d} Q{w['idx'] + 1:02d}: {w['question_preview']}"
            )
    lines.append("")
    return "\n".join(lines)


def emit_batch_checkpoint(
    *,
    output_path: Path,
    batch_idx: int,
    total_batches: int,
    turns: list[TurnResult],
    turn_range: tuple[int, int],
    config_block: dict[str, Any],
    top_n_worst_refuse: int = DEFAULT_LOADTEST_BATCH_TOP_N_WORST_REFUSE,
    preview_chars: int = DEFAULT_LOADTEST_BATCH_LOG_PREVIEW_CHARS,
) -> dict[str, Any]:
    """Write `<output>.batch_<idx>.json` + append to `<output>.batch_log.md`.

    Returns the per-batch summary dict so the caller can log it to stdout.
    """
    summary = summarize_batch(
        turns,
        top_n_worst_refuse=top_n_worst_refuse,
        preview_chars=preview_chars,
    )
    md = format_batch_markdown(
        batch_idx=batch_idx,
        total_batches=total_batches,
        turn_range=turn_range,
        summary=summary,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)

    json_path = output_path.with_name(f"{output_path.stem}.batch_{batch_idx:02d}.json")
    json_payload = {
        "config": config_block,
        "batch": {
            "idx": batch_idx,
            "total": total_batches,
            "turn_range": list(turn_range),
        },
        "summary": summary,
        "turns": [asdict(t) for t in turns],
    }
    json_path.write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    log_path = output_path.with_name(f"{output_path.stem}.batch_log.md")
    # Append-only — first batch creates the file; later batches grow it.
    with log_path.open("a", encoding="utf-8") as f:
        f.write(md)
        f.write("\n")
    return summary


def slice_into_batches(
    turns: list[TurnResult], *, batch_size: int
) -> list[tuple[int, int, list[TurnResult]]]:
    """Split a flat turn list into `(lo, hi, sublist)` tuples (1-indexed)."""
    if batch_size <= 0:
        return []
    out: list[tuple[int, int, list[TurnResult]]] = []
    n = len(turns)
    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        out.append((start + 1, end, turns[start:end]))
    return out


async def main_async(args: argparse.Namespace) -> int:
    md_path = Path(args.questions_file)
    parsed = parse_questions(md_path)
    if not parsed:
        print(f"ERROR: no rooms parsed from {md_path}", file=sys.stderr)
        return 2

    rooms_to_run = [int(x.strip()) for x in args.rooms.split(",") if x.strip()]
    invalid = [r for r in rooms_to_run if r not in parsed]
    if invalid:
        print(f"ERROR: rooms {invalid} not in markdown (have {sorted(parsed)})", file=sys.stderr)
        return 2

    print(
        f"Bot: {args.bot_id} tenant={args.tenant_id} channel={args.channel_type} | "
        f"rooms={rooms_to_run} | bypass_cache={args.bypass_cache} debug={args.debug}",
        flush=True,
    )

    all_turns: list[TurnResult] = []
    async with httpx.AsyncClient() as client:
        # Wrap the JWT in a mutable dict so `ask_with_token_refresh` can
        # rotate it mid-run without every caller re-threading the value.
        token_box: dict[str, str] = {"token": await get_self_token(client, args.base_url)}
        print(f"Token acquired. Starting {len(rooms_to_run)} room(s) SERIAL.", flush=True)
        for ridx, room_num in enumerate(rooms_to_run):
            qs = parsed[room_num]
            print(f"\n=== Room {room_num} — {len(qs)} questions ===", flush=True)
            turns = await run_room(
                client,
                base_url=args.base_url,
                token_box=token_box,
                bot_id=args.bot_id,
                tenant_id=args.tenant_id,
                channel=args.channel_type,
                room_num=room_num,
                questions=qs,
                bypass_cache=args.bypass_cache,
                debug=args.debug,
                inter_question_sleep=args.inter_question_sleep,
            )
            all_turns.extend(turns)
            if ridx < len(rooms_to_run) - 1 and args.inter_room_sleep > 0:
                print(f"  ...sleep {args.inter_room_sleep}s before next room", flush=True)
                await asyncio.sleep(args.inter_room_sleep)

    summary = summarize(all_turns)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    config_block: dict[str, Any] = {
        "bot_id": args.bot_id,
        "tenant_id": args.tenant_id,
        "channel_type": args.channel_type,
        "rooms": rooms_to_run,
        "bypass_cache": args.bypass_cache,
        "debug": args.debug,
        "questions_file": str(md_path),
        # ``getattr`` guards against the harness being invoked with a
        # hand-built ``Namespace`` (or an older parser that pre-dates the
        # ``--batch-size`` flag) — missing attribute = batch mode disabled.
        # Without this guard the summary-write at end-of-run AttributeErrors
        # AFTER all 75 turns have completed, losing the JSON dump entirely
        # (R9 OLD regression — log /tmp/r9_old.log preserved both halves).
        "batch_size": int(getattr(args, "batch_size", 0) or 0),
    }
    payload = {
        "config": config_block,
        "summary": summary,
        "turns": [asdict(t) for t in all_turns],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Batch checkpoints — emit AFTER the run finishes so failures during
    # the run still produce the aggregate JSON. The per-batch files are a
    # post-hoc audit trail; they do not block the run.
    batch_size = int(getattr(args, "batch_size", 0) or 0)
    if batch_size > 0:
        batches = slice_into_batches(all_turns, batch_size=batch_size)
        total = len(batches)
        # Reset/start the batch log fresh each run so old appends don't
        # bleed across repeated invocations writing to the same output.
        log_path = out_path.with_name(f"{out_path.stem}.batch_log.md")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(
            f"# Batch log — {out_path.name} ({total} batches × {batch_size})\n\n",
            encoding="utf-8",
        )
        for bidx, (lo, hi, sub) in enumerate(batches, start=1):
            bsum = emit_batch_checkpoint(
                output_path=out_path,
                batch_idx=bidx,
                total_batches=total,
                turns=sub,
                turn_range=(lo, hi),
                config_block=config_block,
            )
            print(
                f"  [batch {bidx}/{total}] turns {lo}-{hi}  "
                f"PASS={bsum['counts'].get('PASS', 0)}  "
                f"REFUSE_NO_DOCS={bsum['counts'].get('REFUSE_NO_DOCS', 0)}  "
                f"p95={bsum['latency_ms_p95']}ms  "
                f"cost=${bsum['cost_usd_total']:.5f}",
                flush=True,
            )

    print(f"\nWrote {out_path}")
    print(json.dumps(summary, indent=2))
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="75-question load test harness (5 rooms x 15Q).")
    p.add_argument("--bot-id", default=os.getenv("RAGBOT_TEST_BOT_ID", DEFAULT_BOT_ID))
    p.add_argument("--tenant-id", type=int, default=int(os.getenv("RAGBOT_TEST_TENANT_ID", DEFAULT_TENANT_ID)))
    p.add_argument("--channel-type", default=os.getenv("RAGBOT_TEST_CHANNEL", DEFAULT_CHANNEL))
    p.add_argument("--base-url", default=os.getenv("RAGBOT_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--rooms", default="1,2,3,4,5", help="Comma-separated room numbers, e.g. 1,2,3")
    p.add_argument("--questions-file", default=DEFAULT_QUESTIONS_FILE)
    p.add_argument("--bypass-cache", action="store_true", default=True)
    p.add_argument("--no-bypass-cache", dest="bypass_cache", action="store_false")
    p.add_argument("--debug", default="full", choices=["", "full"])
    p.add_argument("--inter-room-sleep", type=float, default=DEFAULT_INTER_ROOM_SLEEP_S)
    p.add_argument("--inter-question-sleep", type=float, default=DEFAULT_INTER_QUESTION_SLEEP_S)
    p.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_LOADTEST_BATCH_SIZE,
        help=(
            "Split the run into N-question batches; emit per-batch JSON + markdown "
            "log alongside the aggregate output. 0 = no batch mode (default, "
            "preserves prior single-shot behavior)."
        ),
    )
    p.add_argument(
        "--output",
        default=f"/tmp/loadtest_75q_{int(time.time())}.json",
    )
    args = p.parse_args()
    missing = []
    if not args.bot_id:
        missing.append("--bot-id (env LOADTEST_BOT_ID)")
    if not args.channel_type:
        missing.append("--channel-type (env LOADTEST_CHANNEL_TYPE)")
    if args.tenant_id is None or args.tenant_id < 1:
        missing.append("--tenant-id positive int (env LOADTEST_TENANT_ID)")
    if missing:
        p.error("bot identity required, missing: " + ", ".join(missing))
    return args


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(_parse_args())))
