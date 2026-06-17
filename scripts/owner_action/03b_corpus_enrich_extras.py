#!/usr/bin/env python3
# Owner-action: relay operator-supplied corpus markdown through
# /api/ragbot/sync/documents (UPSERT). Idempotent on source URL.
#
# Required env: LOADTEST_TENANT_ID, LOADTEST_BOT_ID, LOADTEST_CHANNEL_TYPE.
# Optional env: RAGBOT_BASE_URL, RAGBOT_TOKEN, CORPUS_DOC_DIR.

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:3004"
TOKEN_PATH = "/api/ragbot/test/tokens/self"
SYNC_PATH = "/api/ragbot/sync/documents"

# Per-script HTTP timeouts (seconds).
TOKEN_HTTP_TIMEOUT_S = 10
SYNC_HTTP_TIMEOUT_S = 300

DOCS = [
    ("corpus_doc_booking_flow.md", "Quy trình đặt lịch"),
    ("corpus_doc_complaint_handling.md", "Xử lý khiếu nại"),
    ("corpus_doc_promotions.md", "Khuyến mãi và Ưu đãi"),
]


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: {name} env var REQUIRED", file=sys.stderr)
        sys.exit(1)
    return val


def _fetch_token(base_url: str) -> str:
    url = base_url.rstrip("/") + TOKEN_PATH
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=TOKEN_HTTP_TIMEOUT_S) as r:
            payload = json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        print(f"ERROR: token fetch failed at {url}: {exc}", file=sys.stderr)
        sys.exit(1)
    token = payload.get("token") if isinstance(payload, dict) else None
    if not token:
        print(f"ERROR: token endpoint returned no token: {payload!r}", file=sys.stderr)
        sys.exit(1)
    return token


def _build_payload(corpus_dir: Path, tenant_id: int, bot_id: str, channel_type: str) -> dict:
    docs = []
    for fname, title in DOCS:
        p = corpus_dir / fname
        if not p.is_file():
            print(f"ERROR: missing corpus file {p}", file=sys.stderr)
            sys.exit(1)
        content = p.read_text(encoding="utf-8")
        docs.append(
            {
                "title": title,
                "content": content,
                "url": f"local://corpus_enrich/{fname}",
                "source_type": "owner_action_corpus_enrich",
            }
        )
    return {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "wipe_existing": False,
        "documents": docs,
    }


def _post_sync(base_url: str, token: str, payload: dict) -> dict:
    url = base_url.rstrip("/") + SYNC_PATH
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=SYNC_HTTP_TIMEOUT_S) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body_preview = exc.read().decode("utf-8", errors="replace")[:512]
        print(f"ERROR: sync POST {url} failed http={exc.code}: {body_preview}", file=sys.stderr)
        sys.exit(2)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"ERROR: sync POST {url} failed: {exc}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    tenant_id = int(_require_env("LOADTEST_TENANT_ID"))
    bot_id = _require_env("LOADTEST_BOT_ID")
    channel_type = _require_env("LOADTEST_CHANNEL_TYPE")

    base_url = os.environ.get("RAGBOT_BASE_URL", DEFAULT_BASE_URL)
    corpus_dir = Path(os.environ.get("CORPUS_DOC_DIR", "/tmp"))

    token = os.environ.get("RAGBOT_TOKEN") or _fetch_token(base_url)

    payload = _build_payload(corpus_dir, tenant_id, bot_id, channel_type)
    print(
        f"INFO: posting {len(payload['documents'])} docs to {base_url}{SYNC_PATH} "
        f"tenant={tenant_id} bot={bot_id} channel={channel_type}"
    )

    result = _post_sync(base_url, token, payload)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
