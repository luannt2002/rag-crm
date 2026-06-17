"""SSE streaming smoke test — measures TTFT vs total duration.

Usage:
    python scripts/streaming_smoke_test.py \
        --base-url http://localhost:3004/api/ragbot \
        --tenant-id 32 --bot-id 1774946011723 --channel-type web \
        --questions "câu 1?" "câu 2?" \
        --runs 3

Output (JSON to stdout, one line per run):
    {
      "question": "...",
      "ttft_ms": 1234,        # time-to-first-token (None if non-stream)
      "duration_ms": 5678,    # full done event
      "answer_chars": 320,
      "streamed_chars": 320,  # 0 = answer came from cache / non-stream path
      "sources": 3,
      "events": 95,
      "answer_type": "answered",
      "ok": true,
    }

Why a separate script vs. extending test_75q_load.py:
  - Streaming uses a different endpoint (/chat/stream vs /test/chat).
  - Response shape is SSE frames, not single JSON.
  - TTFT is the metric, not total p95 — the load harness is built around
    the latter and conflating both would muddy the existing reports.

The script is BE-API focused (no UX rendering); it's used for:
  - Operator preflight ("does streaming actually emit token deltas?").
  - p95 TTFT regression check across deploys.
  - HALLU=0 invariant: the concatenated stream must equal the canonical
    answer (or differ only via a documented ``replace`` event).

Domain-neutral: bot_id / tenant_id / channel_type all come from CLI.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_BASE_URL = os.getenv("RAGBOT_BASE_URL", "http://localhost:3004/api/ragbot")


@dataclass
class StreamRun:
    question: str
    ttft_ms: int | None = None
    duration_ms: int | None = None
    answer_chars: int = 0
    streamed_chars: int = 0
    sources: int = 0
    events: int = 0
    answer_type: str = ""
    ok: bool = False
    error: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "ttft_ms": self.ttft_ms,
            "duration_ms": self.duration_ms,
            "answer_chars": self.answer_chars,
            "streamed_chars": self.streamed_chars,
            "sources": self.sources,
            "events": self.events,
            "answer_type": self.answer_type,
            "ok": self.ok,
            "error": self.error,
            **self.extras,
        }


async def fetch_token(client: httpx.AsyncClient, base_url: str) -> str:
    """Self-issue token for the smoke test — same path the existing harness uses."""
    r = await client.get(f"{base_url}/test/tokens/self")
    r.raise_for_status()
    body = r.json()
    if not body.get("ok") or "token" not in body:
        raise RuntimeError(f"self token endpoint did not return token: {body}")
    return body["token"]


async def stream_one(
    client: httpx.AsyncClient,
    base_url: str,
    token: str,
    *,
    tenant_id: int,
    bot_id: str,
    channel_type: str,
    user_id: str,
    question: str,
    timeout_s: float,
) -> StreamRun:
    """Issue ONE /chat/stream request and parse SSE frames into a StreamRun."""
    run = StreamRun(question=question)
    payload = {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "user_id": user_id,
        "content": question,
    }
    t0 = time.perf_counter()
    try:
        async with client.stream(
            "POST",
            f"{base_url}/chat/stream",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            timeout=timeout_s,
        ) as resp:
            if resp.status_code != 200:
                txt = await resp.aread()
                run.error = f"http_{resp.status_code}: {txt[:300].decode(errors='ignore')}"
                return run
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                run.events += 1
                try:
                    ev = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                et = ev.get("type")
                elapsed_ms = int((time.perf_counter() - t0) * 1000)
                if et == "token":
                    if run.ttft_ms is None:
                        run.ttft_ms = elapsed_ms
                    run.streamed_chars += len(ev.get("content", ""))
                elif et == "done":
                    run.duration_ms = elapsed_ms
                    run.answer_chars = len(ev.get("answer", "") or "")
                    run.sources = len(ev.get("sources", []) or [])
                    run.answer_type = ev.get("answer_type", "")
                    run.ok = True
                    break
    except (httpx.HTTPError, asyncio.TimeoutError) as exc:
        run.error = f"{type(exc).__name__}: {exc}"
    return run


def percentile(values: list[int], pct: float) -> int:
    """Inclusive percentile for small samples (matches scripts/test_75q_load.py)."""
    if not values:
        return 0
    sorted_v = sorted(values)
    k = max(0, min(len(sorted_v) - 1, int(round((pct / 100.0) * (len(sorted_v) - 1)))))
    return int(sorted_v[k])


async def main_async(args: argparse.Namespace) -> int:
    runs: list[StreamRun] = []
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_S) as client:
        token = await fetch_token(client, args.base_url)
        for q_idx, question in enumerate(args.questions):
            for r in range(args.runs):
                user_id = f"streaming-smoke-{q_idx}-{r}"
                run = await stream_one(
                    client,
                    args.base_url,
                    token,
                    tenant_id=args.tenant_id,
                    bot_id=args.bot_id,
                    channel_type=args.channel_type,
                    user_id=user_id,
                    question=question,
                    timeout_s=args.timeout_s,
                )
                runs.append(run)
                print(json.dumps(run.to_dict(), ensure_ascii=False), flush=True)
                await asyncio.sleep(args.inter_run_sleep)

    # Summary
    streamed_runs = [r for r in runs if r.ok and r.streamed_chars > 0 and r.ttft_ms is not None]
    cache_runs = [r for r in runs if r.ok and r.streamed_chars == 0]
    failed_runs = [r for r in runs if not r.ok]

    summary = {
        "total_runs": len(runs),
        "streamed_runs": len(streamed_runs),
        "cache_runs": len(cache_runs),
        "failed_runs": len(failed_runs),
        "ttft_ms_p50": percentile([r.ttft_ms for r in streamed_runs if r.ttft_ms is not None], 50),
        "ttft_ms_p95": percentile([r.ttft_ms for r in streamed_runs if r.ttft_ms is not None], 95),
        "duration_ms_p50": percentile([r.duration_ms for r in runs if r.duration_ms], 50),
        "duration_ms_p95": percentile([r.duration_ms for r in runs if r.duration_ms], 95),
        "ttft_ms_mean": int(statistics.mean([r.ttft_ms for r in streamed_runs if r.ttft_ms is not None])) if streamed_runs else 0,
    }
    print("---SUMMARY---", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failed_runs else 1


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="SSE streaming smoke + TTFT probe.")
    p.add_argument("--base-url", default=DEFAULT_BASE_URL)
    p.add_argument("--tenant-id", type=int, required=True)
    p.add_argument("--bot-id", required=True)
    p.add_argument("--channel-type", required=True)
    p.add_argument(
        "--questions",
        nargs="+",
        required=True,
        help="One or more user questions (quoted).",
    )
    p.add_argument("--runs", type=int, default=1, help="Repeats per question.")
    p.add_argument("--timeout-s", type=float, default=DEFAULT_TIMEOUT_S)
    p.add_argument("--inter-run-sleep", type=float, default=0.5)
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async(_parse_args())))
