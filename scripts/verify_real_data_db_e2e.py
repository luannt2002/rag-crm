"""End-to-end L1→L7 on ALL real customer documents pulled from the DB (3 bots).

Reads every document's ``raw_content`` from the database (the real ingested source —
spa price sheets, xe tire-policy sheets + warranty doc, the legal Thông tư) and runs
the FULL AdapChunk pipeline on each, reporting per-file what each layer produced and
flagging anomalies. Two source shapes are handled:

  * tabular (CSV)  → L1 ``rows_to_structured_markdown`` → L2…L7   (the 7 sheets)
  * prose/doc      → already markdown/text → L2…L7                (xe-4, the Thông tư)

Domain-neutral CODE — the customer data stays in the DB; nothing is hardcoded.

    set -a && source .env && set +a
    python scripts/verify_real_data_db_e2e.py
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import asyncpg  # noqa: E402

from ragbot.infrastructure.doc_profile.rule_based_doc_profile import (  # noqa: E402
    RuleBasedDocumentProfileAnalyzer,
)
from ragbot.shared.chunking import smart_chunk  # noqa: E402
from ragbot.shared.chunking.analyze import analyze_document, apply_cross_check, select_strategy  # noqa: E402
from ragbot.shared.chunking.blocks import _is_atomic_block_type, _split_into_blocks_with_atomic  # noqa: E402
from ragbot.shared.document_stats import parse_table_chunks  # noqa: E402
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402


_MD_HEADING_LINE = re.compile(r"^#{1,6}\s+\S")
_PRICE_HDR_RE = re.compile(r"\b(giá|đơn giá|price|gia|don gia|phí|amount|cost)\b", re.IGNORECASE)


def _looks_tabular(text: str) -> bool:
    """CSV/sheet source if most non-empty lines carry ≥1 comma AND the content does
    NOT already carry markdown headings. A doc that already has ``# Chương`` / ``##
    Điều`` (the legal Thông tư, parsed structured upstream) is a DOC, not a sheet —
    forcing it through the CSV converter would fabricate tables out of prose commas."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:60]
    if not lines:
        return False
    headings = sum(1 for ln in lines if _MD_HEADING_LINE.match(ln.strip()))
    if headings >= 3:  # noqa: PLR2004 — already a structured doc
        return False
    commas = sum(1 for ln in lines if ln.count(",") >= 1)
    return commas >= max(3, len(lines) // 2)


def _has_price_column(md: str) -> bool:
    """True when some header row names a price column — only then is 0% price
    coverage a real anomaly (an inventory / manifest / script sheet has no price
    column and legitimately yields 0 priced rows)."""
    for ln in md.splitlines():
        if ln.startswith("|") and "---" not in ln and _PRICE_HDR_RE.search(ln):
            return True
    return False


def verify_doc(name: str, raw: str) -> list[str]:
    anomalies: list[str] = []
    tabular = _looks_tabular(raw)

    # L1 — to structured markdown (sheets via the converter; docs already text/md)
    if tabular:
        rows = list(csv.reader(io.StringIO(raw)))
        md = rows_to_structured_markdown(rows)
        src_cells = {c.strip() for r in rows for c in r if c.strip()}
        lost = [c for c in src_cells if len(c) < 60 and c not in md]  # noqa: PLR2004
        if len(lost) > len(src_cells) // 10 + 1:
            anomalies.append(f"L1 data-loss: {len(lost)}/{len(src_cells)} short cells missing")
    else:
        md = raw

    sections = [ln.lstrip("#").strip() for ln in md.splitlines() if ln.lstrip().startswith("#")]
    tables = md.count("| --- ")

    # L2 — block detection
    blocks = _split_into_blocks_with_atomic(md)
    if not all(_is_atomic_block_type(t) for t, _ in blocks if t in ("table", "formula", "image")):
        anomalies.append("L2 atomic-tag broken")

    # L3 — profile
    prof = RuleBasedDocumentProfileAnalyzer().analyze(md)

    # L4 / L5
    prof_dict = analyze_document(md)
    strat, conf = select_strategy(prof_dict, text=md)
    fstrat, _, reason = apply_cross_check(strat, conf, prof_dict)

    # L6 — chunking: no table row lost
    chunks = [c if isinstance(c, str) else c.get("content", "") for c in smart_chunk(md)]
    table_rows = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln]
    rows_lost = [r for r in table_rows if not any(r in c for c in chunks)]
    if rows_lost:
        anomalies.append(f"L6 {len(rows_lost)} table row(s) lost in chunking")

    # L7 — stats extraction (sheets: price coverage; docs: entity count only)
    ents = parse_table_chunks([{"content": md}])
    priced = [e for e in ents if e.price_primary]
    cov = (len(priced) * 100 // len(ents)) if ents else 0

    kind = "SHEET" if tabular else "DOC"
    print(f"\n{'─' * 92}\n{name}  [{kind}]  raw={len(raw)} chars\n{'─' * 92}")
    print(f"  L1  sections={len(sections)} tables={tables}")
    if sections[:5]:
        print(f"      headings: {[s[:34] for s in sections[:5]]}")
    print(f"  L2  blocks={len(blocks)} (atomic ✓)   "
          f"L3  table_count={prof.table_count} h={prof.heading_counts.total} lang={prof.detected_language}")
    print(f"  L4  {strat} ({conf})  →  L5 {fstrat} (override={reason})   L6 chunks={len(chunks)}")
    if tabular:
        has_price_col = _has_price_column(md)
        catalog = "PRICE-CATALOG" if has_price_col else "non-price sheet (inventory/manifest)"
        print(f"  L7  [{catalog}] entities={len(ents)} priced={len(priced)} coverage={cov}%  "
              f"sample: " + ", ".join(f"{e.name[:16]}={e.price_primary}" for e in priced[:3]))
        # 0% coverage is only an anomaly when the sheet HAS a price column.
        if ents and cov == 0 and has_price_col:
            anomalies.append("L7 0% price coverage despite a price column")
    else:
        cats = len({e.category for e in ents if e.category})
        print(f"  L7  [DOC] chunks={len(chunks)} entities={len(ents)} (prose — price N/A, {cats} categories)")

    print("  " + ("✅ L1→L7 clean" if not anomalies else "⚠️  " + " | ".join(anomalies)))
    return anomalies


async def main() -> None:
    dsn = re.sub(r"\+\w+", "", os.environ.get("DATABASE_URL", ""))
    if not dsn:
        print("DATABASE_URL not set")
        sys.exit(2)
    con = await asyncpg.connect(dsn)
    rows = await con.fetch(
        "SELECT document_name, raw_content FROM documents "
        "WHERE deleted_at IS NULL AND raw_content IS NOT NULL ORDER BY document_name"
    )
    await con.close()

    print(f"REAL-DATA DB E2E — {len(rows)} customer documents (3 bots)")
    total = 0
    for r in rows:
        total += len(verify_doc(r["document_name"], r["raw_content"]))
    print(f"\n{'█' * 92}")
    print(f"RESULT: {len(rows)} docs · {total} anomalies"
          + ("  → ALL CLEAN ✅" if total == 0 else "  → see ⚠️ above"))


if __name__ == "__main__":
    asyncio.run(main())
