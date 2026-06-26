# RAG SOTA 2026 — case studies that fix OUR root causes, multi-bot compliant (2026-06-25)

Multi-agent web research (20+ search agents) on the latest 2024–2026 RAG literature, mapped
to the two root causes found in the spa/xe deep-dive. **Citations are agent-gathered — verify
the arXiv URLs before depending on a number.**

## Headline
The entire 2024–2026 table-RAG literature **converges on our mindset**: every system surveyed
is **schema-agnostic** (the table's own column headers ARE the schema; NONE hardcode a domain
or a column meaning). Our "engine knows STRUCTURE not MEANING" stance is the SOTA consensus, not
a local invention. Three concrete, domain-neutral techniques map 1:1 to our 3 root causes.

---

## Root cause A — 2-row / merged header → unlabeled `col_N` (xe N5 date/image, N2 stock)
Our bug: xe-1 header is split across 2 rows (row1 `Tên kho|Mã|Tên hàng`, row2 `date1|date2|hình
ảnh…`); parser reads only row1 → date/image columns become unlabeled `col_3/col_4` → LLM gets
"col_3: 26" and can't map it to "date" → refuses despite having the value.

**SOTA fix — hierarchical/merged-header preservation (header-path concatenation):**
| Source | Year | Technique | Domain-neutral | Deterministic |
|---|---|---|---|---|
| **MixRAG** (SIGKDD 2026, arXiv 2504.09554) | 2026 | **H-RCL** (Hierarchy Row-and-Column-Level): preserves headers + merged cells + column hierarchy instead of linearizing. +46% top-1 retrieval | ✅ structural, no domain | hybrid |
| **SpreadsheetLLM / SheetCompressor** (Microsoft, arXiv 2407.09025) | 2024 | structural-anchor layout detection; auto-detects header region | ✅ | ✅ |
| **NVIDIA Nemotron pipeline** (dev blog, Feb 2026) | 2026 | table→**markdown** (not CSV/JSON) "significantly reduces numeric hallucination" — markdown keeps header↔cell binding | ✅ | ✅ |
| **Docling / TATR** (header-path) | 2024 | row-span/col-span propagation merges multi-row headers into one labelled column ("Sản phẩm > date1") | ✅ | ✅ (no LLM) |

→ **Our fix (domain-neutral, deterministic, no LLM):** detect a **multi-row header** (row 1 has
empty cells that row 2 fills) and **merge by header-path concatenation** → the column label
becomes "date1"/"hình ảnh1" (from row 2), not `col_3`. Pure structural, works for ANY industry's
2-row sheet. This is the SOTA-standard table preprocessing we're missing.

---

## Root cause B — price-coupled stats + aggregate-render drops labelled attributes (spa combo/trải nghiệm)
Our bug: the synthetic chunk renders `name: price_primary` and drops the labelled `attributes_json`
("Giá Combo 10 buổi": 1499000); the structured index is hardwired to `price_*`.

**SOTA fix — schema+cell retrieval + labelled-cell linearization (= our ADR-0007):**
| Source | Year | Technique | Maps to |
|---|---|---|---|
| **TableRAG** (Google, NeurIPS 2024, arXiv 2410.04739) | 2024 | **schema retrieval + cell retrieval** — index COLUMN-SCHEMA and individual CELLS separately, retrieve by relevance; column headers ARE the schema (no hardcoding) | exactly our **PRICE-index → ATTRIBUTE-index** (ADR-0007) |
| **Table-linearization consensus** (multiple, 2024–2025) | 2024–25 | "**col: value**" labelled serialization keeps every cell bound to its column label; markdown > CSV/JSON for header retention | our **render-faithful synthetic chunk** (S1) |
| **TAG / Chain-of-Table / DATER** (2023–2024) | — | schema consumed at runtime from live headers; no domain pre-encoding | confirms domain-neutral generic-attribute approach |

→ **Our fix:** ADR-0007 S1 (render ALL labelled `attributes_json` in the synthetic chunk, not just
price) + S2-S4 (generic numeric-attribute index). The research validates this is exactly TableRAG's
schema+cell design — domain-neutral, SOTA.

---

## Root cause C — multi-sheet fragmentation (Mã in sheet A, Giá in B, stock in C — not joined)
Our bug: a query "tồn kho của X" matches the product-master entity (no stock) and refuses; stock
lives in a different sheet under the same identity, never joined.

**SOTA fix — column-embedding joinability (LLM-free), NOT hardcoded keys:**
| Source | Year | Technique | Domain-neutral | Cost |
|---|---|---|---|---|
| **REaR** (arXiv 2511.00805) | 2025 | Retrieve→**Expand** (join via precomputed column embeddings)→Refine; **LLM-FREE** join discovery | ✅ join key inferred, not hardcoded | low (offline embeddings) |
| **Join-Aware Retrieval / JAR** (ACL 2024, arXiv 2404.09889) | 2024 | join discovered from column-overlap / value-overlap signals per query | ✅ | MIP (higher) |
| **GTR / T-RAG** (arXiv 2504.01346) | 2025 | hierarchical index + auto-built schema graph linking tables by shared columns | ✅ auto | medium |

→ **Our fix:** cross-sheet entity-join keyed on a **shared identifier column discovered by
column-embedding similarity** (e.g. the SKU "Mã" present in multiple sheets), NOT a hardcoded key.
REaR's LLM-free column-embedding join is the cheapest domain-neutral fit (matches ADR-0003
entity-join, now SOTA-validated). A sheet sharing no key (the Chinese manifest) simply doesn't join.

---

## What this means for the program
1. **Mindset confirmed**: schema-agnostic / no-hardcoded-domain is the unanimous 2024–2026 consensus.
   Our ADR-0006 (owner-declared roles) + ADR-0007 (PRICE→ATTRIBUTE) are SOTA-aligned, not bespoke.
2. **3 concrete, domain-neutral, mostly-deterministic fixes** — each maps to a published case study:
   - **A. Multi-row header merge** (header-path concat — Docling/TATR/MixRAG) → fixes xe N5/N2. **Deterministic, no LLM, highest ROI.**
   - **B. Render-faithful labelled-cell linearization + attribute-index** (TableRAG) → fixes spa combo + the price-coupling betrayal. = ADR-0007 S1–S4.
   - **C. Column-embedding cross-sheet join** (REaR, LLM-free) → fixes multi-sheet fragmentation. = ADR-0003.
3. **Sequencing** (cheapest/most-deterministic first): A (header-merge) → B-S1 (render-faithful) →
   C (join) → B-S2..S4 (attribute index). All flag-gated + measured A/B (no-guess-must-measure).

## Skills available for the work (no new install needed)
`deep-debug-to-expert` (5-axis + adversarial verify + TDD in worktree), `rag-flow-debug`
(end-to-end trace), `deep-research`, `rag-loadtest`, `rag-painpoints` — already registered.

## Caveat (rule#0)
Subagents hit the session limit and the opus safety-classifier was unavailable for some; the
paper IDs/URLs are agent-gathered and cross-checked across multiple independent agents (TableRAG,
MixRAG, REaR appeared repeatedly) but **verify each arXiv URL before citing externally**. The
FIX techniques (header-path merge, labelled linearization, column-embedding join) are
well-established and independently verifiable against our own code.
