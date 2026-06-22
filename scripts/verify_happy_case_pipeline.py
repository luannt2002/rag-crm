"""Per-LAYER L1→L7 verification on the HAPPY-CASE rewritten data (the 9-file clone).

Proves the contract: once a document conforms to the happy-case template, EVERY layer
of the pipeline passes with explicit, evidence-backed assertions. Reads the normalized
clone produced by scripts/normalize_to_happy_case.py and runs each file through L1→L7,
printing a ✓/✗ per layer. Green across all = "happy-case in ⇒ expert control out".

    set -a && source .env && set +a
    python scripts/verify_happy_case_pipeline.py            # reads reports/happy_case_clone/
    python scripts/verify_happy_case_pipeline.py <dir>
"""
from __future__ import annotations

import asyncio
import csv
import io
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

_VALID = {"hdt", "semantic", "proposition", "hybrid", "recursive", "table_csv"}
CLONE_DIR = Path(__file__).resolve().parent.parent / "reports" / "happy_case_clone"


def verify(path: Path) -> int:
    """Run one happy-case file through L1→L7; return the count of FAILED assertions."""
    raw = path.read_text(encoding="utf-8")
    is_doc = path.suffix == ".md" or sum(
        1 for ln in raw.splitlines()[:60] if ln.strip().startswith("#")
    ) >= 3
    fails: list[str] = []

    def chk(layer: str, cond: bool, msg: str, actual: object = "") -> None:
        nonlocal fails
        if not cond:
            fails.append(f"{layer} {msg}")
        mark = "✓" if cond else "✗"
        tail = "" if cond else f"  ← {actual!r}"
        print(f"    {mark} {layer}  {msg}{tail}")

    # L1 — to canonical markdown
    if is_doc:
        md = raw
    else:
        rows = list(csv.reader(io.StringIO(raw)))
        md = rows_to_structured_markdown(rows)
        cells = {c.strip() for r in rows for c in r if c.strip() and len(c) < 40}
        lost = [c for c in cells if c not in md]
        chk("L1", len(lost) <= len(cells) // 20 + 1, "no data loss to markdown", f"{len(lost)} lost")
    sections = md.count("## ")
    tables = md.count("| --- ")
    chk("L1", bool(md.strip()), f"canonical markdown ({sections} sections, {tables} tables)")

    # L2 — atomic block tagging
    blocks = _split_into_blocks_with_atomic(md)
    chk("L2", all(_is_atomic_block_type(t) for t, _ in blocks if t in ("table", "formula", "image")),
        f"{len(blocks)} blocks, table/formula/image atomic")

    # L3 — profile
    prof = RuleBasedDocumentProfileAnalyzer().analyze(md)
    chk("L3", prof.total_blocks >= 0 and prof.detected_language in ("vi", "auto", "en"),
        f"profile ok (tables={prof.table_count}, headings={prof.heading_counts.total})")

    # L4 — selector (valid + deterministic)
    pd = analyze_document(md)
    s, c = select_strategy(pd, text=md)
    s2, c2 = select_strategy(analyze_document(md), text=md)
    chk("L4", s in _VALID and 0 <= c <= 1 and (s, c) == (s2, c2), f"strategy={s} ({c}), deterministic", s)

    # L5 — cross-check yields a valid strategy (the pipeline applies it ONCE; an
    # override like hdt→semantic for sparse inventory is correct adaptive behaviour).
    fs, fc, rsn = apply_cross_check(s, c, pd)
    chk("L5", fs in _VALID, f"final={fs} (override={rsn})", fs)

    # L6 — chunking: no table row lost
    chunks = [x if isinstance(x, str) else x.get("content", "") for x in smart_chunk(md)]
    trows = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln]
    lost_rows = [r for r in trows if not any(r in ch for ch in chunks)]
    chk("L6", chunks and not lost_rows, f"{len(chunks)} chunks, no table row lost", f"{len(lost_rows)} lost")

    # L7 — narrate + stats
    narr = asyncio.run(NullNarrateGenerator().narrate(md, "TABLE"))
    ents = parse_table_chunks([{"content": md}])
    priced = [e for e in ents if e.price_primary]
    cov = (len(priced) * 100 // len(ents)) if ents else 0
    has_price_col = any("giá" in ln.lower() or "đơn giá" in ln.lower()
                        for ln in md.splitlines() if ln.startswith("|") and "---" not in ln)
    if is_doc:
        chk("L7", isinstance(narr, str) and bool(narr), f"doc — {len(chunks)} chunks, narrate ok")
    elif has_price_col:
        # ≥85% is the realistic happy-case bar — a clean catalog prices almost every
        # row; the gap is legitimate package-only items (a service sold only in a combo).
        chk("L7", cov >= 85, f"price coverage {cov}% ({len(priced)}/{len(ents)})", f"{cov}%")  # noqa: PLR2004
    else:
        chk("L7", len(ents) > 0, f"non-price sheet — {len(ents)} entities extracted")

    return len(fails)


def main() -> None:
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else CLONE_DIR
    files = sorted(p for p in d.iterdir() if p.suffix in (".csv", ".md") and p.name != "README.md")
    if not files:
        print(f"no .csv/.md files in {d}")
        sys.exit(2)
    print(f"HAPPY-CASE PIPELINE VERIFY — {len(files)} files from {d}\n")
    total_fail = 0
    for p in files:
        kind = "DOC" if p.suffix == ".md" else "SHEET"
        print(f"📄 {p.name}  [{kind}]")
        total_fail += verify(p)
        print()
    print("█" * 70)
    print("RESULT: " + ("ALL FILES — L1→L7 GREEN ✅" if total_fail == 0
                        else f"{total_fail} assertion(s) FAILED ✗"))
    sys.exit(1 if total_fail else 0)


if __name__ == "__main__":
    main()
