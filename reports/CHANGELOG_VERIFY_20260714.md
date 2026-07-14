# CHANGELOG VERIFY — 2026-07-14

Mỗi thay đổi ghi đủ 5 mục để **verify độc lập**: `SỬA GÌ` · `NGUYÊN NHÂN (root cause)` · `BẰNG CHỨNG` · `ĐÃ VERIFY THẾ NÀO` · `RỦI RO / BLAST`.
Nhánh: `fix-260623-ingest-expert`. Base trước phiên: `71682a2`.

---

## NHÓM 1 — Fix do phiên này thực hiện

### C1 — REVERT `_COUNT_COL_TOKENS` (governance + domain-neutral)

| | |
|---|---|
| **File** | `src/ragbot/shared/document_stats.py` (−43 dòng) · **xóa** `tests/unit/test_stats_count_column_not_price.py` (−63 dòng) |
| **SỬA GÌ** | Gỡ frozenset `_COUNT_COL_TOKENS` + toàn bộ machinery role `"count"` (biến `count_idxs`, nhánh `elif role == "count"`, key `"count"` trong roles dict, block `count_cols` trong `_extract_entity_from_row`). Gỡ test ghim hành vi đó. |
| **NGUYÊN NHÂN** | Commit `5c4fdda` (2026-07-13, phiên trước) thêm `_COUNT_COL_TOKENS` — **tái phạm đúng quyết định owner đã bác 11 ngày trước** ở `6796cd9` (2026-07-02): *"denylist redesign fixed it but **grows the baked header vocabulary** … decided to **DROP ING-F1 entirely** … owners can already fix it per-bot via `custom_vocabulary["column_roles"]` … **no engine change**"*. Cùng bug-class (Q13/bug#13), cùng số ví dụ `40400` xuất hiện nguyên văn ở cả 2. Ngoài ra: 15 literal tiếng Việt (`so luong`, `ton kho`, `khoi luong`, `dien tich`…) trong core `src/` = **vi phạm domain-neutral rule** (tenant Thái/Indo/Nhật nhận 0 detection). |
| **BẰNG CHỨNG** | `git show 6796cd9` (body owner-decision) · `git show 5c4fdda -- document_stats.py` (hunk) · `git log --diff-filter=A -- tests/unit/test_stats_count_column_not_price.py` → thêm bởi `5c4fdda` · docstring test: *"which the owner did **not** declare as an attribute"* = chính Q13 known-limit owner để mở |
| **ĐÃ VERIFY** | (1) `git apply -R --check` sạch (không commit sau `5c4fdda` đụng file) · (2) **229 stats test PASS, 0 fail** (`pytest -k "stats or taxonomy or table_shape or crossdoc or tabular"`) · (3) **cơ chế owner CHẠY đúng** — `parse_table_chunks([c], custom_roles={'Quantity':'attribute'})` → `price_secondary=None`, Quantity giữ làm attribute → **revert KHÔNG mất coverage** · (4) grep literal VN trong `document_stats.py` → **0 hit** |
| **BLAST** | Không migration, không re-ingest, không reindex. Q13 (stock-as-price khi owner KHÔNG khai roles) trở lại thành **KNOWN LIMITATION** — đúng như owner đã quyết. Bot cần fix → khai `custom_vocabulary["column_roles"]` (đã hoạt động, chứng minh ở mục verify-3). |
| **⚠ Ghi chú tự-bắt-lỗi** | Test verify owner-path ĐẦU TIÊN của em **FAIL** vì em plumb sai API (`column_roles` là key chunk-dict ≠ tham số `custom_roles=`). Trace đường thật (`ingest_stages_final.py:507→532` → `parse_table_chunks(..., custom_roles=_declared)` → `_column_roles`) rồi chạy lại đúng mới PASS. **KHÔNG kết luận "mất coverage" từ test bịa sai** (rule#0). |

---

## NHÓM 2 — 4 thay đổi có SẴN trong working tree từ đầu phiên (em VERIFY, không tự tạo)

> Provenance: 4 file này đã ở trạng thái `M` khi phiên bắt đầu (không rõ tác giả). Em đã đọc + kiểm chứng từng cái an toàn trước khi commit. **62 test liên quan PASS, 0 fail.**

### C2 — Xóa Redis L2 write chết trong model resolver

| | |
|---|---|
| **File** | `src/ragbot/application/services/model_resolver/service.py` (−5 dòng logic) |
| **SỬA GÌ** | Gỡ khối `await self._cache.set(key, json.dumps(cfg.mask())…)` trong `resolve_runtime` |
| **NGUYÊN NHÂN** | Đó là **side-effect chết**: ghi vào Redis namespace key `model_runtime:*` (dựng bởi `_runtime_key`), nhưng key này **chỉ** được dùng bởi `_l1_put`/`_l1_get` (dict in-process). `_get_cached` (`:204-218`) dựng key **KHÁC** (`{CACHE_KEY_MODEL_RESOLVER}:…` = `ai_cfg:*`) và đọc namespace đó. → payload masked (no api_key) **không rehydrate được runtime config**, và **không ai đọc lại** `model_runtime:*` từ Redis. Mỗi lần resolve tốn 1 `json.dumps` + 1 Redis round-trip vô ích. |
| **BẰNG CHỨNG** | `grep _runtime_key\|_l1_get\|_l1_put\|_cache.get` → key `model_runtime:` chỉ ở `_cache_mixin.py:185/187/193`; `_get_cached:211` dùng `ai_cfg:*` |
| **ĐÃ VERIFY** | `pytest -k model_resolver` trong nhóm 62 test → PASS. `_cache.set` ở `_cache_mixin.py:269` (ghi `ai_cfg`, CÓ đọc lại ở `:217`) **giữ nguyên** — đúng. |
| **BLAST** | Win perf thuần. Không đổi hành vi (giá trị đó chưa bao giờ được đọc). |

### C3 — Sửa docstring nói dối `zeroentropy_embedder`

| | |
|---|---|
| **File** | `src/ragbot/infrastructure/embedding/zeroentropy_embedder.py` (2 dòng docstring) |
| **SỬA GÌ** | `2560-dim` → `1280-dim (matryoshka)` |
| **NGUYÊN NHÂN** | Docstring ghi 2560 trong khi wire dim thật + cột DB `document_chunks.embedding` = `vector(1280)`. Comment nói dối lái người đọc sai. |
| **BẰNG CHỨNG** | psql `vector_dims` = 1280 · `DEFAULT_ZEROENTROPY_EMBEDDING_DIM = 1280` |
| **ĐÃ VERIFY** | chỉ docstring, không đổi code. |
| **BLAST** | 0. |

### C4 — Scrub brand literal trong `llm_usage`

| | |
|---|---|
| **File** | `src/ragbot/shared/llm_usage.py` (1 dòng comment) |
| **SỬA GÌ** | `the innocom gateway` → `certain LLM gateways` |
| **NGUYÊN NHÂN** | Tên brand tenant trong file tracked = **vi phạm domain-neutral / tenant-literal rule** (CLAUDE.md). |
| **BẰNG CHỨNG** | CLAUDE.md "Tenant-identifier literals — CẤM HOÀN TOÀN trong file tracked" |
| **ĐÃ VERIFY** | chỉ comment. |
| **BLAST** | 0. |

### C5 — Gather-first cho MQ embedding cache pre-warm

| | |
|---|---|
| **File** | `src/ragbot/orchestration/query_graph.py` (~+14/−4 trong `_embed_query` pre-warm loop) |
| **SỬA GÌ** | Thay vòng lặp tuần tự `for qp in prefixed: cached = await get_cached_embedding(...)` bằng 1 `asyncio.gather(*(get_cached_embedding(...) for qp in prefixed))` |
| **NGUYÊN NHÂN** | Các Redis read per-variant độc lập → tuần tự trả `sum(RTT)`; gather trả `max(RTT)`. Async Rule 1 (gather-first) trong CLAUDE.md. |
| **BẰNG CHỨNG** | comment tại chỗ: *"Behavior-identical … same results, same order via enumerate over `prefixed`, same first-exception propagation"* |
| **ĐÃ VERIFY** | hành vi giữ nguyên (cùng kết quả, cùng thứ tự qua `zip(prefixed, cached_list)` + `enumerate`); test suite liên quan PASS. |
| **BLAST** | Win latency MQ pre-warm. Không đổi kết quả retrieval. |

---

## TỔNG

| # | Commit dự kiến | Files | Test |
|---|---|---|---|
| C1 | `revert(stats): drop _COUNT_COL_TOKENS — owner already ruled this a known-limit (6796cd9)` | `document_stats.py`, `-test_stats_count_column_not_price.py` | 229 pass |
| C2-C5 | `chore: verified working-tree cleanups (dead Redis L2 write · docstring · brand scrub · gather-first)` | 4 file | 62 pass |

**Chưa fix (đợi plan-v3):** 0.2 CB flap · 0.3 PII boundary · 0.4 pii_vi_phone · 0.5 degeneration · 1.x seed/RBAC · 2.x VN-segment/raw_bytes.
