# Deep-dive: `src/ragbot/shared/` (rest-of-shared scope) — code-shared-rest

**Date**: 2026-07-02 · **Reader**: deep code reader (every line read, no sampling)
**Scope**: ALL of `src/ragbot/shared/` EXCEPT `document_stats.py`, `tabular_markdown.py`, `number_format.py`, `i18n.py`, `constants/` (covered by the shared-data reader). 46 modules + 8 chunking submodules, ~13,800 lines total read.
**Method**: full file reads + verification greps for wiring/orphan claims + one live regex repro (`python3` on the from-to range regex). All claims carry `file:line`. FACT vs HYPOTHESIS labelled per rule #0.

---

## Part 1 — File-by-file: what it does + how it connects to the pipeline

### Kernel / contracts

| File | Purpose | Pipeline connection |
|---|---|---|
| `__init__.py` (99 ln) | Re-exports Clock/errors/Result/types. Docstring line 1: “Shared kernel — lowest layer, zero dependencies beyond stdlib.” | Imported everywhere. **This claim is violated by 6+ modules — see Finding L1.** |
| `errors.py` (325) | 3-branch exception hierarchy (Domain/Application/Infrastructure), stable `code` + `http_status`, `to_envelope()`. Narrow classes for broad-except sweep (`AuditEmitError`, `RetrievalError`, `EmbeddingError`, `IngestError`, errors.py:240-265). | HTTP error envelope + worker error taxonomy. Clean; no issues found. `CitationHallucinated` deliberately maps to 500 (errors.py:72). |
| `types.py` (157) | NewType IDs, Literal enums (`JobStatus`, `DocumentState`, `ChunkingStrategyName` dual vocabulary, `BlockType`, `QueryIntent`, `LLMIntent`). | Static typing only. **Version-ref comments** `# v0.3.0 —` (types.py:21) and `# v0.4.0 —` (types.py:25) violate the no-version-ref comment rule (WHY-only). Naming drift: `BotId = NewType(UUID)` (types.py:16) while the platform convention says `bot_id` = external VARCHAR slug / `record_bot_id` = internal UUID — the type name matches neither convention cleanly (FACT: name only, no runtime effect). |
| `result.py` (85) | `Ok`/`Err` Result + `ApiEnvelope`/`ErrorPayload` pydantic response shape. | HTTP layer. Clean. |
| `clock.py` (61) | `Clock` Protocol, `SystemClock`, `FrozenClock`, module singleton `get_clock`/`set_clock`. | Testability shim. Clean. |
| `rbac.py` (56) | `ROLE_LEVELS` numeric map (7-tier + aliases), `get_role_level` (unknown→0 guest), `check_min_level`/`require_min_level` reading `request.state.role`. | Every admin route. Fail-safe default (unknown role = guest = 0) is correct-closed. Clean. |
| `perf.py` (45) | `timer(label, log_threshold_ms)` async CM emitting `perf_timer` structlog event; exception still emits timing (perf.py:39-45). | CLAUDE.md “measure don’t guess” helper. Clean. |
| `llm_usage.py` (110) | `extract_usage_from_response` (usage tuple incl. cached tokens) + `estimate_tokens_fallback` (tiktoken cl100k estimate that only fills ZERO counts, never overwrites provider counts — llm_usage.py:61-62). | Cost logging in router + structured-output helper. **Domain-neutral violation**: docstring names a tenant-adjacent brand — “the innocom gateway” (llm_usage.py:54). |
| `result/pagination.py` (22) | `page_limit(requested, default=20, max_limit=50)` keyset-pagination page size. | Routes. Inline defaults 20/50 (pagination.py:10) — technically zero-hardcode drift, whitelisted-adjacent. |

### Config resolve chain (special focus)

**`bot_limits.py` (638)** — per-bot limit SSoT. `PLAN_LIMIT_SCHEMA` (52-370) documents ~45 keys; `resolve_bot_limit` chain = threshold_overrides > dedicated column > plan_limits > system_default (caller) > schema default (bot_limits.py:390-465) with a schema-driven numeric range guard that REJECTS out-of-range bot values (438-457). `validate_plan_limits` (524-598) write-time sanitiser. `get_effective_config` (601-628) merges all keys. Used by pipeline-config assembly in chat_worker + test_chat.

Findings:
- **B1 (multi-bot, MEDIUM, FACT)** — `validate_plan_limits` `list_str` branch lowercases every item: `token = item.strip().lower()` (bot_limits.py:589). `understand_greeting_patterns` is a `list_str` key whose items are **regex patterns** consumed at `query_graph.py:537`. Lowercasing a regex inverts the escape classes `\S→\s`, `\W→\w`, `\D→\d`, `\B→\b`. Failure scenario: owner writes pattern `^ok\S*` (match “ok…” token) → stored as `^ok\s*` → matches “ok ” + anything → greeting skip-gate fires on non-greetings. Also case-only-distinct patterns get deduped. Regex validity is also never checked at write time (invalid regex stored silently).
- **B2 (zero-hardcode, LOW-MED, FACT)** — inline schema defaults not lifted to constants: `"retrieval_top_k" default 20` (53), `cache_ttl_s 3600` (95), `grounding_check_threshold 0.30` (136), `guard_output_min_score 0.15` (137), `generate_context_chars_cap 2900` (138), `rerank_cliff_gap_ratio 0.35` (159), `pdf_max_bytes max 200*1024*1024` (190). Most other keys do import `DEFAULT_*`; these are stragglers.
- **B3 (doc-rot, LOW, FACT)** — `resolve_semantic_cache_threshold` docstring still claims “that helper applies max(bot, system)” (bot_limits.py:483-489) while `resolve_bot_limit` removed the max() heuristic (399-405). Stale rationale for the dedicated resolver.
- **B4 (LOW, FACT)** — column keys (`max_documents` etc.) resolved via `resolve_bot_limit` get **no schema default**: `PLAN_LIMIT_SCHEMA` lacks them, so if the bot column is NULL and system_config has no row, `get_effective_config` returns `None`, not `COLUMN_DEFAULTS["max_documents"]=5` (374-379 declared but never consulted in the resolve path). HYPOTHESIS on impact: ORM defaults probably mask this; the helper alone is inconsistent with its own `COLUMN_DEFAULTS` comment (“Single source of truth”).

**`bootstrap_config.py` (363)** — sync psycopg2 reader for `system_config` with 30 s TTL cache + `_ALLOWED_KEYS` whitelist (46-258, ~130 keys; the file’s own comments document 3 historical whitelist-drift bugs at 110-117, 131-134, 171-178). `invalidate_cache` for admin writes.

Findings:
- **C1 (T2-perf, HIGH, FACT structural / HYPOTHESIS magnitude)** — `get_boot_config` opens a **blocking** `psycopg2.connect(dsn, connect_timeout=3)` (bootstrap_config.py:330) on every cache miss/TTL expiry. Verified callers on the **async hot path**: `orchestration/nodes/understand.py`, `query_complexity.py`, `query_decomposer.py`, plus the whole chunking package during async ingest (`ingest_stages.py:770 → smart_chunk → get_boot_config`) — none wrapped in an executor. Failure scenario: PG slow/handshake stall → the event loop of the worker freezes up to `DEFAULT_DB_BOOTSTRAP_CONNECT_TIMEOUT_S` (3 s) for EVERY in-flight request, once per expired key per 30 s. Per-query comment even says “the classifier runs on every query (hot path)” (62-66).
- **C2 (MED, FACT)** — negative cache stores the **caller’s default** under the key (bootstrap_config.py:347-348): `_cache[key] = (now, default)`. Two call sites reading the same missing key with different defaults → the second caller silently receives the first caller’s default for 30 s. No key in the whitelist is currently proven to have two divergent defaults (HYPOTHESIS for live impact), but the mechanism is a foot-gun the file’s own history of whitelist bugs makes plausible.
- **C3 (LOW, FACT)** — warnings use stdlib `logger.warning(..., extra={...})` (306, 332, 341). Project’s own recorded lesson (`feedback_issue4`) says `extra=` fields are swallowed under the structlog ProcessorFormatter → operator sees event names with empty bodies (can’t tell WHICH key was rejected).
- **C4 (design, INFO)** — the whitelist itself is a proven drift generator (3 documented incidents where operator UPDATEs silently no-op’d). The pin test `test_pipeline_cfg_keys_parity` mitigates; still a single-list-two-sources architecture.

**`autonomy_resolver.py` (88)** — clamp + band mapping for answer-autonomy percent. Finding **A1 (multi-bot, MEDIUM, FACT)**: `resolve_effective_autonomy_percent = max(clamp(bot), clamp(system))` (autonomy_resolver.py:56-59) — a bot can never opt **down** (stricter) below the platform default. If platform default is ever raised to 30 (“constrained”), a compliance bot demanding 0 (“docs_only”) cannot get it. Directly inconsistent with `bot_limits` which removed exactly this max() pattern because “bot owners couldn’t override numeric defaults DOWNWARD” (bot_limits.py:399-405).

### Query parsing helpers (special focus)

**`query_range_parser.py` (575)** — VN price-range / list / price-of-entity / code / summary detectors that route queries to the stats-index structured lookup instead of vector retrieve. Wired at `orchestration/nodes/retrieve.py:215-250` and `query_graph.py:327,1948,2162` (verified).

Findings:
- **Q1 (T1, HIGH, CONFIRMED by repro)** — the from-to branch has **no bare-number floor guard**. `_RANGE_FROM_TO_RE` (157-162) happily matches non-price ranges; live repro:
  - `"tre em tu 6 den 12 tuoi co dung duoc khong"` → groups `('6 ', '12 ')` → `RangeFilter(price_min=6, price_max=12, confidence=0.9)`
  - `"giam gia cho khach 25 - 30 tuoi"` → `(25, 30)`; `"combo 3 - 5 buoi gia bao nhieu"` → `(3, 5)`.
  The `_MIN_BARE_PRICE_VND = 1000` doc-number guard exists ONLY in `_find_money_after_token` (542) used by the dưới/trên branches — the from-to branch (213-227) and fuzzy branch (229-242) skip it. Failure: age/session/quantity range questions get hijacked to the stats price path at confidence 0.9 (> RANGE_QUERY_MIN_CONFIDENCE), where a 6–12 VND price filter returns 0 rows. Downstream behaviour (refuse vs fall-through) is HYPOTHESIS — needs a runtime trace — but the mis-parse itself is CONFIRMED.
- **Q2 (multi-locale/multi-currency, HIGH, FACT)** — money regexes are VN-only: unit alternation `ty|trieu|tr|nghin|ngan|k|dong` hardcoded in `_RANGE_FROM_TO_RE`/`_FUZZY_RE`/`_ANY_MONEY_RE` (158, 167, 173) and the floor `_MIN_BARE_PRICE_VND = 1000` (185) assumes VND scale. The below/above/superlative token lists were properly moved to locale `RoutingSignals`, but the **amount grammar** wasn’t: an EN bot’s “under $50” parses `money=50 < 1000` → rejected as doc-number → range routing silently dead for any low-denomination currency; “$”/“€”/“m” units unsupported. Half-migrated locale-scoping (violates the metadata-optional-hint “currency/scale-neutral” mandate).
- **Q3 (i18n, MED, FACT)** — `matches_summary_pattern` uses `SUMMARY_QUERY_PATTERNS_VI` only (27, 561); not on `RoutingSignals`, so non-VI summary asks never hit the summary route — inconsistent with the rest of the module’s locale threading.
- **Q4 (MED, FACT)** — below/above token detection is **substring**, not word-boundary: `if token in folded` (246, 259) and `_find_money_after_token` uses `folded.find(token)` first-occurrence (521). Failure scenario: any query whose fold contains a signal token inside a larger word (e.g. EN word “trend” contains vi-fold “tren”) plus a number → bogus above-range. The file already documents one such collision (“Thông tư” fold → “tu”, 182-184) which was patched with the money floor — the substring root cause remains.
- **Q5 (LOW, FACT)** — `_ascii_fold` (60-62) folds precomposed NFC chars only; **no `unicodedata.normalize` call**. NFD input (macOS pastes) has combining marks → fold produces different-length text and misses tokens → all detectors silently no-op → vector fallback. `_extract_original_span`’s same-length assumption (509-516) also only holds for NFC.
- **Q6 (dead config knob, MED, FACT)** — comment says the code pattern is “operator-overridable via system_config 'code_query_pattern'” (446), but `_CODE_QUERY_RE` is compiled once from `DEFAULT_CODE_QUERY_PATTERN` (448) and grep shows **no call site anywhere reads `code_query_pattern` from system_config** — the override is documentation-only. Built-but-not-wired.
- **Q7 (zero-hardcode, LOW, FACT)** — inline confidences 0.9 / 0.75 / 0.85 / 0.8 (226, 241, 254, 267, 359, 415), `_FUZZY_MARGIN = 0.10` (177), `_MIN_BARE_PRICE_VND = 1000` (185) — thresholds living outside `shared/constants.py`.
- Note (GOOD): `parse_code_query` letter-required guard (501) and quoted-SKU handling (475-488) are careful, domain-neutral. `is_price_ask_query`’s `getattr(sig, "price_word_signals", ())` (441) is defensive but the field exists (i18n.py:131) — harmless.

### Chunking package (`shared/chunking/`, 3,947 ln)

**`__init__.py` (931)** — `smart_chunk` (auto strategy: analyze → select → L5 cross-check → dispatch), `generate_parent_child_chunks`, `merge_orphan_chunks`, `_prefix_section_headings`, atomic-protect wrapper, AdapChunk Layer-6 `smart_chunk_atomic` (Block→Chunk). Wired from ingest at `document_service/ingest_stages.py:770`.

- **K1 (T1, HIGH, FACT)** — **HDT fast-path defeats the strategy selector and table protection.** `select_strategy` T2 fast path: `(total_headings + vn_markers) >= 3 → ("hdt", 1.0)` (analyze.py:462-463, `DEFAULT_HIERARCHICAL_PROMOTE_MIN_MATCHES=3` at constants/_12:107). Any markdown doc with ≥3 headings — i.e. most real DOCX/PDF→markdown output — is forced into HDT. Consequences: (a) the whole weighted scorer + semantic/hybrid/proposition branches (analyze.py:484-541) are near-dead code for structured docs; (b) `smart_chunk`’s table-isolation branch explicitly **excludes hdt** (`strategy not in ("recursive","hdt")`, chunking/__init__.py:521), and `_chunk_hdt` splits oversized sections with a plain `RecursiveCharacterTextSplitter` (strategies.py:339-353) with **no table awareness** — so a long table under a heading is cut mid-table with the header stranded in the first shard, unless the atomic-protect flag is on, and `DEFAULT_FORMULA_IMAGE_ATOMIC_PROTECT_ENABLED = False` (constants/_00:105). Failure scenario: markdown doc “# Giới thiệu / ## Bảng giá / [80-row table] / ## Liên hệ” → HDT → section > 2×chunk_size → table rows split header-less → row-level retrieval loses column labels → wrong price attribution.
- **K2 (bug, MED, FACT)** — `_prefix_section_headings` wrong-section attribution: when a chunk’s 60-char fingerprint can’t be located (`pos < 0`), the loop `if pos < 0 or hpos <= pos: active = htext` (chunking/__init__.py:399-405) never breaks → `active` = **the LAST heading in the document**, which then gets prepended (405-406). Failure: any synthetic/reordered chunk (table row-group with re-prepended header, orphan-merged chunk) gets the final section’s heading glued on → embedding + stats extractor bind rows to the wrong section.
- **K3 (orphan, MED, FACT)** — `smart_chunk_atomic` (653-810) + `_chunk_text_blocks_to_chunks` + `_block_to_atomic_chunk` are called by **tests only**; a pin test even asserts non-wiring (`tests/unit/test_block_feed_s1_plumbing.py:51-52`). The Wave-B1 scaffold stamps **random sentinel UUIDs** for `record_tenant_id`/`record_bot_id`/`document_id` when identity isn’t passed (chunking/__init__.py:719-721) — if S2/S3 wiring ever lands without plumbing identity, persisted chunks carry random tenant ids (multi-tenant latent risk, HYPOTHESIS until wired).
- **K4 (LOW, FACT)** — `generate_parent_child_chunks` child-splitter separators `["\n\n","\n",". "," ",""]` (207) have no table/CJK awareness but the table-parent branch (179-200) mitigates tables. `merge_orphan_chunks` clean.

**`analyze.py` (732)** — `analyze_document` profile, `_is_csv_format`, `_is_table_line`, `select_strategy` weighted scorer, Layer-5 `apply_cross_check`, `analyze_document_blocks`.

- **K5 (multi-format, HIGH, FACT)** — **CSV-ness is comma-only.** `_is_csv_format` counts `,` exclusively (analyze.py:73, 92-99); `_is_csv_shape_line` in the chunker likewise (csv_chunker.py:123-126). A semicolon-delimited CSV (the default Excel/Sheets export for vi-VN/de-DE and most European locales, where `,` is the decimal separator) or a TSV never triggers `table_csv`/`table_dual_index` → rows go through recursive/semantic and tuples are cut. `_is_table_line` at least handles TSV + pipes (183), but the strategy fast-path key `is_csv_format` does not. Same bias in `mime_sniff.sniff_real_mime` CSV heuristic `first_line.count(",") >= 3` (mime_sniff.py:159).
- **K6 (i18n, LOW, FACT)** — `_is_table_line` header heuristic char-class `^[A-ZÀ-Ỹa-zà-ỹ\s]+[|]` (analyze.py:209) is Latin+VN only; CJK/Cyrillic header rows fail this branch (leading-pipe tables still caught).
- **K7 (LOW, FACT)** — `apply_cross_check` rule 2 (`hdt_but_few_headings`, 669) counts `total_headings` only, not `vn_hierarchical_markers` — a plain-text VN legal doc that reached HDT via markers without markdown promotion would be demoted to semantic. In practice `smart_chunk` promotes first (443), so markers become headings; the inconsistency is latent for the block path (`analyze_document_blocks` reports both fields).
- Positive: `has_toc` has a language-agnostic dotted-leader fallback (292-299); `analyze_document_blocks` carefully mirrors the dict contract (359-377).

**`strategies.py` (778)** — recursive-with-tables, HDT, semantic (lexical + embed), proposition, hybrid, `_HeadingIndex`, `_split_sentences`.

- **K8 (multilingual, HIGH, FACT)** — `_split_sentences` splits only on `[.!?]` + whitespace (strategies.py:410); CJK “。”/“！”/“？” (no following space) never match → a Japanese/Chinese prose doc = 1 “sentence”. `_chunk_semantic` then early-returns **the whole document as ONE chunk with no size cap** (`if len(sentences) <= 1: return [text.strip()]`, 436-437). Failure: 50 KB Japanese doc routed to semantic (few headings, long) → single mega-chunk → embedder truncation/API error → retrieval blind. `_chunk_proposition`’s clause connectors are hardcoded VN+EN word lists (662-664), `_ABBREVIATIONS` VN+EN (379-386) — language DATA inline in structure-deciding code, exactly what the multilingual-no-vocab mandate forbids (should be locale packs).
- **K9 (LOW, FACT)** — recursive table split assumes header = first 2 lines (`if i < 2 or separator`, strategies.py:148); a 3-row hierarchical header loses row 3; a headerless table replicates 2 data rows as “header”. Inline `chunk_size * 3` oversize multiplier (142).
- **K10 (LOW, FACT)** — `_HeadingIndex.parents_for_chunk` collects **every** H2 from doc start to chunk end (243-248); correct only under the H1-hard-break invariant, which does not hold for semantic/proposition outputs → `parent_headings` metadata over-attributes earlier sections’ H2s.

**`csv_chunker.py` (452)** — row-as-chunk (`_chunk_table_csv`), multi-region detector (`_detect_csv_regions_all`), header/footer synthetic chunks, dual-index.

- **K11 (multi-format/T1, HIGH, FACT)** — flag-off default path (`DEFAULT_TABLE_CSV_EMIT_HEADER_FOOTER_CHUNKS_ENABLED = False`, constants/_11:74) delegates to `_chunk_table_csv` where `header = lines[0]` **unconditionally** (csv_chunker.py:45). For a mixed doc that reached `table_csv` via criterion-2 (“dominant table run” — intro + table + notes, analyze.py:85-103), `lines[0]` is the intro/title line, not the column header: every row chunk becomes `"<title line>\n<row>"`, the real column-header row is emitted as a data chunk, and no multi-region logic runs at all on this default path. Happy-case-only: default behaviour is correct ONLY for pure headerful single-table CSV.
- **K12 (multi-doc/table, HIGH, FACT)** — `_doc_table_header` returns **one document-level header** (the first CSV-shape line) and prepends it to **every region** (csv_chunker.py:236-239, 302, 313, 389). Multi-table docs with different schemas (e.g. a Sheets export with a price table `Tên,Giá` followed by a staff table `Tên,Chức vụ,SĐT`) stamp table-1’s column names on table-2’s rows → the stats extractor / LLM reads Role values under a “Giá” label. The 2026-06-13 fix (documented at 228-234) removed the duplicated-data-row bug but introduced the single-schema assumption; regions already carry their own `header_idx` that could resolve per-region headers.
- Positive: `_is_empty_csv_row` (242-248), region boundary duplication (198-205) and dual-index group packing (400-421) are sound.

**`blocks.py` (329)** — `_split_into_blocks` (text/table), `_split_into_blocks_with_atomic` (+formula/image/code), M18 table-footer merge, `_atomic_protect_enabled` flag read. Solid; only notes: fence toggling is line-based (`"```"` inside prose flips state, 84-91) and `_split_into_blocks` (non-atomic variant) keeps code fences inside text blocks by design.

**`vn_structural.py` (346)** — VN legal-marker detection/promotion, roman↔arabic normalisation, structural anchor for retrieval pre-filter.

- **K13 (multilingual, MED, FACT)** — the multilanguage refactor is **half-wired**: `resolve_struct_markers(lang)` exists (20-34) and constants carry `en`/`ja` packs (constants/_24:24-38), but **all** module regexes are built once from the default (`vi`) tuple at import (76-110), every public function (`promote_vn_hierarchical_headings`, `detect_vn_structural_anchor`, `normalize_vn_section_numerals`) has **no `lang` parameter**, and grep shows **zero callers** of `resolve_struct_markers` outside the module. An EN legal doc (“Chapter II / Section 3 / Article 5”) never gets structural promotion despite the pack existing. Built-but-not-wired.
- **K14 (fragility, LOW, FACT)** — module-level unpack `_STRUCT_MARKERS[0..3]` (88-93) IndexErrors at import if the default locale’s tuple has <4 entries; the documented “unknown locale → empty tuple” contract (28-30) is import-fatal if it ever meets this line (e.g. operator flips `DEFAULT_STRUCTURAL_MARKERS_LANG` to `"ja"` whose pack is `()`), taking down the whole chunking package. HYPOTHESIS on trigger (requires misconfig), FACT on structure.

**`coverage.py` (255)** — lossless char-coverage gate (normalize-with-offsets, interval union, whitespace-gap bridging). Pure, careful, observe-only. No issues found; this is the quality bar the rest of the package should meet.

**`tenant_style.py` (124)** — per-bot style normalizer (ALL-CAPS→`## `, owner separator→pipe rows), opt-in via `plan_limits.chunking_config.style_profile`. Clean; `_PROMOTED_HEADING_RE` (121) is defined-but-unused (micro dead code). Note `.isupper()` promotion can’t work for caseless scripts (CJK) — inherent, opt-in only.

**`chunking_policy.py` (114)** — 3-tier chunking policy resolver (per-bot > platform > constants), validated enums, style-profile resolve. Clean, matches the config-driven mandate.

### Ingest-adjacent helpers

| File | Verdict |
|---|---|
| `chunk_identity.py` (121) | UUID5 deterministic chunk id (bot,doc,content) + UUIDv7 time-ordered id. **Finding I1 (MED, FACT)**: seed excludes chunk index by design (chunk_identity.py:98 + 65-68) → two chunks with identical stripped content in the SAME document (repeated per-page footer, repeated table header chunk, repeated disclaimer) collide to one UUID → bulk INSERT PK-conflicts or UPSERT collapses N rows to 1 (silent content loss). Opt-in flag (`chunk_hash_id_enabled`, default OFF) limits blast radius today. |
| `chunk_quality.py` (329) | 4-signal heuristic quality score + ingest gate (`select_passing_indices`); gate flag default OFF, `DEFAULT_CHUNK_QUALITY_MIN_SCORE=0.5` (constants/_19:59). **Finding I2 (multilingual, MED, FACT)**: `_score_information_density` is whitespace-token TTR (163-168) → CJK/Thai chunks (no spaces) = 1 token → density 0.0 always; with weights (len .3 / lang .2 / dens .2 / corr .3, constants/_19:61-65) a CJK chunk’s ceiling is 0.8 and mid-length CJK chunks sink toward the 0.5 cutoff → systematic drop-bias against non-space-delimited languages when the gate is enabled. **Layering**: imports `application.ports.chunk_quality_port` at module level (38-41). |
| `contextual_enrichment.py` (178) | Anthropic-CR chunk prefixing; **wired in production ingest** (`ingest_stages_enrich.py:541`, verified). **Finding I3 (multilingual, HIGH, FACT)**: ALL prompts and the no-LLM fallback template are hardcoded **Vietnamese** — system prompt “Tài liệu… Bạn sẽ nhận từng đoạn…” (41-48), user prompt (58-64), fallback `"Tài liệu: {title}. Đoạn giữa (phần i/n)."` (67-75) — and get **prepended into stored chunk content** for every bot regardless of corpus language. An EN/JA corpus gets Vietnamese prefixes embedded into every vector (recall pollution) and Haiku is instructed in Vietnamese. Inconsistent with the platform’s own locale-pack pattern (`constants/_26_narrate_prompt_locale_pack.py` exists for the narrator, but not here). |
| `context_buffer.py` (197) | Sentence-window context for atomic blocks; flag default OFF; sentence regex includes “。” (59) — better than strategies.py. Layering: module-level `domain.entities.document.Block` import (41). Env-var mirror pattern documented. Clean otherwise. |
| `context_utils.py` (37) | Lost-in-middle interleave; verified correct against its own docstring example. Clean. |
| `dedup.py` (61) | Jaccard near-dup pairs, pre-tokenised O(N) tokenisation. Clean; O(N²) pairwise inherent. |
| `diff_reingest.py` (268) | **DEAD CODE** — entire module commented out with an explicit 2026-06-03 dead-code notice (1-22): “Helper functions copy-pasted inline into document_service.py. Module itself never imported.” |
| `embedding_cache.py` (95) | Redis embedding cache keyed provider+model+dim+sha16; explicit `unknown` sentinel + warning. Global (not tenant-scoped) — safe: vectors are deterministic per model; no cross-tenant data readable. Clean. |
| `hashing.py` (30) | NFC+lower content hash (GDPR helper). Note: `content_hash_required` lowercases while `deterministic_chunk_id` deliberately does not — two identity systems with different normalisation, documented, OK. |
| `ingestion_validator.py` (139) | Advisory post-ingest checks. **Finding I4 (T2, MED, FACT)**: check 4 is O(N²) pairwise Jaccard with **per-pair re-tokenisation** (85-91, `_jaccard_similarity` splits both strings each call) — a 5,000-row CSV ingest ⇒ ~12.5 M pair comparisons × 2 tokenisations. Inline `duplicate_threshold = 0.95` (83) and `min_chunk_chars=20` (32) violate zero-hardcode. `async def` with zero awaits (27). |
| `intrinsic_metrics.py` (399) | Ekimetrics 5-metric selector (flag default OFF). X-ref patterns EN+VN only (66-71) → RC metric vacuous for other languages (defaults 1.0, so no mis-route — degrades to “no signal”). Clean implementation. |
| `late_chunking.py` (285) | Context-prefix “late chunking” + sliding-window variant. `context_prefix_chars: int = 200` inline (59, 161); `[Document context: …]` English literal embedded into vectors (83, 256) — embed-only, low impact. Clean logic (window math verified). |
| `markdown_normalizer.py` (76) | CSV region → pipe-table + VN heading promotion (flag default OFF). **Finding I5 (LOW-MED, FACT)**: `split(",")` naive cell split (28, 35) — quoted CSV cells containing commas (`"Dịch vụ A, B",100`) shear mid-cell; cells containing `|` corrupt the pipe table. Acceptable only while flag stays OFF. |
| `mime_sniff.py` (169) | Magic-byte sniffer for ambiguous uploads (PDF/HTML/zip-Office/UTF-8 text/CSV). **Finding I6 (multi-format, MED-HIGH, FACT)**: no OLE2 signature (`D0 CF 11 E0`) → legacy **.doc/.xls/.ppt** uploaded as octet-stream fall through every branch (binary ≠ UTF-8 text) and return the ambiguous declared mime (126-163) → parser registry no-match → 0-chunk silent ingest, the exact failure the module exists to prevent. No UTF-16/BOM handling either (51-69) → Windows-exported UTF-16 CSV/TXT also lost. CSV sniff comma-only ≥3 (159). CLAUDE.md declares DOC/XLS first-class formats. |
| `mmr.py` (219) | MMR with cosine (vectorised) or trigram fallback. **Finding I7 (LOW-MED, FACT)**: `mmr_score = λ·relevance − (1−λ)·max_sim` (184-187) assumes relevance is 0..1-commensurate with similarity; raw BM25 (≫1) or RRF (≪1) scores skew the trade-off silently (HYPOTHESIS for which scale callers pass). Inline defaults `lambda_param=0.7`, `similarity_threshold=0.88` (75-76). Numpy path correctness verified (zero-norm guard 133-140). |
| `pii_universal.py` (395) | Universal-surface PII redaction (audit/steps/telemetry), double-toggle opt-in, DefaultRedactor regex provider. **Finding I8 (multi-locale, MED, FACT)**: the “universal” pattern set is VN-national — EMAIL + PHONE_VN(+intl) + CCCD + CMND + BANK_ACC (101-108); no generic passport/SSN/IBAN classes, so non-VN tenants’ PII passes unmasked while the feature name promises universality. Layering: module-level `application.ports.pii_redactor_port` import (29). Overlap-resolution + oversize guard well done. |

### Prompt/token/text helpers

| File | Verdict |
|---|---|
| `prompt_compression.py` (372) | 4-tier language-aware chunk compressor. **Finding P1 (T1, MED-HIGH, FACT)**: negation protection is Vietnamese-only — `_NEGATION_WORDS` “không chưa chẳng…” (33-35), `_NEGATION_PHRASES` (38-40) — while the comment claims universality (“regardless of language”, 31-34). For an EN bot with operator stopwords (EN stopword lists standardly include “not”/“no”), a negated sentence (“does **not** cover water damage”) earns no negation bonus and can fall below `min_sentence_score=0.15` (inline, 230) → dropped from the compressed chunk → LLM answers the opposite. Tabular/full-doc bypass guards (289-306, 343-344) are good. |
| `prompt_token_opt.py` (213) | min-score filter / n-gram dedupe / factoid history skip; flag-gated; constants imported. Clean. |
| `prompt_injection_guard.py` (89) | **DEAD CODE** — fully commented out with 2026-06-03 notice (1-22): “Zero references in src/. Prompt-injection handled inline in local_guardrail.py.” |
| `proposition_llm.py` (325) | **DEAD CODE** — fully commented out with 2026-06-03 notice (1-22): “Only referenced by proposition_decomposer_port.py spec; never wired.” |
| `json_io.py` (57) | orjson wrappers, exception-compat documented. Clean. |
| `json_parse.py` (395) | 4-strategy robust LLM-JSON parser with telemetry; quote-aware bracket extraction verified. Never fabricates (raises `JSONParseError`). Clean (style: `raise` inside `except` without `from`, 333). |
| `sentence_similarity.py` (125) | Lexical blend (0.6 SequenceMatcher + 0.4 Jaccard) + cosine helper + Port impl. Weights module-inline but documented as behaviour-pinned. Clean. |
| `token_budget.py` (179) | Quota arithmetic + `truncate_to_token_budget` (head-always-kept contract). Clean, well-specified. |
| `text_normalization.py` (46) | NFC canonical helpers with cache-contract warnings. Clean. |
| `text_utils.py` (54) | VN filler-token stripper for BM25; overridable via system_config per docstring (caller passes tokens). Clean. |
| `vn_honorific.py` (43) | Label-only honorific detection (no answer override — Quality Gate #10 clean). Clean. |
| `vi_tokenizer.py` (545) | underthesea segmentation w/ race-safe warmup, code-token masking, teencode/abbreviation expansion (4-tier, DB-merged, TTL cache), diacritic utils. Findings **V1 (LOW, FACT)**: `_VI_ABBREVIATIONS_SEED` has duplicate key `"ib"` (227 and 236) — silent overwrite (same value, benign, but a lint hole). **V2 (LOW, FACT)**: `expand_abbreviations_async` ends in `if sync_lang in VI_DOMAIN_LANGUAGES: return _apply_abbreviations(...)` / `return _apply_abbreviations(...)` — both branches identical (500-505), vestigial dead conditional. **V3 (LOW-MED, FACT)**: teencode `"k": "không"` (222) whole-word replaces a standalone “k” — query “giá dưới 500 k” → “giá dưới 500 không” → money unit destroyed before range parsing (attached “500k” is safe; spaced variant breaks). VI-language-gated so non-VN bots unaffected. |
| `workspace_id_validator.py` (113) | 4-key slug validation + tenant-UUID fallback with warn breadcrumb. Matches the IDENTITY RULE spec exactly. Clean. |
| `single_flight.py` (177) | Bounded per-key lock registry + stampede metric. Correct double-check under `_registry_mutex`; LRU eviction never drops locked entries. Layering: guarded infra metrics import (43-47). Clean. |
| `rate_limit_policy.py` (147) | Pattern→policy table, constants-driven. Note `/demo-ragbot(/.*)?` is UNLIMITED (61) — fine while the demo surface is gateway-blocked per the headless-BE rule; a breadcrumb if it ever leaks external. `/api/ragbot/documents/*` intentionally falls to `_DEFAULT_POLICY`. Clean. |
| `hmac_signing.py` (113) | Constant-time HMAC verify, algorithm whitelist, fail-open/closed left to caller. Clean, textbook. |
| `callback_validator.py` (109) | SSRF-guarded callback URL validation. **Finding S1 (security, MED, FACT)**: TOCTOU DNS rebinding — `_is_url_safe` resolves DNS (47-58), then `httpx` re-resolves for the actual POST (99-100); an attacker DNS that flips A-records between the two resolutions posts the validation payload to an internal IP. Blocked-port/network lists are solid; `timeout_s: int = 10` inline (91). Vietnamese literal in the sample payload (73) — cosmetic. |
| `api_key_pool.py` (415) | Round-robin multi-key pool w/ Redis cooldown (hashed key ids), env concurrency override, DB-backed hot-reload factory (30 s TTL, double-checked lock, decrypt-in-memory). All broad-excepts carry `noqa: BLE001 + reason` (205, 225, 384, 397) — policy-compliant. `LIMIT 5` inline (379). Clean overall; `ai_keys` read is platform-scoped (no tenant column) — consistent with provider-key ownership model. |
| `anthropic_cache.py` (33) | **Pure re-export shim**: `shared` module whose only content is `from ragbot.infrastructure.llm.dynamic_litellm_router import _apply_anthropic_cache_control` (14-16) — a *shared→infrastructure, module-level, private-symbol* import created explicitly to let application code dodge the layer boundary. See L1. |
| `auto_merge_retrieval.py` (274) | HiChunk sibling→parent collapse; pure, HALLU-safe (missing parent content → children kept, 192-198), rank-preserving, telemetry struct. Verified merge loop correctness (222-255). Clean — one of the best-written modules in scope. |
| `complexity_sizing.py` (186) | Databricks adaptive chunk sizing; pure; constants-driven; validation raises loud. Clean. |
| `bot_bindings.py` (132) | Idempotent `bot_model_bindings` insert helper w/ workspace lift from parent bot (94-103). Docstring still says “3-key identity” (bot_bindings.py:4) — stale vs 4-key platform contract (workspace handling in code is correct). Inline SQL literals rank=0/weight=100/top_p=1.0 (114) within whitelist. |

---

## Part 2 — Cross-cutting analysis

### L1. Layering: `shared/` imports upward (contradicts its own contract)

`shared/__init__.py:1` declares “lowest layer, zero dependencies beyond stdlib”. Verified upward imports (grep, excludes commented dead code):

| Module | Import | Severity |
|---|---|---|
| `anthropic_cache.py:14` | **module-level**, `infrastructure.llm.dynamic_litellm_router._apply_anthropic_cache_control` (private symbol) | worst — import cycle risk; any router import failure breaks `shared` |
| `chunk_quality.py:38` | module-level, `application.ports.chunk_quality_port` | high |
| `pii_universal.py:29` | module-level, `application.ports.pii_redactor_port` | high |
| `context_buffer.py:41` | module-level, `domain.entities.document.Block` | med |
| `single_flight.py:43` | guarded try/except, `infrastructure.observability.metrics` | low |
| `chunking/__init__.py:701,831,898` | function-local, `domain.entities.document` | low |
| `api_key_pool.py:363` | function-local, `infrastructure.security.env_secrets` | low |
| `bot_bindings.py:74` | function-local, `application.dto.ai_specs` | low |
| `late_chunking.py:49` | TYPE_CHECKING only | ok |

FACT. The correct direction is to move the Port protocols (tiny Protocol classes) into `shared/` or `domain/`, and to move the cache-control helper out of the router into a genuinely shared module rather than re-exporting a private symbol upward.

### L2. Happy-case-only inventory (owner’s #1 concern)

1. **Comma = table** (K5, K11, K12, I5, mime_sniff:159) — the entire tabular fast-path chain (format detect → strategy select → chunking → markdown normalize → mime sniff) assumes comma-delimited, single-schema, header-first tables. `;`-CSV, TSV-as-CSV, multi-schema multi-table docs, headerless tables, and mixed intro+table docs all silently degrade.
2. **NFC + space-delimited + VN/EN language** — `_ascii_fold` (Q5), `_split_sentences` (K8), TTR density (I2), negation guard (P1), enrichment prompts (I3), summary patterns (Q3), x-ref patterns (intrinsic_metrics 66-71). The locale-pack architecture EXISTS (RoutingSignals, structural marker packs, 4-tier stopwords) but at least six shared modules bypass it with inline VN(±EN) vocabulary.
3. **Single-currency VND money grammar** (Q2) with a 1000-VND magic floor.
4. **≥3 headings ⇒ HDT** (K1) — the strategy selector is effectively a two-outcome function (table_csv | hdt) for most real documents; the four other strategies and the L5 cross-check exist mostly for near-structure-less docs.
5. **Score-scale assumptions** — MMR (I7) and prompt_token_opt min-score assume 0..1 relevance.

### L3. Multi-axis summary

- **multi-doc**: K12 (one header across regions) is the concrete cross-table breakage; auto_merge_retrieval and dedup are properly per-bot-scoped pure helpers (caller owns scoping — documented in diff_reingest/dedup contracts).
- **multi-bot**: config chain (bot_limits/chunking_policy/vi_tokenizer/prompt_compression) is genuinely per-bot and well built — EXCEPT B1 (regex lowercasing corrupts owner overrides), A1 (autonomy can’t opt down), and the enrichment prompts (I3) which no bot config can localise.
- **multi-format**: I6 (no OLE2/UTF-16 sniff) + K5/K11 (comma-only) mean DOC/XLS/PPT and European-locale CSV are NOT at parity with PDF/markdown, contradicting the “ingest đa định dạng first-class” mandate.
- **multi-tenant**: shared/ itself is mostly pure and takes scoping from callers (correct design). Risks: K3 sentinel random tenant UUIDs (latent), embedding_cache global-by-design (assessed safe), api_key_pool/ai_keys platform-scope (by design).

### L4. Dead code / orphans (all FACT, grep-verified)

1. `diff_reingest.py` — whole module commented out (notice 1-22).
2. `prompt_injection_guard.py` — whole module commented out (notice 1-22).
3. `proposition_llm.py` — whole module commented out (notice 1-22).
4. `smart_chunk_atomic` + helpers — test-only; pin test asserts non-wiring (`test_block_feed_s1_plumbing.py:51`).
5. `resolve_struct_markers` per-locale path — zero non-module callers; en/ja packs unreachable (K13).
6. `system_config.code_query_pattern` override — documented at query_range_parser.py:446, never read anywhere (Q6).
7. `tenant_style._PROMOTED_HEADING_RE` (121) — unused.
8. `_detect_csv_regions` single-region shim (csv_chunker.py:59-84) — back-compat only; verify remaining callers before deleting.

### L5. CLAUDE.md compliance ledger

- **Zero-hardcode**: violations listed at B2, Q7, I4, I7, P1 (min_sentence_score), late_chunking 59/161, callback 91, pagination 10. The majority of the scope IS constants-driven — these are stragglers, not systemic.
- **Domain-neutral**: 1 hit — `llm_usage.py:54` “innocom gateway”. No brand/bot literals elsewhere in scope. Language literals (VN vocab) are a *multilingual* problem, not a tenant-literal problem.
- **No-version-ref**: `types.py:21,25` (`v0.3.0`/`v0.4.0`); pervasive wave/sprint/date breadcrumbs in comments (bot_limits “Wave K2/J2”, bootstrap “260525 Bug #7c”, csv_chunker “Bug #9”) — comment-rule violations (WHY-only), grep-guard patterns don’t catch these forms.
- **Broad-except**: all `except Exception` in scope carry `noqa: BLE001` + reason (api_key_pool 205/225/384/397, llm_usage 30, contextual_enrichment 149, vi_tokenizer 184/436/454/543, strategies 607, single_flight 46/173, chunk_quality 133) — **policy-compliant**.
- **Provider if/elif in business logic**: none found in scope (strategy dispatch on generic names in `smart_chunk` is the sanctioned config-string registry pattern; `chunking_policy` validates against an allow-set).
- **App-inject/override answer**: none in scope touch the answer path. Enrichment/late-chunking prefixes modify *stored/embedded* content (ingest-side, allowed) — flagged only for locale correctness (I3), not for Gate #10.
- **4-key identity**: `workspace_id_validator` + `bot_bindings` workspace lift comply; `bot_bindings.py:4` docstring stale (“3-key”).

---

## Part 3 — Ranked findings (dedup of the above)

| # | Sev | Axis | Finding | Evidence |
|---|---|---|---|---|
| 1 | HIGH | multi-format/T1 | Comma-only table detection across the whole tabular chain — `;`-CSV/TSV/European exports never reach table_csv; rows cut mid-tuple | analyze.py:73,92-99; csv_chunker.py:123-126; mime_sniff.py:159 |
| 2 | HIGH | multi-format/T1 | Default (flag-off) `table_csv` uses `lines[0]` as header — mixed intro+table docs stamp the title on every row; no multi-region support on default path | csv_chunker.py:45; constants/_11:74 |
| 3 | HIGH | multi-doc | One doc-level header prepended to ALL table regions — multi-schema multi-table docs get wrong column labels on tables 2..N | csv_chunker.py:236-239,302,313 |
| 4 | HIGH | T1-smartness | HDT fast-path (≥3 headings, conf 1.0) bypasses the selector AND table isolation; `_chunk_hdt` splits oversized sections with a table-blind splitter; atomic-protect default OFF | analyze.py:462-463; chunking/__init__.py:521; strategies.py:339-353; constants/_00:105 |
| 5 | HIGH | multi-format (lang) | `_chunk_semantic` returns whole CJK doc as ONE uncapped chunk (sentence splitter has no CJK terminators); proposition/abbrev vocab VN+EN inline | strategies.py:410,436-437,662-664,379-386 |
| 6 | HIGH | multi-bot (lang) | Contextual-enrichment prompts + fallback prefixes hardcoded Vietnamese, prepended into every stored chunk for all locales; wired in production ingest | contextual_enrichment.py:41-75,133-143; ingest_stages_enrich.py:541 |
| 7 | HIGH (CONFIRMED) | T1 | From-to range regex hijacks age/session/quantity ranges as price filters at conf 0.9 (no bare-number floor in that branch) — repro’d live | query_range_parser.py:157-162,213-227 vs 542 |
| 8 | HIGH | T2-perf | `get_boot_config` blocking psycopg2 connect (3 s timeout) on async hot path (understand / complexity / decomposer nodes + ingest chunking) — event-loop stall on every TTL miss | bootstrap_config.py:264,330; grep of callers |
| 9 | MED-HIGH | multi-format | mime_sniff misses OLE2 (.doc/.xls/.ppt) and UTF-16 → legacy Office/Windows uploads as octet-stream silently yield 0 chunks | mime_sniff.py:51-69,126-163 |
| 10 | MED-HIGH | T3-design | shared/ imports upward into application/infrastructure/domain (worst: module-level re-export of a PRIVATE router symbol), contradicting its own “lowest layer” contract | anthropic_cache.py:14; chunk_quality.py:38; pii_universal.py:29; context_buffer.py:41; shared/__init__.py:1 |
| 11 | MED | multi-bot | `validate_plan_limits` lowercases owner regex patterns (`\S→\s` inversion) for `understand_greeting_patterns` | bot_limits.py:589; query_graph.py:537 |
| 12 | MED | multi-locale | Money grammar VND-only + `_MIN_BARE_PRICE_VND=1000` kills sub-1000 currencies; summary patterns VI-only; substring signal-token matching | query_range_parser.py:158,173,185,561,246,259 |
| 13 | MED | multi-bot | Autonomy resolver `max(bot, system)` — bots cannot opt DOWN below platform default (pattern bot_limits already removed) | autonomy_resolver.py:56-59 |
| 14 | MED | T1 | `_prefix_section_headings`: unlocatable chunk gets the LAST heading of the doc prepended (wrong-section attribution) | chunking/__init__.py:399-405 |
| 15 | MED | multilingual | Per-locale structural-marker packs (en/ja) built but unreachable — no lang param on promote/detect; module regexes frozen to `vi`; import-fatal unpack if default pack <4 markers | vn_structural.py:76-110,88-93; constants/_24:24-42 |
| 16 | MED | ingest | Deterministic chunk UUID5 collides for repeated identical content within one document (opt-in flag) | chunk_identity.py:98 |
| 17 | MED | multilingual | Chunk-quality info-density = 0 for CJK (whitespace TTR) → drop-bias when gate enabled; PII “universal” patterns VN-national only; prompt-compression negation guard VN-only | chunk_quality.py:163-168; pii_universal.py:101-108; prompt_compression.py:33-40,230 |
| 18 | MED | security | Callback validator DNS-rebinding TOCTOU (validate-then-POST re-resolves) | callback_validator.py:47-58,99-100 |
| 19 | LOW-MED | test-health/dead | 3 fully-commented dead modules; smart_chunk_atomic orphan (with random sentinel tenant UUIDs if ever wired naively); dead `code_query_pattern` knob; bootstrap negative-cache caller-default poisoning; stdlib `extra=` logging swallowed | diff_reingest.py:1-22; prompt_injection_guard.py:1-22; proposition_llm.py:1-22; chunking/__init__.py:719-721; query_range_parser.py:446-448; bootstrap_config.py:306,332,341,347-348 |
| 20 | LOW | hygiene | Zero-hardcode stragglers (B2/Q7/I4/I7); domain literal “innocom” (llm_usage.py:54); version-ref comments (types.py:21,25); vi_tokenizer duplicate `"ib"` key + identical-branch dead conditional (227,236,500-505); teencode `"k"→"không"` breaks spaced “500 k” money | listed inline |

## Recommendations (short list, T1-first)

1. Add the bare-number/unit guard to the from-to and fuzzy branches of `parse_range_query` (mirror `_find_money_after_token`) — 1-file fix, kills the confirmed mis-route (#7).
2. Per-region headers in `csv_chunker` (regions already carry `header_idx`); make `_chunk_table_csv` use `_doc_table_header` instead of `lines[0]` (#2, #3).
3. Delimiter-general CSV shape detection (comma/semicolon/tab, pick dominant) in ONE shared helper consumed by analyze + csv_chunker + mime_sniff (#1, #9 partially).
4. Route `get_boot_config` through `asyncio.to_thread` (or an async read-through cache) for the three query-graph nodes and ingest chunking (#8).
5. Thread `language` from bot config into `_split_sentences` / proposition connectors / enrichment prompts via the existing locale-pack pattern (#5, #6, #15 wiring).
6. Exempt regex-valued `list_str` keys from lowercasing in `validate_plan_limits` and compile-validate at write time (#11).
7. Decide the layering rule for Ports and delete the `anthropic_cache` shim (#10); physically delete the 3 dead modules after operator sign-off (#19).

**CHƯA verify (runtime)**: downstream behaviour after the #7 mis-parse (needs one traced request through the stats route), actual event-loop stall duration for #8 (needs a perf_timer measurement with PG latency injected), and drop-rates for #17 (needs the gate enabled on a CJK fixture). All other findings are code-level FACTs with line evidence.
