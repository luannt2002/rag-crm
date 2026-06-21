#!/usr/bin/env python3
"""qa_chat.py — single helper for live conversational QA of a bot.

Sends ONE turn to the test chat endpoint and prints the bot's answer plus the
retrieved chunk text (debug=full) so a QA/QC agent can verify the answer against
the actual evidence — not guess. Multi-turn: reuse the same --connect-id across
calls and the bot keeps the conversation history for that room.

Usage:
  python scripts/qa_chat.py --bot chinh-sach-xe --workspace xe \
      --connect-id qa-room-1 "Shop có lốp 205/55R16 không, giá sao?"

  # follow-up turn (same room → bot remembers context):
  python scripts/qa_chat.py --bot chinh-sach-xe --workspace xe \
      --connect-id qa-room-1 "Thế còn loại rẻ hơn?"

  --json   emit the raw structured result (answer + chunks + diagnostics)
  --quiet  answer only (no chunk dump)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx

BASE = os.environ.get("RAGBOT_BASE_URL", "http://localhost:3004")


def _token(client: httpx.Client) -> str:
    r = client.get(f"{BASE}/api/ragbot/test/tokens/self", params={"role": "owner"})
    r.raise_for_status()
    d = r.json()
    return d.get("data", {}).get("token") or d.get("token") or ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="qa_chat")
    p.add_argument("--bot", required=True)
    p.add_argument("--workspace", default="")
    p.add_argument("--channel", default="web")
    p.add_argument("--connect-id", default="qa-default")
    p.add_argument("--bypass-cache", action="store_true", default=True)
    p.add_argument("--json", action="store_true")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("question")
    a = p.parse_args(argv)

    with httpx.Client(timeout=90) as client:
        headers = {"Authorization": f"Bearer {_token(client)}"}
        body = {
            "bot_id": a.bot,
            "channel_type": a.channel,
            "workspace_id": a.workspace or a.bot,
            "question": a.question,
            "connect_id": a.connect_id,
            "debug": "full",
            "bypass_cache": bool(a.bypass_cache),
        }
        r = client.post(f"{BASE}/api/ragbot/test/chat", json=body, headers=headers)
        try:
            payload = r.json()
        except ValueError:
            print(f"HTTP {r.status_code}: {r.text[:300]}", file=sys.stderr)
            return 1
        data = payload.get("data", payload)

    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    answer = data.get("answer") or data.get("response") or ""
    chunks = data.get("retrieved_chunks_content") or data.get("retrieved_chunks") or []
    print(f"Q: {a.question}")
    print(f"A: {answer}\n")
    if not a.quiet:
        print(f"--- evidence: {len(chunks)} retrieved chunk(s) ---")
        for i, c in enumerate(chunks[:6]):
            txt = c if isinstance(c, str) else (c.get("content") or c.get("text") or str(c))
            print(f"  [{i + 1}] {txt[:240].strip()}")
    refused = not answer.strip() or any(
        k in answer.lower() for k in ("không có thông tin", "xin lỗi", "chưa có")
    )
    print(f"\n[meta] refused≈{refused} · answer_len={len(answer)} · chunks={len(chunks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
