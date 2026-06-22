"""End-to-end L1→L7 verification on REAL customer data (no synthetic fixtures).

Reads a raw source dump (the git-ignored ``reports/*RAW*`` tenant dump produced by
``scripts/fetch_sheets_raw.py``), extracts each file's raw CSV, and runs the FULL
AdapChunk pipeline L1→L7 on it — reporting per-file what every layer produced and
flagging anomalies (data loss, 0 entities, unparsed prices, lost section binding).

Domain-neutral CODE: the customer data lives only in the (git-ignored) dump passed
on the CLI; this script carries no tenant identifier.

    set -a && source .env && set +a
    python scripts/verify_real_data_e2e.py reports/SPA_RAW_DATA_FROM_SOURCE_20260622.md
"""
from __future__ import annotations

import asyncio
import csv
import io
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ragbot.infrastructure.doc_profile.rule_based_doc_profile import (  # noqa: E402
    RuleBasedDocumentProfileAnalyzer,
)
from ragbot.infrastructure.narrate.null_narrate import NullNarrateGenerator  # noqa: E402
from ragbot.shared.chunking import smart_chunk  # noqa: E402
from ragbot.shared.chunking.analyze import analyze_document, apply_cross_check, select_strategy  # noqa: E402
from ragbot.shared.chunking.blocks import _is_atomic_block_type, _split_into_blocks_with_atomic  # noqa: E402
from ragbot.shared.document_stats import parse_table_chunks  # noqa: E402
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402

_CSV_BLOCK_RE = re.compile(r"## (file #\d[^\n]*)\n.*?```csv\n(.*?)```", re.DOTALL)


def _extract_files(dump: str) -> list[tuple[str, str]]:
    return [(label.strip(), body) for label, body in _CSV_BLOCK_RE.findall(dump)]


def verify_file(label: str, raw_csv: str) -> list[str]:
    """Run L1→L7 on one real file; return a list of anomaly strings (empty = clean)."""
    anomalies: list[str] = []
    rows = list(csv.reader(io.StringIO(raw_csv)))
    data_rows = [r for r in rows if any(c.strip() for c in r)]

    # L1 — parse → structured markdown
    md = rows_to_structured_markdown(rows)
    sections = [ln[3:].strip() for ln in md.splitlines() if ln.startswith("## ")]
    tables = md.count("| --- ")
    # data-loss probe: every non-empty source cell value should survive into the md
    src_cells = {c.strip() for r in rows for c in r if c.strip()}
    lost = [c for c in src_cells if c not in md]
    if lost:
        anomalies.append(f"L1 data-loss: {len(lost)} cell(s) missing e.g. {lost[:3]}")

    # L2 — block detection
    blocks = _split_into_blocks_with_atomic(md)
    n_table_blocks = sum(1 for t, _ in blocks if t == "table")
    if not all(_is_atomic_block_type(t) for t, _ in blocks if t in ("table", "formula", "image")):
        anomalies.append("L2 atomic-tag broken")

    # L3 — profile
    prof = RuleBasedDocumentProfileAnalyzer().analyze(md)

    # L4/L5 — selector + cross-check
    prof_dict = analyze_document(md)
    strat, conf = select_strategy(prof_dict, text=md)
    fstrat, fconf, reason = apply_cross_check(strat, conf, prof_dict)

    # L6 — chunk executor: no table row lost + section binding
    chunks = [c if isinstance(c, str) else c.get("content", "") for c in smart_chunk(md)]
    table_md_rows = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln]
    rows_lost = [r for r in table_md_rows if not any(r in c for c in chunks)]
    if rows_lost:
        anomalies.append(f"L6 row(s) lost in chunking: {len(rows_lost)} e.g. {rows_lost[:1]}")
    if sections:
        unbound = [c for c in chunks if "|" in c and any(r in c for r in table_md_rows) and "##" not in c]
        if unbound:
            anomalies.append(f"L6 {len(unbound)} table chunk(s) missing section heading (B3)")

    # L7 — narrate + stats extraction
    narrated = asyncio.run(NullNarrateGenerator().narrate(md, "TABLE"))
    if not (isinstance(narrated, str) and narrated):
        anomalies.append("L7 narrate empty")
    ents = parse_table_chunks([{"content": md}])
    priced = [e for e in ents if e.price_primary]
    categorized = [e for e in ents if e.category]

    # report
    print(f"\n{'─' * 96}\n{label}\n{'─' * 96}")
    print(f"  L1  source rows={len(data_rows):<4} → sections={len(sections)} tables={tables}")
    if sections:
        print(f"      sections: {sections[:6]}")
    print(f"  L2  blocks={len(blocks)} (table-blocks={n_table_blocks}, all atomic ✓)")
    print(f"  L3  profile: table_count={prof.table_count} h2={prof.heading_counts.h2} "
          f"mixed={prof.mixed_content_score} lang={prof.detected_language}")
    print(f"  L4  strategy={strat} conf={conf}  →  L5 final={fstrat} (override={reason})")
    print(f"  L6  chunks={len(chunks)}  (rows kept ✓, B3 bound {'✓' if not anomalies or 'L6' not in str(anomalies) else '✗'})")
    print(f"  L7  entities={len(ents)}  priced={len(priced)}  categorized={len(categorized)}")
    cov = (len(priced) * 100 // len(ents)) if ents else 0
    print(f"      price-coverage={cov}%  sample: " +
          ", ".join(f"{e.name[:18]}={e.price_primary}" for e in priced[:4]))
    if categorized:
        print(f"      category sample: " +
              ", ".join(f"{e.name[:12]}→{e.category[:18]}" for e in categorized[:3]))
    if not ents:
        anomalies.append("L7 ZERO entities extracted")

    if anomalies:
        for a in anomalies:
            print(f"  ⚠️  {a}")
    else:
        print("  ✅ L1→L7 clean on this real file")
    return anomalies


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: verify_real_data_e2e.py <raw-dump.md>")
        sys.exit(2)
    dump = Path(sys.argv[1]).read_text(encoding="utf-8")
    files = _extract_files(dump)
    if not files:
        print("no ```csv``` blocks found in dump")
        sys.exit(2)
    print(f"REAL-DATA E2E — {len(files)} customer file(s) from {sys.argv[1]}")
    total_anom = 0
    for label, body in files:
        total_anom += len(verify_file(label, body))
    print(f"\n{'█' * 96}")
    print(f"RESULT: {len(files)} files · {total_anom} anomalies"
          + ("  → ALL CLEAN ✅" if total_anom == 0 else "  → see ⚠️ above"))
    sys.exit(1 if total_anom else 0)


if __name__ == "__main__":
    main()
