# ISSUE RE-CHECK + DEEP ANALYSIS — 2026-06-30

> Re-verification of ALL ~48 inventory issues (`COMPLETE_INVENTORY_20260626.md`) against current code (branch `fix-260623-ingest-expert`, HEAD `647f4a2`+) via 4 parallel Opus read-only audit agents, each returning file:line evidence. Plus a real end-to-end xe re-ingest that exposed a live lifecycle bug. Discipline: rule #0 — every verdict has evidence.

---

## 1. Issue re-check — status table

### ✅ FIXED (16)
| Issue | Evidence |
|---|---|
| RLS-2 missing_ok policy | `alembic/20260626_rls_missing_ok_setting.py:57` |
| SB-2 conversation_state tenant-scope + GUC | `jsonb_conversation_state.py:141-161` |
| AG-A2 grounding fail_closed | `guard_output.py:230-242`; default `DEFAULT_GROUNDING_FAILURE_MODE="fail_closed"` |
| #1 provider revive (innocom/zembed) | `alembic/20260626_revive_grounding_slot_innocom.py:36-50` |
| #5/SB-3/PLM-5 qwen3 capability-route | `structured_output_helper.py:157-189` (branches on `supports_json_mode`, not name substring) |
| MT-1 multi-turn reconcile | `history_reconcile.py:124-169` (merges chat_histories + messages by bot+connect_id) |
| reranker resolver fallback | `reranker_resolver.py:165-201` (binding → system_config → Null, logs drift) |
| #2 PRICE_MIN_VND floor | numeric coverage generic `min_digits` (`number_format.py:208`); floor scoped to price-index only |
| #4 col_N header 2-row merge | `tabular_markdown.py:102-258` (`_is_header_continuation`+`_merge_header_fill`) |
| #6 fabricate-URL rule | `alembic/20260627_seed_anti_fabricate_rule_lang_packs.py:50-88` (append-only) |
| ING delete idempotency DLC-1 mark_done | `document_worker.py:672`; `ingest_idempotency_service.py:227` (live, not dead) |
| DLC-2 failed-stuck/no-retry | no terminal `failed` state; transient → bus redelivery; recovery sweep `document_recovery_worker.py:187` |
| RQ-2 article-filter per-bot gate | `bootstrap.py:416-428` (NullObject default), `nodes/retrieve.py:271` |
| RQ-4/RQ-5 chunk_quality wired | `ingest_stages_enrich.py:578-594` (called, observability-only) |
| **OBS-1 empty-answer warn** | `generate.py:96` (`_is_empty_answer`), `952-955` (`generate_empty_answer` event) — **shipped this session `647f4a2`** |
| FMT-3 caption per-locale | `llm_narrate.py:54-115`, `_26_narrate_prompt_locale_pack.py` (no hardcoded VN literal) |

### 🟠 PARTIAL (4)
| Issue | Gap | Evidence |
|---|---|---|
| RLS-1/SB-1 superuser bypass | Code + boot health-check built, but live `.env` connects as `postgres` (superuser) + `RAGBOT_ALLOW_SUPERUSER_RUNTIME=1` → check only WARNs, RLS inert. **OPS fix**: switch DSN to `ragbot_app`. | `engine.py:60`, `.env:10,110`, `app.py:170` |
| CB-CLIENT-4XX | Only 429/RateLimitError excluded from breaker; other client-4xx (400/401/403/404/422) still count as provider failure → 1 misconfigured bot can OPEN a healthy provider. | `dynamic_litellm_router.py:149-156` |
| #3 price-centric stats | F7 attribute-generic numeric map REVERTED (`9416f4d`); per-row string `attributes_json` persist, but `aggregate_summary` still price-only. | `document_stats.py:1157` |
| ING-7 delete purge stats | `delete_document` use-case calls `stats_index_repo.delete_by_document`, but `bootstrap.py:804` Factory **never injects** the repo → `None` → purge dead in prod (relies on `deleted_at IS NULL` serving filter). | `use_cases/delete_document.py:90`, `bootstrap.py:804` |

### 🔴 NOT-FIXED (6)
| Issue | Evidence |
|---|---|
| #8 cross-sheet reconcile | no impl — entities stay flat per-chunk list |
| RQ-1 sparse VN-pinned | tsquery hardcoded `'simple'` everywhere (`pgvector_store.py:442`, `pg_bm25_retrieval.py:101`) — blocks non-VN bots |
| OBS-2 completion_tokens qwen3 | streaming `completion_total` only from provider `usage`; qwen3 (no usage payload) meters 0; `tokens_yielded` proxy discarded (`dynamic_litellm_router.py:916`) |
| PERSIST-CACHE-TASK | bare `asyncio.create_task` no strong-ref/done-callback → GC-droppable (`persist.py:197`); contrast correct pattern `ingest_stages_final.py:421` |
| SB-4 SSRF webhook | `webhook_dispatcher.py:232` POSTs `render_url()` with zero IP/DNS-rebind guard |
| SB-5 PII vs slot | redactor masks query at worker boundary; slot extractor reads raw message — no shared unredacted-original reconciliation (`chat_worker/payload.py:65`, `generate.py:250`) |

### ⏸ DEFER — Phase-2 architectural (plan defers to T1≥95%)
| Issue | Evidence |
|---|---|
| S2-A god-node | `retrieve.py` = 1852 lines; 2 decomposers (`decompose`+`adaptive_decompose`) both wired; `condense_question` LIVE (not dead — do not remove) |
| RQ-3 anisotropy (informational) | dim=1280 matryoshka, no whitening/isotropy correction → platform implicitly BM25-dependent |

**Tally: 16 FIXED · 4 PARTIAL · 6 NOT-FIXED · 2 DEFER.**

---

## 2. xe re-ingest — end-to-end (NEW live finding)

**Converter proof (zero-API, on the real source CSVs)** via `rows_to_structured_markdown`:
- `xe-2.csv` → **0 col_N** (clean row-1 header `Marks | Cargo | Ngày về` used)
- `xe-1.csv` → header recovered: `col5..col10` → `date1 | date2 | hình ảnh1 | ẢNH 1 | ẢNH 2 | Ảnh 3` (only col1 STT residual)
- `xe-3.csv` → clean main header `Tên | Giá | Mã | Số lượng | Ngày | Ảnh | Aliases`

**Live re-ingest** via `POST /api/ragbot/sync/documents` (HTTP 200, ZE-embedded): the `GoogleSheetsParser` (which DOES call `rows_to_structured_markdown`, `google_sheets_parser.py:81`) re-chunked **row-as-chunk** — xe-2 went 1 monolithic chunk → 65 row-level chunks each carrying the header (this directly addresses the header-data-split failure class).

**🔴 BUG EXPOSED — re-sync does not purge prior chunks.** After re-ingest xe held **819 chunks = 335 old + 484 new** (two clean created_at clusters: old `03:1x`, new `16:5x`), both `doc_deleted_at IS NULL`, under the SAME document_ids. The handler *has* purge logic (`replace_documents_for_bot` `__init__.py:880` + `DELETE FROM document_chunks` `ingest_stages_store.py:688`) but it did not fire for this path — same family as **ING-7** (purge wiring incomplete). Root cause needs a focused `/diagnose` (likely source_url normalization mismatch between the stored URL and the re-sync payload URL, so `replace_documents_for_bot` matched nothing).

**Resolution:** user-authorized rollback → xe restored to exactly **335 chunks (col_N=3)**, its known baseline (golden 19/40). No clean runtime col_N lift was measured because the re-sync would need the purge bug fixed first.

---

## 3. Deep-analysis verdict

- **Framework**: ✅ sound (Hexagonal/Port/DI/4-key/sacred), 0-regression proven (byte-identical failure set baseline↔HEAD).
- **The "dây chưa nối" thesis holds**: the remaining failures are wiring gaps, not framework errors — ING-7 purge unwired, RLS DSN not switched (ops), CB-4xx partial, OBS-2/PERSIST-CACHE/SB-4/SB-5 unwired, RQ-1 locale not threaded.
- **col_N**: fixed + proven at the converter level (multi-bot, shape-only). The runtime lift is blocked by the **re-sync purge bug**, not by the converter.
- **3-bot eval reality**: biggest raw-score drag = provider 503 (transient) + scorer false-positives; the real input-data layer = table-row-binding, which the row-as-chunk + converter fix targets — pending a clean re-ingest.

## 4. Prioritized next steps
1. **Fix re-sync purge bug** (`replace_documents_for_bot` source_url match / chunk purge) → then a clean xe re-ingest measures the real col_N + product-code-lookup lift. ← unblocks the re-test.
2. **Wire ING-7** (inject `stats_index_repo` in `bootstrap.py` Factory) — 1-line DI fix.
3. **PERSIST-CACHE-TASK** strong-ref the cache-write task (mirror `ingest_stages_final.py:421`).
4. **RLS-1** ops: switch DSN to `ragbot_app`, drop `RAGBOT_ALLOW_SUPERUSER_RUNTIME`.
5. **CB-4xx** general 4xx-vs-5xx split. **OBS-2** qwen3 streaming token count. **SB-4** SSRF guard.
6. Provider 503 stability + eval scorer tightening (cuts false drag).
