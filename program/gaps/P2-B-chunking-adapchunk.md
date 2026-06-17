# P2-B — CHUNKING & ADAPCHUNK AUDITOR · Phase 2 gap report

> Agent: P2-B (was P1-B) · Date: 2026-06-10 · Mode: READ-ONLY src/alembic/tests + READ-ONLY DB SELECT.
> Anchor: branch `fix-260604-action-slotmachine-dead-key`, HEAD `7dd1f84`.
> Every claim = file:line / commit / DB-row / link. CHARTER stance = EVOLVE (keep mindset, swap engine via ADR).
> Settles P1-SYNTHESIS §5 contradiction (_narrate_service LIVE vs DEAD) with DB evidence + line-by-line runtime trace.
> Labels: ✅ live-and-sound · 🕰 outdated-vs-SOTA-2026 / dark-since-ship · ↔️ doc≠code conflict · 🐛 bug with repro.

---

## (1) `_narrate_service` — DEFINITIVE VERDICT + trace + DB evidence

### VERDICT: **LIVE-and-working** ✅ — certain (code trace × DB ground truth agree). P1-E correct; the "DEAD/waiting" claim (charter pre-seed) conflated the *block-feed* gap (real, 🐛-A) with the *narrate* gap (does not exist).

**Runtime ingest call path, line by line (document_worker → ingest → embed):**
1. **Worker builds the service every ingest** — `document_worker.py:322-354`: reads `narrate_provider` (default `"llm"`, `constants/_20_cag_mode_cache_augmented_gen.py:57`) + `narrate_then_embed_enabled` (default `True`, `_20:80`). **No `system_config` row exists for either key (psql 2026-06-10)** → constants rule → ON. Provider `"llm"` → resolves `intent="enrichment"` LLM spec (`:331-335`) → `build_narrate("llm", …)` → `NarrateService(strategy=…, enabled=True)` (`:345-347`).
2. **Injected** — `DocumentService(…, narrate_service=_narrate_svc)` `document_worker.py:383`; ingest invoked with flattened `content=full_text` `:387-399`.
3. **Called at embed time (U7)** — `document_service.py:2891-2902`: after the embedding-text strategy computes `texts_to_embed`, `if self._narrate_service is not None:` → `await _narrate_chunks_for_embed(...)` → **`texts_to_embed = narrated_texts` (:2902)**. The dense encoder then consumes exactly these bytes (passage prefix `:2912-2917` → embed → INSERT).
4. **Per-chunk routing** — `narrate_dispatch.py:147-156`: `classify_chunk_block_type` → `narrate_service.narrate_chunk(text, block_type)`; eligible types `("TABLE","FORMULA","IMAGE")` (`constants/_18_admin_all_tenants_analytics_.py:177`, `NARRATE_BLOCK_TYPES_DEFAULT`); TEXT → passthrough, zero LLM cost.
5. **Dual persistence** — `content` column NOT overwritten (stays raw); `raw_chunk` + `narrated_text` + `block_type` → `metadata_json` (`document_service.py` ~:3417-3435).

**DB measurement (`psql` on `$DATABASE_URL` minus `+asyncpg`, ragbot_v2_dev, 2026-06-10):**

| Query | Result |
|---|---|
| `system_config` rows `LIKE '%narrate%'` | **0** → defaults apply (enabled=True, provider=llm) |
| total `document_chunks` | 560 |
| chunks carrying `narrated_text`/`raw_chunk`/`block_type` meta | **560 / 560** (dispatch ran on every chunk) |
| `block_type` histogram (metadata) | **TEXT=349 (narrated==raw, passthrough), TABLE=211** |
| `narrated_text IS DISTINCT FROM raw_chunk` | **211/211 TABLE chunks** — every one got a *different* LLM narration |
| TABLE chunks containing a pipe `\|` / tab | **9 / 211** · **0 / 211** |
| TABLE label × strategy | recursive 123 · table_csv 48 · semantic 34 · hdt 6 |
| live strategy mix (all 560) | recursive 280 · semantic 93 · hdt 90 · table_csv 83 · hybrid 14 |

**Real rows seen (legal corpus sample):** raw = `[Chương 2 > Mục 4 > Điều 23…] Tổ chức thực hiện quản lý an toàn… 1. Xây dựng quy định về…` → narrated (embedded) = `"Điều 23 quy định tổ chức quản lý an toàn, bảo mật hệ thống mạng bao gồm xây dựng quy định quản lý, lập hồ sơ sơ đồ mạng…"`. Raw preserved in `content` → citations + answer LLM read source, narration is embed-target only.

**Conclusion:** the narrator demonstrably ran 211 times (output ≠ input), the narration IS what got embedded, the raw is what answers cite. AdapChunk Tầng-6 spec behaving as designed. **LIVE.**

**The catch discovered while settling it (🐛-B, now root-caused to the exact line):** of 211 "TABLE" chunks only 9 contain real pipe-table markup; **~163 (recursive 123−9 + semantic 34 + hdt 6) are prose** misclassified TABLE. This session **replayed the repo classifier on a real stored chunk**: VN legal clause lines (`a) Chia tách thành các vùng mạng khác nhau theo đối tượng sử dụng, mục đích…;`) trip `_is_table_line`'s CSV-comma rule — **`chunking.py:253-256`**: ≥`DEFAULT_CSV_MIN_COMMAS` commas + no `". "` + doesn't end `"."` (legal điểm-lines end `";"`) → line flagged `table` → dominant-by-chars → whole chunk TABLE → LLM-narrated and embedded as summary. See 🐛-B + repro T1.

---

## (2) Labeled component table (✅ / 🕰 / ↔️ / 🐛)

| Component | Label | Evidence (file:line / DB / commit) |
|---|---|---|
| **L1** Kreuzberg layout-aware parse → typed Blocks w/ `is_atomic`+heading ctx | ✅ at parser | `kreuzberg_parser.py:157-309`; default engine `_13:11` |
| **L1→L4 boundary: Block stream flattened (Block→str)** | 🐛 -A | `document_worker.py:295` + `document_service.py:1293`; kills L2/L6 input |
| **L2** `_smart_chunk_with_atomic_protect` FORMULA/IMAGE/CODE protect | 🕰 dark since 2026-05-13 | `chunking.py:2425`, dispatch `:2586-2593`; flag const False `_00:95`, 0 DB row (psql this session), 0 alembic seed; commit 62a1a05 ship-dark, **no A/B ever** (§5 Q11) |
| **L2** `attach_context_buffer` context-binding | ↔️ | `context_buffer.py:110` — only docling/simple parsers + B2 branch fed `[]`; never on the live kreuzberg path |
| **L3** dict profile `analyze_document` | ✅ | `chunking.py:508`; feeds live selector |
| **L3** DocumentProfile 10-feature entity | 🕰/↔️ telemetry-only | `document_service.py:1994-1998` "NOT yet wired into select_strategy"; flag OFF, no DB row |
| **L4** deterministic weighted selector + 2 fast-paths (**no per-doc LLM call**) | ✅ (praise) | `select_strategy` `chunking.py:663-792`; DB strategy mix proves all 5 outputs exercised |
| **L4** Ekimetrics 5-metric selector | ↔️ dead | `chunking.py:667,691-706`; grep this session: **no caller passes `ekimetrics_enabled`**, no config-read for the key |
| **L5** rule cross-check, flag ON in prod | ✅ | `chunking.py:827`, run `:2556-2576`; psql `adapchunk_layer5_cross_check_enabled=true` |
| **L5** double cross-check in B2 branch | ↔️ | `document_service.py:1954-1960` + `chunking.py:2556` — idempotence unproven (repro T4) |
| **L6** `smart_chunk_atomic` (`list[Block]→list[Chunk]`) | ↔️ dead + dishonest flag | `chunking.py:2737`; grep: **0 prod callers**; B2 gate feeds `parsed_blocks=[]` `document_service.py:1920` while `DEFAULT_ADAPCHUNK_BLOCK_PIPELINE_ENABLED=True` `_12:176` claims "deps landed" |
| `table_csv` row-as-chunk + multi-region + header/footer | ✅ | `chunking.py:1268,1368,1456`; fast-path `:724`; DB flag true; 83 live chunks; its 48 TABLE labels are the *correctly* classified ones |
| `recursive` (table-aware) | ✅ | `:1580`; 280 live chunks (50% of corpus) |
| `hdt` + VN legal hierarchy (Roman↔Arabic norm, heading promote, fast-path) | ✅ | `:311-418,467,732`; 90 live chunks carry `[Chương > Mục > Điều]` paths (DB samples) |
| `semantic` (lexical SequenceMatcher+Jaccard `:1873-1884`) | 🕰 | 93 live chunks; SOTA verdict §3/§5-Q14: semantic boundary detection unjustified vs recursive on real docs |
| `_chunk_semantic_embed` (async, embedding-based) | 🕰 dead → DELETE | `:1988`; grep: 0 callers; sync `smart_chunk` can't dispatch; flag False; §5-Q14 |
| `proposition` rule-based (**no LLM rephrase → no fabrication-at-ingest**) | ✅ (praise) | `:2132-2189` regex clause split only — downgrades synthesis-Q13 risk |
| **`proposition` split deletes conditional connectors** | 🐛 -D | `:2155-2159` — `re.split` non-capturing group consumes `nếu/khi/vì/và…` → condition severed from claim (repro T3); inherited by `hybrid` `:2226` |
| `hybrid` (HDT macro + PROP micro, re-prepends structural path) | ✅ | `:2197`, path re-prefix `:2228-2230`; 14 live chunks |
| `whole_document` / `parent_child` / `parser_preserve` + orphan-merge row exemption | ✅ | `document_service.py:1840-1865, :2078-2096, :2110-2123` |
| **Narrate-then-embed dual-content (Tầng 6)** | ✅ LIVE (§1) | `document_service.py:2891-2902`; DB 560/560 meta, 211 narrated≠raw; degrade→raw at 3 levels (`narrate_dispatch.py:119-132`) |
| **Narrate block-type classified from flattened text → ~163/211 prose mis-narrated** | 🐛 -B | root cause `chunking.py:253-256` (replayed live, §1); `narrate_dispatch.py:94` |
| **Narrate silently overrides `raw_only` embed-text strategy** | ↔️/🐛 -C | `document_service.py:2838-2843` picks raw_only for structural docs ("CR prefix must not dilute exact-anchor lookup") → `:2902` replaces those bytes with LLM narration anyway |
| Anthropic CR (U5) + embed-text strategy incl. structural auto `raw_only` | ✅ | `document_service.py:2829-2846` |
| §8 eval-by-question-type × strategy feedback loop | 🕰 partial | grading SOP `plans/260609-prod-test-framework/FRAMEWORK.md:22-25`, 13 GRADED_* reports; no automated harness keyed by `chunking_strategy_selected` |

**Count: ✅ 11 · 🕰 5 (atomic-protect dark · profile entity · lexical semantic · semantic_embed · eval-loop) · ↔️ 5 (context-buffer path · Ekimetrics · double cross-check · B2 dishonest flag · narrate×raw_only) · 🐛 4 (A flatten-boundary · B narrate misclassify · C raw_only override · D proposition connector).**

---

## (3) 🕰 items — chuẩn 2026 + independent sources

### 🕰-1 SEMANTIC chunking (per-sentence cosine) vs recursive+atomic — **NOT worth it; keep dead / delete**
- **[Is Semantic Chunking Worth the Computational Cost? — arXiv 2410.13070 (NAACL Findings)](https://arxiv.org/abs/2410.13070)**: systematic eval across document-retrieval, evidence-retrieval, answer generation — gains inconsistent and context-dependent; **on non-synthetic real-world documents fixed-size/recursive often performed better**.
- **[Chunking Methods on RAG — Effectiveness vs Computational Cost — arXiv 2606.00881](https://arxiv.org/abs/2606.00881)**: deep semantic processing drastically inflates compute; only acceptable for static corpora with substantial measured retrieval gains.
- Practitioner benchmarks concur: [Firecrawl 2026 survey](https://www.firecrawl.dev/blog/best-chunking-strategies-rag) (recursive/structure-aware default, semantic situational); Vectara-style end-to-end runs show semantic over-fragmenting.
**Applied:** Ragbot's dead `_chunk_semantic_embed` is *defensible-by-accident* — paying zembed-1 per sentence-pair is exactly what 2410.13070 condemns. The live lexical `semantic` (93 chunks) costs nothing but inherits the quality non-result → demote/fold into recursive pending Phase-5 ablation. Structure-aware (HDT/table_csv) is where this codebase already wins — that IS the 2026 direction.

### 🕰-2 Proposition chunking — **still recommended ONLY in rule-based or verified form**
- Upside real: **[Dense X Retrieval — arXiv 2312.06648](https://weaviate.io/papers/paper10)**: propositions beat sentence/passage retrieval (+17-25% Recall@5, EntityQuestions).
- Risk documented: **[DnDScore — arXiv 2412.13175](https://arxiv.org/pdf/2412.13175)**: decomposition + decontextualization methods "are at risk of injecting hallucinated information not in the original claim" → verification required; also overly granular for narrative text ([Revisiting Chunking in the RAG Pipeline](https://aiexpjourney.substack.com/p/revisiting-chunking-in-the-rag-pipeline)).
**Applied:** Ragbot's live proposition is regex-only → no fabrication surface (✅) but has the connector-deletion distortion (🐛-D). Any future LLM-proposition upgrade MUST ship the §5-Q13 entailment design.

### 🕰-3 Per-document strategy granularity (vs per-section) + eval loop
One strategy per document (`select_strategy` whole-doc profile) vs 2026 hierarchical/per-section selection ([HiChunk arXiv 2509.11552](https://arxiv.org/pdf/2509.11552): +18-25% retrieval over flat). Partially mitigated by live TABLE isolation `:2613-2627` + `hybrid`; full fix routes through the Block feed (🐛-A). Eval loop: continuous per-strategy ablation harness is 2026 standard ([futureagi 2026](https://futureagi.com/blog/evaluating-rag-chunking-strategies-2026/)) — Ragbot has the grading SOP + `chunking_strategy_selected` audit event but no correlation harness.

---

## (4) 🐛 repro tests (proposed — code blocks only, NOT written to tests/)

### T1 / 🐛-B — `_is_table_line` misclassifies VN legal clause prose → narrate fires on prose

```python
# tests/unit/test_block_classify_vn_legal_prose.py
"""Repro: CSV-comma rule (chunking.py:253-256) flags VN legal điểm-lines
(comma-rich, ending ';') as table rows. Replayed live 2026-06-10 on a stored
chunk; DB impact: ~163/211 narrated 'TABLE' chunks are prose (9 contain a pipe)."""
from ragbot.application.services.narrate_dispatch import classify_chunk_block_type
from ragbot.shared.chunking import _is_table_line

VN_LEGAL_CLAUSE = (
    "a) Chia tách thành các vùng mạng khác nhau theo đối tượng sử dụng, "
    "mục đích sử dụng và hệ thống thông tin, tối thiểu: (i) phân vùng;"
)

def test_legal_clause_line_is_not_a_table_row():
    assert _is_table_line(VN_LEGAL_CLAUSE) is False  # FAILS today

def test_legal_article_chunk_classifies_text_not_table():
    chunk = (
        "[Chương 2 > Mục 4 > Điều 23. Quản lý an toàn]\n"
        "Tổ chức thực hiện quản lý an toàn, bảo mật hệ thống mạng như sau:\n"
        + VN_LEGAL_CLAUSE + "\n"
        "b) Có thiết bị có chức năng tường lửa để kiểm soát các kết nối, "
        "truy cập vào ra các vùng mạng quan trọng;\n"
    )
    assert classify_chunk_block_type(chunk) == "TEXT"  # FAILS today ("TABLE")

def test_real_pipe_table_still_classifies_table():  # regression guard for the fix
    table = "| STT | Dịch vụ | Giá |\n|---|---|---|\n| 1 | Gội đầu | 60,000đ |"
    assert classify_chunk_block_type(table) == "TABLE"
```
Fix direction (Phase 3): require ≥2 consecutive comma-CSV lines with consistent field counts, or exclude lines matching VN điểm/khoản pattern `^[a-zđ]\)\s` / ending `;`. Long-term fix = parser `Block.type` truth (🐛-A).

### T2 / 🐛-C — narrate silently overrides the `raw_only` embed-text strategy

```python
# tests/unit/test_narrate_vs_embed_text_strategy.py
"""Repro: document_service.py:2838-2843 picks raw_only for structural docs
(exact-anchor preservation) but :2902 replaces those bytes with an LLM summary.
Contract: the literal anchor must survive in the embed target."""
import pytest
from ragbot.application.services.narrate_dispatch import narrate_chunks_for_embed
from ragbot.application.services.narrate_service import NarrateService

class _FakeNarrator:  # strategy double: always rewrites TABLE
    async def narrate(self, content, block_type):
        return "tóm tắt nội dung điều khoản"

@pytest.mark.asyncio
async def test_structural_chunk_embed_text_keeps_exact_anchor():
    svc = NarrateService(strategy=_FakeNarrator(), enabled=True)
    raw = "Điều 23. Quản lý an toàn:\na) yêu cầu một, yêu cầu hai, yêu cầu ba;"
    rewritten, _meta = await narrate_chunks_for_embed([raw], narrate_service=svc)
    assert "Điều 23" in rewritten[0]  # FAILS when T1 misroutes prose→TABLE
```

### T3 / 🐛-D — proposition splitter deletes conditional connectors

```python
# tests/unit/test_proposition_connector_retention.py
"""Repro: chunking.py:2155-2159 re.split consumes 'nếu/khi/vì…' —
'A, nếu B' → propositions 'A' and 'B' with the condition severed
(L2 conditional-factoid hazard per prod-test framework taxonomy)."""
from ragbot.shared.chunking import _chunk_proposition

def test_conditional_clause_keeps_its_condition():
    text = ("Khách hàng được hoàn tiền toàn phần, nếu hủy lịch trước "
            "hai mươi bốn giờ so với giờ hẹn đã xác nhận.")
    chunks = _chunk_proposition(text, chunk_size=1024, chunk_overlap=0)
    assert "nếu" in " ".join(chunks), (
        "connector must survive — otherwise the refund fact is "
        "decontextualized into an unconditional claim"
    )  # FAILS today: connector sits inside the split pattern and is dropped
```

### T4 / ↔️ — honesty pins: B2 empty feed + cross-check idempotence

```python
# tests/unit/test_adapchunk_wiring_honesty.py
"""Pin built-but-not-wired state so silent change (or the fix) is visible."""
import inspect
import ragbot.application.services.document_service as ds
from ragbot.shared.chunking import apply_cross_check

def test_b2_block_pipeline_still_feeds_empty_blocks():
    # The day this literal disappears, the block feed exists → update L6 verdict.
    assert "parsed_blocks: list = []" in inspect.getsource(ds)

def test_cross_check_is_idempotent_for_all_rules():
    # Applied twice on the B2 path (document_service.py:1954 + chunking.py:2563).
    profile = {"total_headings": 2, "heading_counts": {"h2": 0}, "table_count": 0,
               "avg_text_length": 80.0, "mixed_content_score": 0.0,
               "has_toc": False, "total_words": 900}
    s1, c1, _ = apply_cross_check("hdt", 0.9, profile)
    s2, c2, _ = apply_cross_check(s1, c1, profile)
    assert (s2, c2) == (s1, c1)
```

### 🐛-A — Block stream flattened (static repro, root of the dead engines)
`grep -n 'join(b.content' document_worker.py` → `:295`; `grep -rn 'smart_chunk_atomic(' src/` → definition only (re-verified this session); B2 gate `document_service.py:1920 parsed_blocks: list = []`. → `smart_chunk_atomic`, FORMULA/IMAGE/CODE protect, and `attach_context_buffer` have no input on the production path despite commits 67b9883/e7d4f41/62a1a05.

---

## (5) Answers to open Qs (P1-SYNTHESIS §4 Q9–Q15 + wire-or-delete verdicts)

**Q9 — parser→Block adapter owner / canonical schema:** highest-leverage AdapChunk fix; charter-blessed local rewrite, Phase 3 + ADR. Canonical = kreuzberg `Block` (`is_atomic`, `type`, `context_before/after`) — richest schema; excel/sheets `parser_preserve` row-dicts map to `Block(type="TABLE", is_atomic=True)` so the side-channel `document_service.py:2078-2089` can retire. Steps: stop flatten at `document_worker.py:295`, add `blocks=` param to `ingest()`, feed B2 → `smart_chunk_atomic`.

**Q10 — `_narrate_service`:** **LIVE** (§1, definitive — trace + DB). Contradiction settled in P1-E's favour.

**Q11 — why atomic_protect default-OFF; was there an A/B?** **NO A/B — definitive from the full commit message of 62a1a05 (read this session).** It documents only: flag default OFF, 38 unit assertions on synthetic fixtures incl. "flag-off regression (legacy text/table emission unchanged)", telemetry contract. Zero retrieval/answer/corpus measurement. Deliberate ship-dark inside the ~24-stream mom-260514 merge window (flag-off = zero cross-stream risk). Never flipped: no alembic seed (grep=0), no system_config row (psql today), const still False. **Before any flip**: query the M25 block-type histogram — current 560-chunk corpus shows 0 FORMULA/IMAGE/CODE labels → flipping buys nothing on this DB; decide per-deployment from data, then A/B.

**Q12 — two parallel atomic impls, which survives:** **the Block-native impl (`smart_chunk_atomic`)**. The narrate misclassification (🐛-B) is *empirical production proof* that regex re-detection on flattened text is structurally unsound — the parser already computed the truth and `:295` throws it away. Consolidation: (1) wire Block feed (Q9); (2) keep `_split_into_blocks_with_atomic` ONLY as fallback classifier for block-less sources (direct-text API ingest), with the T1 `_is_table_line` fix applied regardless because `narrate_dispatch.py:94` uses it **today**; (3) delete `_smart_chunk_with_atomic_protect`'s duplicated strategy elif-ladder (already drifting vs `smart_chunk`: `:2488-2499` vs `:2628-2637`).

**Q13 — proposition verify-vs-source (HALLU-at-ingest):** **No fabrication risk in production** — live `_chunk_proposition` is regex-only, never rephrases, never calls an LLM (corrects the synthesis framing). Its real defect is *distortion*: connector deletion (🐛-D). Design, two tiers:
- *Now (rule-based)*: retain connector tokens (capturing group or re-attach), and adopt narrate's proven dual-persistence — store `source_sentence` in `metadata_json` per proposition chunk. Metadata-only cost.
- *If LLM-proposition ever wired*: mandatory entailment gate per DnDScore — `entail(source_passage, proposition)` via the existing grounding-judge model; fail → fall back to source sentence verbatim (same degrade-contract as `LLMNarrateGenerator` raw-fallback); persist `{source_sentence, proposition, entailment_score}`; batch with bounded gather (ingest-time, latency budget generous).

**Q14 — semantic chunking cost vs recursive+atomic:** **Not worth it — keep recursive+atomic default** (sources §3.1: arXiv 2410.13070, arXiv 2606.00881, Firecrawl 2026). `_chunk_semantic_embed` dead is defensible-not-defect.

**Q15 — large-table: atomic-absolute vs table_csv row-as-chunk. Proposed rule (one sentence):** **a table is atomic at the ROW, never at the character; the header travels with every emitted fragment; only FORMULA and IMAGE are atomic-absolute regardless of size.** Concretely:
1. Whole-doc CSV (fast-path `:724`) → `table_csv` row-as-chunk + header context (current ✅, keep; validated by spa-07 fix `plans/260604-deepaudit-rootcause-fix/plan.md:25`).
2. Embedded table in mixed doc, ≤ chunk_size → emit whole (atomic) — current `_emit_atomic_block`.
3. Embedded table > chunk_size → split on **row boundary**, re-emit header per fragment (62a1a05 already states "TABLE/CODE may split on row boundary when oversized"; `_chunk_table_csv_with_context` already owns header re-emission). **Consolidate both paths into one shared `_emit_table_rows(header, rows, chunk_size)` helper** so they cannot drift.
4. FORMULA/IMAGE → never split (keep `atomic_block_oversized_kept_whole` warning). Decision input = parser `Block.is_atomic` once Q9 lands; until then `table_csv` fast-path is the right live default.

**Ekimetrics selector — wire-or-delete:** **WIRE-FOR-ABLATION with a kill date.** Unlike semantic_embed it is rule-based and free at runtime (no LLM/embed), carries a peer-reviewed +5-8pp Answer-Correctness claim (LREC 2026, cited `chunking.py:812-816`), and is **the only consumer that gives the dead L3 DocumentProfile entity a purpose — one wire resolves two dead layers**. Wire = real `ekimetrics_5metric_selector_enabled` config-read at the `select_strategy` call site (+ pass `text=`), A/B on the 13 GRADED_* corpora in Phase 5. No measured Coverage/Correctness lift → delete `intrinsic_metrics.py` + `_19_sprint3_ekimetrics_selector_.py` (~600 lines) same phase. Do not let it enter a third month dark.

**`_chunk_semantic_embed` — wire-or-delete:** **DELETE** (+ SentenceSimilarityPort scaffolding). Triple-condemned: 0 callers since 2811be9; needs an async U4 dispatch rewrite just to be callable; SOTA says the embed cost it exists to spend is unjustified (§3.1). Pure drift surface.

---

## (6) ĐÃ CHUẨN — đừng đụng (honest praise, with evidence)

1. **Narrate-then-embed safety architecture is exemplary** — embed the narration, answer from raw `content`; raw preserved in two places (`content` + `metadata_json.raw_chunk`); identity-passthrough degrade at 3 levels (service None / flag off / LLM fail → raw, `narrate_dispatch.py:119-132`). Even the 🐛-B misclassification **cannot** put a fabricated word into a user-visible answer — only into a retrieval vector. Quality-Gate-#10 mindset executed correctly at ingest. DB: 560/560 forensic metadata. **Do not "simplify" by overwriting `content`.**
2. **Deterministic rule selector (no per-doc LLM judge)** — `select_strategy` `:663-792` weighted-score + 2 fast-paths: cheap, reproducible, debuggable; vindicated by Ragbot's own e86c0f6 legal-LLM-branch revert. **Keep rule-based.**
3. **table_csv is production-grade** — multi-region detect `:1368`, header/footer chunks (flag ON in DB), row-atomicity protected from orphan-merge `document_service.py:2110-2123`; its 48 TABLE-labelled rows are the only chunks the narrate classifier labels *correctly* — converging evidence both features work on real tabular data. **Do not re-enable orphan-merge for table_csv.**
4. **Proposition chunker rule-based** — zero ingest-fabrication surface `:2132`. Resist "upgrade to LLM propositions" without the Q13 entailment gate.
5. **VN legal hierarchy + L5 cross-check** — live, flag-ON, DB-verified (90 HDT chunks with citation paths), domain-neutral post-e86c0f6. The revert was the right call.
6. **Ship-dark discipline is honest in the code itself** — `document_service.py:1915-1919` openly documents the empty block feed instead of faking a source. The dishonesty is one flag comment (`_12:172-176` "deps landed") — one line to fix.

---

## (7) Phase-3 decision queue (priority-ordered)

| # | Decision | Tier | Effort |
|---|---|---|---|
| 1 | Fix `_is_table_line` comma rule (T1) — stops mis-narration of legal prose TODAY, independent of rewiring | T1 | S |
| 2 | Resolve narrate × raw_only ordering (narrate only on true TABLE/FORMULA/IMAGE; assert anchor survival) | T1 | S |
| 3 | Proposition connector retention (T3) + `source_sentence` metadata | T1 | S |
| 4 | Delete `_chunk_semantic_embed` + port scaffolding | T3 | S |
| 5 | Wire Ekimetrics behind real config key → Phase-5 A/B → kill-date | T1 | M |
| 6 | Block feed end-to-end (un-flatten `:295`) → `smart_chunk_atomic` survivor path | T1 | L |
| 7 | Consolidate table emission into `_emit_table_rows` (Q15 rule) | T2 | M |
| 8 | atomic_protect: per-deployment M25 histogram query before any flip | T2 | S |
| 9 | Honest-up B2 flag comment + de-dupe double cross-check (T4) | T3 | S |

### Charter axes scorecard (chunking slice)
- **ĐÚNG:** strong — answers read raw `content`; narration embed-only; proposition rule-based → ingest-HALLU surface ≈ 0.
- **ĐỦ:** medium — per-document granularity + Block-feed gap cap the ceiling (🕰-3, 🐛-A); connector-deletion threatens L2 conditional-factoid coverage (🐛-D).
- **RẺ:** one measured leak — ~163/211 prose chunks pay an unneeded narrate LLM hop per ingest (🐛-B); CR + narrate + decomposer stack on ingest cost is unmeasured (Q-CR-cost-interaction).

*P2-B Phase 2 complete. No src/alembic/tests modified. DB read-only SELECT only. Single file written: this one.*
