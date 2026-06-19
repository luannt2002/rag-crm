# [T1-Smartness + T2-CostPerf] EXPERT-RAG MASTER PLAN — đưa TẤT CẢ flow ≥90/100

> Status: **DRAFT — chờ user approve trước khi đụng `src/`** (CLAUDE.md /plan mandate).
> Mindset binding: **CẤM ĐOÁN** (rule #0). Mỗi claim phải có evidence (log/psql/test/load-test/`file:line`). "Fix xong" CHỈ tuyên bố khi có load-test/eval output. EVOLVE-không-REWRITE.
> Baseline đo ngày 2026-06-18: tổng **~62/100** (kiến trúc ~78, wiring/verify ~45). Chi tiết: `reports/FLOW_SCORECARD_BASELINE_20260618.md`.
> Mục tiêu: mọi step của mọi flow **≥90/100, verified bằng số thật** trên 3 bot + 9 file corpus.

---

## 0. NGUYÊN TẮC ĐO (định nghĩa "90+")
Một step đạt ≥90 CHỈ khi đủ 3 điều, mỗi điều có evidence:
1. **Wired** — nối dây đúng (không dead-wire, không gated-off nếu đã chứng minh +lift), `file:line`.
2. **Tested** — unit test real-assertion pass + integration nếu có fixture.
3. **Runtime-verified** — load-test/eval trên corpus thật ra **số đạt gate** (conflate 0, Coverage≥0.95, Faithfulness≥0.9, HALLU=0, latency không tăng).
→ Thiếu #3 = trần 85 (chưa verify). Đây là lý do bắt buộc Phase 0+1 TRƯỚC khi fix.

---

## TÀI SẢN ĐÃ CÓ (verified — không xây lại)
| Thứ | Vị trí | Dùng cho |
|---|---|---|
| 3 bot + sysprompt | alembic `0236` (legal) + `0239` (spa/xe); bot_id `test-spa-id`/`chinh-sach-xe`/`thong-tu-09-2020-tt-nhnn`, ws `spa`/`xe`/`legal`, tenant `c2f66cb2-…` | seed |
| 9 URL corpus | `tests/scenarios/bot_sources.json` + `scripts/init_bots_from_urls.py` | ingest |
| Google Sheets/Docs parser | `infrastructure/parser/google_sheets_parser.py` + `google_link_service.py` (public-only, no OAuth) | ingest |
| Load-test 23 case/3 bot | `scripts/verify_fixes_loadtest.py` | gate |
| RAGAS golden 30Q/bot | `tests/eval/datasets/30Q_golden_*.json` + `tests/eval/golden_runner.py` | eval-CI |
| Backward-verify anchor | `request_chunk_refs(request_id,chunk_id,rank,score)` alembic 0109 → `document_chunks.id` → `documents.source_url` | debug-trace |
| Replay tool | `scripts/audit_logger_replay.py` + `PipelineAuditLogger` (JSONL, off by default) | debug-trace |
| token_ledger schema | alembic `0226` | token-stats |

---

## PHASE 0 — NỀN TẢNG: stack chạy được + 3 bot + corpus (UNBLOCK mọi verify)
**Mục tiêu**: từ DB rỗng → 3 bot seeded + 9 file ingested + chunk_count>0, server+worker chạy.
**Files (tạo/sửa):**
- `scripts/db/seed_3test_bots.py` (TẠO — model theo `seed_dev_drmedispa_bot.py`): 3 bot dùng tenant `c2f66cb2-…`, sysprompt lấy từ alembic 0236/0239 (KHÔNG copy literal — import/đọc), AI provider+model+29 binding/bot. Idempotent.
- `scripts/verify_fixes_loadtest.py` (SỬA 1 dòng): header `X-Loadtest-Bypass` → `X-Ragbot-Loadtest-Bypass` (khớp server constant — bug đã verify).
**Steps** (runbook `scripts/db/REBUILD_DEV_DB_RUNBOOK.md`):
1. schema (create_all + bootstrap_ddl + `alembic stamp head`) — vì migration chain gãy rev 0006.
2. `init_system_config.py` + `seed_rbac_permissions_s11b/s12a.py` + language_packs (alembic 0056).
3. `seed_3test_bots.py` → `redis-cli FLUSHDB`.
4. start FastAPI :3004 + document-worker; `until curl healthz`.
5. `python scripts/init_bots_from_urls.py --apply` → poll chunk_count tới ổn định.
**Gate (evidence bắt buộc):** psql `SELECT bot_id, count(*) FROM document_chunks JOIN … GROUP BY bot_id` → 3 bot đều >0 chunk. KHÔNG qua gate = KHÔNG sang Phase 1.
**Blocker đã biết:** Google file phải public; cần API key OpenAI+ZeroEntropy trong `.env`; worker phải chạy mới embed.

## PHASE 1 — XƯƠNG SỐNG VERIFY: eval-CI + debug-trace (lưới đo TRƯỚC khi fix)
**Mục tiêu**: mỗi câu sai → verify ngược được tới chunk→ingest; mỗi fix có gate số.
**1.1 eval-CI dual-gate** (anti-whack-a-mole):
- Wire `tests/eval/golden_runner.py` + `30Q_golden_*.json` → report **Coverage** (corpus có đáp án & bot trả đúng) vs **Faithfulness** (không bịa) 4-quadrant + silent-refusal rate.
- Chạy **baseline** trên 3 bot → ghi số mốc (đây là điểm "trước fix" thật, thay cho baseline tĩnh).
**1.2 debug-trace per-run** (yêu cầu "log input/output mỗi step + verify ngược"):
- Bật `PipelineAuditLogger` JSONL (env `RAGBOT_PIPELINE_AUDIT_ENABLED`).
- **Capture còn thiếu** (3 điểm, observability-only, gate sau `debug_full` flag — KHÔNG đổi answer):
  - `generate.py:617` — thêm `chunk_ids_in_prompt: list[str]` vào `request_steps[prompt_build].metadata_json` (biết LLM NHẬN chunk nào).
  - `rerank` — emit per-chunk survivor + rerank_score (biết chunk nào QUA topK).
  - `verify_fixes_loadtest.py::_ask()` — capture `X-Trace-Id` response header + dump per-case JSON.
- **Assembler**: script đọc `request_chunk_refs` + `document_chunks` + `messages` + JSONL → 1 file/run: `RUN_<ts>/case_<n>.json` chứa: query → route → candidates(score) → topK survivors → chunk_ids_in_prompt → answer → grounding map → tokens → **lineage chunk→document→source_url**.
**Gate:** chạy 1 case sai → mở file → truy được "chunk đáp án có ingest không / có retrieve không / có qua topK không / có vào prompt không" — đủ 4 mắt xích.

## PHASE 2 — FIX FLOW ĐỎ ≥90 (mỗi fix TDD + verified bằng lưới Phase 1)
**2.1 Ingest U4 chunk dead-wire** (35→90): wire block-pipeline `ingest_stages.py:501` `parsed_blocks=[]` → emit block list thật từ parser (rewrite cục bộ parser adapter — đúng phạm vi cho phép trong charter). Test: doc đa-dịch-vụ → chunk per-row exclusive, không co-occur. Verify: debug-trace chunk quality.
**2.2 Query retrieve BUG-1 conflate** (45→90): theo `plans/260618-phaseA-bug1-conflate/plan.md` — thêm `parse_price_of_entity_query` → `query_by_name_keyword`. TDD failing-test-first. Gate: conflate 0/6 phrasing, Coverage≥0.95.
**2.3 Grounding warn→enforce** (55→90): `local_guardrail.py:538-552` warn-only → enforce có ngưỡng + per-bot opt-out. Test: câu bịa số → bị chặn/HITL. Gate: HALLU=0 giữ, Coverage không tụt.
**Gate Phase 2:** load-test 3 bot — conflate 0, Coverage≥0.95, Faithfulness≥0.9, HALLU=0, no regression (legal Điều/Khoản vẫn đúng).

## PHASE 3 — TOKEN-STATS / LOG CENTER ≥90 (T2, CRM report)
Theo `reports/LOG_CENTER_OBSERVABILITY_DESIGN_20260618.md`:
**3.1 D1** capture rerank/embed usage (20→90): 5 adapter (jina/voyage/litellm rerank + jina/litellm embed) đọc `usage` → emit `AsyncDBTokenLedger`. ContextVar auto-attribute. Price từ `ai_models`. Provider không trả usage → flag, không bịa.
**3.2 D2** timeseries API (20→90): `GET /metrics/usage/timeseries?scope=bot|workspace|tenant|all&from&to&group_by&breakdown` đọc `token_ledger` (date_trunc), RBAC-scoped (bot≥20/ws≥60/tenant≥80/all=100).
**3.3 D3** cumulative token per-request (35→90): accumulator `GraphState`, sum ở `persist`.
**Gate:** psql token_ledger có row `action='rerank'/'embedding'` cost>0; API trả đúng tổng per bot/ws/tenant theo khoảng thời gian.

## PHASE 4 — BẬT TRÍ TUỆ có kiểm soát (A/B từng cờ DEFAULT=False)
Với mỗi cờ (cascade routing, query_complexity, adaptive_decompose, rewrite_and_mq, reflect, critique, hyde, contextual-enrich): bật → load-test → đo Coverage/latency/cost delta. **Chỉ giữ cờ +lift** (bài học Wave E: cascade lift noise -0.42%, không bịa "-30%"). Cờ không lift → để off + ghi lý do.
**Gate:** mỗi cờ có số đo trước/sau; node 45-55 → ≥90 chỉ khi chứng minh +lift, ngược lại giữ điểm thật + đánh dấu "intentionally off".

## PHASE 5 — RE-SCORE ≥90 + master report
Chạy lại scorecard với evidence load-test/eval. Cập nhật `STATE_SNAPSHOT.md`. Mỗi step ≥90 phải kèm dòng evidence (load-test case / eval number / psql). Step không đạt 90 → ghi rõ blocker, KHÔNG làm tròn lên.

---

## PHASE 6 — REFACTOR toàn bộ codebase theo chuẩn mới nhất (sau Phase 5 scoring)
**Trigger**: sau khi mọi flow ≥90 + verified. KHÔNG refactor trước (tránh đập cái đang chạy đúng — EVOLVE).
**Tiêu chuẩn**:
- **Clean code + SOLID**: mỗi class 1 trách nhiệm (SRP); tách god-class; DI/Port giữ nguyên.
- **Tách file**: KHÔNG file nào > **~1200 dòng**. God-file (vd `query_graph.py` ~3900 dòng, `generate.py`, `retrieve.py`) → tách theo node/luồng thành module con (1 node = 1 file).
- **Tách func/class**: func dài → tách hàm nhỏ thuần (pure) dễ test; tách class theo concern.
- **Clean comment**: bỏ comment rác/version-ref/temporal; chỉ giữ WHY. Docstring chuẩn.
- **Clean resource**: tách luồng rõ ràng → đọc/debug nhẹ; dead-code/orphan strategy stub → xóa.
**Ràng buộc (bắt buộc)**: refactor = behavior-preserving. Mỗi bước tách phải **full pytest pass + load-test/eval không đổi số** (regression=0). KHÔNG đổi logic khi đang tách. Surgical, từng file một, commit nhỏ.
**Verify**: `grep` file > 1200 dòng = 0; pytest pass; eval/load-test số không tụt.

## PHASE 7 — CLEAN `docs/` (single-source index)
**Mục tiêu**: `docs/` gọn — **1 file index detail** trỏ tới các file con cần thiết; xóa file thừa/lỗi thời.
- Audit toàn bộ `docs/` + `docs/master/` + `docs/_archive/` → phân loại: GIỮ (còn đúng) / MERGE (gộp) / XÓA (lỗi thời/trùng).
- Viết `docs/INDEX.md` (hoặc dùng `RAGBOT_MASTER.md`) = index detail → link tới file con còn lại.
- Xóa file orphan/lỗi thời (git giữ lịch sử, revert được). KHÔNG xóa file truth-of-record đang dùng (STATE_SNAPSHOT, CLAUDE.md, plan đang chạy).
**Verify**: mọi link trong INDEX resolve; không file docs mồ côi; `docs/` chỉ còn file cần.

## CROSS-CUTTING — CLAUDE.md no-guess reinforcement
User yêu cầu "update claude thêm cái gì không đoán". **Rule #0 CẤM ĐOÁN đã tồn tại** (đầu CLAUDE.md). Đề xuất bổ sung NHỎ (chờ approve): thêm vào BUG-mandate 1 dòng — *"CẤM tuyên bố 1 flow/step 'đạt ≥X/100' khi chưa có debug-trace backward-verify + load-test/eval output cho step đó; baseline tĩnh ≠ verified."* Surgical, không trùng lặp.

## THỨ TỰ & PHỤ THUỘC
```
0 (stack+seed+corpus) ──► 1 (eval-CI + debug-trace) ──► 2 (fix flow đỏ)
                                                    └──► 3 (token-stats)  [song song được sau 1]
2 ──► 4 (bật trí tuệ A/B) ──► 5 (re-score ≥90)
```
**0 và 1 là bắt buộc trước mọi thứ** — không có data + lưới đo thì mọi "fix" là đoán (đúng cái user cấm).

## RISK / ROLLBACK
- Mỗi fix Phase 2-4 có flag per-bot/system_config → tắt về hành vi cũ không redeploy.
- Capture debug-trace gate sau `debug_full` → off ở production, không tốn cost/latency thường.
- HALLU>0 sau bật cờ → revert cờ đó + post-mortem.

## VERIFY CHECKLIST mỗi phase (trước commit)
- [ ] Failing test trước (bug fix), pass sau.
- [ ] Evidence số thật (load-test/eval/psql) — KHÔNG "có vẻ ổn".
- [ ] grep zero-hardcode + no-version-ref + 4-key + RBAC + tenant-isolation.
- [ ] Self-audit sacred 11/11.
- [ ] Backward-verify 1 case qua debug-trace.
