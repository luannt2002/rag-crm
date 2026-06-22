"""Fetch the raw CSV of one or more Google-Sheet tabs to a local file.

Generic + domain-neutral: sheet URLs are passed on the CLI (or one-per-line via
--urls-file), NEVER hardcoded — the script carries no tenant identifiers. Each
``…/edit?gid=N`` URL is rewritten to the ``…/export?format=csv&gid=N`` endpoint
and fetched. Output is a single markdown file (default under reports/, which is
git-ignored for tenant data — do NOT commit the dump).

    set -a && source .env && set +a
    python scripts/fetch_sheets_raw.py --out /tmp/sheets.md \\
        "<sheet-url-1>" "<sheet-url-2>" ...
"""
from __future__ import annotations

import argparse
import re
import sys

import httpx


def _csv_export_url(edit_url: str) -> tuple[str, str, str]:
    """(export_url, sheet_id, gid) from an /edit?gid= sheet URL."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", edit_url)
    if not m:
        raise ValueError(f"not a spreadsheet URL: {edit_url}")
    sheet_id = m.group(1)
    gm = re.search(r"[?&#]gid=([0-9]+)", edit_url)
    gid = gm.group(1) if gm else "0"
    return (
        f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}",
        sheet_id,
        gid,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("urls", nargs="*", help="sheet edit URLs")
    p.add_argument("--urls-file", help="file with one sheet URL per line")
    p.add_argument("--out", default="reports/SHEETS_RAW_DUMP.md")
    a = p.parse_args(argv)

    urls = list(a.urls)
    if a.urls_file:
        with open(a.urls_file, encoding="utf-8") as fh:
            urls += [ln.strip() for ln in fh if ln.strip() and not ln.startswith("#")]
    if not urls:
        print("no URLs given", file=sys.stderr)
        return 2

    parts: list[str] = ["# Google-Sheet RAW source dump\n"]
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for i, url in enumerate(urls, 1):
            try:
                export_url, sid, gid = _csv_export_url(url)
                r = client.get(export_url)
                ok = r.status_code == 200 and "<html" not in r.text[:200].lower()
                body = r.text if ok else f"<fetch failed: http {r.status_code} / not public>"
            except (httpx.HTTPError, ValueError) as exc:
                sid = gid = "?"
                body = f"<error: {exc}>"
                ok = False
            parts.append(
                f"\n\n{'=' * 70}\n## file #{i}  (sheet_id={sid}, gid={gid})  "
                f"[{'OK' if ok else 'FAIL'}, {len(body)} chars]\n"
                f"- url: `{url}`\n\n```csv\n{body}\n```"
            )
            print(f"#{i} gid={gid}: {'OK' if ok else 'FAIL'} ({len(body)} chars)")

    with open(a.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(parts))
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
