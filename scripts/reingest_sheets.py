#!/usr/bin/env python3
"""reingest_sheets.py — fetch Google Sheets URLs as CSV, POST sync API.

Use case: after ``clean_bot_data.py`` wiped a bot's corpus, re-ingest
the canonical sheet set for that bot. Each URL is exported via
``/spreadsheets/d/{id}/export?format=csv&gid={N}``, the CSV bytes are
attached as ``content`` (the Stream A pipeline parses them via
``GoogleSheetsParser`` server-side).

URLs file format (JSON list, one entry per sheet)::

    [
      {"title": "Thông tin spa", "url": "https://docs.google.com/.../edit?gid=0#gid=0"},
      {"title": "Thông tin các dịch vụ", "url": "https://docs.google.com/...#gid=749628067"}
    ]

Public-share URLs only (MVP). Private sheets need OAuth handler — out
of scope here.

Usage::

    python scripts/reingest_sheets.py \\
        --bot-id 1774946011723 --tenant-id 32 --channel-type web \\
        --urls-file plans/260506-streamA-doc-pipeline/drmedispa_sheets.json \\
        --token "$RAGBOT_TOKEN"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# Default base URL (overridable by env or --base-url)
DEFAULT_BASE_URL = "http://localhost:3004"
SYNC_PATH = "/api/ragbot/sync/documents"
SELF_TOKEN_PATH = "/api/ragbot/test/tokens/self"
SHEET_TIMEOUT_S = 30.0
SYNC_TIMEOUT_S = 300.0  # ingest can take minutes for large sheets

# Google Sheets URL parser:
#   https://docs.google.com/spreadsheets/d/{ID}/edit?gid={GID}#gid={GID}
# Extract spreadsheet ID + gid for the CSV export endpoint.
_SHEET_ID_RE = re.compile(
    r"https?://docs\.google\.com/spreadsheets/d/(?P<id>[A-Za-z0-9_-]+)"
)
_SHEET_GID_RE = re.compile(r"[?&#]gid=(?P<gid>\d+)")


def parse_sheet_url(url: str) -> tuple[str, str] | None:
    url = url.strip()
    id_m = _SHEET_ID_RE.match(url)
    if not id_m:
        return None
    sheet_id = id_m.group("id")
    # Search for gid anywhere in URL (query OR fragment); default 0 (first sheet).
    gid_m = _SHEET_GID_RE.search(url)
    gid = gid_m.group("gid") if gid_m else "0"
    return sheet_id, gid


def csv_export_url(sheet_id: str, gid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


async def fetch_sheet_csv(client: Any, url: str) -> bytes:
    parts = parse_sheet_url(url)
    if parts is None:
        raise ValueError(f"not a recognisable Google Sheets URL: {url}")
    export = csv_export_url(*parts)
    resp = await client.get(export, follow_redirects=True, timeout=SHEET_TIMEOUT_S)
    resp.raise_for_status()
    return resp.content


async def get_self_token(client: Any, base_url: str) -> str:
    """Mint a dev/owner token via /api/ragbot/test/tokens/self (GET).

    Requires server has RAGBOT_DEV_TOKEN_ENABLED=true (loopback only).
    Caller can also pass --token directly to skip this.
    """
    resp = await client.get(
        f"{base_url}{SELF_TOKEN_PATH}",
        params={"role": "owner"},
        timeout=SHEET_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    # Endpoint returns either {"data": {"token": ...}} or {"token": ...}
    if "data" in data and isinstance(data["data"], dict) and "token" in data["data"]:
        return data["data"]["token"]
    return data["token"]


async def post_sync_documents(
    client: Any,
    *,
    base_url: str,
    token: str,
    tenant_id: int,
    bot_id: str,
    channel_type: str,
    documents: list[dict[str, Any]],
) -> dict[str, Any]:
    body = {
        "tenant_id": tenant_id,
        "bot_id": bot_id,
        "channel_type": channel_type,
        "documents": documents,
        "wipe_existing": False,  # we already wiped via clean_bot_data
    }
    resp = await client.post(
        f"{base_url}{SYNC_PATH}",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
        timeout=SYNC_TIMEOUT_S,
    )
    if resp.status_code >= 400:
        print(f"[sync] HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        resp.raise_for_status()
    return resp.json()


async def _amain(args: argparse.Namespace) -> int:
    import httpx

    urls_file = Path(args.urls_file).resolve()
    if not urls_file.exists():
        print(f"ERROR: urls-file not found: {urls_file}", file=sys.stderr)
        return 2
    sheets = json.loads(urls_file.read_text(encoding="utf-8"))
    if not isinstance(sheets, list) or not sheets:
        print(f"ERROR: urls-file must be a non-empty JSON list", file=sys.stderr)
        return 2
    print(f"[sheets] {len(sheets)} URLs from {urls_file}", flush=True)

    async with httpx.AsyncClient() as client:
        # Token
        token = args.token or os.environ.get("RAGBOT_TOKEN", "")
        if not token:
            print("[token] no --token / RAGBOT_TOKEN; minting via /test/tokens/self", flush=True)
            token = await get_self_token(client, args.base_url)
        print(f"[token] using {token[:30]}...", flush=True)

        # Fetch all sheets first (so a fetch failure doesn't leave partial ingest)
        documents: list[dict[str, Any]] = []
        for i, sheet in enumerate(sheets):
            title = sheet.get("title", f"Sheet {i}")
            url = sheet.get("url", "")
            print(f"[fetch {i + 1:02d}/{len(sheets):02d}] {title}", end=" ", flush=True)
            try:
                content_bytes = await fetch_sheet_csv(client, url)
                # CSV bytes → utf-8 string for `content` field. Server's
                # GoogleSheetsParser handles row-as-chunk parsing.
                content = content_bytes.decode("utf-8", errors="replace")
                documents.append({
                    "title": title,
                    "content": content,
                    "url": url,
                    "source_type": "google_sheets",
                })
                print(f"OK ({len(content_bytes)} bytes)", flush=True)
            except Exception as exc:  # noqa: BLE001 — fetch must continue past one bad URL
                print(f"FAIL: {exc}", flush=True)
                if args.strict:
                    return 3

        if not documents:
            print("ERROR: 0 sheets fetched successfully — abort sync", file=sys.stderr)
            return 4

        if args.dry_run:
            print(f"\n[dry-run] would POST {len(documents)} docs to {args.base_url}{SYNC_PATH}")
            for d in documents:
                print(f"  - {d['title']:40s} {len(d['content']):>8} chars")
            return 0

        # POST sync
        print(f"\n[sync] POST {len(documents)} docs to {args.base_url}{SYNC_PATH}", flush=True)
        result = await post_sync_documents(
            client,
            base_url=args.base_url,
            token=token,
            tenant_id=args.tenant_id,
            bot_id=args.bot_id,
            channel_type=args.channel_type,
            documents=documents,
        )
        # Pretty-print result summary
        data = result.get("data") or result
        total_chunks = data.get("total_chunks", 0)
        docs_result = data.get("documents", []) or data.get("doc_results", [])
        print(f"\n[done] total_chunks={total_chunks} docs={len(docs_result)}", flush=True)
        for d in docs_result:
            print(
                f"  - {d.get('title', '?'):40s} "
                f"chunks={d.get('chunks', 0):>4} "
                f"embedded={d.get('embedded', 0):>4}",
                flush=True,
            )
        return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Re-ingest Google Sheets via /sync/documents.")
    p.add_argument("--bot-id", required=True)
    p.add_argument("--tenant-id", type=int, required=True)
    p.add_argument("--channel-type", required=True)
    p.add_argument("--base-url", default=os.environ.get("RAGBOT_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--urls-file", required=True, help="JSON list of {title, url} sheet entries")
    p.add_argument("--token", default="", help="Bearer token (else mint via test endpoint)")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch sheets + show what would POST, but don't call sync.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Abort on any sheet fetch failure (default: skip + continue).",
    )
    return p.parse_args()


def main() -> int:
    return asyncio.run(_amain(_parse_args()))


if __name__ == "__main__":
    sys.exit(main())
