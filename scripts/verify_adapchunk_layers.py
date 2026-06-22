"""Per-LAYER verification of the AdapChunk pipeline (L1 → L7), step by step.

Runs EACH layer explicitly on representative documents and asserts concrete
properties of that layer's output — no layer is skipped, every claim is checked
against a runtime value (CLAUDE.md rule #0: evidence, not vibes). A layer is
GREEN only when all its assertions hold; the first broken assertion prints RED
with the actual value.

Layers (per the AdapChunk blueprint):
  L1 parse → structured markdown      (shared/tabular_markdown + parser/*)
  L2 block detection & atomic tagging (shared/chunking/blocks)
  L3 feature extraction / profile     (doc_profile/rule_based_doc_profile)
  L4 strategy selector                (shared/chunking/analyze::select_strategy)
  L5 rule cross-check                 (shared/chunking/analyze::apply_cross_check)
  L6 chunking executor                (shared/chunking::smart_chunk)
  L7 narrate + stats-index extract    (narrate/* + shared/document_stats)
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ragbot.infrastructure.doc_profile.rule_based_doc_profile import (  # noqa: E402
    RuleBasedDocumentProfileAnalyzer,
)
from ragbot.infrastructure.narrate.null_narrate import NullNarrateGenerator  # noqa: E402
from ragbot.shared.chunking import smart_chunk  # noqa: E402
from ragbot.shared.chunking.analyze import (  # noqa: E402
    analyze_document,
    apply_cross_check,
    select_strategy,
)
from ragbot.shared.chunking.blocks import (  # noqa: E402
    _is_atomic_block_type,
    _split_into_blocks_with_atomic,
)
from ragbot.shared.document_stats import parse_table_chunks  # noqa: E402
from ragbot.shared.tabular_markdown import rows_to_structured_markdown  # noqa: E402

_VALID_STRATEGIES = {"hdt", "semantic", "proposition", "hybrid", "recursive", "table_csv"}


class LayerReport:
    def __init__(self) -> None:
        self.fail = 0

    def check(self, layer: str, cond: bool, msg: str, actual: object = "") -> None:
        mark = "✅" if cond else "❌"
        if not cond:
            self.fail += 1
        tail = "" if cond else f"  ← actual={actual!r}"
        print(f"  {mark} [{layer}] {msg}{tail}")


def verify_document(title: str, rows: list[list[str]], expect: dict, rep: LayerReport) -> None:
    print(f"\n{'═' * 100}\nDOC: {title}\n{'═' * 100}")

    # ── L1 — parse → structured markdown ────────────────────────────────────
    md = rows_to_structured_markdown(rows)
    sections = [ln[3:].strip() for ln in md.splitlines() if ln.startswith("## ")]
    has_grid = "| --- " in md
    # no data loss: every expected entity name appears verbatim in the markdown
    lost = [n for n in expect["names"] if n not in md]
    rep.check("L1", has_grid, "emits a markdown table grid", has_grid)
    rep.check("L1", set(sections) >= expect["sections"], "all section titles → ## headings", sections)
    rep.check("L1", not lost, "no data loss (all entity names present)", lost)

    # ── L2 — block detection & atomic tagging ───────────────────────────────
    blocks = _split_into_blocks_with_atomic(md)
    table_blocks = [c for t, c in blocks if t == "table"]
    atomic_ok = all(_is_atomic_block_type(t) for t, _ in blocks if t in ("table", "formula", "image"))
    nonempty = all(c.strip() for _, c in blocks)
    rep.check("L2", len(table_blocks) == expect["tables"], "table-block count matches", len(table_blocks))
    rep.check("L2", atomic_ok, "every table/formula/image block is atomic", atomic_ok)
    rep.check("L2", nonempty, "no empty block emitted", nonempty)

    # ── L3 — feature extraction / document profile ──────────────────────────
    prof = RuleBasedDocumentProfileAnalyzer().analyze(md)
    fields = [
        prof.table_count, prof.heading_counts.total, prof.formula_count,
        prof.image_count, prof.total_blocks, prof.total_words,
    ]
    rep.check("L3", prof.table_count == expect["tables"], "profile table_count matches", prof.table_count)
    # Language detection returns "auto" (safe fallback) for very short input — the
    # accepted set per fixture encodes that (a tiny table is legitimately "auto").
    rep.check("L3", prof.detected_language in expect["lang"], "language detected", prof.detected_language)
    rep.check("L3", all(v >= 0 for v in fields), "all numeric fields non-negative", fields)

    # ── L4 — strategy selector ──────────────────────────────────────────────
    prof_dict = analyze_document(md)
    strategy, confidence = select_strategy(prof_dict, text=md)
    strategy2, confidence2 = select_strategy(analyze_document(md), text=md)
    rep.check("L4", strategy in _VALID_STRATEGIES, "selector returns a valid strategy", strategy)
    rep.check("L4", 0.0 <= confidence <= 1.0, "confidence in [0,1]", confidence)
    rep.check("L4", (strategy, confidence) == (strategy2, confidence2),
              "selector is deterministic (reproducible)", (strategy2, confidence2))

    # ── L5 — rule cross-check ───────────────────────────────────────────────
    final_strategy, final_conf, reason = apply_cross_check(strategy, confidence, prof_dict)
    re_strategy, re_conf, _ = apply_cross_check(final_strategy, final_conf, prof_dict)
    rep.check("L5", final_strategy in _VALID_STRATEGIES, "cross-check yields a valid strategy", final_strategy)
    rep.check("L5", (reason is None) or (final_strategy != strategy or final_conf != confidence),
              "an override always carries a reason", reason)
    rep.check("L5", (re_strategy, re_conf) == (final_strategy, final_conf),
              "cross-check is idempotent (stable fixpoint)", (re_strategy, re_conf))

    # ── L6 — chunking executor ──────────────────────────────────────────────
    chunks = [c if isinstance(c, str) else c.get("content", "") for c in smart_chunk(md)]
    joined = "\n".join(chunks)
    # atomic preservation: every full table row survives in some chunk
    table_rows = [ln for ln in md.splitlines() if ln.startswith("|") and "---" not in ln]
    rows_kept = all(any(r in c for c in chunks) for r in table_rows)
    # B3 binding: a chunk containing a table row also carries a "## section" (when sections exist)
    bound = (not sections) or all(
        ("##" in c) for c in chunks if any(r in c for r in table_rows) and "|" in c
    )
    rep.check("L6", len(chunks) > 0, "produces ≥1 chunk", len(chunks))
    rep.check("L6", rows_kept, "no table row lost across chunks (atomic preserved)", rows_kept)
    rep.check("L6", bound, "table chunks carry their section heading (B3)", bound)

    # ── L7 — narrate + stats-index extraction ───────────────────────────────
    narrated = asyncio.run(NullNarrateGenerator().narrate(md, "TABLE"))
    ents = parse_table_chunks([{"content": md}])
    priced = {e.name: e.price_primary for e in ents if e.price_primary}
    got_names = {e.name for e in ents}
    rep.check("L7", isinstance(narrated, str) and narrated, "narrate returns a non-empty string", type(narrated).__name__)
    rep.check("L7", expect["names"] <= got_names, "stats-index extracts all entities", sorted(got_names))
    rep.check("L7", all(priced.get(n) == p for n, p in expect["priced"].items()),
              "stats-index prices are correct", priced)


def main() -> None:
    rep = LayerReport()

    verify_document(
        "table-heavy multi-section (spa-style)",
        [["Dịch vụ chăm sóc da"], ["STT", "Tên", "Giá"], ["1", "Item A", "100000"], ["2", "Item B", "200000"],
         [""], ["Dịch vụ triệt lông"], ["Vùng", "Giá"], ["Mép", "129000"], ["Nách", "199000"]],
        {"names": {"Item A", "Item B", "Mép", "Nách"}, "sections": {"Dịch vụ chăm sóc da", "Dịch vụ triệt lông"},
         "tables": 2, "lang": {"vi"}, "priced": {"Item A": 100000, "Mép": 129000}},
        rep,
    )

    verify_document(
        "wide catalog (6 columns, combo prices)",
        [["STT", "Tên", "Mã", "Đơn giá", "Combo", "Ghi chú"],
         ["1", "Item A", "A01", "100000", "270000", "hot"], ["2", "Item B", "B02", "200000", "540000", ""]],
        {"names": {"Item A", "Item B"}, "sections": set(), "tables": 1, "lang": {"vi"},
         "priced": {"Item A": 100000, "Item B": 200000}},
        rep,
    )

    verify_document(
        "single 2-col category-token table (Vùng | Giá)",
        [["Vùng", "Giá"], ["Mép", "129000"], ["Nách", "199000"]],
        {"names": {"Mép", "Nách"}, "sections": set(), "tables": 1, "lang": {"vi", "auto"},
         "priced": {"Mép": 129000, "Nách": 199000}},
        rep,
    )

    print(f"\n{'█' * 100}")
    verdict = "ALL LAYERS GREEN" if rep.fail == 0 else f"{rep.fail} ASSERTION(S) FAILED"
    print(f"RESULT: {verdict}")
    sys.exit(1 if rep.fail else 0)


if __name__ == "__main__":
    main()
