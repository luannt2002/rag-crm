# MASTER DIAGNOSIS — Session 2026-06-26

> Tổng hợp toàn bộ session: revive stack + deep multi-agent diagnosis (2 workflow, 8 agent Opus) + code audit vs CLAUDE.md.
> **Nhãn rule#0**: SỰ THẬT = có evidence (code `file:line` / DB query / log). GIẢ THUYẾT = chưa runtime-verify.
> Diagnosis chủ yếu **TĨNH** (code-evidence) — mọi % lift / "fixed" cần load-test backward-verify trước khi tuyên bố.
> Branch: `fix-260623-ingest-expert`. Anchor commit: `fc16f3b`.

---

## 0. TL;DR

Hệ thống **đã sống lại** (provider revive) nhưng **CHƯA chuẩn** — 10 bug gốc mới **diagnose, chưa fix**, tập trung 1 chỗ: **ingest bảng nhúng giả-định-giá (price-centric) thay vì giữ STRUCTURE+ROLE cho owner khai qua config**.

**Gốc rễ sâu nhất (2 workflow đồng thuận):** Engine nhúng GIẢ ĐỊNH MIỀN (commercial-VN, price-centric, provider-tên-chứa-`openai`) vào inference/routing/guard. Happy-case chạy hoàn hảo; **VỠ THẦM LẶNG khi data/provider lệch giả định**. Khung đúng (Hexagonal/Port/DI/4-key/sacred) → fix = **NỐI DÂY, KHÔNG REWRITE**.

---

## 1. Provider state (verified session này)

| Vai trò | Provider | Key | Trạng thái |
|---|---|---|---|
| chat / LLM answer | **innocom** `ai.innocom.co` (qwen3, OpenAI-compatible) | `INNOCOM_API_KEY` | ✅ sống |
| embed | **ZeroEntropy** zembed-1 @1280 | `ZEROENTROPY_API_KEY` (chung với rerank) | ✅ sống |
| rerank | ZeroEntropy zerank-2 | `ZEROENTROPY_API_KEY` | ✅ sống |
| ~~OpenAI chat+embed~~ | gpt-4.1-mini / text-embedding-3-small | `OPENAI_API_KEY` | ❌ 429 quota burned |
| ~~Jina embed~~ | jina-embeddings-v3 | — | ❌ 403 burned |

**Endpoint innocom**: cũ `llm.innocom.co` (= `.env LMSTUDIO_BASE_URL`) bị CF-1010/502 chặn từ server IP `42.113.107.11` (FPT **dynamic** IP). Mới `ai.innocom.co` = host khác, không chặn → 200. innocom **chat-only** (`/v1/embeddings`=404).

### Đã ship (alembic tracked, sacred #7, commit `fc16f3b`)
- `chat_swap_innocom_260626`: 31 LLM binding → innocom (`openai/claude` passthrough).
- `embed_swap_ze1280_260626`: cột `document_chunks.embedding` + `semantic_cache.query_embedding` `vector(1024)→(1280)` (HNSW m=32/ef=200); system_config + binding → zembed-1@1280; re-embed 222 chunk (xe 106, spa 43, legal 73, 0 fail).
- Scorer mới `retest_golden_generic.py`: semantic 2-path (specific_facts OR content-word overlap≥0.5, accent-fold, VN-stopword-drop).

---

## 2. Golden test — kết quả mới nhất (stack mới + scorer mới)

| Bot | Trước | Sau | Δ |
|---|---|---|---|
| spa (test-spa-id) | 72% | **92%** (46/50) | +20 |
| xe (chinh-sach-xe) | 70% | **78%** (31/40) | +8 |
| legal (thong-tu-09-2020-tt-nhnn) | 0% (provider dead) | **86%** (43/50) | +86 |

HALLU fabricate (số) = 0. Phần tăng = provider sống + scorer sửa ~11 false-negative paraphrase.

---

## 3. Phân tích 20 câu sai (3 run mới nhất)

| Nhóm | Số | Câu | Bản chất | Evidence |
|---|---|---|---|---|
| B — render sai số | 7 | spa3,4 · xe1-4 · legal2 | bot trả số tự tin nhưng SAI | DB: cột Kho=string title, số tồn mất |
| C — coverage/retrieve miss | 5 | spa1,2 · xe8 · legal1,4 | corpus có nhưng không lấy được | sc thấp / wrong-chunk |
| HALLU bịa URL | 1 | xe9 | bot bịa `namphat.vn` | namphat=0 chunk corpus |
| EMPTY bền (KHÔNG transient) | 3 | xe5,6,7 | 1 sub-call (slot/grounding) chết OpenAI → empty cả turn | log: SlotSchema→openai 429 |
| Scorer/cắt câu | 3 | legal3,5,6 | answer ĐÚNG bị chấm fail | legal6 "một năm một lần" đúng |
| Golden file sai | 1 | legal7 | câu lốp-xe nhét vào bot luật | bot refuse = ĐÚNG |

**Đính chính rule#0**: xe5,6,7 ban đầu báo "transient 500" → re-run **VẪN empty** → KHÔNG transient, là provider-swap chưa xong.

---

## 4. Code audit vs CLAUDE.md (grep guard)

| Sacred rule | Verdict | Evidence |
|---|---|---|
| Domain-neutral (no brand literal trong code) | ✅ PASS | grep medispa/landspider/rovelo/namphat `src/`=0 |
| Provider hardcode (`if provider==`) | ✅ PASS | 0 thật (chỉ regex auditor_agent) |
| Version-ref (`_v1/_legacy`) | ✅ PASS | 0 thật (comment + filename) |
| App-override answer (math_lockdown) | ✅ PASS | persist.py chỉ dùng `extract_numeric_claims` quyết cache-skip, *"never to alter the answer"* |
| Broad-except giảm dần | ✅ ~PASS | 3 chưa annotate / 248 có `noqa BLE001` |
| DI: orchestration chỉ import Port | ⚠ SOFT FAIL | query_graph→litellm_router, rerank→null_reranker (pre-existing) |
| OOS refusal no i18n fallback (rule 3) | ⚠ NGHI | `bot_config.py:142` "None = fall back to i18n default" |

→ **Code core SẠCH** với sacred rules quan trọng. Vi phạm = soft/pre-existing.

---

## 5. TẤT CẢ vấn đề (10 issue ranked)

| # | Vấn đề | Tầng | Evidence | Nhãn | Status |
|---|---|---|---|---|---|
| 1 | 4 binding query-path còn OpenAI dead → empty answer | provider | grounding nano + slot mini → 429 | SỰ THẬT | chưa fix |
| 2 | **PRICE_MIN_VND floor lọc mất số phi-giá** (tồn 404 < 10000 → None) | ingest | `document_stats.py:235` | SỰ THẬT | chưa fix |
| 3 | Engine **PRICE-CENTRIC** — tồn/ngày/link là attribute hạng-2 | ingest | name/price/alias first-class; rest=dump (`:431`) | SỰ THẬT | chưa fix (ADR-0007 proposed) |
| 4 | Header 2-dòng mangle → col_N rác | ingest | 63/496 entity col_N (DB verified) | SỰ THẬT | chưa fix |
| 5 | **qwen3 fail structured-output** — routing substring `openai` ép strict json_schema + bỏ qua cột `supports_json_mode` | provider/llm | 9/11 schema call, UnderstandOutput validation fail | SỰ THẬT | chưa fix |
| 6 | Bịa URL (INGEST ~55% + sysprompt ~30% + cắt 120-char ~15%) | ingest+sysprompt | namphat=0, link thật 96/222 chunk, 0/496 entity key image | SỰ THẬT | chưa fix |
| 7 | Stale-entity: delete không purge stats + serving không filter `deleted_at` | lifecycle | code confirmed (document_commands.py + stats_index_repository.py) | code SỰ THẬT; **87% GIẢ THUYẾT** | chưa fix |
| 8 | Cross-sheet không reconcile (1 lốp = 2 entity) | ingest | DB confirmed | SỰ THẬT | chưa fix |
| 9 | God-node `retrieve.py` 96KB + 120 config-key | arch | workflow đếm | SỰ THẬT | chưa fix (T3) |
| 10 | Dead node `condense_question` + 2 decomposer trùng | arch | flag không seed | SỰ THẬT | chưa fix (T3) |

---

## 6. TẤT CẢ luồng — check

| Luồng | Số liệu | Đánh giá |
|---|---|---|
| Query graph | 21 node / 12 static / 9 conditional edge, cap 2 loop | ✅ topology CHUẨN SOTA, KHÔNG loạn |
| Config keys | 120 key fork flow | ⚠ loạn ở đây (không phải graph) |
| God-node | `retrieve.py` 96KB | ⚠ tách (T3) |
| Dead/trùng | condense_question chết + 2 decomposer | ⚠ gỡ/gộp |
| Ingest flow | bytes→parse→markdown→chunk→stats-index→embed | ⚠ chunk flatten bảng (2/106) + stats price-centric = chỗ vỡ |
| Provider flow | resolver 3-tier (binding > system_config > null) | ✅ chuẩn DI, thiếu json_object capability branch |

---

## 7. Câu hỏi kiến trúc — trả lời (verified)

### Row-as-record: "tại sao không làm theo cách này?"
**ĐÃ làm** — có `table_csv`/`table_dual_index` + `ParsedEntity` (1 hàng=1 record). Nhưng làm **chưa tới**:
1. **PRICE-CENTRIC**: chỉ name+price+alias hạng-nhất; tồn/ngày/link→generic dump → mất khi header lỗi.
2. **PRICE_MIN_VND floor**: số nhỏ (404) bị lọc vì tưởng SKU/ordinal.
3. **Header fragile**: bảng nhiều-sheet + header 2-dòng + dòng-title "Kho lốp ROVELO" → col_N + title-as-data.
→ Fix = **EVOLVE** (attribute-generic + header-robust + bỏ price-floor cho cột phi-giá), KHÔNG REWRITE.

### N+1 bot (data khác → sửa code?): **PARTIAL**
- Owner khai `custom_vocabulary["column_roles"]` → KHÔNG sửa code (Tier-2 authoritative, `document_stats.py:392-482`).
- Owner KHÔNG khai (cả 3 bot hiện 0 khai → 100% Tier-1 inference) → inference price-centric/VN-commercial → **vỡ trên ngành lạ** → hiện phải sửa code.
- **UX đúng** = code tự xử khi upload (KHÔNG bắt khách khai column_roles); column_roles chỉ là override nâng-cao.

### qwen3 strategy
ROOT: routing match SUBSTRING `openai` trong `openai/claude` → ép nhánh strict `json_schema` mà qwen3 endpoint không honor; cột `supports_json_mode` ĐÃ CÓ DB nhưng helper KHÔNG đọc. Fix: `structured_output_mode=json_object` (đo trước) + routing theo capability + retry-repair bounded + nới UnderstandOutput optional. Config-driven qua `bot_model_bindings` purpose-based.

### Fabrication (bịa) — do đâu?
Tầng CHÍNH = **INGEST ~55%** (cột image mangle col_N, 0/496 entity key image) + sysprompt thiếu anti-fabricate-URL ~30% + `_is_field_like` cắt >120 char ~15%. Fix: append URL-grounding rule vào language_pack (sacred #2 — KHÔNG override) + role media first-class + re-ingest.

---

## 8. Thứ tự fix đề xuất (CHƯA làm)

- **P0** (~3-5h, 0-API, T1): repoint 4 binding sót → innocom · qwen3 `structured_output_mode=json_object` + đọc `supports_json_mode` · append URL-grounding rule language_pack · gỡ dead `condense_question`.
- **P1** (~1-2 ngày, re-ingest, T1): **ATTRIBUTE-index** (bỏ price-centric, mọi cột=attribute gắn nhãn header) · bỏ/sửa PRICE_MIN_VND cho cột phi-giá · header-merge + cross-sheet reconcile · role media first-class.
- **P2** (DEFER tới T1≥95%, T2/T3): tách god-node retrieve.py · gộp 2 decomposer · purge stale-entity (delete + filter deleted_at).

---

## 9. GIẢ THUYẾT mở (cần runtime verify trước khi tuyên bố fixed)
- Stale-entity 87% (số chính xác chưa đo trên serving path).
- Replay HALLU URL sau khi append anti-fabricate rule.
- Parse-rate qwen3 sau `json_object` (đo trước/sau).
- innocom endpoint có honor strict json_schema không.
- Sequencing P1: header-merge TRƯỚC re-ingest TRƯỚC purge (chưa verify thứ tự).

## 10. Tham chiếu
- `reports/DEEP_ANALYSIS_MULTIBOT_20260626.md` (workflow 1: ingest/retrieval/multibot/scorer).
- `reports/DEEP_MULTIBOT_ARCH_20260626.md` (workflow 2: N+1/fabrication/flow/qwen3).
- Commit `fc16f3b`. 4 alembic mới: chat_swap_innocom + embed_swap_ze1280 (+ rerank_swap/embed_swap_openai/drop_jina/rebind từ trước).
