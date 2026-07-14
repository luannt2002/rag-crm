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

**Chưa fix (đợi plan-v3):** 0.2 CB flap · 0.3 PII boundary · 0.4 pii_vi_phone · 1.x seed/RBAC · 2.x VN-segment/raw_bytes.

---

## NHÓM 3 — Batch SHIP-NOW plan-v3 (phiên 2026-07-14, sau /compact) — 6 task, red→green TDD

> Mọi task: **red-test viết TRƯỚC, chứng minh ĐỎ ở HEAD** (quote output), XANH sau fix. Import-check 7 module OK. Sacred guards (version-ref/secret/magic-number) = 0. **74 test file-touched PASS.**

### S1 — 0.5 degeneration tokenizer (false-positive bảng markdown)

| | |
|---|---|
| **File** | `src/ragbot/shared/degeneration.py` · `constants/_14_...py` (−1 constant) · `+tests/unit/test_answer_degeneration.py` (4 test) |
| **SỬA GÌ** | Thêm `_tokens()` strip markdown scaffolding (`\|`*#>~_=[]`) + filter alnum; **bỏ clause `top_token_ratio`** khỏi `is_degenerate` (giữ giá trị trong return cho log); xóa constant `DEFAULT_DEGENERATION_TOP_TOKEN_RATIO_MAX`. |
| **NGUYÊN NHÂN** | `answer.split()` đếm `\|`/`---` là "word" → bảng markdown hợp lệ đẩy `top_token_ratio` (bảng feature) hoặc `distinct_word_ratio` (bảng 60 dòng) qua ngưỡng → **false-positive degenerate**. `top_token_ratio` recall trên degeneration thật = **0** (bug#8 ttr=0.167 < 0.40; dwr/dtr đã bắt). |
| **BẰNG CHỨNG** | Probe đo ratio 4 biến thể fix (scratchpad): feature_matrix strip-only vẫn FLAG (ttr .429), long-table droptt-only vẫn FLAG (dwr .088) → **cần CẢ HAI**. HEAD test đỏ: `assert True is False`. |
| **ĐÃ VERIFY** | 14/14 pass (10 cũ + 4 mới, 2 discriminator chứng minh cần cả strip+drop-ttr + recall guard bug#8). Grep constant sau xóa = 0 ref. |
| **BLAST** | Restart (latent). KHÔNG đo được live (0/1683→0/1683 — plan §7); TDD-only. #10 ✅ (giảm substitution). |

### S2 — 1.4-U1 xóa test MMR stale + fix comment

| | |
|---|---|
| **File** | `tests/unit/orchestration/test_per_intent_caps.py` · `constants/_14_...py` (comment) |
| **SỬA GÌ** | Thay `test_default_constant_aggregation_loosens_threshold` (so map vs **constant**, cả hai =0.98 → đỏ) bằng invariant đúng (within-map: aggregation/comparison > factoid). Fix comment "Default 0.88" (module default giờ =0.98). **KHÔNG đụng threshold/map value.** |
| **NGUYÊN NHÂN** | `002-D`/`9f93804` nâng `DEFAULT_MMR_SIMILARITY_THRESHOLD` 0.88→0.98 (bằng aggregation) → assert `map["aggregation"] > constant` thành `0.98 > 0.98` = False. Runtime thật: DB global=0.88, map loosens aggregation lên 0.98 (test khác cover). |
| **BẰNG CHỨNG** | HEAD đỏ: `AssertionError: assert 0.98 > 0.98`. |
| **ĐÃ VERIFY** | 43/43 pass. |
| **BLAST** | 0 runtime (test+comment). 1.4-U2 (collapse map) DEFER (§8, cần A/B factoid). |

### S3 — 3.6 dense-query NFC (BẮT thêm path thứ 3 plan bỏ sót)

| | |
|---|---|
| **File** | `orchestration/query_graph.py` (`_embed_query` + `_prewarm_embedding_cache`) · `orchestration/nodes/retrieve.py` (`_embed_batch_queries`) · `+tests/unit/test_embed_query_nfc.py` |
| **SỬA GÌ** | `normalize_vn()` (NFC, shared helper) trên query text ở **3 embed path dense** trước prefix/cache — cache key byte-identical, embedder nhận NFC. |
| **NGUYÊN NHÂN** | Sparse (`pgvector_store.py:389`) + ingest đã NFC; **dense không** → query NFD (iOS/macOS IME) embed ra vector khác corpus NFC = silent dense-recall miss. **Test behavioral bắt path thứ 3** (`_embed_batch_queries` retrieve.py:1007) mà plan §6 chỉ nêu 2 path — source-pin sẽ bỏ sót. |
| **BẰNG CHỨNG** | RED-at-HEAD chứng minh nghiêm ngặt: `git stash` 2 file fix → 2 test đỏ (embedder nhận `giá lốp` decomposed); pop → xanh. Fixture = `NFD(NFC-hợp-lệ)` round-trip. |
| **ĐÃ VERIFY** | 2 test NFC pass; regression MQ/prewarm/pipeline/normalization = 30 pass, 0 fail. |
| **BLAST** | Restart. Latent (0 NFD trong 6527 row — mẫu ASCII harness). Cache-key đổi cho NFD input (miss 1 lần, tự lành). |

### S4 — 3.5 cache-guard content-hash (RuleSet.version)

| | |
|---|---|
| **File** | `application/services/guardrail_rule_loader.py` · `+tests/unit/infrastructure/guardrails/test_guardrail_rule_loader.py` (3 test) |
| **SỬA GÌ** | `RuleSet.version` từ **monotonic counter** (`_version_counter += 1`) → **content-hash** (`_ruleset_content_version()`: sha256 nội dung rule, order-independent, empty→`""`). |
| **NGUYÊN NHÂN** | Counter reset mỗi restart + bump cả khi content KHÔNG đổi + khác nhau giữa process → **desync** cache. Là "loader counter" plan §6 chỉ đích danh bỏ. **0 consumer đọc `.version`** (dormant, safe đổi type int→str). |
| **BẰNG CHỨNG** | HEAD đỏ: 2 loader fresh đều `version=1` (change-test `1==1` fail); empty `assert 1 == ''` fail. |
| **ĐÃ VERIFY** | 9/9 loader pass; guardrail suites rộng 97 pass, 0 fail. |
| **BLAST** | Restart. **Honest scope:** đây là *primitive* content-hash; wire vào `_compute_bot_cache_version` end-to-end = KHÔNG 0-dep (thread ruleset-hash vào state + cache-flush) → follow-up, ngoài SHIP-NOW. |

### S5 — 1.3a wire `check_config_completeness` vào CI (advisory)

| | |
|---|---|
| **File** | `+.github/workflows/config-completeness.yml` · `+tests/unit/test_config_completeness_wired.py` |
| **SỬA GÌ** | Workflow mới: Postgres + `alembic upgrade head` + chạy gate script (baseline-aware). **Advisory (`continue-on-error`)** tới khi 1.1 seed fresh-DB; comment flip-to-required. |
| **NGUYÊN NHÂN** | `grep .github check_config_completeness` = 0 hit; `README_DEVOPS:133` hứa "required CI step, red=no build". Gate tồn tại, **guard 0**. |
| **BẰNG CHỨNG** | HEAD red-test: `assert []`. YAML valid, gate step present. Smoke read-only (prod-denom): contract 172/seeded 264/0 NEW gate-blocking, exit 0. |
| **ĐÃ VERIFY** | red→green (workflow chứa literal). **⚠ màu gate fresh-CI = L8** (không chạy Actions ở đây được); advisory tránh block team khi 1.1 chưa seed. |
| **BLAST** | Near-zero (advisory). Target = FRESH DB, KHÔNG prod (wrong-denominator). |

### S6 — SEC.1 IDOR write-fence (cherry-pick `integ-260624-wave1`, adapted)

| | |
|---|---|
| **File** | `infrastructure/repositories/document_repository.py` · `conversation_repository.py` · `+tests/unit/repositories/test_idor_write_fence.py` (5 test) |
| **SỬA GÌ** | Nhánh UPDATE của `save()`: từ `session.get(Model, id)` (PK-only) + mutate → **một statement `update().where(id==.. AND record_tenant_id==tid).returning(...)`**; 0 row → INSERT tenant-scoped. Message INSERT force `record_tenant_id=tid` (không tin field entity). |
| **NGUYÊN NHÂN** | HEAD check `document.record_tenant_id` (**self-declared** — attacker set = tid mình, pass) rồi `session.get(PK)` fetch **victim row** rồi mutate + commit **không** check `existing.record_tenant_id==tid` → clobber cross-tenant. RLS inert (DSN chạy `postgres` BYPASSRLS) → fence repo là lớp DUY NHẤT. |
| **BẰNG CHỨNG** | RED-at-HEAD nghiêm ngặt: stash 2 repo file → 5 test đỏ (HEAD dùng `session.get` → FakeSession không có `.get` → AttributeError); pop → 5 pass. |
| **ĐÃ VERIFY** | 5/5 IDOR pass; repositories dir 29 pass; ingest/persist/conversation/document/chat 559 pass, 0 fail. **Adapt:** giữ HEAD `_persistable_metadata` (KHÔNG lấy `dict()` của branch = regress `has_raw_content`). |
| **BLAST** | Restart. Fence semantics = parity (cùng field set; `onupdate` fire trên bulk). **⚠ Honest (GIẢ THUYẾT):** chưa chứng minh entrypoint external nào cho caller truyền cross-tenant `document.id`; finding vững = control tầng repo THIẾU ở HEAD + fix đã viết+test. RLS cutover (RỦI RO §8.1) vẫn cần owner. |

**Không đo được / để lại honest:** 0.5 & 3.6 live (mẫu load-test không có table-query/NFD); 3.5 wiring end-to-end (0-dep vi phạm); 1.3a fresh-CI gate color (L8, không có Actions runner); SEC.1 external IDOR entrypoint (GIẢ THUYẾT).
