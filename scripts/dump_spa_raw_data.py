"""Dump the FULL raw data of the test-spa-id bot from the DB to a markdown file.

Per source document (mapped to its Google-Sheet gid): every chunk (raw
pre-enrichment text preferred) + every extracted stats-index entity. This is the
ground-truth reference of what the bot actually ingested — read-only.

    set -a && source .env && set +a && python scripts/dump_spa_raw_data.py
"""
from __future__ import annotations

import asyncio
import json
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

OUT = "reports/SPA_RAW_DATA_DETAIL_20260622.md"
# gid -> human label from the user's source list.
GID_LABEL = {
    "1394860155": "spa-1",
    "749628067": "spa-2",
    "0": "spa-3",
    "227222648": "spa-4",
}


def _raw_of(meta: object) -> str | None:
    if isinstance(meta, dict):
        return meta.get("raw_chunk")
    if isinstance(meta, str):
        try:
            return json.loads(meta).get("raw_chunk")
        except (ValueError, AttributeError):
            return None
    return None


async def main() -> None:
    eng = create_async_engine(os.environ["DATABASE_URL"])
    lines: list[str] = ["# SPA bot — FULL raw data detail (from DB)\n"]
    async with eng.connect() as conn:
        bid = (
            await conn.execute(
                text("SELECT id FROM bots WHERE bot_id='test-spa-id' LIMIT 1")
            )
        ).scalar()
        docs = (
            await conn.execute(
                text(
                    """SELECT id, source_url, content_chars,
                              substring(source_url from 'gid=([0-9]+)') AS gid
                       FROM documents WHERE record_bot_id = :b
                       ORDER BY substring(source_url from 'gid=([0-9]+)')"""
                ),
                {"b": bid},
            )
        ).fetchall()

        for doc_id, src, chars, gid in docs:
            label = GID_LABEL.get(str(gid), "spa-?")
            lines.append(f"\n\n{'=' * 70}\n## {label}  (gid={gid}, {chars} chars)\n")
            lines.append(f"- source: `{src}`\n- doc_id: `{doc_id}`\n")

            chunks = (
                await conn.execute(
                    text(
                        """SELECT chunk_index, chunk_type, content, metadata_json
                           FROM document_chunks WHERE record_document_id = :d
                           ORDER BY chunk_index"""
                    ),
                    {"d": doc_id},
                )
            ).fetchall()
            ents = (
                await conn.execute(
                    text(
                        """SELECT entity_name, entity_category, price_primary,
                                  price_secondary
                           FROM document_service_index WHERE record_document_id = :d
                           ORDER BY price_primary NULLS LAST, entity_name"""
                    ),
                    {"d": doc_id},
                )
            ).fetchall()

            lines.append(
                f"\n### Extracted stats entities ({len(ents)}) — name | category | price1 | price2\n"
            )
            for n, cat, p1, p2 in ents:
                lines.append(f"- `{n}` | cat=`{cat or ''}` | {p1} | {p2}")

            lines.append(f"\n### Raw chunks ({len(chunks)})\n")
            for idx, ctype, content, meta in chunks:
                raw = _raw_of(meta)
                body = (raw or content or "").strip()
                lines.append(
                    f"\n**chunk {idx}** [{ctype}]{' (raw_chunk)' if raw else ' (content)'}:\n```\n{body}\n```"
                )

    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"wrote {OUT}: {len(docs)} docs")
    await eng.dispose()


if __name__ == "__main__":
    asyncio.run(main())
