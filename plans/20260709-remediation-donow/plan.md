# [T1/T2] Remediation plan — post-619Q-audit · 2026-07-09

> Detail đầy đủ: `reports/REMEDIATION_ROADMAP_20260709.md` (11 fix adversarial-verified) +
> `reports/CURRENT_TRUTH_20260709.md` (sự thật hợp nhất) + `reports/AUDIT_500Q_PART1_ANSWERS.md`.
> Correctness THẬT (reclassify DB): **xe 95.9% · spa 95.3%** · HALLU 1 (S-005). 8 câu SAI-logic thật.

## Phase 0 — Part-2 audit ✅ XONG (commit `c4a6f77`)
- Workflow `wf_5ed1e2b1-4ed` hoàn tất 37/37 agent (0 error, 3.33M token). Ghép DETERMINISTIC từ journal (synthesize bị discard do truncate 70k → chỉ thấy 5 section). `reports/AUDIT_500Q_PART2_ANSWERS.md` (283KB, 18 section + verify).
- Verify tally: **96 CONFIRMED / 14 REFUTED / 1 UNVERIFIABLE** (111 claim sắc nhất). 14 REFUTED = self-correction (cache KHÔNG chết 0%, metadata-extraction live-ON, char-cap value khác claim, DB head = seed migration hôm nay).
- **"Check tất cả luồng" = XONG** (Part-1 + Part-2 full 18 section).

## Phase 1 — verify 2 báo động (read-only, TRƯỚC fix) ✅ DONE 2026-07-09 post-compact
- [x] **HNSW `idx_scan=0`** → **KHÔNG PHẢI BUG** (planner-correct). EXPLAIN ANALYZE: query prod filter `record_bot_id` → `Bitmap Index Scan ix_chunks_bot_doc` + exact top-N heapsort = **1.4ms** (51–153 rows/bot); whole-table 906 rows = seq-scan 17.9ms. HNSW không đáng dùng ở scale này → planner đúng khi bỏ qua. HNSW = dead-weight vô hại (chỉ tốn ingest write-cost). **Latent scale-risk**: pgvector HNSW không push-down `record_bot_id` equality → 1 bot corpus lớn cần `hnsw.iterative_scan`/partial-index. KHÔNG ship fix now. (ST11)
- [x] **embedder CB trip 488×/30d** → **CONFIRMED chính xác 488×** (`event:embedder_circuit_open`), **đang diễn ra** (burst gần nhất Jul-7). Root: `zembed-1`/zeroentropy fail → CB open → cooldown ladder 60→75→90→105s (consec_fails 2/3/4). **Cùng class external-flakiness với innocom LLM** → thuộc track `defer_external` (failover/đổi provider), KHÔNG phải do_now code fix. (EM06)

**Kết luận Phase 1**: cả 2 "báo động" đều KHÔNG phải do_now code-fix. Actionable = Phase 2.

## Phase 2 — 6 fix do_now (mỗi cái: red-test → fix → đo → không ship nếu regress)
1. [ ] **#8 stats-delete scope** — `stats_index_repository.py:236 delete_by_document` hiện CHỈ nhận `record_document_id` (globally-unique UUID → docstring argue an toàn). PARITY GAP: **vector store** `delete_by_document` ĐÃ bắt buộc `record_tenant_id` kwarg (F14-CRIT-1, pin `test_pgvector_store_tenant_scoping.py:36/62`), nhưng **stats repo** thì chưa. Fix = thêm `record_bot_id` param + `AND record_bot_id=:bot_id` (defense-in-depth, mismatch→no-op) + update 2 caller `ingest_stages_final.py:560`/`delete_document.py:92` (cả 2 có `record_bot_id` sẵn — verify) + sửa test mock `assert_awaited_once_with(doc_id)` → thêm bot_id. **KHÔNG phải live-bug, là hardening** — frame đúng. Test: pin bound không inline. (tenant-isolation sacred) ✅ DONE (TDD red→green; repo sig `*, record_bot_id` + `AND record_bot_id=:bot_id` bound; 2 caller + 3 test updated; 24 pass).
2. [x] **#11 persist verdict → request_logs** ✅ DONE+VERIFIED. **DESIGN CORRECTED**: plan cũ "unlock is_correct" là SAI — `is_correct` là cột GRADING (HALLU=`is_correct IS FALSE`, owner/judge-marked); auto-ghi từ guard-verdict sẽ CONFLATE "grounding block" (HALLU đã CHẶN) thành "answer sai" → corrupt HALLU metric. Fix ĐÚNG: persist guard self-verdict → `metadata_json.guard_verdict` (observe-only, sacred#10 safe), **KHÔNG đụng is_correct**. Ship: helper `shared/verdict_meta.py::build_verdict_meta` (pure, `'grounding' in rule_id` substring bắt cả `llm_grounding_fail` mà `.startswith` miss) + wired 3 finalize path (`chat_routes.py:609/991`, `chat_stream.py:397`, `callbacks.py:230`; SKIP `pipeline.py:458` = quota-error path no state). Test: 7 unit `test_verdict_meta.py` + 100 existing pass. **Runtime proof**: row `73ffb91e` metadata_json.guard_verdict populated (before-fix rows = null).
3. [x] **#1 URL-ingest OOM cap** ✅ DONE+VERIFIED. `document_worker.py` helper `_fetch_url_bounded` thay `cli.get().content` (unbounded→OOM). 2 guard: Content-Length preflight + streaming accumulation (bắt cả chunked/lying CL) ceiling `DEFAULT_UPLOAD_STREAM_MAX_BYTES` 500 MiB (đã tồn tại `_21`, chunk 1 MiB — 0 hardcode mới). Sentinel `_RemoteBodyTooLarge(ValueError)` = terminal (KHÔNG trong `_TRANSIENT_INGEST_ERRORS` → mark failed không retry). `except _RemoteBodyTooLarge: raise` TRƯỚC broad-except → **không fall-through OCR refetch** (refetch URL bự = OOM lần 2). 6 unit `test_fetch_url_bounded.py` (preflight/stream-guard/lying-CL/malformed/terminal) + 59 consolidated pass. **Runtime**: restart → `/health` ok + workers ok, worker import sạch.
4. [x] **#7 health worker-liveness** ✅ DONE+VERIFIED. `app.py` expose `app.state.embedded_worker_tasks` (empty khi disabled); `health.py::_check_workers` → None (disabled=API-only, omit dep) / "ok" (all alive) / "down" (any `.done()` = supervised worker exit-on-crash, `_supervise` không auto-restart). 5 unit `test_health_worker_liveness.py` + 8 pass. **Runtime**: `/health` → `dependencies.workers:"ok"` (key mới, trước không có).
5. [x] **#5 config-parity guard** ✅ DONE. Widen scan `_all_pcfg_keys()` = query_graph + 18 `nodes/*.py` (43→**165 key**, trước mù 122 node-read). `_KNOWN_PCFG_DRIFT` = 9 key read-but-not-built, **verify rule#0**: cả 9 UNSEEDED trong system_config (constant-fallback benign, không dead-seed) + 0-hit worker builder (không remap). +2 teeth-test (scan phải cover nodes / allowlist không stale). 6 pass. Test-only, không runtime change.
6. [x] **#3a cliff-floor clone parity** ✅ DONE+VERIFIED. Const `_01:169` `0.05→0.2` (comment cũ nhắc "Jina v3" = STALE, stack giờ zerank-2; DB đã retune 0.2) + alembic `20260709_seed_cliff_floor_mmr_parity.py` seed cliff=0.2 (từ const) + mmr_similarity_threshold=0.88 (live-parity, KHÔNG flip 0.98=const, để #3b measure). **Sacred#7**: ON CONFLICT DO NOTHING → no-op trên live (verify: upgrade head, live values UNCHANGED 0.2/0.88), seed fresh clone. Seed jsonb format proven `number`/`float` khớp row cũ. 11 cliff test + 53 consolidated pass. resolve-chain verify: live đọc DB=0.2, const chỉ fallback → an toàn.

## Phase 3 — measure_first (load-test trước khi bật, mỗi cái flag riêng)
- [ ] **#9a+6p comparison** — unique per-leg synthetic id `f"{SENTINEL}:{leg}:{sub}"` trong `_stats_chunks_for_sub_queries` (`retrieve.py:188`) + atomic-identifier rule `query_decomposer.py:58` + i18n VI:502/EN:630. Đo `loadtest_graded xe` G-095/096/097/098 ≥9/10, HALLU=0, single-lookup G-043..054 ≤0 regress. (fix M1 G-097/098)
- [ ] **#4 CRAG mixed-branch top-1 rescue** — `grade.py:508` else-branch rescue top-1-by-rerank nếu clears floor; flag `DEFAULT_CRAG_MIXED_TOP1_RESCUE_ENABLED`. Đo S-039/046/075. (fix M3)
- [ ] #2 rrf_round_robin wire (`retrieve.py:1448`, gate INTENT_COMPARISON + thread `decompose_entity_quota` 2 builders) · #3b MMR 0.88→0.98 flip · #10 grounding 30s→8s per-bot.

## Phase 4 — GAP (roadmap chưa design — cần thiết kế + đo)
- [ ] **GAP-A arrival G-063/067** — bảng "NGÀY VỀ" là chunk RIÊNG không link entity giá → khi intent=arrival, booster-retrieve/attach arrival table chunk.
- [ ] **GAP-B S-005** — claim-fidelity Tier-1b (non-numeric grounding gate) cho hotline/contact fabrication.

## Phase 5 — chuẩn-hoá deploy/worker (owner hỏi, low-risk)
- [ ] **worker-assert**: boot raise nếu `embed_workers_enabled AND uvicorn_workers>1` (chống double-consume; hoặc tách API/worker horizontal mode đã có).
- [ ] **`/ready` route** (readiness: DB+Redis+graph+workers) → deploy.sh poll chạy đúng.
- [ ] **config-gate vào CI** (`.github/workflows/config-gate.yml` required: spin-DB → alembic → `check_config_completeness --strict` → chặn build) + hard-gate trong deploy.sh.
- [ ] **fail-loud REQUIRED-split**: `get_required(key)` raise; giữ `get(key,default)` cho optional — CHỈ sau khi config-gate CI xanh (thứ tự bắt buộc).

## Constraints (nhắc)
- Rule#0: mỗi fix có red-test + đo trước/sau. Không ship mù (đã revert 2 lần: comparison multi-code, chitchat).
- Sacred #7: DB content qua alembic tracked. Secrets (DB password / tenant IP / brand host) KHÔNG vào file tracked.
- Commit chỉ khi owner duyệt; feature branch. z_luannt_deubg.txt KHÔNG commit.
